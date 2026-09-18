# 统一会话收件箱 v0.4.0

状态：v0.4.0 已构建并通过完整 bundle 签名校验；在 v0.3 能力（Pi 标题、应用图标、成功打开后自动处理、系统通知、CLI 启动入口与官方图标）之上重做界面：列表懒加载（滚动到底自动追加，无手动翻页）、列表内 agent 名称用各家品牌主色、苹果风格排版与控件。Zcode 辅助功能授权已生效，"打开会话"停在任务搜索结果页的语义已实测；CLI 检测与进程 PATH 解耦（含已知安装路径第二通道）。统计类能力（工作日报、热力图与 Top 项目）独立成篇：[daily-report.md](daily-report.md)。

## 界面更新（2026-09-14）

## agent 会话过滤（2026-09-17）

多 agent 协作测试会批量产生用户不关心的会话。注册时为每行标记启动方式（`origin`：user/agent），agent 会话**不通知、不进待查看、不计入待查看/Dock 角标、默认不在列表与日报合计**；数据保留在库，工具栏「显示 agent 会话」开关（`rows --include-agents`）可审计。判定采用"声明优于推断"的分层设计（2026-09-17 第三轮定型）：**声明层**（最高优先级）——拉起方设置环境变量 `SESSION_MANAGER_ORIGIN=agent|user`（hook 继承 claude 进程环境，环境沿进程树继承；非法值忽略），多 agent 工具加一行 env 即精确判定，对任何新拉起方式免疫；**推断层**（未声明时的兜底，按来源取最强）：Codex 看 rollout `originator` 白名单（`HUMAN_ORIGINATORS`：Codex Desktop / codex-tui / codex_cli_rs=人工；workbench、ACP、codex_exec 等=agent，存量行一次性回填 `codex-origin:backfill-v1`）；Claude 用三级信号（同日两轮修复：控制终端被子进程继承，只看 tty 会把"终端里跑的工具拉起的 claude"误判成人工；Claude Desktop 内嵌会话同样无终端，只看 tty 又会把 Desktop 误判成 agent——用户实测抓到）——a) 有终端看直接父进程 `ps -o ucomm=`：终端 shell（`SHELL_PARENTS`）=手敲=人工，工具进程=agent；b) 无终端先查 Desktop 登记表（claude-code-sessions 的 cliSessionId，限近期活跃文件）命中=Desktop 界面驱动=人工；c) 未命中=无头 CLI=agent。collect_claude 每次刷新对登记表成员权威覆盖 origin=user 自愈（实测纠正 Desktop 误伤、wb-gate 无头行保持 agent）。存量 claude 行按目录约定回填（`CLAUDE_TMP_PREFIXES` 临时目录=agent，`claude-origin:tmp-backfill-v1`，实测 7 行），真实工作区目录的存量行按人工保留；受管理 pi/kimi/opencode/agy 经包装器启动天然人工；Zcode 桌面任务视为人工。手动改判是最终兜底：行内右键可把单条会话改判 agent/user，并可沉淀为目录覆盖规则（`origin-rules`，metadata 存储，收件箱 display 与日报 excluded 读时生效、即时反馈；CLI 等效命令 `inbox origin --id ID --set agent|user [--rule-project DIR]`）。已知边界：agent 用 shell 包装（`zsh -c`）或 pty 拉起无法识别（请改用声明变量）；Desktop 会话若被 UI 自动化驱动视为人工（登记表权威）；`codex_exec` 默认算 agent（白名单可配置）；agent 直接调用受管理包装器视为人工。

