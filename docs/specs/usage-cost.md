# 用量金额与跨周期统计（规范）

状态：**终版设计，实施中**（2026-09-23 定稿；2026-09-24 验收后修订 §2 Zcode 归属、§3.1 合并规则、§4.4 别名与历史，修订处均标注日期）。实施步骤与分工见 [执行计划](../plans/usage-cost-widgets-execution.md)；设计取舍背景见 [方案](../plans/usage-cost-report.md)（与本文冲突时以本文为准）。
关联：[工作日报](daily-report.md)（本规范将其报告 schema 从 v7 升到 v8）、[桌面组件](desktop-widgets.md)（使用本规范的 `inbox usage` 输出）。

## 1. 范围与口径

- 金额 = **按 API 标价折算的等价金额**，不代表实际账单。订阅、免费额度、促销价都不体现。凡是展示金额的地方，注脚都要写明「按标价估算」。
- 支持两种币种：USD、CNY，可以随时切换。汇率为单一的手动汇率。
- 周期分日、周、月。周按 ISO 周算，从周一到周日；月按自然月算；归日使用本机时区，规则与现有日报一致。
- 统计视角分三种：harness（即来源 provider）、model、项目。
- 不改变现有口径：三类 token（输入 / 缓存 / 输出）的定义、热力分级、agent 会话排除规则、今天实时计算而过去日定稿固化的机制，全部保持原样。

## 2. 采集：逐条 model 与四项原始 token

每条用量记录（逐请求、逐消息或逐轮）要产出 `model_raw` 和下列四项原始值。output 一律包含 reasoning。

| 来源 | 计量粒度 | model_raw | fresh_input | cache_write | cache_read | output |
|---|---|---|---|---|---|---|
| Claude | assistant 消息 | `message.model` | `input_tokens` | `cache_creation_input_tokens` | `cache_read_input_tokens` | `output_tokens` |
| Codex | `token_count` 增量 | 该增量之前最近一条 `turn_context.payload.model`；之前没有则为 `unknown` | `last_token_usage.input_tokens` | `cache_write_input_tokens` | `cached_input_tokens` | `output_tokens + reasoning_output_tokens` |
| Zcode | `turn_usage` 逐轮（2026-09-24 修订） | 该轮 `model_usage.model_id`（规则见下方「Zcode 归属」） | `input_tokens − cache_read_input_tokens`（下限为 0） | `cache_creation_input_tokens` | `cache_read_input_tokens` | `output_tokens + reasoning_tokens` |
| Pi | assistant 消息 | `message.model` | `usage.input` | `usage.cacheWrite` | `usage.cacheRead` | `usage.output + usage.reasoning` |
| Kimi | `usage.record` | `model` | `inputOther` | `inputCacheCreation` | `inputCacheRead` | `output` |
| OpenCode | assistant 消息 | `modelID` | `tokens.input` | `tokens.cache.write` | `tokens.cache.read` | `tokens.output + tokens.reasoning` |

规则：

- 三类 token 由四项推出：输入 = `fresh_input + cache_write`，缓存 = `cache_read`，输出 = `output`。**v8 的三类合计必须与 v7 逐位一致**；Zcode 例外，保持现有跨零点分摊容差 <2.2%。
- **Zcode 归属**（2026-09-24 修订，原「逐项严格对账」在真实数据上约 2/3 的轮失败：轮级 token 普遍大于该轮 `model_usage` 之和，差额是未挂在该 turn_id 下的请求）：
  - **token 数量一律以 `turn_usage` 为准**，三类合计与 v7 同源；`model_usage` 只用于确定 model 归属，不再作为计量来源。
  - 该轮 `model_usage` 只出现一个 model（大小写不敏感）：整轮四项 token 归给这个 model。本机 2026-09 共 1132 轮，全部属于这种情况。
  - 出现多个 model：按各 model 在该轮 `model_usage` 中的四项 token 占比，逐项拆分轮级 token；取整误差归给占比最大的 model，保证拆分后合计与轮级相等。
  - 该轮没有 `model_usage` 记录，或表不存在：记为 `unknown`。
  - 归日仍按轮区间分摊（与 v7 相同）；活动段 `segments` 的计算不变。
  - 子代理归属父任务的规则不变。
