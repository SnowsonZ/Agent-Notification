"""Read-only collectors for the locally verified desktop formats."""

import hashlib
import json
import sqlite3
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from uuid import UUID

from codex_rollout_events import RolloutReader

# 人工启动方式白名单（rollout session_meta.originator）：桌面应用与交互式 TUI
# （codex-tui=新版 TUI，codex_cli_rs=旧版 CLI；两者都是终端手敲形态）。
# 白名单之外（workbench/ACP/codex_exec 等程序化拉起）标记 agent：不通知、不进待查看、
# 日报默认不计。新入口被误判时把 originator 加进这里即可（2026-09-17 近 7 天实测分布）。
HUMAN_ORIGINATORS = {"Codex Desktop", "codex-tui", "codex_cli_rs"}
# 悬置回合判定窗口：活回合会持续向 rollout 流式写 token_count/reasoning 行，超过
# 24h 无任何新行的 running 必为死回合（进程被杀且此后未重开），修复为 interrupted。
CODEX_STALE_TURN_SECONDS = 24 * 3600
# zcode 流式活性窗口：part 表在回合期间持续落行，2 分钟内有过更新即视为回合进行中；
# 窗口只影响 running 的「拾起时机」——状态一旦推成 running，仍由真实回合结束事件收口。
ZCODE_STREAM_WINDOW_MS = 120_000


def spawn_origin(originator):
    return "user" if originator in HUMAN_ORIGINATORS else "agent"


def seconds(value):
    if isinstance(value, (int, float)):
        return value / 1000 if value > 100_000_000_000 else value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).timestamp()
        except ValueError:
            pass
    return 0


