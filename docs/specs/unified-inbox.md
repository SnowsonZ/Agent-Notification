# 统一会话收件箱 v0.4.0

状态：v0.4.0 已构建并通过完整 bundle 签名校验；在 v0.3 能力（Pi 标题、应用图标、成功打开后自动处理、系统通知、CLI 启动入口与官方图标）之上重做界面：列表懒加载（滚动到底自动追加，无手动翻页）、列表内 agent 名称用各家品牌主色、苹果风格排版与控件。Zcode 辅助功能授权已生效，"打开会话"停在任务搜索结果页的语义已实测；CLI 检测与进程 PATH 解耦（含已知安装路径第二通道）。统计类能力（工作日报、热力图与 Top 项目）独立成篇：[daily-report.md](daily-report.md)。

## 界面更新（2026-09-14）

## agent 会话过滤（2026-09-17）

## 进行中视图（2026-09-20）

收件箱主页面分段由「待查看 / 全部会话」扩为三段，新增「进行中（N）」（同日修复：第三段标签缩短为「全部」，行尾预留搜索图标宽度，避免与放大镜重叠）：只列出**正在等待模型回复的回合**（`state == running`，口径为用户 2026-09-20 确认）。`waiting`（PermissionRequest 等，等的是用户确认权限）不算进行中；开着但空闲（idle）不算；进程存活与否不参与判定——不引入 lsof/绑定租约探测，纯回合状态口径，数据完全来自既有 `rows --all` 载荷，Python 后端零改动。列表复用全部会话的行布局与既有刷新节拍（见下文双节拍）；排序按最新活动时间倒序；搜索框同样生效；行首勾选框与批量已读栏只在待查看分段出现；懒加载分页仅全部会话分段保留（进行中列表天然小）。agent 会话跟随收件箱默认过滤（不显示、不计数；工具栏审计开关打开后自然可见）。分段计数与列表同口径（`inboxActiveListed`：running 且非「closed 不可打开」行）。

边界：Claude Desktop 无 hooks 永远到不了 running，明确降级不进列表（数据源边界，不是采集器缺陷）；进程被硬杀时无结束事件，running 会残留——沿用「来源无事件不能凭静默判完成」的事件语义，不做超时推断；受管理四家退出时有包装器 SessionEnd 兜底，正常离场。

多 agent 协作测试会批量产生用户不关心的会话。注册时为每行标记启动方式（`origin`：user/agent），agent 会话**不通知、不进待查看、不计入待查看/Dock 角标、默认不在列表与日报合计**；数据保留在库，工具栏「显示 agent 会话」开关（`rows --include-agents`）可审计。判定采用"声明优于推断"的分层设计（2026-09-17 第三轮定型）：**声明层**（最高优先级）——拉起方设置环境变量 `SESSION_MANAGER_ORIGIN=agent|user`（hook 继承 claude 进程环境，环境沿进程树继承；非法值忽略），多 agent 工具加一行 env 即精确判定，对任何新拉起方式免疫；**推断层**（未声明时的兜底，按来源取最强）：Codex 看 rollout `originator` 白名单（`HUMAN_ORIGINATORS`：Codex Desktop / codex-tui / codex_cli_rs=人工；workbench、ACP、codex_exec 等=agent，存量行一次性回填 `codex-origin:backfill-v1`）；Claude 用三级信号（同日两轮修复：控制终端被子进程继承，只看 tty 会把"终端里跑的工具拉起的 claude"误判成人工；Claude Desktop 内嵌会话同样无终端，只看 tty 又会把 Desktop 误判成 agent——用户实测抓到）——a) 有终端看 claude 的直接父进程 `ps -o ucomm=`：终端 shell（`SHELL_PARENTS`）=手敲=人工，工具进程=agent。hook 的父进程有两种形态（2026-09-20 修复回归：旧 node 版经 sh 包装派生，父进程即 sh；原生安装器版本 2.1.277 起，二进制在 ~/.local/share/claude/versions/<版本号>、ucomm 为版本号或 claude，直接 exec 派生 hook，父进程即 claude 本体——升级后终端会话全量误判 agent，store 自升级时刻起无一条 user 的 cli 行；修复为识别本体形态后判定进程上移一层，pty 真实拓扑复现验证通过）；b) 无终端先查 Desktop 登记表（claude-code-sessions 的 cliSessionId，限近期活跃文件）命中=Desktop 界面驱动=人工；c) 未命中=无头 CLI=agent。collect_claude 每次刷新对登记表成员权威覆盖 origin=user 自愈（实测纠正 Desktop 误伤、wb-gate 无头行保持 agent）。存量 claude 行按目录约定回填（`CLAUDE_TMP_PREFIXES` 临时目录=agent，`claude-origin:tmp-backfill-v1`，实测 7 行），真实工作区目录的存量行按人工保留；受管理 pi/kimi/opencode/agy 经包装器启动天然人工；Zcode 桌面任务视为人工。手动改判是最终兜底：行内右键可把单条会话改判 agent/user，并可沉淀为目录覆盖规则（`origin-rules`，metadata 存储，收件箱 display 与日报 excluded 读时生效、即时反馈；CLI 等效命令 `inbox origin --id ID --set agent|user [--rule-project DIR]`）。已知边界：agent 用 shell 包装（`zsh -c`）或 pty 拉起无法识别（请改用声明变量）；Desktop 会话若被 UI 自动化驱动视为人工（登记表权威）；`codex_exec` 默认算 agent（白名单可配置）；agent 直接调用受管理包装器视为人工。