- **Pi、OpenCode 的原生 cost**：`usage.cost.total` 与 `cost` 都按美元解析，随 model 累加到 `native_cost_usd`，只用于对账和兜底（见 §4.3）。
- 读取范围不变：只读 model 名和数值字段，不新增正文读取。

### 2.1 model 规范名

`canonical(raw)` 的规则：

1. 去掉首尾空白，转成小写；空值记为 `unknown`。
2. 如果含 `/`，只取最后一个 `/` 之后的部分，例如 `zai-coding-plan/glm-5.3-flash` → `glm-5.3-flash`。
3. 去掉末尾的日期后缀，匹配 `-\d{8}$` 或 `@\d{8}$`，例如 `claude-sonnet-4-5-20250929` → `claude-sonnet-4-5`。

报告中的 model 以规范名为键，同一规范名下出现过的原始写法（最多 5 个）记入 `raw_names`。**只做以上确定性变换，不做相似度匹配。**

## 3. 报告 schema v8

`REPORT_VERSION = 8`。在 v7 基础上新增字段，不删除任何现有字段。

```json
"tasks": [{
  "...v7 字段": "...",
  "models": {
    "glm-5.3-flash": {
      "fresh_input": 0, "cache_write": 0, "cache_read": 0, "output": 0,
      "native_cost_usd": null,
      "raw_names": ["GLM-5.3-Flash", "zai-coding-plan/glm-5.3-flash"]
    }
  }
}],
"totals": { "...v7 字段": "...", "models": { "<canonical>": { "fresh_input": 0, "cache_write": 0, "cache_read": 0, "output": 0, "native_cost_usd": null } } },
"migrated_from": 7
```

- 定稿报告中**不写金额**。金额是读取时的派生值（§5），因此价格表或汇率变化后不需要重扫来源。
- `fidelity=unavailable` 的任务保持 `models = {}`。
- `migrated_from` 只在 §3.1 的保留情形下出现。

### 3.1 v7 → v8 迁移（不得降级）

1. 首次遇到 v7 报告时，把 `reports/` 下所有 v7 文件复制一份到 `reports.v7.bak/`。只做一次，目录已存在就跳过。
2. 对过去日重扫，生成 v8 报告。
3. **按任务合并，不按整天取舍**（2026-09-24 修订。原规则按整天合计比较：只要合计变小就整天回退到 v7。但合计变小也可能是会话事后被改判为 agent 拉起，这样会把已排除的 agent 会话算回来，并让全天 model 变成 unknown）。以 `(provider, session_id)` 为键：
   - 重扫中存在的任务：采用 v8 记录。
   - 只在 v7 中存在的任务（来源已被清理）：保留 v7 记录，改写为 `models = {"unknown": {"fresh_input": input_tokens, "cache_write": 0, "cache_read": cache_tokens, "output": output_tokens}}`，并标记 `restored_from: 7`。**但如果该会话按当前有效来源判定为 agent，就不恢复**，计入 `agent_excluded`。
   - 同一任务重扫的三类合计小于现有报告（来源部分被清理，或两次扫描间类别迁移使某一类上升）：三类按类别取 max(现有, 重扫)，正差额（现有 − 重扫）记为 `unknown`（2026-09-25 H0925-4 修订：此前三类合计沿用现有报告，某类上升时与 model 明细不一致；代价是同一批 token 换类别时合计偏高，用户已知悉）。
   - totals 按合并后的任务重新汇总；只要有恢复发生，报告顶层就标记 `migrated_from: 7`。`unknown` 永远不定价。
4. **不降级是长期约束，不只在迁移时生效**：任何重写过去日报告的路径（`--refresh`、以后再升 schema、`inbox usage` 补录），都按第 3 步的规则与磁盘上的现有报告（任意版本）合并，现有报告缺失时再用 `reports.v7.bak/` 中的同日文件。重扫结果永远不能让某个任务丢失 token，只有改判为 agent 属于正当减少。
5. 今天的报告照常实时计算，不参与迁移。

