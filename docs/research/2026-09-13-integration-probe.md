# 本机接入验证记录

日期：2026-09-13。当前状态：Claude 正常结束事件到原会话跳转闭环通过一次；五路完整验收未完成。下方静态检查表保留初次检查结果，以后续运行时记录为当前结论。

## 后续运行时验证：Claude 闭环通过

本机 Claude 1.52386.3，使用隔离目录 `scratch/hook-probe/` 的项目级 `.claude/settings.json`。只在该目录注册 SessionStart、UserPromptSubmit、Stop 到 [诊断接收器](../../scripts/capture_hook.py)，未修改全局 hooks。创建测试会话仅要求回复 OK、不调用工具或修改文件。

观察到的有效主会话事件（UTC）：

| 事件 | 时间 |
|---|---|
| SessionStart | 2026-09-13T15:57:03.252620+00:00 |
| UserPromptSubmit | 2026-09-13T15:57:07.298369+00:00 |
| Stop | 2026-09-13T15:57:15.121580+00:00 |

另收到一个不同 runtime ID 的 SessionStart，未确认其用途，不将单独启动事件当作用户任务完成。

链路结果：

1. 主会话 runtime ID 为 `21230691-2ad0-4e09-b7b2-d3286588a362`。
2. Desktop 元数据同时保存 cliSessionId 和 sessionId；解析得到 `local_ff4202bd-ddd8-4bb8-bdba-2e3adb0cb237`。
3. AX 确认测试会话回复 OK 且状态为 Idle。
4. 从另一会话打开 `claude://code/continue?session=local_ff4202bd-ddd8-4bb8-bdba-2e3adb0cb237`，AX 确认回到同一 ID 的 `/epitaxy/` 页面和测试标题。

这验证了一次真实“事件 → ID 映射 → 原会话导航”，不是 20 次稳定性测试，也不覆盖错误、审批、中断、账号切换或冷启动。CLI locator 只解析 URL，不负责执行导航或宣称导航成功。项目级信任已在测试目录确认，测试会话和文件保留供复核。

新增 [Claude ID 解析器](../../scripts/claude_locator.py)，按明确 ID 读取 Desktop 元数据；跨账号多匹配、损坏元数据、归档、缺失记录均不返回可用链接。9 项测试通过，其中 5 项验证定位、4 项验证接收器数据最小化和不影响父 hook 决策。测试入口见 [README](../../README.md)。

## Zcode 通知回调的新证据

本机安装包 `out/main/index.js` 的 dispatchTaskNotification：通知 click 回调持有原 BrowserWindow，先恢复/聚焦窗口，再发送 `TaskNotificationClick` 和 taskId；窗口已销毁则拒绝。

`out/renderer/assets/styles-DyAcaLKy.js` 的 onTaskNotificationClick：遍历 workspace 的 taskListCache/activeTaskId，用 taskId 找到项目并 setActiveTaskId。内部 IPC 名为 `zcode:task-notification-click`。这是一条 App 内部回调链，不是外部可直接调用的接口。

真实任务菜单提供“复制会话 ID”“复制任务路径”等，但未显示复制会话链接。原生通知点击保留准确身份这一点有代码依据；仅抓通知标题和正文无法重建该回调，也不能在原窗口关闭后保证复现。尚未将其升级为 Zcode 精确定位已支持。

## 本轮新增证据

| 来源 | 本机证据 | 判断 |
|---|---|---|
| Claude 1.52386.3 | 安装包中 Code 路由处理 `/continue`，读取 `session` 参数；Kcn 在已有、未归档会话中按 sessionId 精确查找，然后 getSessionRoute 导航 | 找到具体候选入口：`claude://code/continue?session=<desktop-session-id>`。有功能开关，不能只凭代码承诺可用 |
| Claude 实际 UI | AX 返回当前页面 `/epitaxy/local_<UUID>`，侧栏提供 Idle 与错误状态 | 能通过页面 ID 验证是否跳对；Desktop ID 与 hook runtime ID 的映射尚待确认 |
| Pi 0.85.1 | 随包扩展文档有 agent_settled、ui_prompt_start/end、session_start、session_info_changed | 可覆盖稳定空闲和扩展 UI 等待；agent_settled 优于 agent_end，仍应区分错误/中断 |
| Kimi 0.42.0 | 二进制存在 SessionHeartbeat、TurnStarted、PermissionRequest、StopFailure；CLI help 支持 session 管理、指定 ID 恢复 | 进一步支持本机有生命周期实现的判断，但字符串存在不等于 payload/配置契约联调通过 |
| iTerm2 3.7.0 | EnableAPIServer preference 未显式设置 | 不能据此断言 API 已启用或已关闭；需要实际连接验证 |
| Zcode 3.11.2 | 仍仅确认 workspace/open 链接处理路径 | 精确会话定位尚未解决，不把工作区入口作为完整支持 |

本机代码证据是针对指定版本的兼容线索，不是厂商稳定接口承诺。当前 Claude 链接不能使用 hook ID 直接拼接，必须先验证两种 ID 是否相同或建立映射。也不能用 `session=last` 代替明确 ID。

## 可复现检查

运行 `python3 scripts/probe_capabilities.py`，输出静态能力 JSON。脚本只读安装包元数据和代码，以及 Pi 随包文档；Kimi 只执行 `--version`。不读取会话正文、不改配置、不启动 agent，不打印密钥。

- [检查脚本](../../scripts/probe_capabilities.py)
- [本轮检查结果](2026-09-13-local-capabilities.json)
- Claude 代码位置：`/Applications/Claude.app/Contents/Resources/app.asar` 内 `.vite/build/index.chunk-BLfVpHdT.js`，函数 Wcn/Gcn/Kcn；应用升级后 chunk 名可能变化。
- Pi 文档：`/opt/homebrew/lib/node_modules/@earendil-works/pi-coding-agent/docs/extensions.md`，agent_start / agent_end / agent_settled 与 ui_prompt_start / ui_prompt_end。

脚本通过实际执行生成结果。JSON 中 runtime_delivery_verified 和 runtime_focus_verified 均保持 false；静态探针不是 UI 自动化测试。

## 联调限制与下一步

Computer Use 成功读取 Claude 界面，但首次调用耗时约 232 秒。随后对 iTerm2 bundle ID 的访问被工具拒绝：`Computer Use is not allowed to use the app 'com.googlecode.iterm2' for safety reasons.` 未绕过此拒绝。该结论仅表示当前工具限制，不表示产品无法通过 iTerm 官方 API 实现。

仍待完成：

1. Claude 已完成一次正常结束闭环；待补审批、错误、中断、冷启动及多会话稳定性验证。
2. Zcode 找到可验证的 task/session 定位方式；否则明确保留部分支持。
3. iTerm 在允许的验证环境下检查官方 API 连接与 session ID，再验证 Pi/Kimi 的 pane 精确聚焦。
4. 扩展其他来源的受控 hooks/extension 验证；Claude 目前仅正常结束实际通过。

在以上通过之前，不开始宣称五路完整支持，不把通知抓取改成默认依赖，也不将当前方案升级为“已验证、可全量执行”的规格。