产品显示名统一为“Agent Notification”。主页面使用“待查看 / 进行中 / 全部”（进行中分段 2026-09-20 新增，口径见上方专节），操作为“标记已读 / 前往会话”；待查看列表支持勾选批量已读（2026-09-17，经用户反馈由“一键全部”收敛为手动选择）：行首勾选框默认全不选、手动选择任意子集，筛选行下批量栏提供「全选 / 全不选」与「标记已读（N）」；确认把点击时勾选项的整份 (id, revision) 快照交后端逐项 CAS 确认（`ack-batch`），确认瞬间已有新活动的项 revision 失配被跳过并保留未读。“已读”只确认通知已查看，不代表任务完成。内部 revision 校验和运行状态语义不变。应用包路径及 bundle identifier 保持不变，构建后刷新本应用的系统注册；系统设置已打开时需重新进入页面查看新名称或图标。

主窗口默认 400 × 620 点，最小宽度 360 点；旧窗口尺寸首次升级时迁移一次，此后保留用户调整。使用系统字体和语义颜色，标题、待查看计数、CLI 快捷入口、筛选与搜索依次排列。会话标题最多两行，状态、项目与时间放入内容区；右侧使用带辅助功能标签的“标记已读”和“前往会话”图标按钮。未读项保留橙色圆点和淡底色。列表来源图标为 30 点、CLI 入口 24 点：各来源用官方标志（Claude Code 用 Clawd 而非 Claude 桌面图标；Codex、Antigravity 抠掉应用瓦片只留标志；Kimi 按官方 favicon 重绘矢量；OpenCode 用 GitHub 组织头像的官方六边形标志亮度键控为透明底 glyph，2026-09-16），渲染时裁掉自带留白后等比缩进同一方框，按目标点数绘制不做二次放大；pi 官方 svg 的 prefers-color-scheme 暗色白字变体 NSImage 光栅化不执行（2026-09-20 用户反馈暗色下黑方块不可见），采集侧按当前外观做像素级亮度翻转：暗色白字、亮色黑字，图标缓存键带外观；操作按钮有常态淡底和按下缩放/加深反馈，禁用时不显示可交互反馈。

App 图标由 `native/GenerateAppIcon.swift` 绘制：agent 头像为主体，右上角橙色通知圆点，分别生成亮色与暗色资源，Dock 随系统外观切换。未读角标直接画进 Dock 图标（橙底白字，随 3 秒 tick 更新，与托盘同口径；`dockTile.badgeLabel` 在运行时替换 `applicationIconImage` 的应用上不渲染，实测弃用）。Finder 使用默认亮色资源。本轮 66 项 Python 检查通过，Swift 构建与 bundle 签名校验通过；图标两套已视觉检查，真实列表布局已打开检查。导航行为未作为本轮 UI 改动的重新验收项。

## 启动与日常使用

```sh
bin/session-manager app
```

