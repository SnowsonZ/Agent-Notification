# CLI 会话与 iTerm2 pane 绑定

状态：绑定代码已实现，32 项检查和真实跨会话伪终端测试通过。Pi/Kimi 的正常聚焦与退出后旧绑定拒绝已在真实 iTerm2 会话中验证；OpenCode 于 2026-09-16 加入受管理（插件事件通道，见下），Antigravity CLI（agy）同日加入（hooks 插件通道，见下），两者全链路（登记→回合事件→聚焦→退出 closed→旧绑定拒绝）已在真实 iTerm2 验证；该验证不覆盖同名多会话、挂起恢复和标签页复用等全部场景。详见 [修复记录](../research/2026-09-14-cross-terminal-ownership.md)。

## OpenCode 受管理接入（2026-09-16）

- 启动：`bin/session-manager opencode`（App「新建会话」与 CLI 同入口），managed_run 记录 pane/tty/pgid 并持锁。
- 事件通道：包装器设置 `OPENCODE_CONFIG=<state>/opencode-plugin.json`（内容仅指向 `scripts/opencode_capture.js`），实测为**叠加**注入——用户全局配置零改动；非受管理会话不设该变量，插件不加载（比 Kimi 全局 hooks 更干净的边界）。
- 插件映射 SDK 事件 → 既有 hook 名：session.created/updated→SessionStart、chat.message→UserPromptSubmit、session.idle→Stop、assistant error→StopFailure、permission.updated(type=ask)→PermissionRequest、permission.replied→PermissionResult；事件经同一 `event` 入口与 record_event 链路上报。退出由 managed_run 兜底 SessionEnd。
- 边界：非受管理启动的 opencode 会话不再进入收件箱（与 Pi/Kimi 同覆盖模型，用户决定避免双套逻辑）；工作日报的 token 统计仍直读本地 SQLite，与采集管道分离。

## agy（Antigravity CLI）受管理接入（2026-09-16）

- 启动：`bin/session-manager agy`，managed_run 同一合同。
- 事件通道：`setup-agy` 生成捕获插件（`plugin.json` + 命名钩子 `hooks.json`，官方 claude 风格插件格式）并经 `agy plugin install` 注册到 `~/.gemini/antigravity-cli/plugins/session-manager/`；handler 调 `session_binding.py agy-hook --event <E>` 读 stdin 载荷转译上报。
- 跳转：绑定死亡时受管理恢复（新标签 `--conversation <conversationId>`，四家统一，见行为合同恢复降级）；标题来自 summaries 库（title 空时回退 preview）。
- 事件映射（agy 官方 hooks 仅五种工具/调用事件，无 SessionStart/SessionEnd/权限/失败）：PreInvocation→UserPromptSubmit（标记运行中，并按 Pi 模型把绑定改指当前 conversationId，TUI 内切换会话自动跟随）；Stop→Stop；退出由 managed_run 兜底 SessionEnd。载荷为 camelCase protojson：conversationId→session_id、workspacePaths[0]→cwd。
- 边界：无等待权限/失败/中断状态；回合事件可能在回合结束时才落地（运行中显示 best-effort）；agy 进程启动时缓存 hooks，setup-agy 后需重启会话生效；非受管理 agy 会话不进收件箱；工作日报暂不计 agy token（hooks 无 usage 数据）。
- TUI 内切换会话由下一回合的 PreInvocation 自动改绑；旧会话收 SessionEnd（record_event 通用逻辑）。

## 真实会话验证记录

- Pi：跨标签页 focus 成功；退出后旧命令返回 refused / binding expired, superseded, or session changed。退出后是否完全未切换标签页未单独记录。
- Kimi：刚启动时 session_id 为 null；完成首轮对话后登记 session_id（样例值 session_909ecfd8…）。在该样例中，第一轮才出现会话登记，不能将启动阶段的 null 直接判为 hook 失败。
- Kimi：run_id 11057a14…（对应 pane D9678062…）的 focus 实际切回原标签页，返回 managed_binding_verified=true、agent_ownership_verified=true 和 activation_call_completed=true。随后按退出测试步骤重试，同一命令返回 refused / binding expired, superseded, or session changed，旧绑定拒绝通过；是否完全未切换标签页未单独记录。

## 行为合同

