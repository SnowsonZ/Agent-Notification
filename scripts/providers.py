"""来源注册表：接入或调整 agent CLI 的纯数据改这里，避免散落各模块的同构常量漂移。

覆盖：展示名、安装探测路径（GUI PATH 缺用户目录时的第二通道）、按会话 ID 恢复
参数、受管理 hooks 的登记/注销事件映射。各模块从本表派生自己的视图；Swift 侧
（native/ProviderStyles.swift 的 providerStyles）与 Python 的口径需人工对齐，
改这里时同步检查。行为差异（pi 注入 pi_capture、opencode 注入 OPENCODE_CONFIG、
kimi 预检 hook 注册）仍留在 session_binding，不属于数据。
"""

# 收件箱「新建会话」行的展示顺序。
LAUNCH_ORDER = ("claude", "codex", "pi", "kimi", "agy", "opencode")

# Gemini CLI 已于 2026-06-18 对消费级用户停服，Google 官方继任者是 Antigravity CLI（agy）。
AGENTS = {
    "claude": {
        "name": "Claude",
        "managed": False,
        "resume_args": ("--resume",),
        "well_known": (
            "~/.local/bin/claude",
            "/opt/homebrew/bin/claude",
            "/usr/local/bin/claude",
        ),
    },
    "codex": {
        "name": "Codex",
        "managed": False,
        "resume_args": ("resume",),
        "well_known": (
            "/opt/homebrew/bin/codex",
            "/usr/local/bin/codex",
            "~/.local/bin/codex",
        ),
    },
    "pi": {
        "name": "Pi",
        "managed": True,
        "resume_args": ("--session",),
        "well_known": ("/opt/homebrew/bin/pi", "/usr/local/bin/pi"),
        "start_events": ("session_start",),
        "end_events": ("session_shutdown",),
    },
    "kimi": {
        "name": "Kimi",
        "managed": True,
        "resume_args": ("--session",),
        "well_known": (
            "~/.kimi-code/bin/kimi",
            "/opt/homebrew/bin/kimi",
            "/usr/local/bin/kimi",
        ),
        "start_events": ("SessionStart",),
        "end_events": ("SessionEnd",),
    },
    "agy": {
        "name": "Antigravity CLI",
        "managed": True,
        "resume_args": ("--conversation",),
        "well_known": (
            "~/.local/bin/agy",
            "/opt/homebrew/bin/agy",
            "/usr/local/bin/agy",
        ),
        "start_events": ("UserPromptSubmit",),
        "end_events": ("SessionEnd",),
    },
    "opencode": {
        "name": "OpenCode",
        "managed": True,
        "resume_args": ("--session",),
        "well_known": (
            "/opt/homebrew/bin/opencode",
            "/usr/local/bin/opencode",
            "~/.opencode/bin/opencode",
            "~/.local/bin/opencode",
        ),
        "start_events": ("SessionStart",),
        "end_events": ("SessionEnd",),
    },
}

# 展示名：含不可启动的桌面来源（zcode 只进收件箱与日报，无启动入口）。
DISPLAY_NAMES = {key: spec["name"] for key, spec in AGENTS.items()} | {"zcode": "Zcode"}

MANAGED_PROVIDERS = tuple(key for key in LAUNCH_ORDER if AGENTS[key]["managed"])

# 四家受管理 CLI 均原生支持按会话 ID 恢复（实测 help：pi --session、kimi --session、
# opencode --session、agy --conversation）：绑定死亡时跳转统一降级为受管理恢复。
RESUMABLE_PROVIDERS = MANAGED_PROVIDERS

RESUME_ARGS = {key: spec["resume_args"] for key, spec in AGENTS.items()}
WELL_KNOWN = {key: spec.get("well_known", ()) for key, spec in AGENTS.items()}
# 受管理 provider 的会话登记/注销事件名（record_event 用）。
START_EVENTS = {
    key: frozenset(spec["start_events"])
    for key, spec in AGENTS.items()
    if "start_events" in spec
}
END_EVENTS = {
    key: frozenset(spec["end_events"])
    for key, spec in AGENTS.items()
    if "end_events" in spec
}
