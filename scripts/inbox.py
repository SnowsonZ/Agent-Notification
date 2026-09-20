#!/usr/bin/env python3
"""Unified inbox commands used by the CLI and native menu-bar app."""

import argparse
import json
import os
from pathlib import Path
import shlex
import sqlite3
import subprocess
import sys
import tempfile
import time
from inbox_store import DEFAULT_ROOT, Store, effective_origin, receive
from inbox_sources import refresh
from providers import RESUME_ARGS, RESUMABLE_PROVIDERS
from agent_launch import (
    iterm_select_tty,
    launch as launch_agent,
    ttys_for_command,
    ttys_for_open_file,
)


def setup_claude(root):
    directory = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude")))
    path = directory / "settings.json"
    if path.is_symlink():
        raise ValueError("linked Claude settings require explicit handling")
    original = path.read_text() if path.exists() else ""
    settings = json.loads(original) if original else {}
    command = shlex.join(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--root",
            str(root),
            "hook",
            "--provider",
            "claude",
        ]
    )
    count = 0
    hooks = settings.setdefault("hooks", {})
    for event in [
        "SessionStart",
        "UserPromptSubmit",
        "Stop",
        "Notification",
        "PermissionRequest",
        "SessionEnd",
    ]:
        groups = hooks.setdefault(event, [])
        if not any(
            item.get("command") == command
            for group in groups
            for item in group.get("hooks", [])
        ):
            groups.append(
                {"hooks": [{"type": "command", "command": command, "timeout": 3}]}
            )
            count += 1
    if not count:
        return 0
    directory.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".session-manager-", dir=directory)
    try:
        with os.fdopen(fd, "w") as file:
            json.dump(settings, file, ensure_ascii=False, indent=2)
            file.write("\n")
        if (path.read_text() if path.exists() else "") != original:
            raise ValueError("Claude settings changed during setup")
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    return count


def spawn_origin_from_tty(tty):
    """macOS `ps -o tty=` 的判定规则：?? = 无控制终端 = 无头拉起（agent）。

    空输出多半是查询失败，按人工保留（宁可漏过滤不误藏）。"""
    return "agent" if (tty or "").strip() == "??" else "user"


# 直接父进程为终端 shell = 用户手敲；为其它进程（node/python 等工具进程）= agent 拉起。
SHELL_PARENTS = {
    "zsh",
    "bash",
    "sh",
    "fish",
    "dash",
    "ksh",
    "tcsh",
    "csh",
    "nu",
    "pwsh",
    "powershell",
    "login",
    "sshd",
    "tmux",
    "screen",
}


def spawn_origin_from_parent(name):
    name = (name or "").strip().lower()
    if not name:
        return "user"
    return "user" if name in SHELL_PARENTS else "agent"


# 声明式来源标记：拉起 claude 的进程设置环境变量即可精确声明启动方式
# （hook 继承 claude 进程环境，环境沿进程树继承），优先级高于全部启发式。
DECLARED_ORIGIN_ENV = "SESSION_MANAGER_ORIGIN"


def declared_origin():
    value = (os.environ.get(DECLARED_ORIGIN_ENV) or "").strip().lower()
    return value if value in ("agent", "user") else None


def desktop_registry_hit(home, sid, *, max_age=172800):
    """Claude Desktop 内嵌会话登记表（claude-code-sessions 的 cliSessionId）。

    Desktop 会话的 claude 进程同样无终端（Electron 内嵌），但那是用户在
    Desktop 界面驱动的：无头判为 agent 前必须先查登记表。只扫近期活跃的
    登记文件（会话在跑，登记文件的 activity 更新就在几分钟内），控制开销。"""
    root = Path(home) / "Library/Application Support/Claude/claude-code-sessions"
    if not root.exists():
        return False
    cutoff = time.time() - max_age
    try:
        for path in root.rglob("local_*.json"):
            try:
                stat = path.stat()
                if stat.st_mtime < cutoff or stat.st_size > 4_000_000:
                    continue
                if json.loads(path.read_text()).get("cliSessionId") == sid:
                    return True
            except (OSError, ValueError, AttributeError):
                continue
    except OSError:
        return False
    return False