- 通过受管理启动器运行 Pi/Kimi，从 ITERM_SESSION_ID 记录 pane 候选标识。
- 每次运行生成独立 run_id，启动器在子进程存活期间持有文件锁。PID 复用不能继承该锁。
- SessionStart 登记 agent session_id；Pi 切换会话后替换该 ID。旧会话的迟到 SessionEnd 不清除新会话。
- 同 pane 的新受管理启动替换旧运行；旧运行的事件不再更新当前绑定。
- 校验时要求运行锁存活、会话 ID 一致、原进程组仍是对应 TTY 的前台进程组。挂起/转后台的旧任务不能认领 pane。
- 跨会话校验通过系统 ps 的 pgid/tpgid 元数据完成；macOS 的 tcgetpgrp 仅用于启动器检查自己的控制终端，不能用于从另一标签页查询目标。
- 跳转前查询 live pane，重新验证绑定；跳转后再检查绑定。失效或歧义报错，不恢复或新建会话，不向终端输入命令。
- 恢复降级（2026-09-16，用户验收反馈；四家受管理 CLI 均实测支持按会话 ID 恢复——pi `--session`、kimi `--session`、opencode `--session`、agy `--conversation`）：绑定死亡时跳转降级为受管理恢复——新标签经包装器带会话 ID 重启（注册新绑定），并先查同会话的其它活绑定避免重复开窗；目录缺失仍拒绝。2026-09-21 评审 R3 边界收紧：probe 非零不再一律视为绑定死亡——运行锁（flock）仍被持有即拒绝恢复并保留未读（绑定活着但前台组校验失败等场景），避免复制出第二个会话并错误自动确认；确认锁释放才走替身绑定/受管理恢复，替身活而校验失败同理拒绝。

这是协作式受管理启动合同，不是针对恶意本机进程的安全隔离。未通过启动器运行的会话不自动归属。若 agent 在不发送 SessionStart 的情况下内部切换会话，须补该版本适配后才能保证覆盖。

## 验收（编号）

2026-09-25 由本文既有条款整理，不新增需求；证据类型与覆盖列由 `harness/acceptance.py` 检查（见 [delivery-harness.md](delivery-harness.md)）。

| 编号 | 验收内容 | 证据类型 | 覆盖 |
|---|---|---|---|
| CB1 | 每次运行独立 run_id；启动器在子进程存活期间持有文件锁；锁释放后的旧记录不能通过校验（PID 复用不能继承锁） | 单测 | `test_session_binding.BindingTests.test_live_binding`、`test_session_binding.BindingTests.test_released_lock_rejects_stale_record` |
| CB2 | SessionStart 登记会话 ID，切换会话后替换；旧会话迟到的 SessionEnd 不清除新会话 | 单测 | `test_session_binding.BindingTests.test_new_session_invalidates_old_and_late_shutdown_does_not_clear_new` |
| CB3 | 同 pane 的新受管理运行替换旧运行，旧运行的事件不再更新绑定 | 单测 | `test_session_binding.BindingTests.test_pane_reuse_rejects_old_run_and_late_events` |
| CB4 | 校验要求锁存活、会话 ID 一致、原进程组是该 TTY 的前台进程组；挂起或转后台的旧任务不能认领 pane | 单测 + 真实 PTY | `test_session_binding.BindingTests.test_suspended_or_background_agent_cannot_claim_pane`、`test_foreground_terminal.CrossSessionForegroundTests` |
| CB5 | 跨会话前台组查询用 ps 的 pgid/tpgid（不用 tcgetpgrp） | 真实 PTY | `test_foreground_terminal.CrossSessionForegroundTests.test_live_foreground_group_is_recognized_from_another_session`、`test_foreground_terminal.CrossSessionForegroundTests.test_other_process_group_is_rejected` |
| CB6 | pane 身份：只认精确观测到的 ID，候选冲突时不猜测 | 单测 | `test_iterm_probe.TerminalIdentityTests` |
| CB7 | 正常退出删除对应 run_id 的记录；未受管理的 hook 调用直接返回 | 单测 | `test_session_binding.BindingTests.test_shutdown_clears_binding`、`test_session_binding.BindingTests.test_unmanaged_hook_is_noop` |
| CB8 | Kimi hooks 安装保留既有配置且幂等 | 单测 | `test_session_binding.BindingTests.test_kimi_install_preserves_existing_config_and_is_idempotent` |
| CB9 | OpenCode 受管理：插件事件驱动完整生命周期；非受管理事件忽略、插件不注册钩子 | 模拟协议 | `test_opencode_inbox.OpenCodeManagedTests.test_plugin_events_drive_full_lifecycle`、`test_opencode_inbox.OpenCodeManagedTests.test_unmanaged_events_ignored`、`test_opencode_inbox.OpenCodePluginTests.test_sdk_events_map_to_inbox_names`、`test_opencode_inbox.OpenCodePluginTests.test_unmanaged_plugin_returns_no_hooks` |
| CB10 | agy 受管理：hooks 驱动生命周期并随会话切换改绑；包装器退出兜底 SessionEnd；非受管理载荷忽略；过期锁拒绝 | 模拟协议 | `test_agy_inbox.AgyManagedTests` |
| CB11 | 恢复降级：绑定死亡时带会话 ID 受管理恢复（pi/kimi/opencode `--session`、agy `--conversation`），优先已有活绑定；运行锁仍被持有时拒绝恢复并保留未读 | 单测 | `test_opencode_inbox.OpenCodeManagedTests.test_open_resumes_when_binding_dead`、`test_opencode_inbox.OpenCodeManagedTests.test_open_refuses_resume_when_binding_still_alive`、`test_opencode_inbox.OpenCodeManagedTests.test_open_prefers_live_rebound_binding`、`test_opencode_inbox.OpenCodeManagedTests.test_open_resumes_pi_and_kimi_with_session_flag`、`test_opencode_inbox.OpenCodeManagedTests.test_open_resumes_agy_with_conversation_flag` |
| CB12 | 真实 iTerm2：focus 切回原标签并返回 agent_ownership_verified=true；退出后、同 pane 重启后旧 run_id 均拒绝 | 真机 UI | 用户在真实 iTerm2 执行「用法」一节的入口 |