产品显示名统一为“Agent Notification”。主页面使用“待查看 / 全部会话”，操作为“标记已读 / 前往会话”；待查看列表支持勾选批量已读（2026-09-17，经用户反馈由“一键全部”收敛为手动选择）：行首勾选框默认全不选、手动选择任意子集，筛选行下批量栏提供「全选 / 全不选」与「标记已读（N）」；确认把点击时勾选项的整份 (id, revision) 快照交后端逐项 CAS 确认（`ack-batch`），确认瞬间已有新活动的项 revision 失配被跳过并保留未读。“已读”只确认通知已查看，不代表任务完成。内部 revision 校验和运行状态语义不变。应用包路径及 bundle identifier 保持不变，构建后刷新本应用的系统注册；系统设置已打开时需重新进入页面查看新名称或图标。

主窗口默认 400 × 620 点，最小宽度 360 点；旧窗口尺寸首次升级时迁移一次，此后保留用户调整。使用系统字体和语义颜色，标题、待查看计数、CLI 快捷入口、筛选与搜索依次排列。会话标题最多两行，状态、项目与时间放入内容区；右侧使用带辅助功能标签的“标记已读”和“前往会话”图标按钮。未读项保留橙色圆点和淡底色。列表来源图标为 30 点、CLI 入口 24 点：各来源用官方标志（Claude Code 用 Clawd 而非 Claude 桌面图标；Codex、Antigravity 抠掉应用瓦片只留标志；Kimi 按官方 favicon 重绘矢量；OpenCode 用 GitHub 组织头像的官方六边形标志亮度键控为透明底 glyph，2026-09-16），渲染时裁掉自带留白后等比缩进同一方框，按目标点数绘制不做二次放大；操作按钮有常态淡底和按下缩放/加深反馈，禁用时不显示可交互反馈。

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

窗口顶部「新建会话」行平铺已安装 agent 的图标按钮，启动时检测本机已安装的 agent CLI（经 `shutil.which` 与常见安装位置检测，只显示已安装项）；范围仅限六个 CLI（claude、codex、pi、kimi、agy、opencode），桌面 App 不在入口范围。窗口放不下全部按钮时，行尾收敛为一个「+N」按钮，点击打开图标面板列出剩余 agent（macOS 菜单项不渲染自定义图片），窗口宽度变化时自动重排。点击按钮先选目录（记住上次位置），再在 iTerm2 新标签中 `cd <目录>` 并启动：

- Claude/Codex 直接运行对应命令；会话事件经既有 hooks 与 rollout 采集管道进入收件箱。
- Pi/Kimi 经 `bin/session-manager pi|kimi` 受管理启动，绑定自动注册。该机制要求 iTerm2 标签环境；未检测到 iTerm2 时菜单项置灰并提示原因。
- Antigravity CLI（agy）为受管理 agent（2026-09-16 接入）：经 `bin/session-manager agy` 启动注册绑定，`setup-agy` 安装捕获插件（claude 风格命名钩子，官方机制；事件仅 PreToolUse/PostToolUse/PreInvocation/PostInvocation/Stop 五种）。映射：PreInvocation→UserPromptSubmit（运行中并改绑当前会话，TUI 内切换会话自动跟随）、Stop→本轮已结束+待查看；无等待权限/失败/中断事件（hooks 不提供），退出由包装器兜底 SessionEnd→已退出。事件载荷含 conversationId 与 workspacePaths（camelCase protojson）。agy 进程启动时缓存 hooks，修改 hooks.json 需重启会话。标题由 summaries 库补充采集（只补已有受管理行）。Gemini CLI 已于 2026-06-18 对消费级用户停止服务，不再收录，Google 官方继任者为 Antigravity CLI。
- OpenCode 为受管理 agent（2026-09-16 由被动扫描切换，用户决定与 Pi/Kimi 走同一实现避免双套逻辑）：经 `bin/session-manager opencode` 启动注册绑定；事件通道为插件 `scripts/opencode_capture.js`，由包装器经 `OPENCODE_CONFIG` 环境变量叠加注入（实测叠加语义，用户全局配置零改动；非受管理会话不设该变量、插件不加载）。SDK 事件映射：session.created/updated→SessionStart（登记+标题/目录）、chat.message→UserPromptSubmit（运行中）、session.idle→Stop（结束+待查看）、message.updated 带 error→StopFailure、permission.updated(type=ask)→PermissionRequest（等待）、permission.replied→PermissionResult；包装器退出兜底 SessionEnd。切换时一次性删除旧被动行（44 条，`opencode:managed-migration`）。工作日报的 OpenCode token 统计自 2026-09-17 起同样只统计受管理会话（从全库直读切换，与收件箱对账；口径见 daily-report.md）。
- 首次启动会请求「Agent Notification 控制 iTerm2」自动化授权；拒绝或 AppleScript 失败时启动失败并在窗口内显示原因，不换控制技术绕过。
- 标签创建成功只代表会话已启动；"本轮完成"等状态仍以各来源真实事件为准，不由启动入口推断。

