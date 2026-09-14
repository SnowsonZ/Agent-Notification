# 工作日报 v3（token 三类口径）

状态：v3 已实现并通过 84 项 Python 检查（含 13 项日报）与 Swift 策略检查；真实本机数据 182 天补录、总览/详情截图核对完成。**准确性核对**：独立重算脚本（scratch/check_token_accuracy.py）与报告逐项对比，Codex/Claude/Pi/Kimi 三类逐位一致，Zcode 在跨零点分摊容差内（<2.2%，归日语义差非算术差；证据 scratch/2026-09-15-token-accuracy-check.md）。

## 度量口径（v3 核心）

**token 三类，三类之和 = 合计，参与一切比较与计算（热力分级/排名/占比/趋势），三类在所有展示处一并呈现：**

- **输入** = 新鲜输入 + 缓存写入
- **缓存** = 缓存读取（上下文回放，单独列示）
- **输出** = 输出 + reasoning

取消/出错轮次照计（消耗即计数）。悬浮为**自定义多行卡片**（onHover + 覆盖层实现，鼠标进入即显，不经系统 tooltip 延迟通道；2026-09-15 按用户模板定稿）。样式：第一行日期/名称，随后 `缓存：`/`输入：`/`输出：` 三行（该顺序为用户指定），按需附 `合计：`、任务数、轮次与时间；所有图表（热力格/趋势柱/占比条分段/图例行/项目行/任务卡/节奏带色块）均有悬浮。

| 来源 | 有效性 | 输入 | 缓存 | 输出 | 归日方式 |
|---|---|---|---|---|---|
| Zcode | 逐轮精确 | `input − cache_read + cache_creation` | `cache_read` | `output + reasoning` | 轮区间按天比例分摊 |
| Codex | 请求级增量 | `last_token_usage.input + cache_write` | `cached_input_tokens` | `output + reasoning` | 每条增量按时间戳归日 |
| Claude | 逐条消息（2026-09-14 探针验证：5/5 桌面会话经 cliSessionId 命中 `~/.claude/projects/<hash>/<sessionId>.jsonl`，证据 scratch/2026-09-14-claude-transcript-token-probe.md） | `input_tokens + cache_creation_input_tokens` | `cache_read_input_tokens` | `output_tokens` | 消息时间戳 |
| Pi | 逐条 assistant 消息 | `usage.input + cacheWrite` | `usage.cacheRead` | `output + reasoning` | 消息时间戳 |
| Kimi | 逐轮（`~/.kimi-code/sessions/**/agents/*/wire.jsonl` 的 `usage.record`） | `inputOther + inputCacheCreation` | `inputCacheRead` | `output` | `time` 毫秒 |

- 任务记录 `fidelity ∈ {exact, unavailable}`：`unavailable`（如 Zcode 老库无 token 列、桌面会话无转写）只列出、标〔无 token〕，不参与合计。
- Zcode `sess_subagent_*` 子代理轮次的 token 经 `session.parent_id` **归属到父任务**（独立消耗不遗漏也不与父轮次重复），不计入父任务轮次数；无法归属的孤立子代理轮次宁可不计。
- 任务记录字段：`provider/session_id/title/project/first_at/last_at/input_tokens/cache_tokens/output_tokens/total_tokens/turns/state/fidelity`。无时长字段；一天节奏带保留，只表达时间分布。

## 热力分级（按本机 91 个活跃日毛总量分布校准）

0 无记录 / L1 <20M / L2 <100M / L3 <400M / L4 ≥400M（分布 47/20/12/12）。accent 蓝由浅到深，今日格描边，悬浮显示「日期 · 合计 X（输入 A · 缓存 B · 输出 C）· N 个任务」。

## 触发与固化

- 常驻 App 内每天 20:00 触发（60 秒粒度判定，纯函数在 InboxPolicy：过 20:00 且 `日期#schema版本` 标记不匹配即生成；失败静默重试）。
- **生成标记带 schema 版本**（v2 教训：v1 标记会挡住 v2 当天补跑）。
- 过去日固化为 `~/.local/state/session-manager/reports/YYYY-MM-DD.{json,md}`（`version: 3`，0700 目录，原子写入）；版本不匹配或缺失自动重算（一次性补录 182 天约 4 秒）。当日报告始终实时计算，overview 不固化今天。