## 用法

第一标签页启动一个 agent 并保持运行：

```sh
bin/session-manager pi
```

也可将 `pi` 换成 `kimi`。Pi 自动加载本项目扩展，不改全局 Pi 配置。Kimi 用户级 observer hooks 已扩展为八种生命周期事件以支持统一收件箱；只有带 SESSION_MANAGER_RUN_ID 的受管理启动会话会登记数据，普通会话直接返回。重复 setup-kimi 不添加重复条目；切换 KIMI_CODE_HOME 或运行解释器时需重新 setup-kimi。

第二标签页查询：

```sh
bin/session-manager list
```

等 session_id 不为空后，使用返回的两个身份字段：

```sh
bin/session-manager focus RUN_ID SESSION_ID
```

应切回原 agent 页并返回 agent_ownership_verified=true。退出 agent 后，同一 focus 命令必须拒绝；同 pane 重启 agent 后旧 run_id 仍必须拒绝。原始 --session-id 探针继续用于基础测试，但不会宣称验证了 agent 归属。

## 实现与验证

[session_binding.py](../../scripts/session_binding.py)保存 SQLite 绑定及运行锁；[pi_capture.ts](../../scripts/pi_capture.ts)上报 Pi 会话变化；[iterm_probe.py](../../scripts/iterm_probe.py)按需校验后聚焦；[统一入口](../../bin/session-manager)固定使用已安装 iTerm2 包的隔离 Python 环境。

状态目录为 `~/.local/state/session-manager`。锁文件可在运行结束后保留，但无持锁进程即无效；SQLite 中旧进程异常退出遗留的记录也不能通过校验。正常退出删除对应 run_id 的记录，不影响该 pane 的后来运行。

已测：实际 flock 存活/释放，旧事件迟到，会话切换，pane 新运行替换，后台拒绝（前台查询在单元测试中替换），以及真实伪终端中的前台进程组、子进程回调和退出清理。Kimi 配置追加保留已有内容，重复安装不改变文件。未测：真实 iTerm 中新绑定的整条聚焦路径、Pi/Kimi 交互会话切换。

当前 Computer Use 工具对 iTerm2 的访问受限；最终 UI 验收由使用者在真实 iTerm2 中执行上述入口完成，不通过其他控制技术绕过。