def claude_spawn_origin(sid):
    """注册时的启动方式信号。声明优于推断：
    0) 环境变量 `SESSION_MANAGER_ORIGIN=agent|user`（拉起方显式声明，最高优先级；
       未设置或非法值忽略）；以下为未声明时的启发式兜底：
    1) claude 进程有控制终端时看直接父进程：终端 shell = 手敲（人工）；
       node/python 等工具进程 = agent 拉起。
    2) 无控制终端（无头）时先查 Desktop 登记表：命中 = Desktop 内嵌会话 = 人工
       （控制终端被子进程继承，tty 单独判定会把"终端里跑的工具拉起的 claude"
       误判成人工；同理 Electron 内嵌会被误判成 agent，故必须查表）。
    3) 登记表未命中 = 无头 CLI = agent。
    任一步查不到都不猜，按人工保留；agent 用 shell 包装或 pty 拉起交互形态无法
    识别——这类场景请用声明变量，属信号上限。"""
    declared = declared_origin()
    if declared:
        return declared
    sid = str(sid or "")
    pid = str(os.getppid())
    try:
        tty = subprocess.run(
            ["ps", "-o", "tty=", "-p", pid], capture_output=True, text=True, timeout=3
        ).stdout
        if spawn_origin_from_tty(tty) == "agent":
            if sid and desktop_registry_hit(Path.home(), sid):
                return "user"
            return "agent"
        parent = subprocess.run(
            ["ps", "-o", "ucomm=", "-p", pid], capture_output=True, text=True, timeout=3
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return "user"
    return spawn_origin_from_parent(parent)


# 项目目录在系统临时目录下的 claude 会话回填已收敛到 migrations.py（随 refresh 执行）。


def display_rows(store, all_rows, include_agents=False):
    from session_binding import alive, connect

    with connect(store.root) as db:
        bindings = {
            row["run_id"]: dict(row) for row in db.execute("SELECT * FROM bindings")
        }
    result = store.rows(unread_only=not all_rows)
    if not include_agents:
        # agent 拉起的会话默认不出现在收件箱（不通知、不进待查看）；数据保留在库，
        # rows --include-agents 或应用内开关可查看审计；目录规则（手动改判）读时覆盖。
        rules = store.origin_rule_index()
        result = [row for row in result if effective_origin(rules, row) != "agent"]
    for row in result:
        locator = row["locator"]
        available = locator.get("kind") in ("url", "zcode", "cli")
        if locator.get("kind") == "managed":
            binding = bindings.get(locator.get("run_id"))
            live = bool(
                binding
                and binding["session_id"] == row["session_id"]
                and alive(store.root, binding["run_id"])
            )
            # 绑定死亡但 provider 支持按 ID 恢复（agy/opencode）：跳转改为受管理恢复，仍可点。
            available = live or (
                row["provider"] in RESUMABLE_PROVIDERS and bool(row["session_id"])
            )
        row["open_available"] = available
    return result


def _live_binding_for(root, provider, session_id, *, exclude=None):
    """同 provider+session_id 的其它活绑定（会话在新标签恢复/改绑后行定位滞后时使用）。"""
    from session_binding import alive, connect

    with connect(root) as db:
        rows = [
            dict(row)
            for row in db.execute(
                "SELECT * FROM bindings WHERE provider=? AND session_id=?",
                (provider, session_id),
            )
        ]
    live = [
        row["run_id"]
        for row in rows
        if alive(root, row["run_id"]) and row["run_id"] != exclude
    ]
    if len(live) > 1:
        raise ValueError("multiple live bindings for this session")
    return live[0] if live else None


def _open_ok(store, row, revision, navigation):
    """打开成功的统一收尾：按 UI/通知快照里的 revision 做 CAS 确认（不是导航后的
    新 revision），打开期间到达的新事件因此不会被旧动作吞掉。"""
    acknowledged = store.acknowledge(
        row["id"], row["revision"] if revision is None else revision
    )
    print(
        json.dumps(
            {
                "opened": True,
                "acknowledged": acknowledged,
                "new_activity_preserved": not acknowledged,
                "navigation": navigation,
            }
        )
    )
    return 0


def open_session(store, key, revision=None):
    row = store.get(key)
    locator = row["locator"]
    scripts = Path(__file__).resolve().parent
    if locator.get("kind") == "managed":

        def probe(run_id):
            return subprocess.run(
                [
                    sys.executable,
                    str(scripts / "iterm_probe.py"),
                    "--state-dir",
                    str(store.root),
                    "--run-id",
                    run_id,
                    "--agent-session-id",
                    row["session_id"],
                    "--activate",
                ],
                capture_output=True,
                text=True,
                timeout=100,
            )

        result = probe(locator["run_id"])
        if result.returncode != 0:
            # 绑定死亡：先找同会话的其它活绑定（改绑/恢复后行定位滞后的情形）。
            try:
                alternate = _live_binding_for(
                    store.root,
                    row["provider"],
                    row["session_id"],
                    exclude=locator.get("run_id"),
                )
            except ValueError as error:
                print(error, file=sys.stderr)
                return 1
            if alternate:
                result = probe(alternate)
        if result.returncode != 0 and row["provider"] in RESUMABLE_PROVIDERS:
            # 仍无活绑定：经包装器在新标签受管理恢复目标会话（注册新绑定，
            # 下一回合事件把条目改挂新绑定）；恢复前不自动确认。
            directory = row["project"]
            if not directory or not Path(directory).expanduser().is_dir():
                print("session directory is missing", file=sys.stderr)
                return result.returncode
            try:
                launch_agent(
                    row["provider"],
                    directory,
                    args=RESUME_ARGS[row["provider"]] + (row["session_id"],),
                )
            except ValueError as error:
                print(error, file=sys.stderr)
                return 1
            return _open_ok(store, row, revision, "managed_resume")
        if result.returncode != 0:
            print(
                result.stderr or result.stdout or "Open failed",
                file=sys.stderr,
                end="\n",
            )
            return result.returncode
        return _open_ok(store, row, revision, "verified_adapter")
    elif locator.get("kind") == "zcode":
        command = [sys.executable, str(scripts / "zcode_focus.py"), row["session_id"]]
    elif locator.get("kind") == "cli":
        # claude/codex CLI 直启会话。与受管理 provider 同语义的"已打开则聚焦"：
        # 1) 命令行匹配（经包装器恢复的会话，命令行携带会话 ID）；
        # 2) 会话文件 fd 匹配（运行中的 claude/codex 持有转写/rollout 文件）；
        # 都没有才在新标签按会话 ID 恢复；目录缺失仍拒绝。
        args = RESUME_ARGS[row["provider"]] + (row["session_id"],)
        ttys = []
        try:
            ttys = ttys_for_command(args)
            session_file = locator.get("file", "")
            if session_file:
                ttys += ttys_for_open_file(session_file)
        except OSError as error:
            print(f"process lookup failed: {error}", file=sys.stderr)
            return 1
        seen = []
        for tty in ttys:
            if tty in seen:
                continue
            seen.append(tty)
            try:
                focused = iterm_select_tty(tty)
            except ValueError as error:
                print(error, file=sys.stderr)
                return 1
            if focused:
                return _open_ok(store, row, revision, "iterm_focus")
        directory = row["project"] or locator.get("cwd", "")
        if not directory or not Path(directory).expanduser().is_dir():
            print("session directory is missing", file=sys.stderr)
            return 1
        try:
            launch_agent(row["provider"], directory, args=args)
        except ValueError as error:
            print(error, file=sys.stderr)
            return 1
        return _open_ok(store, row, revision, "cli_resume")
    elif locator.get("kind") == "url":
        url = locator.get("url", "")
        if not url.startswith(("codex://threads/", "claude://code/continue?session=")):
            raise ValueError("unsupported session URL")
        command = ["/usr/bin/open", url]
    else:
        raise ValueError("no verified opener for this session")
    result = subprocess.run(command, capture_output=True, text=True, timeout=100)
    if result.returncode != 0:
        print(
            result.stderr or result.stdout or "Open failed", file=sys.stderr, end="\n"
        )
        return result.returncode
    # Use the UI/notification's revision, not a fresh revision after navigating.
    return _open_ok(
        store,
        row,
        revision,
        "os_dispatch" if locator["kind"] == "url" else "verified_adapter",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    sub = parser.add_subparsers(dest="action", required=True)
    rows = sub.add_parser("rows")
    rows.add_argument("--all", action="store_true")
    rows.add_argument("--refresh", action="store_true")
    rows.add_argument(
        "--include-agents",
        action="store_true",
        help="include agent-spawned sessions (hidden by default)",
    )
    sub.add_parser("sync")
    watch = sub.add_parser("watch")
    watch.add_argument("--interval", type=float, default=3)
    ack = sub.add_parser("ack")
    ack.add_argument("id")
    ack.add_argument("--revision", type=int, required=True)
    batch = sub.add_parser("ack-batch")
    batch.add_argument(
        "--items",
        required=True,
        help="JSON array of [id, revision] pairs from one list snapshot",
    )
    org = sub.add_parser("origin")
    org.add_argument("--id", required=True)
    org.add_argument(
        "--set", dest="set_origin", required=True, choices=["agent", "user"]
    )
    org.add_argument(
        "--rule-project", help="同时沉淀为该目录的覆盖规则（读时优先于自动分类）"
    )
    op = sub.add_parser("open")
    op.add_argument("id")
    op.add_argument("--revision", type=int)
    sub.add_parser("setup")
    hook = sub.add_parser("hook")
    hook.add_argument("--provider", choices=["claude"], required=True)
    sub.add_parser("agents")
    launcher = sub.add_parser("launch")
    launcher.add_argument("--agent", required=True)
    launcher.add_argument("--dir", required=True)
    daily = sub.add_parser("daily-report")
    daily.add_argument("--date", help="YYYY-MM-DD, defaults to today")
    daily.add_argument(
        "--overview",
        action="store_true",
        help="heatmap totals + top projects instead of one day",
    )
    daily.add_argument("--days", type=int, default=182)
    daily.add_argument("--top", type=int, default=5)
    daily.add_argument(
        "--refresh",
        action="store_true",
        help="rescan sources for a past day even if a finalized report is cached",
    )
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    try:
        store = Store(root)
        if args.action == "hook":
            raw = sys.stdin.buffer.read(1_048_577)
            if len(raw) > 1_048_576:
                raise ValueError("oversized hook")
            payload = json.loads(raw)
            sid = payload.get("session_id")
            if not isinstance(sid, str) or not sid:
                raise ValueError("missing session ID")
            event = payload.get("hook_event_name")
            if event == "Notification":
                if payload.get("notification_type") == "permission_prompt":
                    event = "PermissionRequest"
                else:
                    return 0
            receive(
                root,
                "claude",
                sid,
                event,
                project=payload.get("cwd"),
                transcript=payload.get("transcript_path"),
                origin=claude_spawn_origin(sid),
            )
            return 0
        if args.action == "setup":
            from session_binding import install_kimi_hooks

            count = setup_claude(root)
            kimi = install_kimi_hooks(
                Path(os.environ.get("KIMI_CODE_HOME", str(Path.home() / ".kimi-code")))
            )
            print(json.dumps({"claude_hooks_added": count, "kimi_hooks_added": kimi}))
            return 0
        if args.action == "agents":
            from agent_launch import installed_agents

            print(json.dumps({"agents": installed_agents()}, ensure_ascii=False))
            return 0
        if args.action == "launch":
            from agent_launch import launch

            launch(args.agent, args.dir)
            print(json.dumps({"launched": True, "agent": args.agent}))
            return 0
        if args.action == "daily-report":
            from daily_report import generate_day, generate_overview

            if args.overview:
                payload = generate_overview(
                    store,
                    Path.home(),
                    days=max(7, args.days),
                    top=max(1, min(args.top, 10)),
                )
            else:
                payload = generate_day(
                    store, Path.home(), args.date, refresh=args.refresh
                )
            print(json.dumps(payload, ensure_ascii=False))
            return 0
        if args.action in ("sync", "watch"):
            while True:
                print(json.dumps(refresh(store)), flush=True)
                if args.action == "sync":
                    break
                time.sleep(max(args.interval, 1))
            return 0
        if args.action == "rows":
            if args.refresh:
                refresh(store)
            print(
                json.dumps(
                    {
                        "sessions": display_rows(
                            store, args.all, include_agents=args.include_agents
                        ),
                        "health": store.meta("health", {}),
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        if args.action == "ack":
            if not store.acknowledge(args.id, args.revision):
                raise ValueError("new activity arrived; refresh before acknowledging")
            print('{"acknowledged":true}')
            return 0
        if args.action == "ack-batch":
            items = json.loads(args.items)
            if not isinstance(items, list):
                raise ValueError("items must be a JSON array of [id, revision] pairs")
            pairs = [(str(item[0]), int(item[1])) for item in items]
            acked = store.acknowledge_batch(pairs)
            # 跳过的项是确认瞬间已有新活动，保留未读是预期行为而非错误。
            print(
                json.dumps(
                    {"acknowledged": len(acked), "skipped": len(pairs) - len(acked)}
                )
            )
            return 0
        if args.action == "origin":
            row = store.get(args.id)
            store.patch(row["provider"], row["session_id"], origin=args.set_origin)
            if args.rule_project:
                store.set_origin_rule(args.rule_project, args.set_origin)
            print(
                json.dumps(
                    {
                        "id": args.id,
                        "origin": args.set_origin,
                        "rule_project": args.rule_project,
                    }
                )
            )
            return 0
        return open_session(store, args.id, args.revision)
    except KeyboardInterrupt:
        return 0
    except (
        ValueError,
        OSError,
        KeyError,
        TypeError,
        sqlite3.Error,
        subprocess.TimeoutExpired,
    ) as error:
        if args.action == "hook":
            print("inbox hook unavailable: " + type(error).__name__, file=sys.stderr)
            return 0
        print(json.dumps({"status": "error", "reason": str(error)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