## 来源与定位

| 来源 | 状态与元数据 | 打开方式 |
|---|---|---|
| Claude Desktop | 本地元数据补标题/工作区/桌面 ID，hooks 更新运行、结束和等待；旧会话没有可靠状态时明确未知。Claude Code CLI 直启的会话（用户级 hooks 带 cwd）以 cli 定位进入收件箱（2026-09-16 起可见，此前隐藏），标题取转写首条用户消息（≤80 字符），Desktop 元数据若匹配到同会话仍会改写为深链 | Desktop 会话走 claude://code/continue 深链；CLI 会话先聚焦已打开的标签（命令行含 `--resume <ID>` 的进程，或经 lsof 定位持有转写文件 fd 的进程——locator 携带转写路径），都没有才在新标签 `claude --resume <会话ID>`；目录缺失拒绝 |
| Codex Desktop | rollout 完整行增量读取，session_index 补标题，按会话与 turn ID 去重；CLI/exec 来源（codex_cli_rs、codex_exec 等）2026-09-16 起一并纳入（locator 为 cli），首次纳入以当下为提醒基线（`codex-cli:baseline`），历史完成不轰炸；13 个 Desktop 混合身份文件仍跳过；exec/ACP 变体允许生命周期事件缺 turn_id（跳过该条而非整文件报错），纳入中间态的错误缓存做了一次非 Desktop 清理（2026-09-16）。受管理升级经论证可行但暂缓（notify/插件/rollout 三通道拼装成本高于收益），分析见 [Codex 受管理可行性论证](../research/2026-09-16-codex-managed-feasibility.md) | codex://threads 链接（Desktop）；CLI 会话先聚焦已打开的标签（命令行含 `resume <ID>` 的进程，或经 lsof 定位持有 rollout 文件 fd 的进程——locator 携带 rollout 路径），都没有才在新标签 `codex resume <会话ID>`；目录缺失拒绝；OS 分发不等同于本轮 UI 验证 |
| Zcode | 任务索引提供元数据，运行数据库 turn_usage 提供最新轮次的开始、完成、失败与取消；原生未读标记仅补充 | 原生 AX 打开任务搜索并预填标题，切到任务范围后停在结果页；同名/相似命中由使用者自行选择（2026-09-14 起生效，替代自动点击候选的精确跳转） |
| Antigravity CLI（agy） | 受管理插件事件（2026-09-16 起）：PreInvocation→运行中并改绑当前会话、Stop→本轮已结束+待查看、包装器退出兜底已退出；无等待/失败/中断事件（hooks 五事件不含），标题由 summaries 库只读补充（仅已有行） | 与 Pi/Kimi 同构的聚焦（run/session/lease/前台组校验）；绑定死亡时降级受管理恢复：新标签带 `--conversation <会话ID>` 重启并注册新绑定（导航 `managed_resume`），恢复前先查同会话其它活绑定避免重复开窗；目录缺失拒绝 |
| Pi | 原生扩展事件，agent_settled 才标本轮结束 | run/session/lease/前台组验证后的 iTerm2 聚焦；绑定死亡降级受管理恢复（`--session`，四家统一） |
| Kimi | 生命周期 hooks；正常/错误/等待分别处理 | 同 Pi（恢复经 `--session`） |
| OpenCode | 受管理插件事件（2026-09-16 起生效，替代同日早前的被动扫描实现）：`bin/session-manager opencode` 注册绑定后，插件 opencode_capture.js 经 OPENCODE_CONFIG 叠加注入（用户全局配置零改动，非受管理会话不加载）。session.created/updated→登记会话+标题+目录；chat.message→运行中；session.idle→本轮已结束+待查看；assistant error→发生错误+待查看；permission.updated(ask)→等待输入、permission.replied→运行中；包装器退出兜底已退出。事件经 session_binding.record_event 与 Kimi 同一链路去重与登记（迟事件/换会话/复用 pane 拒绝同合同） | 与 Pi/Kimi 同构的聚焦（run/session/lease/前台组校验）；绑定死亡时降级受管理恢复：新标签带 `--conversation <会话ID>` 重启并注册新绑定（导航 `managed_resume`），恢复前先查同会话其它活绑定避免重复开窗；目录缺失拒绝 |

