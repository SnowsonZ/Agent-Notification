# 任务：补齐变异测试暴露的 9 处测试缺口与 2 个暂缓回放项的回归测试

状态：完成（2026-09-26，PR #30，执行方 OpenCode）；V080-R9 按升级包转为[任务 003](task-003-r9-today-usage.md)。原：2026-09-26 由设计与评审方按[任务模板](../templates/task.md)写成，来源：方案 §13 E5、E2。前置：PR #27（变异测试新目标）已合并。执行方由评审方派发。

## 目标终态

- 下列 9 处存活变异被新测试杀死（`python3 harness/mutate.py --only "过去日报告合并,金额计算,用量汇总"` 中不再存活），三个目标得分上升，并用 `--update` 写回基线：
  - **报告合并**（`scripts/daily_report.py` `_merge_day_tasks`）
    - 合计相等但类别不同时，采用重扫记录（`<=` 不能改成 `<`）。
    - 只在现有报告中、合计为 0 的恢复任务丢弃，除非 `fidelity` 为 `unavailable`。
    - 合并后每一类恰等于 max(现有, 重扫)，不只是「合计与明细一致」。
  - **金额计算**（`scripts/usage_cost.py`）
    - `merge_cost` 多次累加的金额。
    - `model_cost` 中 cache_write 的计价项。
    - 缺价时写入 notes 的提示。
  - **用量汇总**（`scripts/usage_report.py`）
    - `period_bounds` 与 `previous_anchor` 的周、月边界：跨月、月末、周一。
    - `_merge_models` 的原生金额累加。
    - 原始模型名列表去重、最多 5 个。
- 两个暂缓回放项有了回归测试，评审方据此把它们从 `DEFERRED` 移入回放用例：
  - **V080-R7 热力图按金额着色全为 0**：经 `generate_overview` 的夹具有多天、带模型与价格的用量；断言 `days[].cost_level` 按金额分级，不全为 0。
  - **V080-R9 最近任务的今日 token 从未填充**：写入器在收到 `todayUsage` 时，把它写进快照条目。优先用 `native/InboxPolicy.swift` 中可测的纯函数与 Swift 测试（`tests/InboxPolicyTests.swift`）覆盖。若写入路径没有可测的纯函数，停下写升级包，不要改产品代码。

## 非目标与禁止动作

- 不改产品代码。测试发现产品行为与规范不符时，停下写升级包。
- 不修改已有测试，只新增。
- 不编辑 `harness/`（缺口清单除外）。回放用例由评审方加入：在 PR 里写明每个回放项的注入点，包括文件、修复后的原文、退回成的写法。
- 不改写已推送历史，不推 tag，不合并 PR。

## 前置条件（不满足就停下报告）

- `python3 harness/git_guard.py status` 显示已安装；`bin/verify` 在当前 main 上通过。
- main 上的 `harness/mutate.py` 已有「金额计算」「用量汇总」两个目标。

## 验收

| 编号 | 验收内容 | 证据类型 | 覆盖（测试名或验证步骤） |
|---|---|---|---|
| — | 上述 9 处存活变异被杀死，三个目标得分上升，基线已更新 | 变异 | `python3 harness/mutate.py --only …` 输出，摘录在 PR |
| — | V080-R7、V080-R9 有回归测试，注入点写在 PR | 单测 | 执行方填写测试名 |

## 风险等级

预判 R3，因为要更新 `harness/mutation-baseline.json`。以 CI 中 `harness/risk.py` 的判定为准。回滚方式：revert 该 PR。

## 预算（超出即停止，把升级包交给评审方）

- CI 轮次：最多 3 轮
- 同一失败的重试：最多 2 次，每次必须带新的信息

## 交付要求

- 提交前 `bin/verify` 与 `bin/verify --full` 通过；PR 附 `mutate.py` 前后得分对比（原样摘录）。
- PR 按 `.github/pull_request_template.md` 填写，不手写「已通过」。

## 升级包（卡住时填写）

- 当前状态与目标差距：
- 已尝试的方案与结果：
- 证据（失败日志、测试输出）：
- 可选方案与推荐：
- 需要评审方或用户决定的具体问题：