## 4. 价格表

### 4.1 三个层级与文件

| 层 | 位置 | 维护方式 | 币种 |
|---|---|---|---|
| 用户覆盖 | `~/.local/state/session-manager/pricing/override.json` | 用户手改，程序只读 | 任意 |
| 国产官方价 | 随包 `scripts/pricing/cny-official.json` | 发版时人工维护；每个条目必须带 `source_url` 与 `checked_at` | CNY |
| 公开价格 | 自动拉取的 `~/.local/state/session-manager/pricing/fetched.json`；不存在时回退到随包快照 `scripts/pricing/litellm-snapshot.json` | 自动拉取；快照在发版时更新 | USD |

条目格式（单价按每百万 token 计）：

```json
{
  "glm-5.3-flash": {
    "currency": "CNY",
    "input": 0.5, "cache_write": 0.5, "cache_read": 0.1, "output": 2.0,
    "aliases": ["glm-5.3-flash"],
    "source_url": "https://...", "checked_at": "2026-09-23",
    "history": [{"until": "2026-08-01", "input": 1.0, "cache_write": 1.0, "cache_read": 0.2, "output": 4.0}]
  }
}
```

- `cache_write` 或 `cache_read` 缺失时：cache_write 按 input 价计，cache_read 按 input 价计。这项回退要在 `pricing check` 中标注出来。
- `history` 按 `until` 升序排列。计算某天 D 的价格时，取第一个 `until > D` 的历史段；找不到就用当前价。

### 4.2 查找顺序

对报告中的一个 model，依次尝试候选名：① 规范名；② `raw_names` 中的每个原始写法（转小写）；③ 每个原始写法去掉 `/` 前缀后的形式。

每个候选名都按「用户覆盖 → 国产官方 → 公开价格」的顺序查找，匹配方式为主键或 `aliases` 精确相等。先命中者生效。

### 4.3 未定价

以上都没有命中时：

- 如果有 `native_cost_usd`，就用它作为该 model 的美元金额，并在 `pricing check` 中标注为「原生兜底」。
- 否则计为未定价。未定价 token 数 = 四项之和，**不计入金额，也不按 0 计**。
- `unknown` 一律计为未定价。

### 4.4 公开价格转换

来源：`https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json`。

转换规则：

- 只保留 `mode ∈ {chat, responses}` 且有 `input_cost_per_token` 的条目。
- 键名处理：先转小写；如果含 `/`，同时生成去掉前缀的别名。**别名只有在所有指向它的条目四项单价完全相同时才保留**；有分歧的别名全部丢弃，并记入 `pricing check`（2026-09-24 修订：上游有 226 个此类别名，同名不同渠道价格不一，按文件顺序取价不可接受）。
- 字段映射（每 token 单价 × 1e6）：
  - `input_cost_per_token` → input
  - `output_cost_per_token` → output
  - `cache_read_input_token_cost` → cache_read
  - `cache_creation_input_token_cost` → cache_write
- 币种固定为 USD，只保留 §4.1 定义的字段。
- 阶梯价字段（`*_above_*_tokens`）忽略，首版按基础档计价。

**防护规则**（出现以下情况时，该条目**原样保留上一版 fetched.json 中的条目**，包括其 `history`，并把原因记入 `last_error`；上一版没有这个条目时才丢弃）：

- 响应不是 https 200 JSON，或解析失败：整次拉取算失败。
- 单价为负数，或不是有限数。
- 与旧值相比，任一单价变化超过 10 倍。

**价格历史**：拉取结果中某个 model 的单价发生变化时，先把旧价追加进该条目的 `history`（`until` 为本次生效日期），再写入新价。所以历史金额不会因为上游调价而被改写。**单价不变时，新条目必须原样继承旧条目的 `history`**，否则历史会在下一次拉取时丢失，而且会被误判为内容变化（2026-09-24 修订）。

### 4.5 自适应拉取节奏（用户确认）

状态文件 `pricing/fetch-state.json`：

```json
{"interval_days": 7, "same_streak": 0, "last_success": 0, "next_due": 0, "content_hash": "", "last_error": ""}
```

