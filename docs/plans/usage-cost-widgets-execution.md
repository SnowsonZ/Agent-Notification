# 用量金额与桌面组件：执行计划

状态：待执行（2026-09-23 定稿）。基线：`main` / `485228f`（v0.7.5）。
合同：[用量金额规范](../specs/usage-cost.md)、[桌面组件规范](../specs/desktop-widgets.md)。**实现以这两份规范为准**；本文只规定顺序、分工、交付物和评审关口。

## 0. 执行者须知

- 开工前先读：[AGENTS.md](../../AGENTS.md)、上面两份规范、[工作日报规范](../specs/daily-report.md)、[探针记录](../research/2026-09-23-widget-adhoc-probe.md)。
- **规范写得不清楚，或者与实际数据不符时，不要自行猜测。** 先在 PR 描述的「偏离与疑点」中写明证据和你的建议，再按以下规则处理：
  - 偏离不改变口径（例如字段路径有出入），可以先按建议实现。
  - 偏离会改变口径（金额、token 合计、确认语义、隐私边界），停下等评审决定。
- **硬约束**（均出自 AGENTS.md 或规范）：
  - 所有检查按 CI 同样的口径通过：`python3 -W error::ResourceWarning -m unittest discover -s tests -v`、`ruff check scripts tests`，以及 Swift 策略测试。
  - 诊断日志不输出正文、标题原文、URL 参数原文或凭据。
  - `scratch/`、`build/` 不提交；清理 `scratch/` 时保留 `scratch/iterm-probe-venv`。
  - 构建前先退出正在运行的「会话通知」。
  - **不改版本号、不打 tag**，发版由用户确认。
  - 不修改用户本机的 Claude、Kimi 等配置。
  - 不向真实终端输入命令；需要真实导航的验证，交给用户做 UI 验收。
- **验证边界**：交付说明里要分清单元测试、真实数据脚本和用户 UI 验收。编译通过不等于导航已验证；只在 macOS 27 上跑过，不得写成「已支持 macOS 14」。

## 1. 工作包与依赖

```
A 数据层 ──► B 计价 ──► C 日报界面
   │           └──────► D4 用量组件
   └ (无依赖) D0 AppIntents 构建探针 ─► D1 组件构建 ─► D2 快照/刷新 ─► D3 URL 跳转 ─► D4 组件界面
```

A 与 D0–D3 可以并行。D4 中的 `usage` kind 依赖 B4 的 `inbox usage` 输出。

### A 数据层（Python，规范 §2–§3）

| 编号 | 内容 | 主要文件 | 完成条件 |
|---|---|---|---|
| A1 | `canonical()` 规范名 | 新建 `scripts/model_names.py` | U4 单测通过 |
| A2 | 六个来源产出 `model_raw` 与四项原始 token；`_Buckets` 按任务、按 model 累加（跨零点分摊同样适用于四项）；Zcode 改为逐请求计量，加对账与回退；Pi、OpenCode 累加 `native_cost_usd` | `scripts/daily_report.py` | U3 单测通过；现有日报测试保持通过，需要改断言的，逐条在 PR 中说明原因 |
| A3 | schema v8、`totals.models`、v7 迁移（先备份，不降级） | `scripts/daily_report.py` | U2 单测通过 |
| A4 | 真实数据比对：写独立脚本 `scratch/check_v8_parity.py`，比较 v7 备份与 v8 的三类合计 | 仅 `scratch/` | U1：3 个过去日逐位一致（Zcode 在容差内），结果写进 PR |

### B 计价（Python，规范 §4–§6）

| 编号 | 内容 | 主要文件 | 完成条件 |
|---|---|---|---|
| B1 | 价格文件：`scripts/pricing/cny-official.json`，覆盖本机实际出现的国产模型（至少 GLM-5.3、GLM-5.3-Flash、MiniMax-M3、deepseek-v4-pro），每条带官方 `source_url` 与 `checked_at`；`scripts/pricing/litellm-snapshot.json` 由 B3 的转换器生成 | `scripts/pricing/` | 价格来自官方页面；**查不到的模型不要编造价格，留空，让它显示为未定价**，并在 PR 中列出 |
| B2 | `usage_cost.py`：价格查找（三层 × 候选名）、历史价、金额对象、换算、`money_text`、fx 读写 | 新建 `scripts/usage_cost.py` | U5、U7、U8（Python 侧）单测通过 |
| B3 | `pricing_fetch.py`：拉取、转换、防护、历史追加、自适应节奏状态机 | 新建 `scripts/pricing_fetch.py` | U6 单测通过（网络全部 mock，状态机用注入的时钟测试） |
| B4 | CLI：`daily-report` 输出附 cost；新增 `inbox usage`、`inbox pricing show/check/update/fx` | `scripts/inbox.py` | U9 单测；各命令的 `--help` 可用；`inbox usage --period all --json` 在本机真实数据上 <1 秒（缓存命中时） |
| B5 | 文档：`daily-report.md` 更新到 v8 口径；README 中英文的命令表 | `docs/`、`README*.md` | 与实现一致 |

### C 日报界面（Swift，规范 §7）

