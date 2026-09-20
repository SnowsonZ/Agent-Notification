"""一次性数据迁移：每条用 metadata 键守护只跑一次，收敛在此，避免在采集器热路径里堆积。

在 refresh() 开头统一执行（app 每 15 秒全量扫描一次 rows --refresh，迁移随下一轮
全量落地）；hook 等轻量路径不跑迁移——codex 回填要全量扫 rollout 文件，不能占 hooks
的执行预算。新迁移加进 run()；跨大版本时可按守护键退役已全员跑过的旧迁移，控制本文件长度。
"""

import json
import time
from pathlib import Path
from uuid import UUID


def backfill_claude_tmp_origins(store):
    """项目目录在系统临时目录下的 claude 会话是工具拉起（如 wb-gate-claude-*），
    存量行注册当时无进程信号可考，按目录约定一次性回填。"""
    if store.meta("claude-origin:tmp-backfill-v1"):
        return
    with store.db() as db:
        rows = db.execute(
            "SELECT id, project FROM sessions WHERE provider='claude' AND origin='user'"
        ).fetchall()
        for key, project in rows:
            if project and project.startswith(
                ("/var/folders/", "/private/var/folders/", "/tmp/", "/private/tmp/")
            ):
                db.execute("UPDATE sessions SET origin='agent' WHERE id=?", (key,))
    store.set_meta("claude-origin:tmp-backfill-v1", True)


def codex_cli_error_cache_reset(store):
    """①CLI 纳入的中间态曾把非 Desktop 文件以 originator mismatch 缓存为错误，清掉
    让其重解析（Desktop 混合身份文件保持缓存不动）；②存量 cli 行的 locator 补
    rollout 文件路径（供跳转做 fd 聚焦）。老游标没有来源记录，读 rollout 首行
    判定一次后即写入缓存。"""
    if store.meta("codex-cli:error-cache-reset"):
        return
    with store.db() as db:
        cached = db.execute(
            "SELECT key, value FROM metadata WHERE key LIKE 'codex-cursor:%'"
        ).fetchall()
    for row in cached:
        value = json.loads(row["value"])
        path = Path(row["key"].replace("codex-cursor:", ""))
        if value.get("error") or value.get("cli-migrated"):
            continue
        try:
            originator = (
                json.loads(path.open(errors="replace").readline())
                .get("payload", {})
                .get("originator")
            )
        except (OSError, ValueError):
            continue
        if originator == "Codex Desktop":
            value["cli-migrated"] = True
            with store.db() as db:
                db.execute(
                    "UPDATE metadata SET value=? WHERE key=?",
                    (json.dumps(value), row["key"]),
                )
            continue
        sid = value.get("sid")
        if sid:
            store.patch(
                "codex", sid, locator={"kind": "cli", "cwd": "", "file": str(path)}
            )
        value["cli-migrated"] = True
        with store.db() as db:
            db.execute(
                "UPDATE metadata SET value=? WHERE key=?",
                (json.dumps(value), row["key"]),
            )
    store.set_meta("codex-cli:error-cache-reset", True)


def codex_origin_backfill(store, home):
    """一次性回填存量行的启动方式：逐 rollout 读首行 originator（游标缓存的文件
    不会在主循环重读首行）。sid 按来源分流（2026-09-21 两轮修订）：Desktop 用
    payload.id（分叉文件归父线程，与采集器采信语义一致）；CLI/exec 用文件名尾
    会话 id（CLI 分叉合同是独立子会话行，用 payload.id 会建出父幽灵行），文件
    名尾非合法 UUID 时回落 payload.id。守卫键保证已跑过的库不受影响。"""
    from inbox_sources import spawn_origin

    if store.meta("codex-origin:backfill-v1"):
        return
    root = home / ".codex/sessions"
    for path in root.rglob("rollout-*.jsonl"):
        try:
            first = json.loads(path.open(errors="replace").readline())
        except (OSError, ValueError):
            continue
        meta = first.get("payload", {})
        if first.get("type") != "session_meta":
            continue
        sid = meta.get("id")
        if meta.get("originator") != "Codex Desktop":
            try:
                sid = str(UUID(path.stem[-36:]))
            except ValueError:
                pass
        if isinstance(sid, str) and sid:
            store.patch(
                "codex", sid, origin=spawn_origin(str(meta.get("originator") or ""))
            )
    store.set_meta("codex-origin:backfill-v1", True)


def purge_passive_opencode(store):
    """opencode 由被动扫描切换为受管理模式（2026-09-16 用户决定），旧被动行
    （directory 定位）删除；受管理运行后同会话 ID 会以 managed 定位重建。"""
    if store.meta("opencode:managed-migration"):
        return
    with store.db() as db:
        removed = db.execute(
            "DELETE FROM sessions WHERE provider='opencode' "
            "AND json_extract(locator,'$.kind')='directory'"
        ).rowcount
    store.set_meta(
        "opencode:managed-migration",
        {"removed": removed, "at": time.time()},
    )


