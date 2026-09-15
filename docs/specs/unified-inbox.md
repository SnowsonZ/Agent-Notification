# 统一会话收件箱 v0.4.0

状态：v0.4.0 已构建并通过完整 bundle 签名校验；在 v0.3 能力（Pi 标题、应用图标、成功打开后自动处理、系统通知、CLI 启动入口与官方图标）之上重做界面：列表懒加载（滚动到底自动追加，无手动翻页）、列表内 agent 名称用各家品牌主色、苹果风格排版与控件。Zcode 辅助功能授权已生效，"打开会话"停在任务搜索结果页的语义已实测；CLI 检测与进程 PATH 解耦（含已知安装路径第二通道）。统计类能力（工作日报、热力图与 Top 项目）独立成篇：[daily-report.md](daily-report.md)。

## 界面更新（2026-09-14）

产品显示名统一为“Agent Notification”。主页面使用“待查看 / 全部会话”，操作为“标记已读 / 前往会话”；“已读”只确认通知已查看，不代表任务完成。内部 revision 校验和运行状态语义不变。应用包路径及 bundle identifier 保持不变，构建后刷新本应用的系统注册；系统设置已打开时需重新进入页面查看新名称或图标。

主窗口默认 400 × 620 点，最小宽度 360 点；旧窗口尺寸首次升级时迁移一次，此后保留用户调整。使用系统字体和语义颜色，标题、待查看计数、CLI 快捷入口、筛选与搜索依次排列。会话标题最多两行，状态、项目与时间放入内容区；右侧使用带辅助功能标签的“标记已读”和“前往会话”图标按钮。未读项保留橙色圆点和淡底色。列表来源图标为 30 点、CLI 入口 24 点：各来源用官方标志（Claude Code 用 Clawd 而非 Claude 桌面图标；Codex、Antigravity 抠掉应用瓦片只留标志；Kimi 按官方 favicon 重绘矢量），渲染时裁掉自带留白后等比缩进同一方框，按目标点数绘制不做二次放大；操作按钮有常态淡底和按下缩放/加深反馈，禁用时不显示可交互反馈。

App 图标由 `native/GenerateAppIcon.swift` 绘制：agent 头像为主体，右上角橙色通知圆点，分别生成亮色与暗色资源，Dock 随系统外观切换。未读角标直接画进 Dock 图标（橙底白字，随 3 秒刷新更新，与托盘同口径；`dockTile.badgeLabel` 在运行时替换 `applicationIconImage` 的应用上不渲染，实测弃用）。Finder 使用默认亮色资源。本轮 66 项 Python 检查通过，Swift 构建与 bundle 签名校验通过；图标两套已视觉检查，真实列表布局已打开检查。导航行为未作为本轮 UI 改动的重新验收项。

## 启动与日常使用

```sh
bin/session-manager app
```

应用为 `build/Agent Notification.app`。窗口显示“待处理 / 全部会话”，菜单栏图标与 Dock 角标显示待处理数量，可重新打开列表。窗口关闭后，App 未退出时仍每 3 秒刷新。收件箱为单实例：重复点通知、托盘或 `open` 只聚焦已有窗口，不会新建。支持系统通知；没有配置登录自启动或全局热键。全部会话按最新活动时间倒序、懒加载：初始 20 条，滚动到底自动追加下一页，无手动翻页；搜索或切换列表重置已加载页。待处理列表保留等待/错误优先级。列表内 agent 名称使用各家品牌主色（Claude 橙红、Codex 蓝、Kimi 深蓝、Pi 灰蓝、Zcode 石墨、Antigravity 谷歌蓝暂定、OpenCode 琥珀暂定）。

Pi/Kimi 继续使用现有入口启动，之后的会话状态自动进入列表：

```sh
bin/session-manager pi
bin/session-manager kimi
```

Claude 已安装用户级观察 hooks；Kimi 的原有两条绑定 hooks 扩展为八种生命周期事件。hooks 不返回审批决定；Kimi 普通非受管理启动直接返回，不收集数据。既有会话不保证热加载新 hooks，完整实时覆盖从之后启动的会话开始。