- `content_hash` 取转换后规范化 JSON（键排序）的 sha256。
- 到期判断：`now >= next_due`。首次运行时没有状态文件，视为立即到期。

| 本次结果 | 状态变化 |
|---|---|
| 成功，哈希与上次不同（含首次） | `interval=7`，`same_streak=0`，写入 fetched.json 与历史 |
| 成功，哈希与上次相同 | `same_streak += 1`；达到 2 时，`interval = min(interval × 2, 30)` 且 `same_streak = 0` |
| 失败 | 间隔与 streak 不变，`next_due = now + 1 天`，记录 `last_error` |

每次成功后更新 `last_success = now`、`next_due = now + interval`。

间隔依次为 **7 → 14 → 28 → 30** 天。例如「变化、相同、相同」之后，间隔从 7 天变为 14 天。

- 手动执行 `inbox pricing update` 不受到期时间限制，其结果同样按上表更新状态。
- 自动触发只做一件事：App 在启动时和每 6 小时，在后台执行 `inbox pricing update --auto`，由该命令自己判断是否到期。
- 网络请求只有这一个 GET，超时 20 秒，不携带任何本机数据。

## 5. 金额计算与汇率

- 某天、某 model 的原币金额 =
  `fresh_input × input + cache_write × cache_write价 + cache_read × cache_read价 + output × output价`（单价按每百万 token），各类价格取该天适用的价格。
- 按三类归集：「输入」金额 = fresh_input 项 + cache_write 项；「缓存」金额 = cache_read 项；「输出」金额 = output 项。
- **金额对象**（所有输出中通用的结构）：

```json
"cost": {
  "input":  {"USD": 0.0, "CNY": 0.0},
  "cache":  {"USD": 0.0, "CNY": 0.0},
  "output": {"USD": 0.0, "CNY": 0.0},
  "unpriced_tokens": 0
}
```

- 按原币分别存放，**不在计算层换算**。
- 展示时换算：CNY 视图 = `USD × rate + CNY`；USD 视图 = `USD + CNY / rate`。
- 汇率配置文件 `~/.local/state/session-manager/pricing/fx.json`，内容形如 `{"USD_CNY": 7.10, "as_of": "2026-09-23"}`。文件不存在时使用上面的默认值。可以用 `inbox pricing fx <rate>` 修改，`as_of` 自动写入当天日期。
- 金额文字 `money_text(value, currency)`，Python 与 Swift 各实现一份，两边测试用例相同：
  - 格式为 `$1,234.56` / `¥1,234.56`，保留 2 位小数，千分位用逗号。
  - `0 < value < 0.01` 显示 `<$0.01` / `<¥0.01`。
  - 等于 0 显示 `$0.00`。
  - 没有可定价 token、只有未定价的，显示 `—`。

## 6. CLI

```sh
bin/session-manager inbox daily-report [--date D] [--refresh]     # 现有 JSON 中，task / totals / totals.sources / totals.projects / totals.models 各附 cost
bin/session-manager inbox daily-report --overview                 # days[] 与 today 附 cost；新增 week_models（近 7 天）
bin/session-manager inbox usage --period day|week|month|all [--date D] [--json] [--currency USD|CNY]
bin/session-manager inbox pricing show [MODEL] | check [--days 30] | update [--auto] | fx RATE
```

`inbox usage --json` 的输出（`--period all` 时返回 `{"day": …, "week": …, "month": …}`）：

```json
{
  "period": "week", "start": "2026-09-21", "end": "2026-09-27", "is_current": true,
  "generated_at": 1790000000,
  "totals": {"input_tokens": 0, "cache_tokens": 0, "output_tokens": 0, "total_tokens": 0, "tasks": 0, "cost": {}},
  "previous": {"total_tokens": 0, "cost": {}},
  "series": [{"date": "2026-09-21", "total_tokens": 0, "cost": {}}],
  "by": {
    "harness": [{"key": "codex", "total_tokens": 0, "cost": {}}],
    "model":   [{"key": "gpt-5.6-terra", "total_tokens": 0, "cost": {}}],
    "project": [{"key": "/path", "name": "session-manager", "total_tokens": 0, "cost": {}}]
  },
  "fx": {"USD_CNY": 7.10, "as_of": "2026-09-23"},
  "pricing": {"fetched_at": 0, "unpriced_models": ["..."]}
}
```