## 界面

- **总览**（日报窗口 600×780，可缩至 560）：今日概览卡（合计大字 + 三类行/任务/轮次/活跃来源）→ 27 周热力图 → 最近 7 天三类层叠柱状图（输入/缓存/输出分色带图例，柱顶标合计）→ 双栏（来源占比 · 近 7 天 | Top 5 项目 · 近 7 天，均按三类合计，行内附三类明细）→ 口径注脚。
- **当日详情**（点热力格进入）：汇总卡（来源占比环形 + 最近 7 天迷你柱 + 三类行）→ 一天节奏带 → 来源占比（分段条 + 逐来源三类行）→ 项目条（含三类行）→ 任务卡（合计主值 + 比例条 + 起止/轮次/状态 + 三类行；unavailable 标〔无 token〕）。
- **全部图表与数字均有悬浮**（.help）：日期格/趋势柱/占比条分段/图例行/项目行/任务卡。
- 数字格式 `tokenText`（k/M/B）：<1k 原值；k/M 段 mantissa<100 保留小数否则取整；B 段两位小数；末尾零去除。
- 通知：「日报已生成 · 今日 token 合计 X · N 个任务」，点击只开日报窗口。

## CLI

```sh
bin/session-manager inbox daily-report [--date YYYY-MM-DD]              # 单日报告（实时重算，幂等落盘）
bin/session-manager inbox daily-report --overview [--days 182] [--top 5]  # 热力图 + 近 7 天 Top 项目（自动补录）
```

## 隐私边界

只读取数值字段（token 数、时间戳）与元数据（标题、项目、状态）；转写/会话文件中的正文不解析、不存储、不输出。

## 验证证据

- 13 项日报单测：各来源三类算术（缓存读剔除、取消轮计入、子代理归属父任务、缺列回退 unavailable）、Codex 增量按时间归日、Pi/Kimi/Claude 解析、跨零点分摊、旧版本报告自动失效重刷、热力阈值、Top5 近 7 天窗口、markdown。全量 84 项通过；Swift 策略检查含 `heatLevel(tokens:)` 与 `tokenText` 边界。
- 真实数据（2026-09-14）：当日三类合计 556M（输入 150M · 缓存 405M · 输出 1.4M）/ 27 任务；近 7 天来源占比 Codex 937M(41%)、Zcode 719M(32%)、Claude 608M(27%)——与各来源原始数据抽查一致（Codex 大盘来自单会话 416 次请求、每次重发约 70 万上下文，属口径内真实消耗）。
- **准确性核对（2026-09-15）**：独立重算脚本与报告逐项对比，Codex/Claude/Pi/Kimi 三类逐位一致；Zcode 在跨零点分摊容差内 <2.2%（归日语义差）。证据 scratch/2026-09-15-token-accuracy-check.md。
- 调度修复实测：旧版本生成标记不再挡住新版本当天补跑（App 重启后报告自动升级重生成）。
- 总览/详情截图核对通过（scratch/daily-report-v3-overview.png、daily-report-v3-day.png）。

## 已知边界

- 各来源缓存语义不同（Zcode 新鲜输入约 2%，Codex 约 50%），跨来源三类合计对比有系统偏差；口径在注脚中明示。
- Codex 逐请求重发上下文会显著放大"新鲜输入"，单日 token 量主要由请求次数与上下文长度驱动。
- Claude 依赖 `~/.claude/projects` 转写存在；转写被清理的历史日会降级为〔无 token〕。
- 热力阈值按当前工作强度校准，工作模式显著变化时可再调整（Python/Swift 两处常量）。

实现：[聚合模块](../../scripts/daily_report.py)、[CLI 接线](../../scripts/inbox.py)、[策略纯函数](../../native/InboxPolicy.swift)、[原生界面与调度](../../native/SessionInbox.swift)、[日报测试](../../tests/test_daily_report.py)、[策略测试](../../tests/InboxPolicyTests.swift)。