本轮已导入 480 个 Codex、76 个 Claude、64 个 Zcode 非隐藏会话，初始未读为 0；Pi/Kimi 会在受管理运行后出现。初次刷新约 7.5 秒，增量刷新约 0.12 秒（本机样本，不是性能保证）。

有 13 个 Codex 文件在目标头之后混入其他 session_meta；当前无法无歧义归属，因此保留跳过并显示“历史记录暂未接入”，不把父会话历史当成当前子会话。目标头之前的祖先前缀可跳过，目标头之后出现不同身份仍拒绝。失败文件的签名缓存避免每次重复解析，文件变化后重试。未来 Codex 格式变化仍需维护适配器。

第一次导入不会把所有历史完成记录变成待处理。Codex 和 Zcode 的完成事件均按首次监控时间作为基线；Zcode 的新完成/失败不依赖原生蓝点，原 App 清除蓝点不会替本收件箱确认已处理。CLI 进程退出后入口禁用，但未处理的结果可保留。

Zcode 会用最新轮次的真实时间作事件时钟，任务的改名或查看时间不产生完成事件。旧版以 updated_at 作时钟的数据会一次性迁移，回补监控开始后漏掉的完成；仅匹配任务索引中的 session ID，子 agent 不冒充主任务。修复证据见 [Zcode 待处理遗漏](../research/2026-09-14-zcode-inbox-fix.md)。

## 数据与状态语义

本地状态位于 `~/.local/state/session-manager/inbox.sqlite`，与现有绑定库并存。保存会话标识、标题摘要、项目、状态、活动时间、事件去重 ID、未读版本和定位信息；不保存提示词全文、回复全文、工具参数或凭据。

状态：running、waiting、idle、failed、interrupted、closed、unknown。“本轮已结束”不表示业务目标成功。Stop 后如果继续运行，后续运行事件会清除过期的待处理状态。来源没有事件时不能凭一段时间无输出判断完成。

状态与展示文案对照：running→运行中、waiting→等待输入、idle→本轮已结束、failed→发生错误、interrupted→已中断、closed→已退出、unknown→状态待确认。未读视图 waiting 优先、failed 次之；closed 且无法打开的项直接过滤，也不计入待查看数量（菜单栏、Dock 角标与列表同口径，2026-09-15 起生效，替代原先"过滤但仍计数"）；closed 与 idle 对用户含义一致（都可经原入口重开查看），卡片外观不做区分。卡片只在未处理时显示“会话时长”：从最近一次通知抬升（`attention_at`）起实时累计；已处理（手动标记与成功打开自动确认，处理时刻记为 `acknowledged_at`）后不再展示时长。来源侧自行清除未读（如 Interrupt、新一轮 running 事件）同样不展示。时长以事件时钟为准，来源事件时间戳滞后（如 Zcode 补扫完成回合）会相应放大显示值。