应用为 `build/Agent Notification.app`。窗口显示“待处理 / 进行中 / 全部”，菜单栏图标与 Dock 角标显示待处理数量，可重新打开列表。窗口关闭后，App 未退出时仍持续刷新。自动刷新为双节拍（2026-09-21 实施，职责拆解依据 docs/research/2026-09-20-push-channels-per-source.md）：3 秒 tick 只读 store（`rows --all` 不带 `--refresh`，纯渲染），全量采集按**墙钟**每 15 秒调度（距上次全量发起 ≥15 秒即扫；在途刷新被 guard 丢弃时下一个 tick 自动补，系统唤醒后立即补扫），启动首刷、窗口重开与托盘「刷新」为全量，agent 开关切换与操作后回读走快读；在途请求未结束时到达的显式全量请求挂起（`pendingFullRefresh`），当前请求完成后立即补执行，不静默丢弃。即时性由各来源 hooks/事件承担；纯轮询来源（codex/zcode）的状态可见延迟典型在 15 秒内、最坏约 18 秒（15 秒节拍 + 一个 tick + 全量扫描实测 ~0.1 秒）。收件箱为单实例：重复点通知、托盘或 `open` 只聚焦已有窗口，不会新建。支持系统通知；没有配置登录自启动或全局热键。全部会话按最新活动时间倒序、懒加载：初始 20 条，滚动到底自动追加下一页，无手动翻页；搜索或切换列表重置已加载页。待处理列表保留等待/错误优先级。列表内 agent 名称使用各家品牌主色（Claude 橙红、Codex 蓝、Kimi 深蓝、Pi 灰蓝、Zcode 石墨、Antigravity 谷歌蓝暂定、OpenCode 琥珀暂定）。

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
| Claude Desktop | 本地元数据补标题/工作区/桌面 ID，hooks 更新运行、结束和等待；旧会话没有可靠状态时明确未知。Claude Code CLI 直启的会话（用户级 hooks 带 cwd）以 cli 定位进入收件箱（2026-09-16 起可见，此前隐藏），标题取转写首条用户消息（≤80 字符；2026-09-20 修复回归：转写文件在回合进行中才落盘、晚于首条 hook 事件，落盘前的查找曾被永久负缓存锁死在「claude · <id>」兜底格式——node 时代之后至修复前仅 7 条 cli 行取到真标题；现负缓存按会话状态门控：未结束（idle/running/waiting 及 failed/interrupted 回合态，会话可继续）每轮重试、已结束（closed 或无 hooks 时代回填的 unknown 死行）仍缺才永久记忆，且优先用 hook 带来的 locator 转写路径、路径失效回退按 sid 全目录找并纠正 locator；旧版写入的陈旧负缓存由一次性迁移 `claude-cli-title:cache-revalidate-v1` 清除——只清「转写现已存在」的，确实缺失的保留），Desktop 元数据若匹配到同会话仍会改写为深链 | Desktop 会话走 claude://code/continue 深链；CLI 会话先聚焦已打开的标签（命令行含 `--resume <ID>` 的进程，或经 lsof 定位持有转写文件 fd 的进程——locator 携带转写路径），都没有才在新标签 `claude --resume <会话ID>`；目录缺失拒绝 |
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

Zcode 边界：任务索引的 task_status 只持久化 running/completed/error，`turn_usage` 只在回合结束后落行且无等待类状态；“等待权限批准”只存在于桌面应用内存，不持久化。采集器用索引弥补回合行滞后：task_status 为 running 且更新时间晚于最新已记录回合结束、且该回合结束事件不是本批刚落库时，推断为“运行中”；推断时间戳不超过该回合结束时刻，真实完成事件始终能覆盖推断（2026-09-14 实现）。残余盲区「索引停在 completed 的运行中会话显示上一回合终态」已于 2026-09-20 补第二通道修复：运行库 part 表在回合期间持续落行（流式文本与工具调用状态），两分钟窗口内有过更新的会话直接推 running（事件挂最新消息 ID 防去重账本挡住下个回合；仅当部件更新晚于最近已记录回合完成时才发，防流式墙钟时间反挡真实结束事件；attention=False 与回合开始同语义）。“等待权限”与“运行中”不可区分，统一显示“运行中”；Zcode 的 waiting 映射仍是死分支。这是数据源边界，不是采集器缺陷；证据与查询见 [Zcode 状态语义排查](../research/2026-09-14-zcode-state-semantics.md)，Zcode 未来持久化等待信号后应回补采集。

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

- 102 项 Python 检查通过（历史轮次数字，最新全量以下方验收记录为准），包括重复完成事件不恢复已处理项、旧 UI 不能清掉新回复、迟到事件不覆盖新运行、相同 unread marker 不重复提醒、源配置安装幂等，以及 Zcode 无蓝点完成、旧时钟迁移、独立已处理状态和运行中推断（活跃回合显示运行中、真实完成覆盖推断、滞后索引不能覆盖已记录完成）。
- OpenCode 受管理化（2026-09-16，第五轮迭代：同日经历被动扫描接入→逐回合口径→全状态识别→聚焦已有标签→按用户决定收敛为与 Pi/Kimi 同一受管理实现，被动扫描器与进程匹配聚焦整体移除，旧被动行 44 条一次性清理）。7 项受管理单测（插件事件全生命周期映射、非受管理忽略、iterm_probe 分发、SDK 事件名翻译含 permission ask/assistant error、非受管理插件空 hooks、迁移幂等）+ agent_launch/绑定既有测试全量 98 项通过。真机验证：受管理启动→绑定登记（lease_alive=true）→提示词触发 SessionStart 登记 session_id、Stop 后收件箱 idle+待查看→focus 返回 managed_binding_verified=true/agent_ownership_verified=true 且标签选中→/exit 退出后状态 closed、绑定删除、旧 run_id 重放 focus 返回 refused。
- 原生 App 已实际打开，显示真实多来源会话，“全部 / 待处理”过滤工作正常。
- 注入明确标注“模拟事件”的测试项后，UI 待处理计数变成 1；点击已处理后变成 0，数据库确认未读已清除。该模拟项随后隐藏，未操作任何真实待办。
- 未通过本轮 UI 操作绕过对 Codex/iTerm2 的 Computer Use 工具限制；对应导航沿用此前已验证的入口，最终从应用点击的权限行为以实际使用为准。

