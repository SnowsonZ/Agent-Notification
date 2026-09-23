# 用量金额与桌面组件：交付说明（按里程碑追加）

对应[执行计划](usage-cost-widgets-execution.md) §3 的交付要求；分支 `docs/usage-cost-widgets-design`，按里程碑提交，review 后合并。

## M1 数据与计价（A1–A4、B1–B5）

### 变更摘要

- **采集（A2）**：六个来源产出 `model_raw` 与四项原始 token（fresh_input/cache_write/cache_read/output，输出一律含 reasoning）。Zcode 从轮级改为逐请求计量（`model_usage`），按 turn 汇总与 `turn_usage` 对账，一致才逐请求记账（各请求按自身区间跨零点分摊，model 取 `model_id`）；不一致或缺表整轮退回轮级并记 `unknown`。Codex 的 model 取该增量前最近一条 `turn_context.payload.model`；Kimi 的 model 在 `usage.record` 顶层 `model` 字段。Pi/OpenCode 的原生美元成本（`usage.cost.total` / `cost`）随 model 累加为 `native_cost_usd`。
- **规范名（A1）**：`model_names.canonical()` 只做确定性变换（小写、取最后 `/` 后、去 8 位日期后缀），不做相似度匹配；`raw_names` 逐字去重保序，至多 5 个。
- **schema v8（A3）**：`REPORT_VERSION = 8`；`tasks[].models`、`totals.models`（totals 条目无 raw_names）；`fidelity=unavailable` 任务 `models = {}`。迁移（§3.1）：首次遇到 v7 把 `reports/` 全部 v7 文件备份到 `reports.v7.bak/`（只做一次）；重扫 v8 合计 < v7（来源被清理）时改用 v7 内容——`version: 8` + `migrated_from: 7` + 任务拆为 `unknown` model，不降级；今天的报告不参与迁移。
- **计价（B2）**：三层价格（用户覆盖 > 国产官方 > 拉取/随包快照），候选名 = 规范名 → raw 小写 → raw 去前缀，主键或 aliases 精确匹配；历史价按 `until` 升序取第一个 > D 的段；cache_write/read 缺失回退按 input 价并在 `pricing check` 标注；`unknown` 一律未定价；未定价 token 计数不折金额；有 `native_cost_usd` 的未命中 model 按该美元金额兜底。`money_text` 与 fx 读写按规范 §5。
- **拉取（B3）**：LiteLLM 上游转换（mode ∈ chat/responses、×1e6、键小写 + 去前缀 aliases、USD）；防护：非 200/解析失败整次失败、负数/非有限丢弃条目、与旧值比跳变超 10 倍沿用旧值；价格变化先把旧价追加 `history`（until=生效日）再写新价。自适应节奏状态机 7→14→28→30（相同 streak 达 2 翻倍封顶 30），失败次日重试且状态不变；`--auto` 只在到期时请求；唯一 GET，超时 20 秒。
- **CLI（B4）**：`inbox daily-report` 响应附 cost（不写入固化报告）；`--overview` 的 `days[]`/`today` 附 cost 并新增 `week_models`；新增 `inbox usage`（day/week/month/all，ISO 周、自然月，by 三维度按 CNY 视图金额降序，`previous` 环比基期，`--json` 全量）与 `inbox pricing show/check/update/fx`。性能：过去日（含无消耗日）补录后固化，`usage --period all --json` 缓存命中 **0.2 秒**（要求 <1 秒）。
- **价格文件（B1）**：`scripts/pricing/cny-official.json` 五条，全部官方页面核价（`source_url` + `checked_at`）。`litellm-snapshot.json` 由转换器从上游生成（3133 条）。

### 验收对照表