def collect_codex(store, home):
    root = home / ".codex/sessions"
    if not root.exists():
        return {"status": "unavailable", "reason": "session directory missing"}
    titles = {}
    activity = {}
    index = home / ".codex/session_index.jsonl"
    if index.exists():
        for line in index.open():
            try:
                record = json.loads(line)
                titles[record["id"]] = str(record["thread_name"])[:300]
                activity[record["id"]] = seconds(record.get("updated_at"))
            except (ValueError, KeyError, TypeError):
                continue
    errors, changed = 0, 0
    # 游标批量载入：逐文件 store.meta 各开一次 SQLite 连接，几百个 rollout 在
    # app 的 3 秒刷新节拍下太浪费；一次性 LIKE 查询取全量后内存比对。
    # （codex 相关的一次性迁移收敛在 migrations.py，随 refresh 统一执行。）
    with store.db() as db:
        prefix = len("codex-cursor:")
        cursors = {
            row["key"][prefix:]: json.loads(row["value"])
            for row in db.execute(
                "SELECT key, value FROM metadata WHERE key LIKE 'codex-cursor:%'"
            )
        }
    baseline = store.meta("started_at")
    for path in root.rglob("rollout-*.jsonl"):
        key, signature = None, None
        try:
            stat = path.stat()
            key = "codex-cursor:" + str(path)
            prior = cursors.get(str(path))
            signature = [stat.st_ino, stat.st_mtime_ns, stat.st_size]
            if prior and prior["signature"] == signature:
                if prior.get("error"):
                    errors += 1
                    continue
                if prior["sid"] in titles:
                    store.patch(
                        "codex",
                        prior["sid"],
                        title=titles[prior["sid"]],
                        activity_at=activity.get(prior["sid"]),
                    )
                continue
            with path.open() as file:
                first = json.loads(file.readline())
            meta = first.get("payload", {})
            if first.get("type") != "session_meta":
                continue
            originator = meta.get("originator")
            desktop = originator == "Codex Desktop"
            cli_origin = bool(originator) and not desktop
            if not desktop and not cli_origin:
                continue
            if cli_origin:
                # CLI/exec 会话首次纳入时以当下为提醒基线：历史完成不轰炸。
                cli_baseline = store.meta("codex-cli:baseline")
                if cli_baseline is None:
                    cli_baseline = time.time()
                    store.set_meta("codex-cli:baseline", cli_baseline)
            sid = meta["id"]
            try:
                sid = str(UUID(path.stem[-36:]))
            except ValueError:
                pass
            reader = RolloutReader(
                path,
                sid,
                allow_ancestry=True,
                originator=originator if cli_origin else None,
            )
            if prior and prior.get("sid") == sid:
                reader.offset, reader.identity, reader.session = (
                    prior["offset"],
                    tuple(prior["identity"]),
                    prior.get("validated_session", sid),
                )
                reader.open_turn = prior.get("open_turn")
            batch = reader.poll()
            project = reader.metadata.get("cwd", "") if reader.metadata else None
            if desktop:
                locator = {
                    "kind": "url",
                    "url": "codex://threads/" + quote(sid, safe=""),
                }
            else:
                # CLI/exec 会话：按会话 ID 经 `codex resume` 恢复；
                # 携带 rollout 路径供「已打开则聚焦」做 fd 匹配。
                locator = {"kind": "cli", "cwd": project or "", "file": str(path)}
            attention_baseline = cli_baseline if cli_origin else baseline
            store.patch(
                "codex",
                sid,
                title=titles.get(sid),
                project=project,
                activity_at=activity.get(sid),
                locator=locator,
                origin=spawn_origin(str(originator or "")),
            )
            for event in batch:
                stamp = seconds(event["timestamp"])
                state = {
                    "task_started": "running",
                    "task_complete": "idle",
                    "turn_aborted": "interrupted",
                }[event["event"]]
                # Import old history without flooding the new inbox.
                attention = (
                    event["event"] == "task_complete" and stamp >= attention_baseline
                )
                store.event(
                    "codex",
                    sid,
                    event_id=event["event_id"],
                    timestamp=stamp,
                    state=state,
                    attention=attention,
                    token=event["turn_id"],
                )
            store.set_meta(
                key,
                {
                    "signature": signature,
                    "sid": sid,
                    "offset": reader.offset,
                    "identity": reader.identity,
                    "validated_session": reader.session,
                    "open_turn": reader.open_turn,
                    "cli": cli_origin,
                    "file": str(path) if cli_origin else None,
                },
            )
            changed += 1
        except (OSError, ValueError, KeyError, TypeError) as error:
            errors += 1
            if key is not None and signature is not None:
                store.set_meta(
                    key, {"signature": signature, "error": type(error).__name__}
                )
    # 悬置回合兜底：cursor 全量已在上文载入，running 行据此映射回 rollout 文件；
    # 活回合持续流式写行，mtime 超窗的 running 必为死回合，修复为 interrupted
    # （不是凭静默推断"完成"，不声明成功，也不抬升待查看）。
    cursor_files = {
        value.get("sid"): Path(key) for key, value in cursors.items() if value.get("sid")
    }
    for row in store.rows():
        if row["provider"] != "codex" or row["state"] != "running":
            continue
        locator = row["locator"]
        candidate = cursor_files.get(row["session_id"])
        if candidate is None and locator.get("kind") == "cli" and locator.get("file"):
            candidate = Path(locator["file"])
        if candidate is None:
            continue
        try:
            mtime = candidate.stat().st_mtime
        except OSError:
            continue
        if time.time() - mtime > CODEX_STALE_TURN_SECONDS:
            store.event(
                "codex",
                row["session_id"],
                event_id=row["session_id"] + ":stale-turn",
                timestamp=mtime + 1,
                state="interrupted",
            )
    return {
        "status": "degraded" if errors else "ok",
        "changed_files": changed,
        "errors": errors,
    }


