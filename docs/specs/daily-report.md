# 工作日报 v3（token 三类口径，报告 schema v8）

状态：**报告 schema 已升到 v8**（2026-09-23，逐 model 用量，见[用量金额规范](usage-cost.md)）：六来源产出 model_raw 与四项原始 token，`tasks[].models` 与 `totals.models` 入报告；金额不写入报告，由 `inbox usage` / 日报界面在读取时按价格表派生。v7 → v8 迁移自动执行（先备份 `reports.v7.bak/`，来源被清理时用 v7 内容做 unknown 拆分，不降级）；真实数据三日比对逐位一致（Zcode 在 1.71% 容差内），证据 scratch/2026-09-23-v8-parity.md。此前 v3（schema v7）通过 102 项 Python 检查（含 15 项日报）与 Swift 策略检查；真实本机数据 182 天补录、总览/详情截图核对完成。OpenCode 来源于 2026-09-16 接入（schema v5→v6）；2026-09-17 agent 会话过滤 + OpenCode 受管理口径（v6→v7，旧固化报告自动重算，现役 schema v7）。**准确性核对**：独立重算脚本（scratch/check_token_accuracy.py）与报告逐项对比，Codex/Claude/Pi/Kimi 三类逐位一致，Zcode 在跨零点分摊容差内（<2.2%，归日语义差非算术差；证据 scratch/2026-09-15-token-accuracy-check.md）；OpenCode 近 3 天独立重算与报告逐位一致（141,744）。

## 度量口径（v3 核心）

**token 三类，三类之和 = 合计，参与一切比较与计算（热力分级/排名/占比/趋势），三类在所有展示处一并呈现：**

- **输入** = 新鲜输入 + 缓存写入
- **缓存** = 缓存读取（上下文回放，单独列示）
- **输出** = 输出 + reasoning

取消/出错轮次照计（消耗即计数）。悬浮为**自定义多行卡片**（onHover + 覆盖层实现，鼠标进入即显，不经系统 tooltip 延迟通道；2026-09-15 按用户模板定稿）。样式：第一行日期/名称，随后 `缓存：`/`输入：`/`输出：` 三行（该顺序为用户指定），按需附 `合计：`、任务数、轮次与时间；所有图表（热力格/趋势柱/占比条分段/图例行/项目行/任务卡/节奏带色块）均有悬浮。

| 来源 | 有效性 | 输入 | 缓存 | 输出 | 归日方式 |
|---|---|---|---|---|---|
| Zcode | 逐轮精确 | `input − cache_read + cache_creation` | `cache_read` | `output + reasoning` | 轮区间按天比例分摊 |
| Codex | 请求级增量（rollout 识别与收件箱同口径：`session_meta` + 任意非空 originator，2026-09-17 起含 CLI/exec/workbench 变体——此前 Desktop-only 漏计当日全部 CLI 会话） | `last_token_usage.input + cache_write` | `cached_input_tokens` | `output + reasoning` | 每条增量按时间戳归日 |
| Claude | 逐条消息（2026-09-14 探针验证：5/5 桌面会话经 cliSessionId 命中 `~/.claude/projects/<hash>/<sessionId>.jsonl`，证据 scratch/2026-09-14-claude-transcript-token-probe.md）。桌面登记表按 cliSessionId 配转写；未被登记的 CLI 直启转写按文件 mtime 预筛后直接解析（2026-09-17 起补采，与收件箱 claude CLI 接入同口径；标题/项目由收件箱已知行补齐） | `input_tokens + cache_creation_input_tokens` | `cache_read_input_tokens` | `output_tokens` | 消息时间戳 |
| Pi | 逐条 assistant 消息 | `usage.input + cacheWrite` | `usage.cacheRead` | `output + reasoning` | 消息时间戳 |
| Kimi | 逐轮（`~/.kimi-code/sessions/**/agents/*/wire.jsonl` 的 `usage.record`） | `inputOther + inputCacheCreation` | `inputCacheRead` | `output` | `time` 毫秒 |
| OpenCode | 逐条 assistant 消息（本地 SQLite `~/.local/share/opencode/opencode.db`），**受管理口径**（2026-09-17 起：只统计经 `bin/session-manager opencode` 注册到收件箱的会话，与 pi/kimi 同构；此前为全库直读，workbench 等其它工具经 server 拉起的会话被误计入——库内实测无创建者身份字段，`agent` 列恒为 build、`workspace_id`/`metadata` 空、`session_input.delivery` 整表未启用） | `input + cache.write` | `cache.read` | `output + reasoning` | 回合完成时刻（`time.completed`，缺失退回消息更新时间） |