| 编号 | 内容 | 主要文件 | 完成条件 |
|---|---|---|---|
| C1 | 解码 cost、models；实现 `moneyText` 与换算纯函数，放在 `InboxPolicy.swift` 并单测（用例与 Python 相同） | `native/DailyReportModels.swift`、`native/InboxPolicy.swift`、`tests/InboxPolicyTests.swift` | U8 双端一致 |
| C2 | 周期与币种分段控件；周、月视图（Swift Charts）；日视图补金额和 model 占比；按金额着色的热力图 | `native/DailyReport*.swift`、`native/DayDetailView.swift`，按需新建 `native/PeriodReportView.swift` | U10：真实数据截图，放 `scratch/`，路径写进 PR |
| C3 | App 启动时及每 6 小时后台执行 `inbox pricing update --auto`，不阻塞主 run loop | `native/SessionInbox.swift` 或 `InboxModels.swift` | 手动确认日志中只有到期时才发请求 |

### D 桌面组件（规范全文）

| 编号 | 内容 | 主要文件 | 完成条件 |
|---|---|---|---|
| **D0** | **构建探针（先做）**：在 CI（macos-26）上，用 `swiftc` 加 `appintentsmetadataprocessor` 构建一个带 `AppIntentConfiguration` 的最小组件，并验证产物里有 `Metadata.appintents`。临时 workflow 分支即可，不合入主线 | 临时分支 | 在 PR 或调研文档中记录可用的完整命令行。**失败时**：配置式组件改为只在 Xcode 环境构建的路径，或整体退回降级方案。两者都要停下来由评审决定，不要自行选择 |
| D1 | 组件构建接入 `build_inbox_app.py`（开发包与 standalone 两种形态），加 CI 断言 | `scripts/build_inbox_app.py`、`.github/workflows/build.yml`、`native/Widget/`、`native/Shared/` | W8；CI 断言全部通过 |
| D2 | `WidgetSnapshotWriter`、`WidgetRefreshPolicy`（纯函数）、原子写入、`hide_titles` | `native/Shared/WidgetSnapshot.swift`，App 侧新建文件 | W3 单测、W6 |
| D3 | URL scheme 注册与处理；URL 解析写成纯函数 | `build_inbox_app.py`（Info.plist）、App 侧 | W4 解析单测 |
| D4 | 三个 kind 的界面；过时提示；图标资源进 appex；降级的静态组件 | `native/Widget/*.swift` | W2、W5、W7；各尺寸截图 |

## 2. 里程碑与评审关口

每个里程碑一个 PR，基于最新 `main`。评审通过后再开始下一个依赖它的里程碑，不依赖它的工作可以继续。

| 里程碑 | 包含 | 评审重点 |
|---|---|---|
| **M1 数据与计价** | A1–A4、B1–B5 | 三类合计与 v7 一致；迁移不降级；六个来源的拆分；查找顺序与历史价；状态机；价格来源真实；没有正文泄漏 |
| **M2 组件骨架** | D0–D3（可以与 M1 并行提交） | 入口链接与 plist；entitlements 只有两项；快照不含正文，`hide_titles` 生效；确认语义与现有 `open` 一致；非法 URL 被拒绝 |
| **M3 日报界面** | C1–C3 | 数字与 CLI 一致；双端 `money_text` 一致；注脚口径；不阻塞主线程 |
| **M4 组件界面** | D4 | 数字与 `inbox usage` 一致；过时和缺快照时的行为；降级构建；用户 UI 验收清单 |

## 3. 每个 PR 必须附带

1. **变更摘要**：只写影响行为和口径的部分。
2. **验收对照表**：列出本 PR 覆盖的 U / W 编号，逐项写明证据（测试名、脚本输出、截图路径）和证据类型（单测 / 真实数据 / 用户 UI）。
3. **偏离与疑点**：与规范不同的地方，以及未覆盖的范围。
4. **命令输出**：全量测试、ruff、Swift 测试的结果摘要，包括通过数量。
5. 需要用户 UI 验收的，附上操作步骤清单：每步写清楚操作什么、预期看到什么。

## 4. 风险与预案

| 风险 | 预案 |
|---|---|
| Zcode 的 `model_usage` 与 `turn_usage` 系统性对不上 | 按规范回退到轮级数据并记为 `unknown`；在 PR 中报告回退比例。超过 20% 时由评审决定是否调整口径 |
| 国产模型官方价格查不到或口径不一（例如按次计费、套餐价） | 该条目留空，显示为未定价；在 PR 中列出，由用户决定 |
| LiteLLM 的键名与本机 model 名对不上，导致大量未定价 | 用 `pricing check` 列出未命中项；由用户在 `override.json` 中补 `aliases`，或评审后补进随包文件。**不得放宽匹配规则** |
| D0 失败 | 见 D0 的完成条件：停下，由评审决定 |
| 旧系统（macOS 14/15/26）上组件行为不同 | 记录为未验证，不阻塞合入 |

## 5. 不在本次范围

阶梯计价；订阅费分摊；按日历史汇率；组件内切换按钮；锁屏或 iOS 组件；为 Release 做公证。