“打开会话”成功后自动标记已处理，仍可手动点“已处理”。打开动作携带点击时的 revision；失败保留未读，打开期间到达的新回复不会被旧动作清除。Pi/Kimi/Zcode 使用适配器成功结果；Claude/Codex URL 以 OS 接受分发为成功，不能据此证明目标 UI 已显示。

App 运行时通知新的待处理事件；首次快照静默，不补发历史堆积。铃铛按钮控制通知，系统需允许通知。通知按事项和事件 token 去重；点击通知仅将收件箱窗口置前，不自动打开目标会话，也不改变未读状态（2026-09-14 起生效，替代原先的"点击即打开目标会话"）。通知内容只包含来源、状态和展示标题。

Pi 标题优先使用自定义会话名，否则截取首条用户消息最多 80 字符；空会话使用项目名。只保存展示摘要，不保存完整正文。既有受管理记录可从身份核对后的本地会话文件限量补标题，后续变更由扩展事件更新。

## CLI 启动入口

窗口顶部「新建会话」行平铺已安装 agent 的图标按钮，启动时检测本机已安装的 agent CLI（经 `shutil.which` 与常见安装位置检测，只显示已安装项）；范围仅限六个 CLI（claude、codex、pi、kimi、agy、opencode），桌面 App 不在入口范围。窗口放不下全部按钮时，行尾收敛为一个「+N」按钮，点击菜单列出剩余 agent，窗口宽度变化时自动重排。点击按钮先选目录（记住上次位置），再在 iTerm2 新标签中 `cd <目录>` 并启动：

- Claude/Codex 直接运行对应命令；会话事件经既有 hooks 与 rollout 采集管道进入收件箱。
- Pi/Kimi 经 `bin/session-manager pi|kimi` 受管理启动，绑定自动注册。该机制要求 iTerm2 标签环境；未检测到 iTerm2 时菜单项置灰并提示原因。
- Antigravity CLI（agy）/OpenCode 目前仅启动：未过受管理启动器、无采集管道，其会话不进入收件箱列表（按 cli-session-binding.md，未通过启动器运行的会话不自动归属）；后续按各家 hook 能力逐家评估接入。Gemini CLI 已于 2026-06-18 对消费级用户停止服务，不再收录，Google 官方继任者为 Antigravity CLI。
- 首次启动会请求「Agent Notification 控制 iTerm2」自动化授权；拒绝或 AppleScript 失败时启动失败并在窗口内显示原因，不换控制技术绕过。
- 标签创建成功只代表会话已启动；"本轮完成"等状态仍以各来源真实事件为准，不由启动入口推断。

## 来源与定位

| 来源 | 状态与元数据 | 打开方式 |
|---|---|---|
| Claude Desktop | 本地元数据补标题/工作区/桌面 ID，hooks 更新运行、结束和等待；旧会话没有可靠状态时明确未知 | 已验证的 claude://code/continue 链接 |
| Codex Desktop | rollout 完整行增量读取，session_index 补标题，按会话与 turn ID 去重 | codex://threads 链接；OS 分发不等同于本轮 UI 验证 |
| Zcode | 任务索引提供元数据，运行数据库 turn_usage 提供最新轮次的开始、完成、失败与取消；原生未读标记仅补充 | 原生 AX 打开任务搜索并预填标题，切到任务范围后停在结果页；同名/相似命中由使用者自行选择（2026-09-14 起生效，替代自动点击候选的精确跳转） |
| Pi | 原生扩展事件，agent_settled 才标本轮结束 | run/session/lease/前台组验证后的 iTerm2 聚焦 |
| Kimi | 生命周期 hooks；正常/错误/等待分别处理 | 同 Pi |

本轮已导入 480 个 Codex、76 个 Claude、64 个 Zcode 非隐藏会话，初始未读为 0；Pi/Kimi 会在受管理运行后出现。初次刷新约 7.5 秒，增量刷新约 0.12 秒（本机样本，不是性能保证）。