- `by` 的各维度按金额排序：先按 CNY 视图的合计降序；金额相同或都为未定价时，按 total_tokens 降序。
- `--json` 模式下返回全量；组件快照的 Top 5 截断由 App 负责。
- `previous` 是上一个同类周期的合计，用于计算环比。
- 周期内已定稿的日子读缓存，今天实时计算，不触发全量重扫。
- 月视图的耗时预算：缓存命中时 <1 秒。
- `pricing check` 列出最近 N 天的：未定价 model 及其 token 量、原生兜底、缓存价回退、上次拉取的状态。

## 7. 界面（原生 App，2026-09-24 重设计）

**总原则**：token 为主线，金额是伴随指标——凡有 token 统计处旁边给出金额；金额不做全局切换、不独立成页；**只展示 USD**（CNY 官价模型按汇率折算），无币种选择器。

- **日报窗口顶部**只有一个分段控件：`日 | 周 | 月`（UserDefaults 持久化）。**日**即总览与当日详情。
- **统一设计语言**：日、周、月、日详情四种屏幕共用同一卡片样式（`dailyReportCard`）、同一手绘堆叠柱图 + 即触即显悬浮卡、同一三类配色（输入 #0A84FF / 缓存 #64D2FF / 输出 #FF9F0A，不随主题强调色漂移）；图例顺序恒为 输入/缓存/输出，悬浮卡顺序保留 缓存/输入/输出 契约。
- **周、月视图**（并入日视图语言，不再使用 Swift Charts 平行体系）：
  1. 汇总卡：token 大字、金额伴随行（正下方、字号小一档）、三类分段条、任务/活跃天/输入/输出瓦片、环比（较上一期，token 口径，涨红降绿）、未定价提示。
  2. 逐天堆叠柱图：输出/缓存/输入自上而下，柱悬浮卡为三类 token + 合计 + 金额；月视图叠加累计虚线（token 口径）。
  3. 双栏：来源 / 模型 两个榜单卡（官方图标 + 名称 + 占比条 + token 主列 + $ 次列），恒按 token 排序。
  4. Top 项目全宽卡（名称 + token + $ + 占比条）。
  5. 周期导航：‹ 上一期 · 本期 · 下一期 ›，下一期不能超过本期。
- **日视图**：
  - 头卡：token 大字、金额伴随行、瓦片（任务/轮次/活跃来源/较昨日）。
  - 总览编排与周/月一致：双栏（来源占比环形 · 本周 | 模型榜 · 本周）+ Top 项目全宽卡，数据来自 `usage` 周（近 7 天窗口），金额在行内与悬浮卡。
  - 热力图只按 token 着色（取消按金额着色开关）；悬浮卡含金额。
- **日详情**：金额并入汇总卡 hero（不再单列金额行）；任务行附官方图标与金额；节奏带悬浮卡含金额。
- **来源身份**：来源/模型行使用官方图标（`AgentIcons.swift` 管线；模型按名称前缀映射家族图标，未匹配退化灰底字牌）。
- **悬浮提示**：固定结构 名称 → 缓存/输入/输出（token）→ 合计 → `金额：$12.34（输入 $a · 缓存 $b · 输出 $c）`，未定价追加 `未定价：X tokens`。榜单行悬浮为 名称 + token + 金额。
- **措辞**：界面不出现「按标价估算」字样；估算免责只在各页注脚保留一句：「金额为按 API 标价的估算值（非账单）· USD 显示，CNY 官价按汇率 X.XX 折算」。

## 8. 验收

证据类型与覆盖列由 `harness/acceptance.py` 检查（见 [delivery-harness.md](delivery-harness.md)）。

