# Session Manager

English | [简体中文](README.md)

Session Manager is a local session inbox for macOS: it aggregates Codex, Claude Code, and Zcode desktop sessions together with managed Pi and Kimi CLI sessions running in iTerm2, surfaces the ones that need attention in a single list, and locates the original session when you act on it.

> Note: the CLI, in-app UI, and all in-depth documentation are written in Chinese. This file covers the essentials in English.

## Components

| Component | Description |
|---|---|
| `bin/session-manager` | Unified CLI: managed launch, binding queries, session focus, inbox maintenance |
| Session Notifications (`build/SessionInbox.app`) | Native macOS app: pending list, system notifications, menu-bar count, one-click CLI launch |
| `build/zcode-focus` | Native helper for Zcode accessibility navigation (built from `native/ZcodeFocus.swift`) |

Key capabilities:

- Aggregates session state across five sources (running, waiting for input, turn finished, failed, interrupted, exited), sorted by recent activity with lazy pagination.
- Pi/Kimi run through a managed launcher that registers a run_id/session_id binding; focusing re-verifies the run lock, session ID, and foreground process group, and stale bindings are rejected once the process exits.
- Zcode sessions are opened via the accessibility API by prefilled task search; the app stops at the results page and you pick the target.
- Opening a session from the app acknowledges it automatically (with a revision check so newly arrived events are never swallowed); failed opens keep the unread state.
- Only management metadata is stored (session identifiers, title summaries, project, status, timestamps, and location hints) — never conversation bodies, typed input, or credentials.

## Sources and how they are opened

| Source | State collection | Opened via |
|---|---|---|
| Claude Desktop | Local metadata + observer hooks | verified `claude://` links |
| Codex Desktop | Incremental rollout-file reads | `codex://` links |
| Zcode | Task index + run database | accessibility-driven task search |
| Pi / Kimi | Managed-launch extension events and lifecycle hooks | iTerm2 tab focus after binding verification |

## Requirements

- Apple Silicon Mac, macOS 14 or later.
- Python 3.11+ (validated on 3.12).
- Xcode Command Line Tools.
- iTerm2: the host terminal for managed Pi/Kimi sessions and focus jumps.
- System permissions: iTerm2 automation and Zcode accessibility, granted on first use.

## Quick start

From the repository root:

```sh
python3 -m venv scratch/iterm-probe-venv
scratch/iterm-probe-venv/bin/python -m pip install -r requirements.txt
mkdir -p build
xcrun swiftc native/ZcodeFocus.swift -o build/zcode-focus
python3 scripts/build_inbox_app.py
```

Notes:

- The virtualenv location `scratch/iterm-probe-venv` is fixed: `bin/session-manager` resolves its Python interpreter at that path, so do not relocate it.
- `scratch/` and `build/` are not tracked by git; the steps above create them.
- The build script refuses to overwrite a running app — quit "Session Notifications" before rebuilding.
- The bundle is locally ad-hoc signed and verified; this is not notarized distribution. After a rebuild you may need to re-grant accessibility permission in System Settings.

Install the observer hooks and launch the app:

```sh
bin/session-manager inbox setup   # installs Claude/Kimi observer hooks, preserves existing config, idempotent
bin/session-manager app           # opens "Session Notifications"
```

The app detects locally installed CLIs (claude, codex, pi, kimi) at startup and offers one-click directory launch in a new iTerm2 tab.

## CLI reference

| Command | Purpose |
|---|---|
| `bin/session-manager pi` \| `kimi` | Managed launch of a Pi/Kimi session with binding registration |
| `bin/session-manager list` | Show current session bindings |
| `bin/session-manager focus RUN_ID SESSION_ID` | Verify a binding and focus the original iTerm2 tab |
| `bin/session-manager zcode-focus TASK_ID` | Open a Zcode task; `--describe` only parses task metadata |
| `bin/session-manager inbox setup` | Install or update observer hooks (idempotent) |
| `bin/session-manager inbox rows \| sync \| open \| ack` | Inbox data queries and maintenance |
| `bin/session-manager app` | Open the "Session Notifications" app |

Run the test suite:

```sh
python3 -m unittest discover -s tests -v
```

## Known limitations

- Apple Silicon and macOS 14+ only; iTerm2 is the only supported host terminal for Pi/Kimi.
- "Turn finished" describes the turn state, not whether the task's goal succeeded.
- Zcode's "waiting for permission approval" state exists only in the desktop app's memory and is indistinguishable from "running" — both display as running. This is a data-source boundary.
- First import uses monitoring start time as its baseline; historical completions are not bulk-marked as pending.
- This is a cooperative session-management tool, not a security boundary against malicious local processes.

## Documentation

The complete documentation (specifications, design plans, and dated research records) is available in Chinese under [`docs/`](docs/). Start with [the unified inbox specification](docs/specs/unified-inbox.md).

## License

[MIT](LICENSE)