def collect_claude(store, home):
    root = home / "Library/Application Support/Claude/claude-code-sessions"
    if not root.exists():
        return {"status": "unavailable", "reason": "desktop metadata missing"}
    grouped = defaultdict(list)
    errors = 0
    for path in root.rglob("local_*.json"):
        try:
            if path.stat().st_size > 4_000_000:
                errors += 1
                continue
            data = json.loads(path.read_text())
            if isinstance(data.get("cliSessionId"), str):
                grouped[data["cliSessionId"]].append(data)
        except (OSError, ValueError, AttributeError):
            errors += 1
    for sid, records in grouped.items():
        if len(records) != 1 or errors:
            store.patch(
                "claude",
                sid,
                origin="user",
                locator={
                    "kind": "unavailable",
                    "reason": "desktop identity ambiguous or incomplete",
                },
            )
            continue
        data = records[0]
        desktop = data.get("sessionId", "")
        if not desktop.startswith("local_"):
            continue
        # Desktop 登记表成员 = 用户在 Desktop 界面驱动，origin 权威覆盖为 user
        # （hook 侧无头判定可能误判 Electron 内嵌形态，此处每次刷新自愈）。
        store.patch(
            "claude",
            sid,
            origin="user",
            title=str(data.get("title") or "Claude · " + sid[:12])[:300],
            project=data.get("cwd", ""),
            hidden=bool(data.get("isArchived")),
            activity_at=seconds(data.get("lastActivityAt")),
            locator={
                "kind": "url",
                "url": "claude://code/continue?session=" + quote(desktop, safe=""),
            },
        )
        if data.get("error"):
            stamp = seconds(data.get("errorAt") or data.get("lastActivityAt"))
            store.event(
                "claude",
                sid,
                event_id=f"claude-error:{sid}:{stamp}",
                timestamp=stamp,
                state="failed",
                attention=stamp >= store.meta("started_at"),
            )
    # claude CLI 直启会话（hook 创建、无桌面元数据）：旧版被隐藏，迁移为可见的
    # cli 定位（cwd 已由 hook 事件写入 project）；标题从转写首条用户消息提取。
    with store.db() as db:
        legacy = db.execute(
            "SELECT session_id, project FROM sessions WHERE provider='claude' "
            "AND locator='{}' AND hidden=1 AND project!=''"
        ).fetchall()
    for sid, project in legacy:
        store.patch(
            "claude",
            sid,
            locator={"kind": "cli", "cwd": project},
            project=project,
            hidden=False,
        )
    titled = 0
    for row in store.rows():
        if row["provider"] != "claude" or row["locator"].get("kind") != "cli":
            continue
        if not row["locator"].get("file"):
            transcript = next(
                (home / ".claude/projects").glob(f"*/{row['session_id']}.jsonl"), None
            )
            if transcript:
                locator = dict(row["locator"], file=str(transcript))
                store.patch("claude", row["session_id"], locator=locator)
            elif not store.meta("claude-cli-title-missing:" + row["session_id"]):
                store.set_meta("claude-cli-title-missing:" + row["session_id"], True)
        if row["title"] and not row["title"].startswith("claude ·"):
            continue
        title = _claude_cli_title(home, row["session_id"], store)
        if title:
            store.patch("claude", row["session_id"], title=title)
            titled += 1
    return {
        "status": "degraded" if errors else "ok",
        "sessions": len(grouped),
        "errors": errors,
        "cli_titled": titled,
        "note": "Live status requires hooks in sessions started after setup",
    }


def _claude_cli_title(home, sid, store, limit=80):
    """claude CLI 转写首条用户消息作展示标题（≤80 字符）；找不到转写返回 None 并记忆，
    避免每次刷新重复全目录查找。"""
    if store.meta("claude-cli-title-missing:" + sid):
        return None
    transcript = next((home / ".claude/projects").glob(f"*/{sid}.jsonl"), None)
    if transcript is None:
        store.set_meta("claude-cli-title-missing:" + sid, True)
        return None
    with transcript.open(errors="replace") as file:
        for line in file:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if record.get("type") != "user" or record.get("isMeta"):
                continue
            message = record.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                text = " ".join(
                    block.get("text", "")
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                )
            else:
                continue
            text = " ".join(text.split())
            if text and not text.startswith("<"):
                return text[:limit]
    return None


