# CLI 会话与 iTerm2 pane 绑定

状态：绑定代码已实现，32 项检查和真实跨会话伪终端测试通过。Pi/Kimi 的正常聚焦与退出后旧绑定拒绝已在真实 iTerm2 会话中验证；该验证不覆盖同名多会话、挂起恢复和标签页复用等全部场景。详见 [修复记录](../research/2026-09-14-cross-terminal-ownership.md)。

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

这是协作式受管理启动合同，不是针对恶意本机进程的安全隔离。未通过启动器运行的会话不自动归属。若 agent 在不发送 SessionStart 的情况下内部切换会话，须补该版本适配后才能保证覆盖。

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