- 任务记录 `fidelity ∈ {exact, unavailable}`：`unavailable`（如 Zcode 老库无 token 列、桌面会话无转写）只列出、标〔无 token〕，不参与合计。
- Zcode `sess_subagent_*` 子代理轮次的 token 经 `session.parent_id` **归属到父任务**（独立消耗不遗漏也不与父轮次重复），不计入父任务轮次数；无法归属的孤立子代理轮次宁可不计。OpenCode 子代理会话（`parent_id` 非空）的消息无法归属到父任务，按同一"宁可不计"规则跳过。
- 任务记录字段：`provider/session_id/title/project/first_at/last_at/input_tokens/cache_tokens/output_tokens/total_tokens/turns/state/fidelity/segments`。无时长字段；一天节奏带只表达时间分布。
- **`segments` = 真实活动段**（schema v5）：Zcode 取 `model_usage` 逐请求 `started_at→completed_at`（轮内等待用户批准/回答的空档不算活动；无该表退化为整轮区间），Codex/Claude/Pi/Kimi 取逐条时间戳；同任务相邻段间隔 ≤15 分钟合并，按天窗口截断。首末时间 `first_at/last_at` 仍是全天跨度，只用于文字展示。v4 前节奏带按首末时间画一根实心条，几个跨半天的会话叠起来就把 24 小时填满，与实际不符。

## 热力分级（按本机 91 个活跃日毛总量分布校准）

0 无记录 / L1 <20M / L2 <100M / L3 <400M / L4 ≥400M（分布 47/20/12/12）。accent 蓝由浅到深，今日格描边，悬浮显示「日期 · 合计 X（输入 A · 缓存 B · 输出 C）· N 个任务」。

## 触发与固化

- 无定时任务，报告按需生成：界面打开总览（窗口 onAppear）或点进某日详情时触发计算。
- 过去日固化为 `~/.local/state/session-manager/reports/YYYY-MM-DD.{json,md}`（`version: 7`，0700 目录，原子写入）；版本不匹配或缺失自动重算（一次性补录 182 天约 4 秒；新增来源等 schema 变更 bump 版本即全量重算，2026-09-16 v5→v6 已验证）。
- **过去日详情读定稿缓存**：版本匹配且生成时刻晚于该日结束即直接返回（约 30ms），缺失/未定稿才重扫来源并落盘；来源事后补录的历史数据需 `--refresh` 强制重算。
- **过去日重扫与磁盘现有报告按任务合并不降级**（`--refresh` 与补录走同一关口，细则见[用量金额规范](usage-cost.md) §3.1）：任务三类合计小于现有报告时，三类按类别取 max(现有, 重扫)，正差额（现有 − 重扫）记 unknown——三类合计与 model 明细保持一致，且不低于现有报告（2026-09-25 H0925-4：此前合计沿用现有报告，重扫总量更小但某一类因类别迁移更大时，合计与 model 明细不一致）。代价：同一批 token 在两次扫描间换了类别时合计会偏高（用户已知悉）。
- **今天始终实时计算、不固化**：现算并展示，详情汇总卡标「实时汇总 · 截至 HH:MM」；次日按未定稿规则补算一次后定稿。

## 界面

- **总览**（日报窗口 600×780，可缩至 560）：今日概览卡（token 大字 + 金额伴随行 + 三类分段条/任务/轮次/活跃来源/较昨日）→ 27 周热力图 → 最近 7 天三类层叠柱状图（输出/缓存/输入自上而下，柱顶标合计）→ 双栏（来源占比环形 · 本周 | 模型榜 · 本周，数据来自 `usage` 周/近 7 天窗口）→ Top 项目 · 本周全宽卡 → 口径注脚。2026-09-24 起与周/月共用同一设计语言（见[用量金额规范](usage-cost.md) §7）：金额为 USD 伴随指标、官方来源图标、无币种/度量切换器。
- **当日详情**（点热力格进入）：汇总卡（来源占比环形 + 三类行 + 今日实时标注）→ 一天节奏带（按 `segments` 着色，空闲留白，悬浮显示该段起止与全天跨度）→ 来源占比（分段条 + 逐来源三类行）→ 项目条（含三类行）→ 任务卡（合计主值 + 比例条 + 起止/轮次/状态 + 三类行；unavailable 标〔无 token〕）。各区块统一用同一卡片样式（14pt 内边距），比例条/节奏带左右边界对齐。原汇总卡内的「最近 7 天」迷你柱已移除（无悬浮、信息与总览趋势图重复）。
- **全部图表与数字均有悬浮**（.help）：日期格/趋势柱/占比条分段/图例行/项目行/任务卡。
- 数字格式 `tokenText`（k/M/B）：<1k 原值；k/M 段 mantissa<100 保留小数否则取整；B 段两位小数；末尾零去除。