| # | 结论 | 证据 |
|---|---|---|
| U1 | ✅ | `scratch/check_v8_parity.py`：9/19、9/20 三类逐位一致；9/21 仅 Zcode +1.71%（<2.2% 容差，进行中轮次次日补全语义），claude/codex 逐位一致。汇总 `scratch/2026-09-23-v8-parity.md` |
| U2 | ✅ | `test_v7_migration_rewrites_to_unknown_when_sources_gone`、`test_v7_migration_uses_v8_when_not_smaller`：改写方向含 `migrated_from`/unknown/备份目录；非降级方向直接采用 v8 |
| U3 | ✅ | 每来源夹具测试（tests/test_daily_report.py）：Zcode 对账一致逐请求/不一致回退 unknown、Codex 缺 turn_context 记 unknown、Pi/Kimi/Claude/OpenCode 四项拆分、native_cost 累加、unavailable 空 models |
| U4 | ✅ | tests/test_model_names.py（10 项）：大小写/前缀/日期后缀合并、`///`→unknown、不做相似度匹配 |
| U5 | ✅ | tests/test_usage_cost.py：三层优先级、alias 精确匹配（不做相似度）、历史价跨调价日、未定价计数、native 兜底 |
| U6 | ✅ | tests/test_pricing_fetch.py：序列「变同同同同同同」→ 7,7,14,14,28,28,30（注入时钟）；失败次日重试状态不变；10 倍防护；`--auto` 未到期跳过；哈希相同不写文件 |
| U7 | ✅ | `test_computed_matches_native_within_one_percent`（同一单价下误差 0%，满足 <1% 口径） |
| U8 | ✅ | `MoneyTextTest.CASES` 12 组边界值（None→—、0→$0.00、<0.01、千分位）；Swift 侧在 M3（C1）用同一组用例对齐 |
| U9 | ✅ | tests/test_usage_report.py：ISO 周跨年（2025-12-29..2026-01-04）、闰年二月 2028、非闰 2026、月界滚动、周/日 previous |
| U11 | ✅ | 全量 Python 257 项通过（`-W error::ResourceWarning`）、`ruff check scripts tests` 通过、Swift 策略测试通过（CI 同口径） |

W 编号不属于本里程碑。

### 偏离与疑点

1. **金额对象新增可选键 `native_fallback`**（规范 §5 未定义归属）：`native_cost_usd` 兜底金额不属于输入/缓存/输出任何一类，放在独立键（USD），展示合计时计入。不改三类口径，仅增加信息；评审如倾向其他归法，改动集中在 `usage_cost.cost_for_models`。
2. **Kimi model 字段路径**：规范 §2 表格写 `model`，实测在 `usage.record` 顶层（`record.model`）而非 `usage.model`，按实测实现，不改变口径。
3. **Zcode 对账判定为四项逐项严格相等**，且要求各请求区间有效（start>0、end≥start），否则整轮回退。真实数据 token 加权回退率 **9.51%**（<20% 评审线，机制内预期）。如评审希望放宽为三类对比或小容差，改动集中在 `_zcode_data`。
4. **`by.harness` / `by.project` 的金额**：规范未定义聚合层金额的来源口径，实现从 task 级 models 按 provider/project 聚合后计价（与任务级 raw_names 语义一致），不改用 totals.sources（v7 遗留结构无 models）。
5. **`series` 含窗口内全部天**（含零消耗日），供图表连续 x 轴；规范未明确，属展示层决定。
6. **性能修复**：`usage` 对无报告的过去日固化空报告（与 overview 补录同语义），否则无消耗窗口每次调用都触发重扫（实测 4.2s → 0.2s）。
7. **未定价清单（真实数据）**：`k3`、`kimi-for-coding` 无官方页面可核价（Kimi 平台为订阅端点，K3 搜索结果无法溯源到官方定价页），按纪律留空显示未定价；`unknown`、`<synthetic>` 按规范/语义不定价。海外模型（claude-opus-5、claude-sonnet-5、gpt-5.6-sol/terra/luna、gpt-6-astra）由随包快照覆盖。
8. **价格口径备注**：MiniMax-M3 取 ≤512k 基础档（首版不含阶梯价）、现价（官方页标注「永久五折」后的 2.10/8.40/0.42 元），缓存写入列 M3 表未提供→回退标注；DeepSeek 取高峰标准档（9.0/0.30/27.0 元），官方有闲置半价档未纳入；GLM 缓存存储按小时计费与本项目 cache_write 口径不同，不写入。

### 需要用户验收的部分

无（M1 全部为 Python 数据层与 CLI，单测 + 真实数据脚本已覆盖；界面验收在 M3/M4）。


## M2 组件骨架（D0–D3）与 M3 日报界面（C1–C3）

### D0 结论（评审重点）

- **手动 swiftc + appintentsmetadataprocessor 在 Xcode 26/Swift 6.3 上不可行**：driver 接受
  `-emit-const-values` 但静默不产出任何 `.swiftconstvalues`；前端 sema/默认模式明确报
  「this mode does not support emitting extracted const values」；output-file-map、
  `-emit-stringsdata`（参数不存在）等 18 轮组合全部排除。结论：const values 产出只能由
  xcodebuild 构建系统驱动。