实现：[状态库](../../scripts/inbox_store.py)、[来源采集](../../scripts/inbox_sources.py)、[命令入口](../../scripts/inbox.py)、[原生界面](../../native/SessionInbox.swift)、[队列测试](../../tests/test_inbox.py)。

### 验收记录（v0.2 起，增量）

- codex guardian 子会话识别与幽灵深链清除（2026-09-21，经 codex 双轮定性/复审后实施）：图 1「显示却跳转报错」的两条为 **guardian 审查子会话**——rollout 首行 `source.subagent.other=guardian`（旧版 <0.155 为 `thread_source`），originator 恒为 "Codex Desktop" 故白名单拦不住；它们不在 Desktop 任务列表、归档与 session_index，codex:// 深链必死。实测规模 guardian 141 / thread_spawn 208（合计 349，其中 2 条 thread_spawn 已进 session_index 且可正常打开）。修复把**身份与可导航性拆开**：全部 subagent 归 `origin=agent`（默认隐藏、审计可见）；locator 仅对「Desktop 且不在 session_index」清空，已进索引的 thread_spawn 保留可点 url，CLI 变体（codex_exec 等）保留 cli 定位；`source` 字段先验类型（实测 616 个 rollout 中 267 个为字符串如 "vscode"，不设防即 AttributeError 且迁移在采集器异常隔离之外）。存量迁移 `codex:subagent-origin-v1` 以 **payload.id 为身份**（不猜文件名尾——复合文件名会再造分叉 ID 行；顺带修订了旧 `codex-origin:backfill-v1` 同款文件名尾推导，守卫键保证已跑过的库不受影响），只更新已存在的行，真机修改 349 行、已索引 thread_spawn 2 条保留链接、user 行死链清零（34 条不在侧栏索引的普通线程经 thread 注册表核实链接有效）。新增 8 项测试矩阵（vscode 字符串、guardian、thread_spawn 未/已进索引、CLI subagent、旧版标记、迁移幂等与范围、复合文件名身份），全量 185 项通过。后经 codex 三轮复审补一处分流修正：`codex-origin:backfill` 的 sid 推导按来源分流——Desktop 用 payload.id（归父线程），CLI/exec 用文件名尾会话 id（CLI 分叉合同是独立子会话行，统一 payload.id 会为父 id 建幽灵行），文件名尾非合法 UUID 时回落 payload.id；新增「CLI 复合文件名不产生父幽灵行」测试，全量 186 项通过。
- 上一条目勘误（2026-09-21 codex 定性）：该条中「01a0beb2 为 compaction 产物」的表述以 codex 实测定性为准修正——图 1 两行为 guardian 子会话（见上条）；图 2 分叉归属父线程的修复经 codex 真实 trace 重放确认（idle），当时 running 为 codex 处理本报文的真实状态。

- codex Desktop resume 分身会话不可见（2026-09-21 用户报「会话找不到」）：`--resume` 生成的分身文件（文件名 `<父id>_<分身id>`）只写**祖先** session_meta、分身 id 仅在文件名尾，RolloutReader 等待自身 meta 永远等不到，整文件回合被跳过、行停 `unknown`（event_at=0 垫底 + 兜底标题 = 事实上不可见）。修复：祖先阶段的生命周期事件暂存，文件尾自身 meta 仍未出现时采信文件名会话 id 统一归属（「先复制父历史再出自身 meta」的既有拓扑语义不变，由既有测试守卫）；一次性迁移 `codex:fork-replay-v1` 重放受害行游标（真机 2 条，其中 1 条实为空会话文件、unknown 忠实）。真机验证：当晚活跃分身 `01a0bfba`（Review code changes 的延续）恢复 idle、事件时钟 01:09:43。同批澄清：`01a0beb2` 为 compaction 产物（仅压缩记录无回合，真实对话在 01a07229 行、一直可见）；图 2 的 `01a0bfb7` interrupted 为 resume 合成中断的既定语义。新增分身拓扑回归测试，全量 177 项 Python 检查通过。已知留痕：分身行标题为兜底格式（标题索引不含分身 id；后续可选按祖先会话继承标题）。

