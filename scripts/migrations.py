"""一次性数据迁移：每条用 metadata 键守护只跑一次，收敛在此，避免在采集器热路径里堆积。

在 refresh() 开头统一执行（app 每 3 秒轮询 rows --refresh，秒级落地）；hook 等
轻量路径不跑迁移——codex 回填要全量扫 rollout 文件，不能占 hooks 的 3 秒预算。
新迁移加进 run()；跨大版本时可按守护键退役已全员跑过的旧迁移，控制本文件长度。
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
    不会在主循环重读首行）。sid 推导与主循环一致（文件名 UUID 优先）避免重复行。"""
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
        sid = str(meta.get("id") or "")
        try:
            sid = str(UUID(path.stem[-36:]))
        except ValueError:
            pass
        if sid:
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


def run(store, home):
    backfill_claude_tmp_origins(store)
    codex_cli_error_cache_reset(store)
    codex_origin_backfill(store, home)
    purge_passive_opencode(store)
    codex_dangling_turn_repair(store, home)
