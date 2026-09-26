# 任务：让「最近任务的今日 token 未传入写入器」（V080-R9）可被编译与测试拦住

状态：完成（2026-09-26，执行方 OpenCode）。评审方派发时误要求带 `Defect: V080-R9`，PR #35 因此在修复证据检查失败并关闭；去掉该行重新提交，规则见规范 §2「修复提交」。原：待执行（2026-09-26 由设计与评审方按[任务模板](../templates/task.md)写成，来源：task-002 的升级包、方案 §13 E2；用户 2026-09-26 同意为此改产品代码）。执行方由评审方派发。

## 背景

V080-R9 的缺陷形态是：调用方没把今日用量传给写入器，组件「最近任务」的今日 token 一直为空。现状有两处让它无法被拦住：

- `native/WidgetSnapshotWriter.swift` 的 `update(rows:todayUsage:)` 给 `todayUsage` 设了默认值 `[:]`，漏传照样能编译。
- 用量映射的键（`"\(provider):\(sessionID)"`）在 `native/InboxModels.swift` 写入、在写入器里读取，两处各写一遍；查找逻辑内联在单例里，Swift 策略测试够不到。

## 目标终态

- `update(rows:todayUsage:)` 的 `todayUsage` 不再有默认值；现有两处调用（`InboxModels.swift` 的 `loadTodayUsage` 与列表刷新）以及写入器内部的重放调用都显式传入。漏传在编译 App 时报错。
- `native/InboxPolicy.swift` 新增纯函数，作为用量键与查找的唯一实现：
  - 生成键：输入 provider 与会话 ID（可空），输出映射键。`InboxModels.swift` 建映射、写入器查找都改用它。
  - 查找：输入 provider、会话 ID 与映射，输出今日 tokens 与金额（找不到时两者为空）。写入器生成最近任务条目时改用它。
- `tests/InboxPolicyTests.swift` 新增断言：
  - 同一 provider 与会话 ID 生成的键能查到对应用量，tokens 与金额都带出来。
  - 会话 ID 为空、provider 不同、会话 ID 不同时查不到。
- 行为不变：组件条目的其他字段、签名与刷新策略都不改。

## 非目标与禁止动作

- 不改快照格式（`native/Shared/WidgetSnapshot.swift`）、刷新策略、日报的 Python 代码。
- 不修改已有测试，只新增。
- 不编辑 `harness/`。回放用例由评审方加入：在 PR 里写明注入点，包括文件、修复后的原文、退回成的写法，以及应当失败的检查（`swift-policy`）。
- 不改写已推送历史，不推 tag，不合并、不批准 PR。

## 前置条件（不满足就停下报告）

- `python3 harness/git_guard.py status` 显示已安装；`bin/verify` 在当前 main 上通过（macOS，含 swift-policy）。

## 验收

| 编号 | 验收内容 | 证据类型 | 覆盖（测试名或验证步骤） |
|---|---|---|---|
| — | 漏传 `todayUsage` 编译失败 | 编译 | 执行方在 PR 里写明：临时删掉一处调用的参数后 `python3 scripts/build_inbox_app.py` 的报错摘录（验证后恢复，不提交） |
| — | 键与查找有纯函数和 Swift 测试 | 单测 | `tests/InboxPolicyTests.swift` 中的新增断言 |
| — | App 能编译，现有检查通过 | verify | `bin/verify --full` |

## 风险等级

预判 R3，因为改到 `native/WidgetSnapshotWriter.swift`。以 CI 中 `harness/risk.py` 的判定为准。回滚方式：revert 该 PR。

## 预算（超出即停止，把升级包交给评审方）

- CI 轮次：最多 3 轮
- 同一失败的重试：最多 2 次，每次必须带新的信息

## 交付要求

- 提交前 `bin/verify` 与 `bin/verify --full` 通过。
- PR 按 `.github/pull_request_template.md` 填写，不手写「已通过」。

## 升级包（卡住时填写）

- 当前状态与目标差距：
- 已尝试的方案与结果：
- 证据（失败日志、测试输出）：
- 可选方案与推荐：
- 需要评审方或用户决定的具体问题：