## CLI

```sh
bin/session-manager inbox daily-report [--date YYYY-MM-DD] [--refresh]  # 单日报告（过去日读定稿缓存，--refresh 重扫；今天实时、不落盘）；响应中 task / totals / sources / projects / models 各附 cost
bin/session-manager inbox daily-report --overview [--days 182] [--top 5]  # 热力图 + 近 7 天 Top 项目（自动补录）；days[] 与 today 附 cost，附 week_models（近 7 天 model 合计与金额）
bin/session-manager inbox usage --period day|week|month|all [--date D] [--json] [--currency USD|CNY]  # 跨周期用量与金额（金额按标价估算；ISO 周/自然月；过去日读定稿缓存，今天实时）
bin/session-manager inbox pricing show [MODEL] | check [--days 30] | update [--auto] | fx RATE  # 价格表查询 / 缺口检查 / 拉取（7→14→28→30 天自适应，--auto 只在到期时请求）/ 手动汇率
```

金额口径见[用量金额规范](usage-cost.md)：等价金额 ≠ 账单；界面只展示 USD（伴随指标，不做切换），估算免责只在各页注脚保留一句。

## 隐私边界

读取范围限于管理所需的元数据（标题、项目、状态）与数值字段（token 数、时间戳）。含正文的完整 JSON 行会在内存中反序列化以定位 usage 与时间字段，但正文不被提取、不持久化、不输出；唯一例外是标题摘要——claude CLI 转写的首条合格用户消息截取 ≤80 字符作为会话标题保存（2026-09-21 评审校准：原文「正文不解析」与实现不符）。

## 验证证据

- **Codex/Claude CLI 口径修复 + OpenCode 受管理口径（2026-09-17，用户反馈“收件箱有 codex/claude/zcode 而日报只有 zcode/opencode”）**：①根因是日报采集器未跟上收件箱 2026-09-16 的 CLI 接入——Codex 只认 `Codex Desktop`（当日 43 个 rollout 仅 1 个 Desktop，42 个 workbench/CLI 变体全部漏计，其中 33 个含 token_count）；Claude 只读桌面登记表配到的转写（当日 17 份转写 10 份纯 CLI 全部漏计）。修复后当日报告 codex 42 任务/3.17M、claude 8 任务/1.03M，与收件箱当日活跃数一致。②OpenCode 反向切换为受管理口径（用户拍板）：修复前日报的 10 个 opencode 任务全部来自 workbench 经 server 拉起的会话（收件箱不列、用户不关心）；调研证伪库内创建者信号（`agent` 恒 build、`workspace_id`/`metadata` 空、`session_input` 整表空、事件载荷无创建上下文）后，按用户选择改为只统计包装器注册的会话——可行性实测：收件箱在册 ID 可稳定取得、用量字段与解析器匹配、绑定删除后数据持久、干跑当日 10 任务→0。口径注记：收件箱侧被动扫描已按 2026-09-16 决定移除，不经包装器的 opencode 用量（含用户裸 TUI）不再计入日报；想要计入请经受管理入口启动。③过去日定稿报告不自动回溯（仍为切换前口径），需要时对单日 `--refresh` 强制重算；历史口径如需统一，与“agent 会话过滤”一起做 schema 版本升级全量重算。③同日第三轮：agent 拉起的会话（store `origin=agent`，判定见 unified-inbox.md）整体退出日报合计，消耗改记 `agent_excluded` 注脚（JSON + markdown），schema v6→v7 全量重算使历史口径统一——过去日定稿按新口径重新生成（codex/claude 计入手动 CLI、剔除 agent 会话、opencode 只含受管理）。切换当日实测：日报任务 codex 42→仅剩人工来源、opencode 10→0。新增 4 项单测（CLI originator rollout 计数、CLI-only 转写计一次且过期桌面登记不双计、未登记 opencode 会话不计入、agent 会话排除+注脚），日报 23 项、全量 138 项通过。
- 19 项日报单测：真实活动段（Zcode 逐请求 / 逐条时间戳聚类 / 跨零点截断）、今天不固化、各来源三类算术（缓存读剔除、取消轮计入、子代理归属父任务、缺列回退 unavailable）、Codex 增量按时间归日、Pi/Kimi/Claude/OpenCode 解析、跨零点分摊、旧版本报告自动失效重刷、热力阈值、Top5 近 7 天窗口、markdown。全量 102 项通过；Swift 策略检查含 `heatLevel(tokens:)` 与 `tokenText` 边界。
- 真实数据（2026-09-14）：当日三类合计 556M（输入 150M · 缓存 405M · 输出 1.4M）/ 27 任务；近 7 天来源占比 Codex 937M(41%)、Zcode 719M(32%)、Claude 608M(27%)——与各来源原始数据抽查一致（Codex 大盘来自单会话 416 次请求、每次重发约 70 万上下文，属口径内真实消耗）。
- **准确性核对（2026-09-15）**：独立重算脚本与报告逐项对比，Codex/Claude/Pi/Kimi 三类逐位一致；Zcode 在跨零点分摊容差内 <2.2%（归日语义差）。证据 scratch/2026-09-15-token-accuracy-check.md。
- 总览/详情截图核对通过（scratch/daily-report-v3-overview.png、daily-report-v3-day.png）。

