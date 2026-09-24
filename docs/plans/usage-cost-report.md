# 日报消耗金额与跨周期统计（设计方案）

状态：**已定稿，现行合同见 [用量金额规范](../specs/usage-cost.md)**，本文保留调研与取舍背景，与规范冲突处以规范为准（例如手动拉取不再重置节奏状态）。编写日期 2026-09-23，基线 v0.7.5 / `485228f`。
关联：现行日报合同 [daily-report.md](../specs/daily-report.md)（报告 schema v7）；桌面组件复用本方案的数据层，见 [desktop-widgets.md](desktop-widgets.md)。

## 需求

1. 日报增加消耗金额，支持美元 / 人民币切换。
2. 支持跨周期统计：日、周、月。
3. 用可视化组件展示。
4. 为桌面组件提供按周期、model、harness（即来源：Claude / Codex / Zcode / Pi / Kimi / OpenCode / Antigravity）三种视角的 token 与金额数据。

## 结论

- **金额按「API 标价折算的等价金额」计算，不代表实际账单。** 订阅制（Claude Max、ChatGPT、各家 coding plan）无法从本地数据得知真实扣费。界面和注脚统一标注「按标价估算」。
- **前提已核实：六个有 token 的来源都能拿到逐条 model。** 现有日报只按任务汇总三类 token，没有记录 model，而金额必须按 model 计价，因此采集层要补一个 model 维度，报告 schema 从 v7 升到 v8。
- **报告只保存「按 model 拆分的原始 token」，金额在读取时用价格表现算，不写入定稿报告。** 这样价格表更新或汇率变化时不需要重扫来源，也就不会因为历史转写被清理而丢数据。
- **周、月由日报定稿聚合而来，不新增来源扫描。** 当期包含今天，今天仍然实时计算。
- **新图表用 Swift Charts**（macOS 13+ 可用，项目最低 macOS 14），替代手绘，新增的周期视图和组件共用同一套图表。

## 来源的 model 字段（2026-09-23 本机实测）

| 来源 | model 所在位置 | 实测示例 | 附带原生 cost |
|---|---|---|---|
| Claude | 转写中 assistant 消息的 `message.model` | `claude-opus-5-5` | 无 |
| Codex | rollout 的 `turn_context.model`，作用于其后的 `token_count` 增量 | `gpt-5.6-terra`、`gpt-6-astra` | 无 |
| Zcode | `model_usage.model_id` + `provider_id`，逐请求记录，自带分项 token | `GLM-5.3-Flash`、`MiniMax-M3`、`deepseek-v4-pro` | 无 |
| Pi | 会话 jsonl 中的 `message.model` / `provider` | `glm-5.3-flash` @ `zai-coding-cn` | 有：`usage.cost.{input,output,cacheRead,cacheWrite,total}`（美元） |
| Kimi | `usage.record.model` | `zai-coding-plan/glm-5.3-flash` | 无 |
| OpenCode | message JSON 的 `modelID` / `providerID` | `muse-spark-1.3-contributor-free` | 有：`cost` |

注意点：

- **同一模型的名称写法不统一**：`GLM-5.3-Flash`、`glm-5.3-flash`、`zai-coding-plan/glm-5.3-flash` 实为同一模型；`MiniMax-M3` 与 `Minimax-M3` 仅大小写不同。需要一层归一化。
- **Zcode 计价粒度要变**：现在按 `turn_usage` 逐轮统计，改为按 `model_usage` 逐请求计价，这样一轮里切换模型也能分别计价。逐请求 token 的合计须与轮级 token 对账；对不上时退回轮级 token，并把这部分记为未归属模型（`model=unknown`）。
- **Codex 的归属规则**：某条 `token_count` 之前如果没有出现过 `turn_context`，该增量记为 `unknown`。

## 数据模型

### 采集层：逐条记录补 model 与细分 token

现有三类 token 把「新鲜输入」和「缓存写入」合并成了「输入」，但两者单价不同（例如 Anthropic 的缓存写入是输入价的 1.25 倍），计价需要拆开。采集层逐条记录改为保存四个原始字段：

```
fresh_input, cache_write, cache_read, output   # output 已含 reasoning
```

三类口径保持不变，由这四项推出：输入 = `fresh_input + cache_write`，缓存 = `cache_read`，输出 = `output`。现有热力分级、排名、占比都不受影响。