- **采用预案 A**：配置式组件只在 Xcode 环境构建——`build_inbox_app.py` 生成最小 pbxproj
  （app-extension target、SWIFT_ENABLE_EMIT_CONST_VALUES=YES、SWIFT_VERSION 5.0），
  xcodebuild 构建后拷贝 appex；本机 CLT 走 swiftc 降级静态组件（已本地验证）。
  CI 验证（Metadata.appintents 断言）待一轮运行确认（见「待 CI 验证」）。
- 过程记录：临时 workflow 20 轮，结论沉淀于本节与 git 历史日期 09-23/09-24；确认结论后删除。

### 变更摘要（M2）

- `native/Widget/Widgets.swift`：inbox / usage / recent 三 kind；配置式（AppEnum 参数）
  与降级（StaticConfiguration + prefs.fallback）条件编译；过时提示与「请打开会话通知」。
- `native/Shared/WidgetSnapshot.swift`：双端共享快照结构 + moneyText/tokenText/换算
  （与 Python 同组测试用例）；by 维度 Top 5 截断合并 `__other__`。
- `WidgetSnapshotWriter`：刷新策略（签名不变不写不 reload；recent 60s 合并；usage 15 分钟），
  0700/0600 原子写，hide_titles 不写标题；快照路径固定 `~/.local/state/session-manager/widget/`。
- URL：`agentnotification://` 注册 + 全 App 级接收（窗口未开时暂存补送）；
  `WidgetURLRouter.parse` host 白名单 + id/revision/period 校验（非法拒绝、不记参数原文）。

### 变更摘要（M3）

- 周期（日/周/月）与币种（CNY/USD，默认 CNY）分段控件，UserDefaults 持久化；
  周/月视图：汇总卡（金额大字/三类/任务/环比/未定价）、金额与 token 三段堆叠柱图、
  月视图累计折线、来源与模型榜（Top 6 + 其他）、Top 5 项目、周期导航（下期不超本期）、口径注脚。
- 日视图：汇总卡金额行与未定价提示、模型占比条（Top 6 + 其他）；overview/today/详情解码附 cost/models。
- pricing：App 启动与每 6 小时后台执行 `pricing update --auto`（由命令判断到期）。
- usage 拉取打开日报窗口 / 切币种立即刷新（§3）。

### 本地验证证据

- 257 项 Python 测试 + Swift 策略测试 + ruff 全部通过（每个里程碑提交前 CI 同口径）。
- 截图（scratch/）：m3-daily-week.png（周视图 CNY 金额模式，¥2,040.20/环比 ↘70%/未定价 467M/
  来源榜金额与 token 一致）、m3-daily-month-tokens.png（月视图 token 模式 + 累计折线，
  ¥59,961.30/582 任务/2.1B 未定价）、m3-daily-day.png（日总览）。
- 快照端到端（本机真实数据）：`~/.local/state/session-manager/widget/snapshot.json` 生成，
  inbox/recent/usage 三周期/fx/prefs 齐全，键名 snake_case，by 维度 Top 5 截断生效。
- Swift 解码交叉验证：overview/day/usage 三份真实 JSON 解码通过（本地最小依赖测试）。
- **组件数字与 CLI 一致性（W5）**：周快照 total 887,002,972 与 `inbox usage --period week` 同源。

### 偏离与疑点

1. **快照 fx 键名为 `usd_cny`**（规范示例 `USD_CNY`）：解码策略 convertFromSnakeCase 的
   转换结果要求；组件与 App 读写同一文件、语义一致。
2. **`@State` 不可用**（裸 swiftc 无 SwiftUIMacros）：金额/token 切换状态放模型层。
3. **日总览页暂无金额行**（金额在汇总卡=当日详情页与周/月视图）；热力图按金额着色开关、
   悬浮卡金额行、x 轴标签截断优化为 M3 尾巴，见「未完成」。
4. 配置式构建的 pbxproj 由脚本生成（非 Xcode 维护）；构建产物经 codesign 与
   Metadata.appintents 断言把关。

### 未完成（下轮补）

- 悬浮提示金额行（§7 悬浮卡）；热力图按金额着色开关（§7）；x 轴日期标签截断优化。
- D4 组件界面视觉细化（当前为基础可读版）；W1/W7/W9 与配置面板的用户 UI 验收。

### 待 CI 验证（1 轮）

- 配置式组件构建：`build_inbox_app.py --standalone` 在 macos-26（Xcode 26）产出
  Metadata.appintents，并全部 D1 断言（plist 键/版本同步/_NSExtensionMain/entitlements 两项/
  URL scheme）通过。本地无 Xcode（缺 xcodebuild 与 appintentsmetadataprocessor），无法自验。