## 验收（编号）

2026-09-25 由本文既有条款整理，不新增需求；证据类型与覆盖列由 `harness/acceptance.py` 检查（见 [delivery-harness.md](delivery-harness.md)）。

| 编号 | 验收内容 | 证据类型 | 覆盖 |
|---|---|---|---|
| DR1 | 三类口径：输入 = 新鲜输入 + 缓存写入，缓存 = 缓存读取，输出 = 输出 + reasoning，三类之和 = 合计；取消/出错轮次照计 | 夹具 | `test_daily_report.DailyReportTests.test_zcode_three_classes_and_cancelled_turns_counted` |
| DR2 | 各来源解析与归日：Zcode 轮区间跨零点按比例分摊，Codex 增量按时间戳，Claude/Pi/Kimi/OpenCode 按消息时间 | 夹具 | `test_daily_report.DailyReportTests.test_turn_tokens_split_proportionally_across_midnight`、`test_daily_report.DailyReportTests.test_codex_tokens_bucketed_by_request_time`、`test_daily_report.DailyReportTests.test_pi_assistant_usage_parsed`、`test_daily_report.DailyReportTests.test_kimi_wire_records_counted`、`test_daily_report.DailyReportTests.test_claude_transcript_tokens_and_missing_fallback` |
| DR3 | Codex 任意非空 originator 的 rollout 与未登记的 Claude CLI 直启转写计入（与收件箱同口径），同一会话不重复计 | 夹具 | `test_daily_report.DailyReportTests.test_codex_cli_originator_rollouts_counted`、`test_daily_report.DailyReportTests.test_claude_cli_only_transcript_counted_once`、`test_daily_report.DailyReportTests.test_claude_transcript_counted_without_desktop_install` |
| DR4 | `fidelity = unavailable` 的任务只列出、不参与合计 | 夹具 | `test_daily_report.DailyReportTests.test_zcode_without_token_columns_degrades_to_unavailable`、`test_daily_report.DailyReportTests.test_unavailable_task_keeps_models_empty` |
| DR5 | Zcode 子代理轮次 token 归属父任务、不计入父任务轮次；无法归属的子代理（含 OpenCode 子代理会话）不计 | 夹具 | `test_daily_report.DailyReportTests.test_subagent_tokens_attribute_to_parent_task_without_counting_turns`、`test_daily_report.DailyReportTests.test_opencode_subagent_tokens_not_counted` |
| DR6 | `segments` 为真实活动段（Zcode 逐请求、其余逐条时间戳，按天窗口截断），不按首末时间画整段 | 夹具 | `test_daily_report.DailyReportTests.test_task_segments_follow_real_activity_not_first_to_last_span`、`test_daily_report.DailyReportTests.test_zcode_segments_use_model_requests_not_whole_turn`、`test_daily_report.DailyReportTests.test_segments_clamped_to_day_window_across_midnight` |
| DR7 | agent 会话退出合计、计入 `agent_excluded` 注脚；手动覆盖与 origin 规则生效；`inbox usage` 补录路径同样带注脚 | 夹具 | `test_daily_report.DailyReportTests.test_agent_sessions_excluded_and_counted_in_footnote`、`test_daily_report.DailyReportTests.test_manual_override_excludes_from_daily_report`、`test_daily_report.DailyReportTests.test_origin_rule_overrides_report_scope`、`test_usage_report.BackfillAgentStatsTest` |
| DR8 | OpenCode 受管理口径：只统计经包装器登记的会话 | 夹具 | `test_daily_report.DailyReportTests.test_opencode_assistant_usage_counted` |
| DR9 | 过去日固化为 json + md；今天始终实时、不落盘 | 夹具 | `test_daily_report.DailyReportTests.test_generate_day_writes_v8_and_markdown`、`test_daily_report.DailyReportTests.test_generate_day_keeps_today_live` |
| DR10 | 过去日读定稿缓存；生成时刻早于该日结束或版本不符时重算 | 夹具 | `test_daily_report.DailyReportTests.test_overview_refreshes_past_day_snapshot_taken_before_day_end`、`test_daily_report.DailyReportTests.test_overview_backfill_cache_invalidates_v1_and_ranks_week_projects` |
| DR11 | 过去日重写按任务合并、不降级（`--refresh` 与补录走同一关口） | 夹具 + 性质 | `test_daily_report.DailyReportTests.test_refresh_does_not_degrade_restored_day`、`test_daily_report.DailyReportTests.test_backup_fallback_when_current_report_missing`、`test_properties.MergeNoDowngradeProperties` |
| DR12 | 热力分级：0 无记录 / L1 <20M / L2 <100M / L3 <400M / L4 ≥400M | 单测 | `test_daily_report.DailyReportTests.test_heat_level_total_token_thresholds` |
| DR13 | 数字格式 `tokenText`（k/M/B 规则）双端一致 | 单测 | `test_daily_report.DailyReportTests.test_token_text_units`、`tests/InboxPolicyTests.swift#tokenText(6_594) == "6.6k"` |
| DR14 | 隐私：正文不提取、不持久化、不输出；唯一例外是 claude CLI 首条合格用户消息截取 ≤80 字符作标题 | 单测 | `test_cli_sessions.ClaudeCliTitlePrivacyTests.test_title_is_first_qualified_user_message_capped_at_80_chars`、`test_cli_sessions.ClaudeCliTitlePrivacyTests.test_title_at_exactly_80_chars_is_kept_whole`、`test_daily_report.DailyReportPrivacyTests.test_report_output_and_store_carry_no_message_bodies` |
| DR15 | 总览与当日详情的编排、全部图表与数字有悬浮卡 | 真机 UI | 用户 UI 验收（截图核对） |
| DR16 | 准确性：独立重算与报告三类逐位一致（Zcode 在跨零点分摊容差 <2.2% 内） | 真实数据 | 独立重算脚本，证据不入库 |