有 13 个 Codex 文件在目标头之后混入其他 session_meta；当前无法无歧义归属，因此保留跳过并显示“历史记录暂未接入”，不把父会话历史当成当前子会话。目标头之前的祖先前缀可跳过，目标头之后出现不同身份仍拒绝。失败文件的签名缓存避免每次重复解析，文件变化后重试。未来 Codex 格式变化仍需维护适配器。

第一次导入不会把所有历史完成记录变成待处理。Codex 和 Zcode 的完成事件均按首次监控时间作为基线；Zcode 的新完成/失败不依赖原生蓝点，原 App 清除蓝点不会替本收件箱确认已处理。CLI 进程退出后入口禁用，但未处理的结果可保留。

Zcode 会用最新轮次的真实时间作事件时钟，任务的改名或查看时间不产生完成事件。旧版以 updated_at 作时钟的数据会一次性迁移，回补监控开始后漏掉的完成；仅匹配任务索引中的 session ID，子 agent 不冒充主任务。修复证据见 [Zcode 待处理遗漏](../research/2026-09-14-zcode-inbox-fix.md)。

## 数据与状态语义

本地状态位于 `~/.local/state/session-manager/inbox.sqlite`，与现有绑定库并存。保存会话标识、标题摘要、项目、状态、活动时间、事件去重 ID、未读版本和定位信息；不保存提示词全文、回复全文、工具参数或凭据。

状态：running、waiting、idle、failed、interrupted、closed、unknown。“本轮已结束”不表示业务目标成功。Stop 后如果继续运行，后续运行事件会清除过期的待处理状态。来源没有事件时不能凭一段时间无输出判断完成。

状态与展示文案对照：running→运行中、waiting→等待输入、idle→本轮已结束、failed→发生错误、interrupted→已中断、closed→已退出、unknown→状态待确认。未读视图 waiting 优先、failed 次之；closed 且无法打开的项直接过滤；closed 与 idle 对用户含义一致（都可经原入口重开查看），卡片外观不做区分。卡片只在未处理时显示“会话时长”：从最近一次通知抬升（`attention_at`）起实时累计；已处理（手动标记与成功打开自动确认，处理时刻记为 `acknowledged_at`）后不再展示时长。来源侧自行清除未读（如 Interrupt、新一轮 running 事件）同样不展示。时长以事件时钟为准，来源事件时间戳滞后（如 Zcode 补扫完成回合）会相应放大显示值。

各状态的可达来源（2026-09-14 梳理）：`waiting` 只来自 hooks 与 Pi 扩展事件（Claude/Kimi PermissionRequest、Pi ui_prompt_start）；`closed` 只来自 Claude/Kimi SessionEnd 与 Pi session_shutdown；`failed` 来自各来源 error/StopFailure；`interrupted` 来自 Codex turn_aborted、Zcode 回合取消和 hook Interrupt；`unknown` 是库默认值及未知 task_status 的兜底。

Zcode 边界：任务索引的 task_status 只持久化 running/completed/error，`turn_usage` 只在回合结束后落行且无等待类状态；“等待权限批准”只存在于桌面应用内存，不持久化。采集器用索引弥补回合行滞后：task_status 为 running 且更新时间晚于最新已记录回合结束、且该回合结束事件不是本批刚落库时，推断为“运行中”；推断时间戳不超过该回合结束时刻，真实完成事件始终能覆盖推断（2026-09-14 实现）。残余盲区：索引自身停在 completed 的运行中会话仍显示上一回合终态；“等待权限”与“运行中”不可区分，统一显示“运行中”；Zcode 的 waiting 映射仍是死分支。这是数据源边界，不是采集器缺陷；证据与查询见 [Zcode 状态语义排查](../research/2026-09-14-zcode-state-semantics.md)，Zcode 未来持久化等待信号后应回补采集。

事件有稳定 ID 时去重；旧时间戳事件不能覆盖较新的状态。CLI hooks 没有可靠 turn ID 的情况按接收顺序处理，尚未承诺异常重排情况下的精确顺序。快照元数据更新不单独构成新注意事项。activity_at 用于排序，与状态事件时钟分离，查看或改名不冒充任务完成。