### 报告 schema v8

每个任务新增 `models`，totals 新增 `models` 维度：

```json
"models": {
  "anthropic/claude-opus-5-5": {
    "fresh_input": 0, "cache_write": 0, "cache_read": 0, "output": 0,
    "native_cost_usd": null
  }
}
```

- key 是归一化后的 `vendor/model`，原始写法保留在 `raw_names` 中备查。
- `native_cost_usd`：来源自带的费用（Pi、OpenCode），只用于对账，以及对价格表未收录的模型兜底。
- 不写入金额字段；金额是报告的派生视图（见下节）。

### v7 → v8 迁移：不能因重算丢数据

现有机制在 schema 升版时会全量重算。但 Claude 转写之类的历史来源可能已被清理，直接重算会把原本有 token 的日子降级成〔无 token〕。迁移规则：

- 重算结果的三类合计 ≥ 旧 v7 定稿：直接采用。
- 重算结果少于旧定稿：保留 v7 的三类合计，差额记入 `models["unknown"]`，这部分金额计为「未定价」。
- 迁移前把 v7 报告目录整体备份为 `reports.v7.bak/`。

## 价格表

### 结构与来源

价格表 `pricing.json` 随包分发，用户可以在 `~/.local/state/session-manager/pricing.override.json` 中覆盖或补充：

```json
{
  "version": "2026-09-23",
  "models": {
    "anthropic/claude-opus-5-5": {
      "currency": "USD", "unit": 1000000,
      "input": 0, "cache_write": 0, "cache_read": 0, "output": 0,
      "aliases": ["claude-opus-5-5"],
      "effective_from": "2026-01-01"
    },
    "zhipu/glm-5.3-flash": {
      "currency": "CNY", "unit": 1000000,
      "aliases": ["GLM-5.3-Flash", "zai-coding-plan/glm-5.3-flash"]
    }
  }
}
```

- **按原币种计价**：国产模型（GLM、DeepSeek、Kimi、MiniMax）官方以人民币标价，就按人民币记录，展示时再换算。这样人民币视图不会被汇率来回折算扭曲。
- **支持历史价格**：`effective_from` 允许同一模型保留多段价格，按用量发生的日期匹配，调价不会改写过去的金额。
- **模型名匹配**顺序：精确 key → aliases → 去掉 provider 前缀并转小写后再匹配。都不命中时，有原生 cost 就用原生 cost，否则计为「未定价」。**不按名称相似度猜价格。**
- **首版不做阶梯价**：超长上下文加价之类的阶梯价按基础档计价，在注脚中列为已知边界。后续需要时，可在逐请求层拆分档位，并在 `models` 下增加档位字段。
- **价格表更新（2026-09-23 用户确认：自动拉取 + 自适应间隔）**：从公开价格数据（如 LiteLLM 的 `model_prices_and_context_window.json`）拉取并转换。
  - **三层叠加**：随包快照 < 自动拉取层 `pricing.fetched.json` < 用户 override。自动拉取只写中间层，永不覆盖用户手改。
  - **自适应间隔**：初始每 7 天拉一次；连续 2 次拉取内容（转换后按规范化 JSON 取哈希）与上次相同，间隔翻倍，依次 7 → 14 → 28 天，封顶 30 天；任何一次内容变化立即回到 7 天。拉取失败（断网、非 200、解析失败）不计入「相同」次数，次日重试，不改变当前间隔。
  - **状态文件** `~/.local/state/session-manager/pricing-fetch.json`：`last_checked`、`last_changed`、`interval_days`、`same_streak`、`content_hash`、`last_error`。
  - **触发方**：没有常驻定时任务。App 启动时及每 6 小时检查一次是否到期，到期则在后台调用 `inbox pricing update --auto`；CLI 手动 `inbox pricing update` 不受间隔限制，且重置状态。
  - 只发一次对公开 URL 的 GET，不携带任何用户数据；只接受 https 与 JSON，模型单价出现负数、或单个模型单价变化超过 10 倍时，该条目不采纳并记入 `last_error`，防止上游数据错误污染金额。
  - 随包快照在发版时同步一次，保证离线首次安装也有价格。

### 汇率