- codex 二次评审修复（2026-09-21，三项核实后落地）：①P2 真标题行跳过 locator 回填——上一轮去重重构把标题 continue 前置，已有真标题的 cli 行永远不做 locator 回填/纠正，而导航的 lsof fd 匹配依赖转写路径；拆分 `_claude_cli_transcript`（解析 + 负缓存唯一所有者）与 `_claude_cli_title`（纯读首条用户消息），循环恢复「先解析回填 locator、再判标题」的旧顺序，附带消除双 Optional 元组形状。②P2 显式全量请求被在途请求静默丢弃——旧 3 秒全量时代被掩盖，双节拍下用户刷新要再等最长 15 秒；`refresh` 在途时显式 full 置 `pendingFullRefresh`，完成后立即补执行（定时全量被在途快读撞掉同路径）。③P3 Timer @Sendable 闭包读 `lastFullScanAt` 违反 MainActor 隔离（构建已告警）；elapsed/full 计算移入 MainActor Task，重建后无并发警告。新增 2 项回归测试（真标题行 locator 回填/过期纠正），全量 176 项 Python 检查通过，policy 断言通过；真机重启后 health 稳态跳变间隔 15–17 秒（墙钟 + tick 量化，符合「典型 15 秒内、最坏约 18 秒」口径），pending 补拍在启动过渡期实际观察到生效。pi 图标暗色的视觉验收属并行工作线，本轮仅编译检查。

- codex 交叉评审修复（2026-09-21，三项全数核实后落地）：①P2 旧负缓存升级路径锁死——旧版写入的 `claude-cli-title-missing:*` 会被新逻辑无条件短路（复现实测：预置标记 + 转写存在，标题仍为兜底格式），此前只有开发机手工清理；新增一次性迁移 `claude-cli-title:cache-revalidate-v1` 清除「转写现已存在」的陈旧标记（确实缺失的保留），真机验证守护键已落、本机幂等清除 0。②P2 忙时丢整轮慢扫描——全量调度从 tick 计数改为墙钟（距上次全量发起 ≥15 秒即扫，`inboxTickShouldScan(elapsed:fullEvery:)`），丢拍下一 tick 自动补、系统唤醒后立即补扫；实测全量扫描 ~0.1 秒（碰撞性余量 25 倍），spec 延迟口径同步改为「典型 15 秒内、最坏约 18 秒」。③P3 migrations.py 头注释的 3 秒轮询表述过期，已同步双节拍。同批并入内部复核遗留：unknown（回填死行）纳入可缓存集合（failed/interrupted 为回合态保持重试，实测 store 该两态零行），policy 断言随墙钟签名更新。全量 174 项 Python 检查通过，policy 断言经 `xcrun swiftc -parse-as-library` 编译运行通过，App 重建后 TCC 重置按既有流程。

- 自动刷新双节拍（2026-09-21 实施，架构讨论于 2026-09-20）：3 秒 tick 从「每轮带 --refresh 全量采集」改为只读 store（`rows --all`），全量采集降为每 15 秒一次（`inboxTickShouldScan(tickIndex:fullEvery:)` 纯函数门控，policy 断言 7 条经 `xcrun swiftc -parse-as-library` 编译运行通过）；启动首刷/窗口重开/托盘「刷新」保持全量，agent 开关切换与操作后回读走快读。职责依据：即时性由五家 hooks/事件承担，全量扫描只剩历史回填、agent 硬杀兜底（codex 悬置 24h 判 interrupted）、健康状态三项，均不需要 3 秒频率（调研文档「轮询的职责拆解」逐条盘问后确认）。代价（知情接受）：纯轮询来源 codex/zcode 的状态可见延迟上限从 3 秒升到 15 秒（进行中分段入口感知同此）。Python 零改动，全量 171 项通过。真机验证：health `checked_at` 在 20 秒采样窗口内恰好推进一次、间隔 ~15 秒（改前同窗口约 6-7 次）；进程采样捕获到无 `--refresh` 的 `rows --all` 快读调用在跑。App 重建后已按既有流程重置辅助功能授权（下次 Zcode 跳转经悬浮徽章重授）。