def codex_dangling_turn_repair(store, home):
    """codex 悬置回合一性收口（2026-09-20）：「进行中」视图暴露出部分行的 rollout
    里最后一条回合事件是无终态的 task_started（进程被杀），running 永久滞留。逐
    running 行读 rollout 佐证后才修复：悬置开始后出现过重开标记
    （thread_settings_applied），或文件 mtime 超过 24h 无新行（活回合会持续流式
    写行）——判 interrupted，不凭静默推断完成、不声明成功。此后由采集器内联规则
    （重开标记合成 turn_aborted + mtime 兜底）持续防复发，本迁移只兜存量。"""
    from inbox_sources import CODEX_STALE_TURN_SECONDS, seconds

    if store.meta("codex:stale-turn-repair-v1"):
        return
    prefix = len("codex-cursor:")
    with store.db() as db:
        rows = db.execute(
            "SELECT session_id, locator FROM sessions "
            "WHERE provider='codex' AND state='running'"
        ).fetchall()
        cursor_paths = {
            json.loads(value).get("sid"): key[prefix:]
            for key, value in db.execute(
                "SELECT key, value FROM metadata WHERE key LIKE 'codex-cursor:%'"
            )
        }
    repaired = []
    for sid, locator_json in rows:
        locator = json.loads(locator_json or "{}")
        target = None
        if locator.get("kind") == "cli" and locator.get("file"):
            target = Path(locator["file"])
        elif cursor_paths.get(sid):
            target = Path(cursor_paths[sid])
        if target is None or not target.exists():
            continue
        last_kind = None
        marker_ts = None
        try:
            for line in target.open(errors="replace"):
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                kind = (record.get("payload") or {}).get("type") or ""
                if kind in ("task_started", "task_complete", "turn_aborted"):
                    last_kind = kind
                    marker_ts = None
                elif kind == "thread_settings_applied" and last_kind == "task_started":
                    marker_ts = record.get("timestamp")
        except OSError:
            continue
        if last_kind != "task_started":
            continue
        try:
            mtime = target.stat().st_mtime
        except OSError:
            continue
        # 标记路径以重开时刻为事件时钟；无标记路径才用 mtime+1。不能取两者的
        # max——文件 mtime 是"刚被读过/写过"的当下时刻，会把事件时钟推到未来，
        # 压制之后到达的真实事件（时间戳守卫拒旧）。
        if marker_ts:
            try:
                stamp = seconds(marker_ts)
            except (TypeError, ValueError):
                stamp = mtime + 1
        elif time.time() - mtime <= CODEX_STALE_TURN_SECONDS:
            # 无重开标记且文件仍在流式写行：可能是活回合，不动。
            continue
        else:
            stamp = mtime + 1
        store.event(
            "codex",
            sid,
            event_id=sid + ":stale-turn",
            timestamp=stamp,
            state="interrupted",
        )
        repaired.append(sid)
    store.set_meta(
        "codex:stale-turn-repair-v1", {"repaired": repaired, "at": time.time()}
    )


def claude_title_cache_revalidate(store, home):
    """旧版标题负缓存按会话无条件永久记忆（转写落盘竞争锁死修复前写入），
    新逻辑读时无条件短路会继承锁死。一次性清除「转写现已存在」的陈旧标记，
    让标题在下轮全量扫描补齐；转写确实缺失的标记保留（语义仍正确）。
    2026-09-20 曾在开发机手工清过一次，本迁移把它带进升级路径（其它库/重置场景）。"""
    if store.meta("claude-cli-title:cache-revalidate-v1"):
        return
    prefix = "claude-cli-title-missing:"
    cleared = 0
    with store.db() as db:
        keys = [
            row[0]
            for row in db.execute(
                "SELECT key FROM metadata WHERE key LIKE ?", (prefix + "%",)
            )
        ]
        for key in keys:
            sid = key[len(prefix) :]
            if next((home / ".claude/projects").glob(f"*/{sid}.jsonl"), None):
                db.execute("DELETE FROM metadata WHERE key=?", (key,))
                cleared += 1
    store.set_meta("claude-cli-title:cache-revalidate-v1", True)
    if cleared:
        store.set_meta("claude-cli-title:cache-revalidate-count", cleared)