- 汇率保存在配置中，形如 `{"USD_CNY": 7.10, "as_of": "2026-09-23"}`。默认值随包分发，用户可以在界面或 CLI 中修改。
- 展示时一律用当前汇率换算（金额本就是读取时现算），界面注明「汇率 7.10 · 2026-09-23」。
- 首版不自动拉取汇率，理由同价格表：不增加后台联网。是否要按日取历史汇率见待决策项。

## 聚合与 CLI

新增 `scripts/usage_cost.py`（纯函数：归一化、计价、换算），`daily_report.py` 只负责按 model 采集，不直接关心价格。

```sh
bin/session-manager inbox usage --period day|week|month [--date YYYY-MM-DD] \
    [--by harness|model|project] [--currency USD|CNY] [--json]
bin/session-manager inbox pricing show|update|check   # check：列出近 N 天未定价的模型及其 token 量
```

周期口径：

- **周**按 ISO 周，周一到周日，与现有日报中「周一」开头的约定一致；**月**按自然月。时区用本机时区，与现有归日规则一致。
- 输出内容：本期合计（三类 token、金额、任务数），按天序列，按维度拆分，与上一周期的环比，以及未定价 token 的占比。
- 周期中已定稿的日子直接读报告缓存，当天实时计算。月视图最多读 31 份报告，约 30ms × 31，可以接受。
- 金额只能由同一批数据推出：未定价部分不计入金额合计，单独显示为「另有 X tokens 未定价」，不按零计也不猜测。

## 界面

日报窗口顶部增加两个分段控件：**日 / 周 / 月** 与 **USD / CNY**，选择记忆在 `UserDefaults`。

| 视图 | 内容 | 图表（Swift Charts） |
|---|---|---|
| 日（现有详情扩展） | 汇总卡新增金额大字；任务卡、来源行、项目行都附金额 | 现有图表保留，新增 model 占比条 |
| 周 | 本周合计（token + 金额 + 环比）→ 按天分布 → harness / model 占比 → Top 项目 | 7 天堆叠柱，可切换 token / 金额两种度量；环形或横向条图 |
| 月 | 同周视图，按天柱改为 28–31 根；另加「本月累计」折线 | 堆叠柱 + 累计折线，二者共用 x 轴 |
| 总览 | 今日卡增加金额；热力图可切换为按金额着色 | 热力图金额分级按分布另行校准，不复用 token 阈值 |

交互延续现有约定：所有图表都有自定义悬浮卡片。金额悬浮格式为 `合计 $12.34（输入 $a · 缓存 $b · 输出 $c）`，另附「未定价 X tokens」。点击柱子可以跳到对应那天的详情。

金额格式：USD 为 `$1,234.56`，CNY 为 `¥8,765.43`；小于 0.01 显示 `<$0.01`。Python 与 Swift 各实现一份 `money_text`，按现有 `token_text` 的做法双端测试。

## 实施分期

| 阶段 | 内容 | 验收 |
|---|---|---|
| P1 数据层 | 六个来源补 model 与四项原始 token；schema v8；不丢数据的迁移；model 归一化 | 抽样 3 天，与 v7 的三类合计逐位一致（Zcode 保持现有 <2.2% 容差）；构造「来源已清理」夹具验证迁移不降级 |
| P2 计价 | `pricing.json`、override、计价纯函数、汇率、`inbox usage` 与 `pricing check` | Pi、OpenCode 的原生 cost 与我方计价在同模型同价时一致；有未定价模型的夹具正确降级 |
| P3 界面 | 周期与币种切换、周 / 月视图、Swift Charts、金额悬浮 | 真实数据截图核对；`money_text` 双端边界测试 |
| P4 组件数据出口 | 给桌面组件用的快照（见组件方案） | 由组件方案验收 |

## 已确认决策（2026-09-23）

1. **金额口径**：只做「API 标价等价金额」，界面注明「按标价估算」，不做订阅分摊。
2. **汇率**：单一手动汇率，界面注明汇率与日期。
3. **价格表更新**：自动拉取，初始每周一次，连续 2 次内容相同则间隔翻倍，最长 1 个月（细则见「价格表」一节）。

## 已知边界

- 等价金额 ≠ 实际账单；订阅、免费额度、促销价都不体现。
- Codex 每次请求都重发上下文，缓存读取量极大，因此金额主要取决于缓存读取单价。缓存价是否准确，对 Codex 的金额影响最大。
- 跨来源的缓存语义本就不同（见日报规范「已知边界」），金额同样继承这种系统偏差。
- 首版不做阶梯价。