## CLI 与维护

```sh
bin/session-manager inbox rows --refresh
bin/session-manager inbox rows --all --refresh
bin/session-manager inbox ack ITEM_ID --revision REVISION
bin/session-manager inbox open ITEM_ID
bin/session-manager inbox sync
bin/session-manager inbox setup
```

setup 可重复运行，保留既有配置与 hooks，不重复添加本项目相同命令。自定义 CLAUDE_CONFIG_DIR / KIMI_CODE_HOME 需要在对应环境执行 setup。App 固定关联当前工作目录，移动项目后应重建并更新 hooks。

重建 App：

```sh
python3 scripts/build_inbox_app.py
```

当前产物面向 Apple Silicon、macOS 14+。先退出应用再重建，构建脚本会拒绝覆盖运行中的应用。图标由 AppKit 生成并打包，完整 bundle 使用本地 ad-hoc 签名并校验；这不是分发公证。新签名需要重新添加辅助功能授权，采用拖拽方式：缺权限时点击 Zcode 事项的「前往会话」会自动打开「隐私与安全性 → 辅助功能」面板并弹出可拖拽的本应用悬浮窗，拖进列表即授权，授权后悬浮窗自动收起；`bin/session-manager permissions` 是等效手动入口。旧开关显示开启不代表新版已获授权。

## 验证证据

- 当前 68 项 Python 检查通过，包括重复完成事件不恢复已处理项、旧 UI 不能清掉新回复、迟到事件不覆盖新运行、相同 unread marker 不重复提醒、源配置安装幂等，以及 Zcode 无蓝点完成、旧时钟迁移、独立已处理状态和运行中推断（活跃回合显示运行中、真实完成覆盖推断、滞后索引不能覆盖已记录完成）。
- 原生 App 已实际打开，显示真实多来源会话，“全部 / 待处理”过滤工作正常。
- 注入明确标注“模拟事件”的测试项后，UI 待处理计数变成 1；点击已处理后变成 0，数据库确认未读已清除。该模拟项随后隐藏，未操作任何真实待办。
- 未通过本轮 UI 操作绕过对 Codex/iTerm2 的 Computer Use 工具限制；对应导航沿用此前已验证的入口，最终从应用点击的权限行为以实际使用为准。

实现：[状态库](../../scripts/inbox_store.py)、[来源采集](../../scripts/inbox_sources.py)、[命令入口](../../scripts/inbox.py)、[原生界面](../../native/SessionInbox.swift)、[队列测试](../../tests/test_inbox.py)。

### 验收记录（v0.2 起，增量）

- 新增测试覆盖打开成功自动确认、失败保留、新回复竞态、活动时间排序和 Pi 标题身份核验；Swift 纯策略测试覆盖分页边界和通知去重/启动静默。
- GUI 已验证全部会话从第 1 页切换到第 2 页；应用图标已渲染检查。
- 系统设置与 App 均显示通知开启，模拟事件的通知请求被系统接受（App 的通知去重记录含 token）。点击通知的行为改为仅置前收件箱；原先的自动打开实现（pendingNotificationOpen）已移除。实际点击验证：收件箱打开，无 Zcode 导航（原生助手日志无新记录），模拟项未读保留；该模拟项验收后已清理（2026-09-14）。
- Zcode 辅助功能授权重新添加后已生效（2026-09-14 验收）：模拟事项经 App「打开会话」首次尝试因搜索框焦点竞态被助手拒绝且保留未读、错误行内显示；重试后原生助手完成搜索、粘贴、候选选中与复制任务路径身份核验（selection_identity_matches=true）并置前目标任务，App 按点击时 revision 自动确认（unread 1→0），界面与数据库一致回到"暂时没有待处理事项"。同日简化 Zcode 打开语义：助手只负责打开任务搜索、切到任务范围并预填标题，停在结果页由使用者自行选择目标；点击候选与复制任务路径核验的自动跳转已移除——精确跳转在多候选竞争与虚拟列表渲染竞态下成功率不稳定，搜索停靠一次即可用。