## 已知边界

- 各来源缓存语义不同（Zcode 新鲜输入约 2%，Codex 约 50%），跨来源三类合计对比有系统偏差；口径在注脚中明示。
- Codex 逐请求重发上下文会显著放大"新鲜输入"，单日 token 量主要由请求次数与上下文长度驱动。
- Claude 依赖 `~/.claude/projects` 转写存在；转写被清理的历史日会降级为〔无 token〕。
- 热力阈值按当前工作强度校准，工作模式显著变化时可再调整（Python/Swift 两处常量）。

实现：[聚合模块](../../scripts/daily_report.py)、[计价模块](../../scripts/usage_cost.py)、[周期聚合](../../scripts/usage_report.py)、[价格拉取](../../scripts/pricing_fetch.py)、[规范名](../../scripts/model_names.py)、[CLI 接线](../../scripts/inbox.py)、[策略纯函数](../../native/InboxPolicy.swift)、[原生界面与调度](../../native/SessionInbox.swift)、[日报测试](../../tests/test_daily_report.py)、[计价测试](../../tests/test_usage_cost.py)、[拉取测试](../../tests/test_pricing_fetch.py)、[周期测试](../../tests/test_usage_report.py)、[策略测试](../../tests/InboxPolicyTests.swift)。

- 节奏带色块不做 hover 缩放（2026-09-20 用户要求：色块随 hover 放大表现为"移动"）；悬浮提示保留，其余图表 hover 不变。