各状态的可达来源（2026-09-14 梳理，2026-09-16 增补 OpenCode 与 agy）：`waiting` 只来自 hooks 与 Pi 扩展事件（Claude/Kimi PermissionRequest、Pi ui_prompt_start）；`closed` 只来自 Claude/Kimi SessionEnd 与 Pi session_shutdown；`failed` 来自各来源 error/StopFailure（OpenCode 为 assistant 消息带 error 字段）；`interrupted` 来自 Codex turn_aborted、Zcode 回合取消、hook Interrupt 和 OpenCode MessageAbortedError；`running` 来自 hooks/rollout/Zcode 推断、OpenCode 流式行（completed 回填前）与 agy PreInvocation；`failed` 追加说明：agy hooks 无失败事件，回合失败不单独标注；`unknown` 是库默认值及未知 task_status 的兜底。OpenCode 边界：无 waiting/closed；交互中的会话在回合落定前保持原状态；进程被强杀时 running 滞留。这是数据源边界，不是采集器缺陷。

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

- 当前 102 项 Python 检查通过，包括重复完成事件不恢复已处理项、旧 UI 不能清掉新回复、迟到事件不覆盖新运行、相同 unread marker 不重复提醒、源配置安装幂等，以及 Zcode 无蓝点完成、旧时钟迁移、独立已处理状态和运行中推断（活跃回合显示运行中、真实完成覆盖推断、滞后索引不能覆盖已记录完成）。
- OpenCode 受管理化（2026-09-16，第五轮迭代：同日经历被动扫描接入→逐回合口径→全状态识别→聚焦已有标签→按用户决定收敛为与 Pi/Kimi 同一受管理实现，被动扫描器与进程匹配聚焦整体移除，旧被动行 44 条一次性清理）。7 项受管理单测（插件事件全生命周期映射、非受管理忽略、iterm_probe 分发、SDK 事件名翻译含 permission ask/assistant error、非受管理插件空 hooks、迁移幂等）+ agent_launch/绑定既有测试全量 98 项通过。真机验证：受管理启动→绑定登记（lease_alive=true）→提示词触发 SessionStart 登记 session_id、Stop 后收件箱 idle+待查看→focus 返回 managed_binding_verified=true/agent_ownership_verified=true 且标签选中→/exit 退出后状态 closed、绑定删除、旧 run_id 重放 focus 返回 refused。
- 原生 App 已实际打开，显示真实多来源会话，“全部 / 待处理”过滤工作正常。
- 注入明确标注“模拟事件”的测试项后，UI 待处理计数变成 1；点击已处理后变成 0，数据库确认未读已清除。该模拟项随后隐藏，未操作任何真实待办。
- 未通过本轮 UI 操作绕过对 Codex/iTerm2 的 Computer Use 工具限制；对应导航沿用此前已验证的入口，最终从应用点击的权限行为以实际使用为准。

实现：[状态库](../../scripts/inbox_store.py)、[来源采集](../../scripts/inbox_sources.py)、[命令入口](../../scripts/inbox.py)、[原生界面](../../native/SessionInbox.swift)、[队列测试](../../tests/test_inbox.py)。

### 验收记录（v0.2 起，增量）

