"""把 harness 命令守卫装成 Zcode 的用户级 PreToolUse 钩子（执行方角色）。

Zcode 的项目级钩子（.zcode/config.json）要逐个工作区授予信任，且实测 CLI 与 TUI 不执行
（docs/specs/delivery-harness.md §4）；新项目打开即可工作、不会有人去授信任。用户级钩子不需要信任，
CLI 与桌面版都会加载，所以装在用户级，并只在含 harness/command_guard.py 的仓库里生效，
其他项目直接放行。

    python3 harness/zcode_hook.py install     写入前备份原文件；保留已有钩子；重复执行结果不变
    python3 harness/zcode_hook.py status
    python3 harness/zcode_hook.py uninstall

改的是用户本机、对所有项目生效的配置：由用户执行，Agent 不代为安装。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

CONFIG = Path.home() / ".zcode" / "cli" / "config.json"
MARKER = "harness 守卫（session-manager）"
MATCHER = "Bash|Edit|Write|mcp__.*(merge_pull_request|auto_merge|merge_pr).*"
# 只在含 harness/command_guard.py 的仓库里调用守卫；找不到时读掉输入并放行（退出码 0）。
COMMAND = (
    'root=$(git -C "${ZCODE_PROJECT_DIR:-.}" rev-parse --show-toplevel 2>/dev/null); '
    'if [ -n "$root" ] && [ -f "$root/harness/command_guard.py" ]; then '
    'cd "$root" && exec python3 harness/command_guard.py --format claude --role implementer; '
    "fi; cat >/dev/null; exit 0"
)


def entry() -> dict:
    return {
        "matcher": MATCHER,
        "hooks": [{"type": "command", "command": COMMAND, "timeout": 30, "statusMessage": MARKER}],
    }


def _ours(item: dict) -> bool:
    return any(hook.get("statusMessage") == MARKER for hook in item.get("hooks") or [])


def load(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def installed(config: dict) -> bool:
    events = (config.get("hooks") or {}).get("events") or {}
    return any(_ours(item) for item in events.get("PreToolUse") or [])


def with_hook(config: dict) -> dict:
    config = json.loads(json.dumps(config))
    hooks = config.setdefault("hooks", {})
    hooks.setdefault("enabled", True)
    pre = [item for item in hooks.setdefault("events", {}).get("PreToolUse") or [] if not _ours(item)]
    hooks["events"]["PreToolUse"] = [*pre, entry()]
    return config


def without_hook(config: dict) -> dict:
    config = json.loads(json.dumps(config))
    events = (config.get("hooks") or {}).get("events") or {}
    if "PreToolUse" in events:
        events["PreToolUse"] = [item for item in events["PreToolUse"] if not _ours(item)]
        if not events["PreToolUse"]:
            del events["PreToolUse"]
    return config


def write(path: Path, config: dict) -> Path | None:
    backup = None
    if path.exists():
        backup = path.with_name(f"{path.name}.bak-harness-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(path, backup)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n")
    return backup


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["install", "status", "uninstall"])
    parser.add_argument("--config", type=Path, default=CONFIG, help="Zcode 用户配置（默认 ~/.zcode/cli/config.json）")
    args = parser.parse_args(argv)
    config = load(args.config)
    if args.action == "status":
        print(f"{'已安装' if installed(config) else '未安装'}：{args.config}")
        return 0 if installed(config) else 1
    updated = with_hook(config) if args.action == "install" else without_hook(config)
    if updated == config:
        print(f"无需改动（{'已安装' if installed(config) else '未安装'}）：{args.config}")
        return 0
    backup = write(args.config, updated)
    print(f"{'已安装' if args.action == 'install' else '已卸载'}：{args.config}" + (f"（原文件备份 {backup.name}）" if backup else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
