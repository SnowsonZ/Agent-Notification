# 本地跨 Agent 会话收件箱：调研与实施方案

状态：设计方案，保留选型与实现路径的历史背景；现行能力、行为与验收以 [统一收件箱规范](../specs/unified-inbox.md) 为准。前期工具比较见 [市场调研](../research/2026-09-13-market-survey.md)；文档管理约定见 [项目入口](../../README.md)。

当前交付已推进到 [统一收件箱规范](../specs/unified-inbox.md) 所载的现行版本：原生 App、事件队列和来源采集已实现并完成列表 UI 验证。下文保留原方案背景，实际能力与限制以该规范为准。

2026-09-14 接入进展见 [最新排查](../research/2026-09-14-integration-findings.md)：Pi 正常事件、Kimi 固定协议响应下的正常/失败事件已测，Codex/Zcode 新增只读兼容脚本；iTerm API 和 Zcode 精确导航尚未完成验收。下文接口建议以该记录的实测边界为准。

日期：2026-09-13。范围：Codex、Claude Code、Zcode 桌面 App；Pi、Kimi CLI 在 iTerm2。保留现有使用入口。本文是方案，不代表五路运行时联调已通过。

当前范围：用户恢复优先接入 iTerm2，API 枚举和一次跨标签页精确聚焦已获用户实测确认，下一步验证 agent 会话绑定及退出/复用失效。[Terminal 调整方案](terminal-migration.md)暂作备选。

后续实现：Pi/Kimi 正常聚焦与退出拒绝均已获用户实测；Zcode 的 [原生自动导航适配器](../specs/zcode-native-navigation.md)已通过一次真实搜索、任务打开及路径 ID 核验，用户和本地日志共同确认。未将单次成功外推为全部场景稳定性通过。

## 决策

推荐自建轻量 macOS 菜单栏 App，以 agent 的结构化事件为主、原会话定位为核心，系统通知抓取只作为可选的兼容补充。现有 cmux/Agent Deck 更适合统一终端，Agent Sessions 更偏历史搜索；本轮未找到确认覆盖当前组合的现成闭环。

核心价值是“谁需要我 → 什么事 → 一点回原会话”。第一版不接管 agent 执行，不另造聊天客户端，不自动审批或发送消息。无需调用模型 API。

## 通知抓取可行性

| 路线 | 已知能力 | 对本需求的结论 |
|---|---|---|
| Apple UserNotifications | 查询本 App 已送达通知 | 不提供这里需要的跨 App 监听能力 |
| Accessibility 观察通知横幅 | 社区项目已实现跨进程观察、读取或操作横幅 | 技术可行，但依赖 UI 结构和可见性，不保证完整历史或 session ID |
| 读取 Notification Center 私有 SQLite/WAL | 社区项目可查询通知内容，部分系统需要完整磁盘访问 | 可补历史；数据库路径、格式、保留策略不是稳定接口 |
| 截屏/OCR | 可识别屏幕上的通知文本 | 没有身份优势，维护和误识别成本更高，不采用 |