- 勾选批量「标记已读」（2026-09-17，两轮：首版为工具栏“一键全部”，用户反馈“要能手动选择会话、而不是默认所有会话”后收敛为勾选式）：待查看行首勾选框默认全不选，筛选行下批量栏「全选/全不选」与「标记已读（N）」，把点击时勾选项的整份 (id, revision) 快照经 `ack-batch` 逐项 CAS 确认。6 项后端单测（批量成功、陈旧 revision 跳过并保留未读、缺失行忽略、CLI 计数返回与非数组拒绝），全量 130 项通过。真机验证采用隔离 HOME 沙箱（`DEFAULT_ROOT` 与来源扫描随 `Path.home()` 切到临时目录，真实待办零触碰；清空真实库再 refresh 是死路——来源扫描会重灌全部会话）：4 条模拟事项勾选 2 条确认 → 待查看 4→2、仅被勾选项 unread=0 且 acknowledged_at 落库、未勾项原样保留、勾选自动清空；「全选」路径 2→0 同样验证。陈旧 revision 路径另经 CLI 端到端验证（skipped=1、未读保留）。验收截图：scratch/batch-select-before.png、batch-select-after.png。
- agy 接入验收补充（2026-09-16 用户验收反馈两项修复）：①agy 卡片标题显示为会话 ID——summaries 行 title 常为空、真实标题在 preview，采集器改为 title→preview 兜底；②CLI 窗口关闭后「前往会话」报 binding expired——为 agy/opencode 增加绑定死亡恢复链（活绑定改查→受管理恢复新标签，navigation=managed_resume），四家受管理 CLI 统一支持（pi/kimi `--session` 亦实测 help 确认）；display_rows 对受管理 provider 常亮按钮。真机验证：agy 死绑定会话 open 返回 managed_resume、新标签以 `--conversation` 恢复且新绑定注册。③同日再补：受管理 agy/opencode 行的项目目录被删时条目隐藏（refresh 每次清扫、目录恢复自动取消隐藏）——否则残留的可点行会让人点到必失败的"session directory is missing"；目录缺失的恢复拒绝仍保留为兜底。
- agy 受管理接入（2026-09-16）：9 项专属单测（事件全生命周期、TUI 切会话改绑、未管理忽略、包装器 SessionEnd、过期锁拒绝、agy-hook 载荷转译、缺 conversationId 静默、标题只补已有行、插件安装幂等）+ 既有绑定测试，全量 107 项通过。真机验证：setup-agy 安装插件→受管理启动注册绑定→TUI 回合触发 PreInvocation/Stop（事件时钟推进、待查看抬升）→focus 三重校验通过且标签选中→进程退出后状态已退出、绑定删除、旧 run_id focus 返回 refused。已知边界：agy hooks 官方仅五事件（无失败/等待/中断）；回合事件可能在回合结束才落地（运行中显示 best-effort）；TUI 退出命令未探明，退出语义经包装器兜底路径验证（模拟进程退出）。agy hooks 在进程启动时缓存，setup-agy 后需重启 agy 会话。
- 新增测试覆盖打开成功自动确认、失败保留、新回复竞态、活动时间排序和 Pi 标题身份核验；Swift 纯策略测试覆盖分页边界和通知去重/启动静默。
- GUI 已验证全部会话从第 1 页切换到第 2 页；应用图标已渲染检查。
- 系统设置与 App 均显示通知开启，模拟事件的通知请求被系统接受（App 的通知去重记录含 token）。点击通知的行为改为仅置前收件箱；原先的自动打开实现（pendingNotificationOpen）已移除。实际点击验证：收件箱打开，无 Zcode 导航（原生助手日志无新记录），模拟项未读保留；该模拟项验收后已清理（2026-09-14）。
- Zcode 辅助功能授权重新添加后已生效（2026-09-14 验收）：模拟事项经 App「打开会话」首次尝试因搜索框焦点竞态被助手拒绝且保留未读、错误行内显示；重试后原生助手完成搜索、粘贴、候选选中与复制任务路径身份核验（selection_identity_matches=true）并置前目标任务，App 按点击时 revision 自动确认（unread 1→0），界面与数据库一致回到"暂时没有待处理事项"。同日简化 Zcode 打开语义：助手只负责打开任务搜索、切到任务范围并预填标题，停在结果页由使用者自行选择目标；点击候选与复制任务路径核验的自动跳转已移除——精确跳转在多候选竞争与虚拟列表渲染竞态下成功率不稳定，搜索停靠一次即可用。