- claude 双回归修复（2026-09-20 夜至 09-21，经双轴评审后收敛）：①origin 误判——claude 升级 2.1.277 原生二进制后 hook 直接 exec 派生（父进程即本体，ucomm 为版本号），旧判定把终端手敲 CLI 会话全判 agent 默认隐藏（store 自升级时刻起无一条 user 的 cli 行即回归窗口证据）；修复为识别本体形态后判定进程上移一层，pty 真实拓扑（zsh fork → 原生 claude）端到端复现判定 user，复现陷阱已记录（`zsh -c` 单命令 exec 优化不忠实于 iTerm）。存量行 origin 落库于注册时不自愈：用户确认的两条已翻正，9/19 凌晨真实目录批次待用户确认。②标题锁死兜底格式——转写文件回合中才落盘（实测进程存活 6.5s 才出现），早于落盘的取标题被永久负缓存（node 时代后真标题行仅 7 条且全在 9/16-17）；修复为负缓存按会话状态门控（未结束每轮重试、已结束仍缺才永久记忆）+ locator 转写路径优先，数据修复清除 26 条陈旧负缓存后真标题 7→19。09-21 双轴 code-review 后再收敛一轮：负缓存规则与 locator 回填收敛进 `_claude_cli_title` 单点（规则双处编码有漂移复发风险）、locator 路径失效时回退按 sid 全目录找并把 locator 纠正为真实路径（否则路径过期会重新引入永久锁死）；评审中「resume 复活不失效负缓存」议题经实测证伪（`--resume` 派生新 session id，不复用旧 sid），未引入对应代码。新增 9 项测试（origin 6 + 标题 3），全量 171 项通过。

