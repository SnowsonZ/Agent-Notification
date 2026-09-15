# Agent Notification

English | [简体中文](README.zh-CN.md)

Agent Notification is a local session inbox for macOS: it aggregates Codex, Claude Code, and Zcode desktop sessions together with managed Pi and Kimi CLI sessions running in iTerm2, surfaces the ones that need attention in a single list, and locates the original session when you act on it.

> Note: the CLI, in-app UI, and all in-depth documentation are written in Chinese. This file covers the essentials in English.

## Interface preview

| Pending | All sessions | Daily report | Day detail |
|---|---|---|---|
| ![Pending list](docs/images/inbox-pending.png) | ![All sessions](docs/images/inbox-all.png) | ![Daily report](docs/images/daily-report.png) | ![Day detail](docs/images/daily-report-day.png) |

## Components

| Component | Description |
|---|---|
| `bin/session-manager` | Unified CLI: managed launch, binding queries, session focus, inbox maintenance |
| Agent Notification (`build/Agent Notification.app`; shown in the UI as 会话通知) | Native macOS app: pending list, system notifications, menu-bar count, one-click CLI launch |
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
- System permissions: iTerm2 automation pops up on first launch; Zcode accessibility is added by dragging the app into the list (see Quick start).

## Quick start

A prebuilt Agent-Notification.dmg is available on the [Releases](../../releases) page (double-click and drag into Applications). To build from source, from the repository root:

```sh
python3 -m venv scratch/iterm-probe-venv
scratch/iterm-probe-venv/bin/python -m pip install -r requirements.txt
mkdir -p build
xcrun swiftc native/ZcodeFocus.swift -o build/zcode-focus
python3 scripts/build_inbox_app.py
```

Notes:

- The virtualenv location `scratch/iterm-probe-venv` is fixed: `bin/session-manager` resolves its Python interpreter at that path, so do not relocate it.
- That is the developer bundle: the app runs the repository's scripts and venv in place. The Release dmg is built with `python3 scripts/build_inbox_app.py --standalone`, which copies the scripts, `bin/session-manager`, `zcode-focus` and the pure-Python deps (`requirements-standalone.txt`) into `Contents/Resources`, so it runs without a checkout; it needs a python3 3.9+ on the machine (Homebrew or Xcode Command Line Tools). Both bundles share one codebase and switch on whether `Resources/pylib` exists.
- `scratch/` and `build/` are not tracked by git; the steps above create them.
- The build script refuses to overwrite a running app — quit "Agent Notification" before rebuilding.
- The bundle is locally ad-hoc signed and verified; this is not notarized distribution. After a rebuild, re-drag the app into the Accessibility list (`bin/session-manager permissions`).
- Two icon sources: `native/GenerateAppIcon.swift` renders the icns (all systems), and `native/AppIcon.icon` is the macOS 26+ Liquid Glass layered icon — the build script compiles it into `Assets.car` when Xcode 26's `actool` is available and silently falls back to the icns with Command Line Tools only. Releases are built by CI on macos-26, so they carry the layered icon.

Install the observer hooks and launch the app:

```sh
bin/session-manager inbox setup   # installs Claude/Kimi observer hooks (idempotent)
bin/session-manager app           # opens "Agent Notification"
bin/session-manager permissions   # opens the Accessibility pane; drag the app into the list
```

Authorization is drag-based: clicking "Go to session" on a Zcode item without the accessibility permission opens System Settings → Privacy & Security → Accessibility and shows a floating, draggable badge of the app — drop it into the list to grant, and the badge dismisses itself once granted. `bin/session-manager permissions` is the manual equivalent (reveals the app in Finder and opens the pane). The bundle is ad-hoc signed, so re-drag after every rebuild — a stale entry switched on does not authorize the new build.

The app detects locally installed CLIs (claude, codex, pi, kimi, agy, opencode) at startup and shows them as flat icon buttons in the "New Session" row for one-click directory launch in a new iTerm2 tab; when the row runs out of width, a "+N" button on the right collapses the remaining agents into a menu. agy (Antigravity CLI, the official successor to Gemini CLI) and opencode are launch-only for now — their sessions do not appear in the inbox yet.

## CLI reference

| Command | Purpose |
|---|---|
| `bin/session-manager pi` \| `kimi` | Managed launch of a Pi/Kimi session with binding registration |
| `bin/session-manager list` | Show current session bindings |
| `bin/session-manager focus RUN_ID SESSION_ID` | Verify a binding and focus the original iTerm2 tab |
| `bin/session-manager zcode-focus TASK_ID` | Open a Zcode task; `--describe` only parses task metadata |
| `bin/session-manager inbox setup` | Install or update observer hooks (idempotent) |
| `bin/session-manager inbox rows \| sync \| open \| ack` | Inbox data queries and maintenance |
| `bin/session-manager app` | Open the "Agent Notification" app |

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