def codex_fork_replay(store):
    """Desktop resume 分身文件（仅祖先 meta，自身 id 在文件名）曾被整文件跳过，
    行停在 unknown（2026-09-21 修复 reader 采信逻辑）。清掉受害行的游标让下一轮
    全量重读；只清行仍为 unknown 的，非受害行（状态已从其它文件落定）不动。"""
    if store.meta("codex:fork-replay-v1"):
        return
    prefix = "codex-cursor:"
    cleared = 0
    with store.db() as db:
        rows = db.execute(
            "SELECT key, value FROM metadata WHERE key LIKE ?", (prefix + "%",)
        ).fetchall()
        for key, value in rows:
            try:
                cursor = json.loads(value)
            except ValueError:
                continue
            sid = cursor.get("sid")
            if not sid or cursor.get("validated_session"):
                continue
            state = db.execute(
                "SELECT state FROM sessions WHERE session_id=?", (sid,)
            ).fetchone()
            if state and state["state"] == "unknown":
                db.execute("DELETE FROM metadata WHERE key=?", (key,))
                cleared += 1
    store.set_meta("codex:fork-replay-v1", True)
    if cleared:
        store.set_meta("codex:fork-replay-count", cleared)


def codex_desktop_fork_merge(store, home):
    """Desktop 分叉文件（<父id>_<分身id> 文件名）的回合曾归属分身 id：分身不在
    session_index，Desktop 侧栏与 codex:// 跳转都不识别（2026-09-21 实测跳转
    报错）。reader 已改为归属父线程；本迁移清掉 Desktop 分叉文件的游标让下一轮
    全量按父线程重放，并归档已按分身 id 建出的行（url 定位、id 为分叉文件名尾）。
    CLI 变体（游标 cli=true）不动——独立会话行的语义保持。"""
    if store.meta("codex:desktop-fork-merge-v1"):
        return
    prefix = "codex-cursor:"
    cleared = 0
    fork_tails = set()
    with store.db() as db:
        rows = db.execute(
            "SELECT key, value FROM metadata WHERE key LIKE ?", (prefix + "%",)
        ).fetchall()
        for key, value in rows:
            try:
                cursor = json.loads(value)
            except ValueError:
                continue
            if cursor.get("cli"):
                continue
            name = key.rsplit("/", 1)[-1]
            name = name.removesuffix(".jsonl")
            if "_" not in name[20:]:
                continue
            tail = name[-36:]
            try:
                from uuid import UUID

                fork_tails.add(str(UUID(tail)))
            except ValueError:
                continue
            db.execute("DELETE FROM metadata WHERE key=?", (key,))
            cleared += 1
        for sid in fork_tails:
            db.execute(
                "UPDATE sessions SET hidden=1 WHERE provider='codex' AND session_id=? "
                "AND locator LIKE '%\"kind\": \"url\"%'",
                (sid,),
            )
    store.set_meta("codex:desktop-fork-merge-v1", True)
    if cleared:
        store.set_meta("codex:desktop-fork-merge-count", cleared)


def codex_subagent_origin(store, home):
    """guardian/thread_spawn 子代理会话曾按 originator 误判人工，并生成 Desktop
    不识别的 codex:// 深链（2026-09-21 用户报"显示却跳转报错"）。身份归 agent；
    locator 仅对「Desktop 且不在 session_index」的幽灵深链清空，已进索引的
    thread_spawn 与 CLI 变体保留原定位。只更新已存在的行（不创建）；身份以
    payload.id 为准，不猜文件名尾（分叉归属教训）。"""
    if store.meta("codex:subagent-origin-v1"):
        return
    from inbox_sources import _codex_session_index, codex_subagent_source

    titles, _ = _codex_session_index(home)
    updated = 0
    with store.db() as db:
        for path in (home / ".codex/sessions").rglob("rollout-*.jsonl"):
            try:
                with path.open() as file:
                    first = json.loads(file.readline())
            except (OSError, ValueError):
                continue
            if first.get("type") != "session_meta":
                continue
            meta = first.get("payload", {})
            if not codex_subagent_source(meta):
                continue
            sid = meta.get("id")
            if not isinstance(sid, str) or not sid:
                continue
            row = db.execute(
                "SELECT state FROM sessions WHERE provider='codex' AND session_id=?",
                (sid,),
            ).fetchone()
            if row is None:
                continue
            if meta.get("originator") == "Codex Desktop" and sid not in titles:
                db.execute(
                    "UPDATE sessions SET origin='agent', locator='{}' "
                    "WHERE provider='codex' AND session_id=?",
                    (sid,),
                )
            else:
                db.execute(
                    "UPDATE sessions SET origin='agent' "
                    "WHERE provider='codex' AND session_id=?",
                    (sid,),
                )
            updated += 1
    store.set_meta("codex:subagent-origin-v1", True)
    if updated:
        store.set_meta("codex:subagent-origin-count", updated)


def run(store, home):
    backfill_claude_tmp_origins(store)
    codex_cli_error_cache_reset(store)
    codex_origin_backfill(store, home)
    purge_passive_opencode(store)
    codex_dangling_turn_repair(store, home)
    claude_title_cache_revalidate(store, home)
    codex_fork_replay(store)
    codex_desktop_fork_merge(store, home)
    codex_subagent_origin(store, home)