def collect_zcode(store, home):
    path = home / ".zcode/v2/tasks-index.sqlite"
    if not path.exists():
        return {"status": "unavailable", "reason": "task index missing"}
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT task_id,title,workspace_path,task_status,unread_at,updated_at,archived,deleted,last_unread_at FROM tasks"
        ).fetchall()
    finally:
        connection.close()
    latest_turns = {}
    stream_sessions = {}
    runtime = home / ".zcode/cli/db/db.sqlite"
    turn_source_error = None
    if runtime.exists():
        connection = sqlite3.connect(runtime.as_uri() + "?mode=ro", uri=True)
        try:
            for turn in connection.execute(
                "SELECT session_id,turn_id,status,started_at,completed_at FROM turn_usage ORDER BY started_at,turn_id"
            ):
                latest_turns[turn[0]] = turn
            # 流式活性：part 表在回合期间持续落行（流式文本与工具调用状态）。
            horizon = int(time.time() * 1000) - ZCODE_STREAM_WINDOW_MS
            for session_id, message_id, part_ms in connection.execute(
                "SELECT session_id, message_id, time_updated FROM part "
                "WHERE time_updated > ? ORDER BY time_updated",
                (horizon,),
            ):
                stream_sessions[session_id] = (message_id, part_ms)
        except sqlite3.Error:
            turn_source_error = "turn_usage schema unavailable"
        finally:
            connection.close()
    else:
        turn_source_error = "turn_usage database missing"
    baseline = store.meta("started_at")
    matched = 0
    for (
        sid,
        title,
        project,
        status,
        unread,
        updated,
        archived,
        deleted,
        last_unread,
    ) in rows:
        store.patch(
            "zcode",
            sid,
            title=str(title or sid)[:300],
            project=project or "",
            hidden=bool(archived or deleted),
            activity_at=seconds(updated),
            locator={"kind": "zcode", "task_id": sid},
        )
        state = {
            "completed": "idle",
            "error": "failed",
            "running": "running",
            "waiting": "waiting",
        }.get(status, "unknown")
        if sid in latest_turns:
            matched += 1
            # v1 used task updated_at as the event clock, which also advances on
            # renames/views. Switch clocks once so real completion is not hidden.
            with store.db() as db:
                # SELECT 是稳态快路径（旗标在则零写）；真正落迁移时以 INSERT OR IGNORE
                # 的 rowcount 做原子仲裁——并发刷新同时见旗标缺失时只允许一个赢家跑
                # UPDATE，否则 revision 双跳会废掉一个合法 ack 的 CAS。
                migrated = db.execute(
                    "SELECT 1 FROM metadata WHERE key=?",
                    ("zcode-turn-clock:" + sid,),
                ).fetchone()
                if (
                    migrated is None
                    and db.execute(
                        "INSERT OR IGNORE INTO metadata VALUES (?,?)",
                        ("zcode-turn-clock:" + sid, "true"),
                    ).rowcount
                ):
                    db.execute(
                        "UPDATE sessions SET event_at=0,revision=revision+1 WHERE provider='zcode' AND session_id=?",
                        (sid,),
                    )
            _, turn_id, turn_status, started, completed = latest_turns[sid]
            start = seconds(started)
            store.event(
                "zcode",
                sid,
                event_id=f"zcode-turn-start:{sid}:{turn_id}",
                timestamp=start,
                state="running",
                attention=False if start >= baseline else None,
            )
            end_applied = False
            if completed is not None and turn_status in (
                "completed",
                "error",
                "cancelled",
            ):
                end = seconds(completed)
                final_state = {
                    "completed": "idle",
                    "error": "failed",
                    "cancelled": "interrupted",
                }[turn_status]
                attention = (turn_status != "cancelled") if end >= baseline else None
                end_applied = store.event(
                    "zcode",
                    sid,
                    event_id=f"zcode-turn-end:{sid}:{turn_id}:{turn_status}:{completed}",
                    timestamp=end,
                    state=final_state,
                    attention=attention,
                    token=f"{turn_id}:{turn_status}",
                )
            # Runtime rows land only when a turn ends, so while a turn is live
            # (including permission waits) the latest row is still the previous
            # turn and its terminal state would stick. The index does carry
            # liveness; trust it only when it advanced past the recorded end,
            # and not in the same refresh where that end first landed — a
            # lagging index must not flip a just-recorded completion back to
            # running. The stamp never exceeds that end, so the real next end
            # event can always override.
            if (
                status == "running"
                and not end_applied
                and completed is not None
                and seconds(updated) > seconds(completed)
            ):
                store.event(
                    "zcode",
                    sid,
                    event_id=f"zcode-live-running:{sid}:{turn_id}:{completed}",
                    timestamp=seconds(completed),
                    state="running",
                    attention=None,
                )
        else:
            signature = hashlib.sha256(
                json.dumps([sid, status, updated]).encode()
            ).hexdigest()
            store.event(
                "zcode",
                sid,
                event_id="zcode-snapshot-v2:" + signature,
                timestamp=seconds(updated),
                state=state,
                attention=None,
            )
        # 流式活性通道（2026-09-20 补第二通道）：任务索引在回合开始后可能长期停在
        # completed（实测整轮 40 分钟未翻转），仅靠索引的 live-running 推断抓不到
        # 进行中的回合。两分钟窗口内 part 有过更新的会话视为回合进行中：事件只负责
        # 把状态推成 running，真实回合结束事件（turn_usage 落行）时间戳更晚会正常
        # 覆盖；为防流式事件的墙钟时间晚于实际完成时间反而挡住结束事件，仅当部件
        # 更新晚于最近已记录回合完成时才发。事件 ID 挂最新消息 ID——下个回合消息
        # ID 变化即可再次触发，不会被去重账本挡住；attention=False 与回合开始事件
        # 同语义（新一轮运行清除过期待处理）。
        if sid in stream_sessions:
            message_id, part_ms = stream_sessions[sid]
            latest = latest_turns.get(sid)
            if latest is None or (latest[4] is not None and part_ms > latest[4]):
                store.event(
                    "zcode",
                    sid,
                    event_id=f"zcode-stream-running:{sid}:{message_id}",
                    timestamp=time.time(),
                    state="running",
                    attention=False,
                )
        # Native unread markers supplement real turn events; clearing a blue dot
        # must not clear this inbox's independently acknowledged attention state.
        marker = max(seconds(unread), seconds(last_unread))
        if marker and (unread or marker >= baseline):
            store.event(
                "zcode",
                sid,
                event_id=f"zcode-unread:{sid}:{marker}",
                timestamp=marker,
                state=state,
                attention=True,
                token=f"native-unread:{marker}",
            )
    return {
        "status": "degraded" if turn_source_error else "ok",
        "sessions": len(rows),
        "turn_sessions": matched,
        "note": turn_source_error
        or "Real turn lifecycle with independent inbox acknowledgement",
    }