| # | 项 | 方法 | 证据类型 | 覆盖 |
|---|---|---|---|---|
| U1 | v8 三类合计与 v7 逐位一致（Zcode 保持现有容差） | 用本机真实数据抽 3 个过去日，由独立脚本对比，证据放 `scratch/` | 真实数据 | 独立脚本对比，证据不入库 |
| U2 | 迁移不降级 | 夹具：v7 报告存在但来源已删；迁移后合计等于 v7，出现 `migrated_from: 7` 和 `unknown` model，备份目录存在 | 夹具 + 性质 | `test_daily_report.DailyReportTests.test_v7_migration_rewrites_to_unknown_when_sources_gone`、`test_daily_report.DailyReportTests.test_partial_cleanup_diff_becomes_unknown`、`test_properties.MergeNoDowngradeProperties` |
| U3 | 六个来源的 model 与四项拆分正确 | 每个来源一份夹具测试，包括 Codex 缺少 turn_context 时记为 unknown；Zcode 覆盖单 model 整轮归属、多 model 按占比拆分（拆分后合计等于轮级）、无 model_usage 记 unknown。真实数据：最近 7 天各来源 `unknown` 的 token 占比写进 PR | 夹具 + 真实数据 | `test_daily_report.DailyReportTests.test_zcode_reconciled_requests_split_by_model`、`test_daily_report.DailyReportTests.test_zcode_multi_model_splits_by_request_share`、`test_daily_report.DailyReportTests.test_zcode_turn_without_model_usage_is_unknown`、`test_daily_report.DailyReportTests.test_codex_model_from_turn_context_and_unknown_without`、`test_daily_report.DailyReportTests.test_claude_pi_models_and_native_cost`、`test_daily_report.DailyReportTests.test_kimi_opencode_models_and_native_cost` |
| U4 | 规范名 | `GLM-5.3-Flash`、`zai-coding-plan/glm-5.3-flash` 合并；日期后缀被去掉；不做相似度匹配 | 单测 | `test_model_names.CanonicalTest` |
| U5 | 查找顺序、历史价格、未定价 | 覆盖层 > 国产官方 > 公开价格；跨调价日的金额分别按新旧价计算；未知 model 进入 `unpriced_tokens` | 单测 + 性质 | `test_usage_cost.LookupOrderTest`、`test_usage_cost.HistoryPriceTest`、`test_usage_cost.CostTest`、`test_properties.PeriodCostProperties` |
| U6 | 拉取节奏状态机 | 序列「变、同、同、同、同、同、同」对应间隔 7,7,14,14,28,28,30；失败后次日重试且状态不变；10 倍防护生效 | 单测 + 性质 | `test_pricing_fetch.FetchStateMachineTest`、`test_pricing_fetch.TransformTest`、`test_pricing_fetch.TransformBoundaryTest`、`test_properties.PricingTransformProperties` |
| U7 | Pi / OpenCode 对账 | 同一 model、同一单价下，我方计价与 `native_cost_usd` 误差 <1% | 单测 | `test_usage_cost.ReconciliationTest.test_computed_matches_native_within_one_percent` |
| U8 | `money_text` 双端一致 | Python 与 Swift 跑同一组边界值 | 单测 | `test_usage_cost.MoneyTextTest`、`tests/InboxPolicyTests.swift#moneyText(0.005, currency: "USD")` |
| U9 | 周期边界 | ISO 周跨年、闰年二月、月末时区边界 | 单测 | `test_usage_report.PeriodBoundsTest` |
| U10 | 界面 | 真实数据下日/周/月三屏截图（`scratch/u10-*.png`）核对：编排与统一设计语言一致、金额为 USD 伴随指标、悬浮卡数字与 `inbox usage` 一致（2026-09-24 重设计后重验收） | 真机 UI | 用户截图核对 |
| U11 | 全量测试 | `python3 -W error::ResourceWarning -m unittest discover -s tests -v`、`ruff check scripts tests`、Swift 策略测试全部通过 | CI 断言 | `.github/workflows/build.yml#harness/verify.py --strict --full` |

## 9. 已知边界

- 等价金额 ≠ 账单。
- 首版不含阶梯价，长上下文请求会被低估。
- 各来源缓存语义本就不同，金额继承这种偏差。
- 公开价格数据由第三方维护，可能滞后。10 倍防护只能挡住明显的错误。
