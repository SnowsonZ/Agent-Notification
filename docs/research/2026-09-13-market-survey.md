# 跨 agent 会话管理调研

状态：前期市场调研，保留当时的范围演进。用户最终确认的宿主和后续接口核查见 [当前方案](../plans/session-inbox.md)，后者为当前选型与实施依据。

调研日期：2026-09-13。依据项目官方文档和 README；未安装实测。当前目录未发现已有实现，也不是 Git 仓库。

## 需求与结论

目标是任务结束或需要输入时，快速定位并介入原会话。要分别验证状态检测、未处理队列、准确跳转、远程支持。恢复历史会话不等于聚焦正在运行的原会话。

已有相近产品，优先验证现成工具。用户已明确全部本地运行：Codex、Claude Code、Pi、Zcode、Kimi。具体使用 CLI 还是原生 App 尚未明确；Zcode 暂按 Z.AI 产品理解，Kimi 暂按 Kimi Code 理解。若保留多个原生 App 和终端，才考虑自建轻量聚合层。远端不属于当前范围。

## 候选

| 工具 | 已查证能力 | 适配判断与边界 |
|---|---|---|
| [cmux](https://github.com/manaflow-ai/cmux) | macOS 终端、通知面板、未读提示、快捷跳转、hooks/OSC 通知 | 最贴近终端内任务完成后介入；不能据此推定能定位外部原生 App 会话 |
| [agent-deck / asheshgoplani](https://github.com/asheshgoplani/agent-deck) | 多 agent TUI、运行/等待/空闲/错误状态、tmux 通知栏直接跳转、SSH 远端实例 | 适合统一到 tmux；远端状态较粗，跨机器完成报告有 remote drain 拉取机制 |
| [agentdeck / huiyu](https://github.com/huiyu/agentdeck) | Claude/Codex hooks、fzf 会话选择、tmux/zellij、点击通知聚焦 | 轻量参考；README 明示 Codex 等待状态是 best-effort，精确宿主聚焦和 zellij 有限制 |
| [Agent Sessions / jazzyalex](https://github.com/jazzyalex/agent-sessions) | macOS 本地历史搜索，多种 agent 格式，支持的 CLI 会话可恢复 | 适合找历史；当前 README 未证实完整实时完成通知闭环。OpenClaw 支持读取但不支持恢复 |

[cmux 通知文档](https://github.com/manaflow-ai/cmux/blob/main/docs/notifications.md)列出 Claude Code、Codex、OpenCode 接入和 Cmd+Shift+U 跳到最新未读通知。

[cmux SSH 文档](https://cmux.com/docs/ssh)说明远端 cmux notify 可回传本机侧栏，并提供重连和远端 relay。需要使用其 SSH 工作区机制，不能理解为自动捕获任意既有 SSH 任务。

以上属于文档确认，不代表可靠性实测；特别是第三方项目描述的 Codex hooks 能力须对照实际安装版本再验证。

## 自建可行方案（设计建议）

产品定位：会话待办收件箱。显示任务标题、agent、机器、状态、最后结果摘要、等待时间和打开入口。

最小组成：每个 agent 一个事件适配器；本地 SQLite 保存会话和未读事件；菜单栏或快捷面板展示待介入队列；按宿主提供打开适配器。CLI、MCP 或 skill 可作接入入口，持续监听由守护进程或宿主 hooks 承担。

会话唯一标识采用 host + provider + session_id，不依赖标题或工作目录。优先消费结构化事件；读日志为兼容回退，事件不足时明确状态未知。状态至少区分运行中、等待输入、回合结束待查看、错误、离线。回合结束不等于任务成功。

打开策略：已有终端聚焦原 pane；原生 App 使用经验证的会话入口；消息来源使用经验证的聊天/消息链接。原入口不存在时才显式提供 resume，不静默新建重复执行会话。远端使用认证连接传输最小状态和定位信息。

先做两个真实 agent 的端到端适配验证，再做完整界面。最大不确定性是各宿主的状态事件和精确打开能力，不能用当前对话内可用的工具推定独立程序也能调用相同接口。

## 选型验收

1. 两种 agent 并行：回合结束、请求输入、报错均能进入正确队列。
2. 同目录同名多个会话仍准确跳转。
3. 已处理事件去重，新一轮完成重新标未读。
4. 远端断线显示离线，不误判成功完成；重连不重复提醒。
5. 原窗口关闭后说明入口不可用，并提供合法恢复方式。

建议：终端工作流先试 cmux；tmux 工作流比较 agent-deck；历史检索用 Agent Sessions。只有必须保留分散宿主且现成方案无法覆盖时，开发聚合层。

## 五种 agent 的覆盖补充

- cmux 官方 README 明列 Claude Code、Codex、Pi 的 hooks/OSC 通知兼容；本轮没有查证 Kimi、Zcode 的完成通知适配，不能据此承诺开箱即用覆盖五种。
- agent-deck README 明列 Claude、Codex，并在 fork 能力中列出 Pi；支持自定义工具，但自定义启动不意味着完整状态识别。未确认 Kimi、Zcode 专用集成。
- Agent Sessions 官方支持表覆盖 Codex、Claude Code、Pi、Kimi Code 的历史读取和受支持会话恢复，没有列出 Zcode。
- [ZCode 官方文档](https://zcode.z.ai/en/docs/ADE-tools)描述桌面开发环境与终端面板。本轮未确认可供外部工具使用的稳定会话精确跳转接口。

针对当前五种本地 agent，未找到经文档确认同时满足“全部接入、完成提醒、精确回到原会话”的工具；这不等于证明不存在。下一步最有价值的验证是 Codex/Zcode 的实际宿主和 Zcode 原会话跳转，再决定统一终端还是聚合现有界面。