def _sweep_managed_directories(store):
    """agy/opencode 受管理行：项目目录被删的条目隐藏（2026-09-16 用户决定：目录缺失
    不允许跳转、条目不可见）；目录恢复存在时自动取消隐藏。"""
    with store.db() as db:
        rows = db.execute(
            "SELECT id, project FROM sessions "
            "WHERE provider IN ('agy','opencode') AND project != ''"
        ).fetchall()
    for row_id, project in rows:
        exists = Path(project).expanduser().is_dir()
        with store.db() as db:
            db.execute(
                "UPDATE sessions SET hidden=? WHERE id=?", (0 if exists else 1, row_id)
            )


def collect_agy_titles(store, home):
    """给已有受管理 agy 行补标题（来自 summaries 库）；不产生新会话与事件。

    agy hooks 载荷没有标题字段，标题只能从 conversation_summaries 反查；
    只 patch store 里已存在的 provider='agy' 行，因此非受管理会话不会因此入箱。
    """
    path = home / ".gemini/antigravity-cli/conversation_summaries.db"
    if not path.exists():
        return {"status": "unavailable", "reason": "summaries database missing"}
    targets = {
        row["session_id"]: row["id"] for row in store.rows() if row["provider"] == "agy"
    }
    titled = 0
    if targets:
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        try:
            # agy 的标题常在 preview 而 title 为空：按 title → preview 兜底。
            for conversation_id, title, preview, workspace in connection.execute(
                "SELECT conversation_id,title,preview,workspace_uris FROM conversation_summaries"
            ):
                if conversation_id not in targets:
                    continue
                display = str(title or "").strip() or str(preview or "").strip()
                store.patch("agy", conversation_id, title=display[:300] or None)
                titled += 1
        finally:
            connection.close()
    return {
        "status": "ok",
        "titled": titled,
        "note": "Titles from summaries db; hooks carry no title field",
    }


def refresh(store, home=None):
    home = Path.home() if home is None else home
    health = {}
    from migrations import run as run_migrations
    from pi_titles import collect_pi_titles

    # 一次性迁移统一在采集前执行（见 migrations.py）；_sweep 是每轮都跑的目录状态同步。
    run_migrations(store, home)
    _sweep_managed_directories(store)
    for name, collector in [
        ("codex", collect_codex),
        ("claude", collect_claude),
        ("zcode", collect_zcode),
        ("pi", collect_pi_titles),
        ("agy", collect_agy_titles),
    ]:
        try:
            health[name] = collector(store, home)
        except (OSError, ValueError, KeyError, sqlite3.Error) as error:
            health[name] = {"status": "unavailable", "reason": type(error).__name__}
    store.set_meta("health", {"checked_at": time.time(), "sources": health})
    return health