[Apple 文档](https://developer.apple.com/documentation/usernotifications/unusernotificationcenter)将 delivered notifications 限定为本 App。[Notiful](https://github.com/ptrinh/Notiful)同时实现通知数据库读取与 AX 横幅捕获；[NotificationNanny](https://github.com/chessper53/NotificationNanny)使用 AX 观察 Notification Center。这说明“能抓”有实现依据，但不代表能完整、稳定地管理 agent 会话。

决定性缺口不是读不到文字，而是文字通常不承诺包含 session ID、turn ID、工作区和可重放的打开动作。Pi、Kimi 的通知来源也可能只显示 iTerm2。即使原通知点击能跳回，也不能推定可以从数据库重建同样动作。可见 AX 元素可尝试点击，但被清除、收起、重建后不能当作持久定位器。

专注模式、前台不发通知、禁用通知、通知合并/清除均应纳入测试；不同模式下是否仍写数据库不能凭推测保证。通知只能作为“有新活动”的弱信号，再用已登记会话核对；匹配不到或多个候选时，展示未匹配事件，绝不按同名/最近会话强行跳转。

默认不启用通知数据库或 AX 抓取。仅当某个 adapter 没有可靠事件时增加该可选能力，限定来源并避免保留无关 App 的通知。

## 五路接入证据与边界

### Codex App

官方 [hooks 文档](https://learn.chatgpt.com/docs/hooks)提供 SessionStart、UserPromptSubmit、Stop、PermissionRequest 等事件和 session_id；[链接文档](https://learn.chatgpt.com/docs/reference/commands)明确支持 `codex://threads/<thread-id>`。

方案：先验证当前桌面版本 hooks 的实际投递，绑定 session_id 和 thread ID；点击使用官方链接。不要把当前 Codex 对话内的 list_threads/wait_threads 工具当作独立程序可用的系统 API。也不要启动另一个 app-server 就假设能收到既有桌面进程的全部事件。

Stop 是停止阶段信号，其他 hook 可要求继续，因此不是任务成功证明。优先使用真正终态事件；否则显示回合结束候选，并在后续活动时撤回。进程退出不能当作完成。

### Claude Code Desktop

[Desktop 官方文档](https://code.claude.com/docs/en/desktop)确认 settings 中的 hooks 对桌面和 CLI 均生效，适用于这里的 Code 本地会话，不据此外推普通 Chat/Cowork。

[Claude 链接文档](https://support.claude.com/en/articles/14729294-open-claude-desktop-with-a-link)明确列出普通 chat 定位和新建 Code 链接；没有列出现有 Code 会话的精确格式。此前“官方已经提供指定 Code 会话链接”的说法过强，现更正。

本机 Claude 1.52386.3 注册 `claude` scheme；只读包检查发现 `claude://resume` 处理相关文本，但不证明它是现有 Desktop 会话聚焦入口，也不能排除导入/恢复语义。

后续本机检查已找到入口：`claude://code/continue?session=<desktop-session-id>`，代码按已有会话 ID 查找并导航，有功能开关。本机已完成一次真实 Stop 事件、cliSessionId 到 sessionId 映射、跨会话链接跳转及 AX 页面 ID 核验；详情与覆盖边界见 [接入验证记录](../research/2026-09-13-integration-probe.md)。

方案：hooks 收状态；原型阶段确认 Desktop 会话 ID 与 runtime session ID 的映射，再验证已有会话导航。若只能 AX 操作，必须通过会话标识或唯一上下文选择并验证结果；不能只按标题点击。

### Zcode Desktop

[官方 hooks 文档](https://zcode.z.ai/en/docs/hooks)提供 SessionStart、UserPromptSubmit、PermissionRequest、Stop，输入包括 session_id，Stop 带 last_assistant_message。先使用只上报、不返回阻断或审批决策的 hook。

本机 ZCode 3.11.2，bundle ID `dev.zcode.app`，注册 `zcode` scheme。只读解析 app.asar 的 `out/main/index.js`，Rh/handleDeepLink 处理路径确认工作区打开、支付回调、OAuth；发现 `zcode://workspace/open?path=...`。该路径未发现精确会话跳转，不能凭 scheme 注册判断支持。

方案：hooks 收事件，工作区链接只作降级。需要确认 taskId/sessionId 映射和可用的现有会话导航接口；若无稳定入口，再测试 AX。若 AX 不能区分同名多会话，Zcode 显示“可提醒、仅打开工作区”，不算完整支持。

### Pi / iTerm2

本机包 `@earendil-works/pi-coding-agent` 0.85.1。[随包扩展文档](/opt/homebrew/lib/node_modules/@earendil-works/pi-coding-agent/docs/extensions.md)明确包含 session_start、sessionManager.getSessionId()、agent_end 和 agent_settled。优先 agent_settled，因为其语义包含没有待重试、压缩、后续轮次；仍须结合错误/中断状态，不能直接叫成功。

用原生 extension 上报事件，并登记当前 iTerm 会话；恢复/切换 Pi session 时更新映射。扩展目录和装载方式以当前版本实测为准。

### Kimi / iTerm2

本机 `kimi --version` 为 0.42.0。[当前官方 hooks 文档](https://www.kimi.com/code/docs/en/kimi-code-cli/customization/hooks.html)提供 session_id/session_title、SessionStart、TurnStarted、Stop、PermissionRequest、StopFailure、Interrupt、SessionHeartbeat 等。文档可能领先本机版本，实施前检查该版本配置解析与事件实现；若缺失，只读日志回退须另验，不能直接写新配置导致旧版本失败。

Pi/Kimi 共用 [iTerm2 Python API](https://iterm2.com/python-api/session.html)：Session.async_activate(select_tab=True, order_window_front=True) 能选择 session 并置前窗口。实施时用原生 session ID 登记，不用 tab 序号或标题；验证 ITERM_SESSION_ID 与 API ID 的对应关系、API 启用与授权状态。pane 关闭即映射失效，不能把复用 pane 内的另一任务误认为原会话。

## 实现设计

采用 SwiftUI 菜单栏界面 + AppKit 窗口/URL 打开 + SQLite。第一版让 App 内的服务接收本地事件，不额外部署常驻 daemon；需要独立 CLI 时提供一个小型 ingest 辅助程序。Python 仅用于 iTerm 官方 API 桥接。

传输优先用户私有目录的 Unix socket。ingest 在 socket 不可用时写入小型待投递文件，App 启动后补收；hook 快速退出、不等待 GUI 或网络、不改变 agent 正常行为。Qt/Electron/Web 服务在当前纯 Mac 范围内没有必要收益。

数据分三类：

- Session：provider、profile/account 范围、session_id、宿主、项目、标题、状态、最后活动、可打开能力。
- Event：event_id、session_id、turn_id（若有）、来源序号、接收时间、类型、摘要、证据等级。
- Locator：App scheme/已验证参数，或 iTerm session ID 与 agent session 的有效期绑定。原始事件不能直接提供任意待执行 shell。

provider + profile + session_id 构成身份；标题、cwd 仅用于展示。状态与未读分开保存：running / awaiting_input / idle / failed / interrupted / unknown；unread 是独立属性。迟到的旧回合事件不得覆盖新回合运行状态。工具失败与整轮失败分开，subagent 停止不得误报父任务完成。

摘要先用已有标题和最终回复短摘，不增加模型成本；正文长度受限，本地保存。默认保留近期事件，可清空。适配器版本和来源格式变化导致解析失败时，显示接入异常，不静默标空闲。

界面默认显示“等我处理”，顺序为等待输入/错误/新回复，可切到全部；每项显示 agent、任务、项目、等待时间和一句结果。Enter 打开，明确提供标已处理。打开失败保留未读；仅将 App 置前不应自动标已读。不依赖“任务完成”的模型自述。

## 实施顺序与验收

阶段 1：连接验证。五个来源各跑受控会话，先不做正式 UI。重点验证 Claude/Zcode 精确跳转和 Kimi 版本兼容。产出每个来源的能力表、脱敏事件样例和定位验证结果。预估 1–3 个工程日，主要取决于两个桌面 App 的导航接口。

阶段 2：可用收件箱。事件存储、去重、菜单栏、快捷键和已验证定位器，接入 Pi/Kimi/Codex 后继续补齐 Claude/Zcode。预估再 3–5 个工程日；不应将未解决的两个入口隐藏在“全部支持”中。

阶段 3：必要时补 AX、通知捕获或版本回退。额外 2–5 个工程日是估算，不是交付承诺；若需要依赖 App 内部实现，维护成本单独计入。

验收针对用户实际痛点：

1. 每种来源至少两个同目录/相近标题会话；连续 20 次定位，无一次误跳。不支持的情况必须显式降级。
2. 结束、审批等待、错误、中断分别触发；原始 hook 出发到本地列表目标 p95 < 2 秒（不含通知数据库回退）。
3. Stop 后自动续跑、工具失败后重试、子 agent 完成不会被标成任务成功。
4. App 重启、重复事件、乱序、pane 关闭及复用不丢未处理事件、不误路由。
5. 关闭横幅、开启专注模式仍能通过结构化事件更新；证明主链路不依赖系统通知。
6. 真实操作验证点击后出现目标会话；“open 命令返回成功”不算验收。

若 Claude/Zcode 没有可靠定位接口，停止扩展该路径，交付明确的部分支持或重新评估接入方式。用户目标要求五种原会话定位，全覆盖验收必须等五种均实际通过。

## 调研边界

已做官方资料核对、本机版本与 URL scheme 检查、Zcode/Claude 安装包定向只读检查、Pi 随包文档检查。未改 hooks、未安装工具、未监听私人通知、未读取通知数据库、未发送 agent 任务、未做真实跳转联调。本方案不需要因系统通知数据库访问权限而阻塞。