- 「进行中」首轮反馈修复（2026-09-20 同日，用户反馈三项：切页签跳变、搜索图标与页签重叠、显示不全；另 codex 状态不对）：①重叠与显示不全同根因——三段标签加宽后居中分段控件钻到行尾悬浮的搜索图标下面，右侧 Spacer 常留 44pt、第三段标签缩短为「全部（N）」，最小宽度 360pt 下三段完整无重叠（OCR 后置核对，此前放大镜伪影与末段文字粘连）；②跳变两轮收敛（用户二轮反馈"切换页签后画面还是有跳变"）：首版把批量已读栏移入滚动区，稳态几何测量证明位移只是换了位置——栏仅在待查看出现，两分段的列表首行相差约 27pt；终版改为**常驻固定高度槽位**（32pt 空间永远保留，栏内容只在待查看渲染，出现/消失零推挤），配合分段控件全宽分布（选中段加粗与计数增减只改段内边界，控件整体不再平移）与 ScrollView 挂 `.id(scope)` 切换重建回顶（不携带旧滚动偏移）。OCR 稳态核对：三个分段的列表首行 y 一致（相差 ≤2px 噪声级），标题/页签行坐标完全相同。第三轮（用户反馈"内容会总体左右移动"）：水平位移源是行首勾选框列——勾选框原只在待查看渲染，两分段的行内容起点相差 27pt（OCR 实测标题 x 0.2420 vs 0.1662）；改为常驻 18pt 固定宽度槽位（勾选框只在待查看渲染），并确认系统滚动条为 Automatic 悬浮式（不占布局，排除次嫌疑）。修复后 OCR 复核：待查看/全部首行标题 x 均为 0.2420，完全一致。第四轮（用户反馈"常驻槽位导致其他列表内容整体下移，左右移动仍未解决"，并确认移动的是头部区域元素）：**放弃全部常驻槽位**——32pt 槽把所有列表永久压低、18pt 槽浪费行宽，方向本身错误；同时撤掉分段切换时 ScrollView 的 `.id(scope)` 整窗重建（重建触发的重排版会波及头部预算式布局，是头部区域闪动的来源）。终版布局（用户三选确认）：①批量已读栏嵌入**底部固定高度行（22pt）**，与「打开会话后自动标记已读」提示文案按待查看/其他分段互换内容，同高零位移、零新增空间，列表顶边与宽度回到最初形态；②批量选择改为 **Mail 式悬停替换**：选择圈悬停浮现、选中常显，盖在 30pt agent 图标列上（图标列三分段恒宽），行首不再有任何条件列，悬停态存模型（`hoveringRow`，视图层不能用 @State）；③分段切换不再重建滚动视图。验证（OCR 稳态坐标）：待查看/全部两分段的标题、新建会话行、列表首行坐标逐位一致（首行 x=0.1630、y=0.6627）；同一画面隔 3 秒（一个刷新周期）双帧逐位一致（无随刷新振荡元素）；底部行在待查看显示「全选/标记已读(N)」、其余分段显示提示文案。已知一次性现象：启动后通知授权状态落定会重建工具栏铃铛项，内容整体出现过一次约 7pt 的下移后稳定（非切换触发、不反复），如后续困扰再单独处理。行分隔线缩进随勾选框列移除调整为 44pt。搜索栏终位（2026-09-20 用户三选确认）：搜索图标从页签行行尾悬浮改为**与页签组合整体居中**（图标紧随页签右侧，页签自适应宽度）——悬浮图标占用的行尾 44pt 预留会把页签挤偏不居中；知情接受的代价：括号计数位增减时组合有轻微左右摆动。居中验证：组合左右边距 17.6pt/≈19.3pt 对称。同日终位再改（用户实测组居中版后否决）：**搜索按钮移入工具栏、替换原「刷新」按钮**（手动刷新保留在托盘菜单，自动 3 秒轮询不变；按钮随展开态切换图标（magnifyingglass↔xmark，动作=收起；circle 变体会把放大镜本体缩小在圆内，弃用）并按 id 强制重建，与通知铃铛同款），页签行内不再放任何元素。顺带查明一个此前误判：macOS 26 分段控件不会拉伸到提案全宽、只按内容取固有宽度——v2 的「全宽分布」实际从未生效，当时的偏斜是尾部 44pt 图标预留把固有宽度控件推左所致；行内清空后页签以固有宽度自动居中（左右边距 35.4/37.5pt 对称），搜索展开/收起页签坐标恒定（0.1427→0.1429）。同日图标统一（用户要求）：控制类图标全部归描边族、同光学重量——通知铃铛简化为开/关两态（`bell`/`bell.slash`，授权状态改看列表下方状态行与 help，弃用实心/徽标变体混重量）；行内「前往会话」由实心应用瓦片 `arrow.up.forward.app` 改描边 `arrow.up.right.square`，与「标记已读」的 `checkmark.circle` 同重量。勾选框类（`checkmark.circle.fill`/`circle`）为标准选中语义、警示类（`exclamationmark.triangle.fill`）为惯用红色告警，均保留。③codex 状态：「进行中」暴露出 rollout 里最后一条回合事件是无终态 task_started（进程被硬杀）的行永久滞留 running（真机 6 条，最早 2026-07-24；另有一例 7 月悬置、9-20 重开只写了 thread_settings_applied 标记而旧采集器不识别）。修复分三层：RolloutReader 见重开标记或新回合即对悬置回合合成 turn_aborted（open_turn 随游标持久化）；collect_codex 兜底 state==running 且 rollout mtime 超 24h 无新行判 interrupted（活回合持续流式写 token_count 行，mtime 必然新鲜；不是凭静默推断完成、不声明成功）；一次性迁移按文件内容修复存量（标记路径以重开时刻为事件时钟，不得取 max(mtime+1)——文件刚写过会把事件时钟推到未来压制真实事件，该项由测试拓扑抓出后修正）。真机验证：6 条全部转 interrupted，running 仅剩真实在跑的会话；新增 6 项测试（三种拓扑 + 连续开始合成 + 迁移幂等/不误伤活回合），全量 158 项通过。OpenCode/裸 Claude CLI 进程被强杀时 running 滞留仍为已知数据源边界（前者 spec 已记，后续如需同样收口另议）。
- 「进行中」漏报修复（2026-09-20 用户反馈 zcode 会话重进运行态但列表不显示）：根因是 zcode 任务索引整轮停在 completed（实测当前回合 40+ 分钟未翻转、updated_at 冻结在上次回合结束），live-running 推断依赖 task_status==running 永远等不到；turn_usage 又只在回合结束落行。修复为上述 part 表流式活性通道（新增 4 项测试：活跃翻转/过期不动/落行结束不被墙钟卡死/子会话部件不冒充主任务），全量 162 项通过。真机端到端：刷新后 payload 立即出现 2 条运行中 zcode（当前 CLI 会话与另一桌面会话各一）。
- 「进行中」分段（2026-09-20）：口径为用户确认的「等待模型回复的状态即为正在运行」——只收 `state==running`，`waiting`（等用户确认权限）与空闲不算，纯回合状态、不引入进程存活探测，后端零改动（`rows --all` 载荷已含 state）。Swift 侧 `showAll: Bool` 升级为 `InboxScope` 三态枚举，分段计数、筛选、勾选框/批量栏/懒加载条件全部迁移；新增 `inboxActiveListed` 纯函数与 7 条断言（policy 测试经 `xcrun swiftc -parse-as-library` 编译运行通过），Python 全量 152 项通过。真机验证（AX 坐标点击中段 + 截图 OCR 后置核对）：分段显示「待查看（27）/ 进行中（3）/ 全部会话（783）」，计数 3 与 CLI 查询的 running 行一致；列表精确显示 3 条运行中会话（1 条 zcode live-running 推断 + 2 条 codex），全部带「运行中」态，勾选框与批量栏在该分段正确消失。验收截图：docs/images/inbox-active.png（README 表格已引用）。
- 勾选批量「标记已读」（2026-09-17，两轮：首版为工具栏“一键全部”，用户反馈“要能手动选择会话、而不是默认所有会话”后收敛为勾选式）：待查看行首勾选框默认全不选，筛选行下批量栏「全选/全不选」与「标记已读（N）」，把点击时勾选项的整份 (id, revision) 快照经 `ack-batch` 逐项 CAS 确认。6 项后端单测（批量成功、陈旧 revision 跳过并保留未读、缺失行忽略、CLI 计数返回与非数组拒绝），全量 130 项通过。真机验证采用隔离 HOME 沙箱（`DEFAULT_ROOT` 与来源扫描随 `Path.home()` 切到临时目录，真实待办零触碰；清空真实库再 refresh 是死路——来源扫描会重灌全部会话）：4 条模拟事项勾选 2 条确认 → 待查看 4→2、仅被勾选项 unread=0 且 acknowledged_at 落库、未勾项原样保留、勾选自动清空；「全选」路径 2→0 同样验证。陈旧 revision 路径另经 CLI 端到端验证（skipped=1、未读保留）。验收截图：scratch/batch-select-before.png、batch-select-after.png。
- agy 接入验收补充（2026-09-16 用户验收反馈两项修复）：①agy 卡片标题显示为会话 ID——summaries 行 title 常为空、真实标题在 preview，采集器改为 title→preview 兜底；②CLI 窗口关闭后「前往会话」报 binding expired——为 agy/opencode 增加绑定死亡恢复链（活绑定改查→受管理恢复新标签，navigation=managed_resume），四家受管理 CLI 统一支持（pi/kimi `--session` 亦实测 help 确认）；display_rows 对受管理 provider 常亮按钮。真机验证：agy 死绑定会话 open 返回 managed_resume、新标签以 `--conversation` 恢复且新绑定注册。③同日再补：受管理 agy/opencode 行的项目目录被删时条目隐藏（refresh 每次清扫、目录恢复自动取消隐藏）——否则残留的可点行会让人点到必失败的"session directory is missing"；目录缺失的恢复拒绝仍保留为兜底。
- agy 受管理接入（2026-09-16）：9 项专属单测（事件全生命周期、TUI 切会话改绑、未管理忽略、包装器 SessionEnd、过期锁拒绝、agy-hook 载荷转译、缺 conversationId 静默、标题只补已有行、插件安装幂等）+ 既有绑定测试，全量 107 项通过。真机验证：setup-agy 安装插件→受管理启动注册绑定→TUI 回合触发 PreInvocation/Stop（事件时钟推进、待查看抬升）→focus 三重校验通过且标签选中→进程退出后状态已退出、绑定删除、旧 run_id focus 返回 refused。已知边界：agy hooks 官方仅五事件（无失败/等待/中断）；回合事件可能在回合结束才落地（运行中显示 best-effort）；TUI 退出命令未探明，退出语义经包装器兜底路径验证（模拟进程退出）。agy hooks 在进程启动时缓存，setup-agy 后需重启 agy 会话。
- 新增测试覆盖打开成功自动确认、失败保留、新回复竞态、活动时间排序和 Pi 标题身份核验；Swift 纯策略测试覆盖分页边界和通知去重/启动静默。
- GUI 已验证全部会话从第 1 页切换到第 2 页；应用图标已渲染检查。
- 系统设置与 App 均显示通知开启，模拟事件的通知请求被系统接受（App 的通知去重记录含 token）。点击通知的行为改为仅置前收件箱；原先的自动打开实现（pendingNotificationOpen）已移除。实际点击验证：收件箱打开，无 Zcode 导航（原生助手日志无新记录），模拟项未读保留；该模拟项验收后已清理（2026-09-14）。
- Zcode 辅助功能授权重新添加后已生效（2026-09-14 验收）：模拟事项经 App「打开会话」首次尝试因搜索框焦点竞态被助手拒绝且保留未读、错误行内显示；重试后原生助手完成搜索、粘贴、候选选中与复制任务路径身份核验（selection_identity_matches=true）并置前目标任务，App 按点击时 revision 自动确认（unread 1→0），界面与数据库一致回到"暂时没有待处理事项"。同日简化 Zcode 打开语义：助手只负责打开任务搜索、切到任务范围并预填标题，停在结果页由使用者自行选择目标；点击候选与复制任务路径核验的自动跳转已移除——精确跳转在多候选竞争与虚拟列表渲染竞态下成功率不稳定，搜索停靠一次即可用。
