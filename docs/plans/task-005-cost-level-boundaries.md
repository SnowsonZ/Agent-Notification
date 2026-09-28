---
task: T005
class: K2
risk: R0
designer: claude-code
size: small
architecture: false
spec_refs: []
no_spec_reason: 补已有行为的边界测试（杀死变异存活），不新增需求；金额热力分级在规格正文中定义，无独立验收编号
budget:
  wall_clock_min: 30
  ci_rounds: 2
  retries: 2
  tokens: null
rollback: git revert
---

# 任务：补金额热力分级在 1 元以下的边界测试

状态：待派发（2026-09-28 由设计评审方写成，来源：待办 B21 端到端验收）。

## 目标终态

`scripts/daily_report.py` 的 `cost_level` 在金额或最低阈值小于 1 时的行为有测试覆盖，`python3 harness/mutate.py --only 热力金额分级` 中不再有存活变异。现状：第 54 行 `if cny_amount <= 0 or not thresholds or thresholds[0] <= 0:` 里两处 `0` 改成 `1` 都不会让任何测试失败（21 个变异中存活 2 个）。

需要测试证明的行为（按现有代码，不改变行为）：

- 金额大于 0 但小于 1（如 0.5）、阈值为正时，按阈值分级，不是 0 级。例：阈值 `[0.8, 2, 5]` 时 0.5 为 1 级。
- 最低阈值大于 0 但小于 1（如 `[0.6, 0.9, 3]`）时照常分级。例：0.7 为 2 级、4 为 4 级。
- 金额为 0 或负数、阈值为空或最低阈值为 0 时仍为 0 级（已有测试覆盖，不要改动）。

## 非目标与禁止动作

- 不改产品代码。测试发现现有行为与上面描述不符时，停下写升级包。
- 不修改或删除已有测试，只新增；可以追加到 `tests/test_daily_report.py`，或新建测试文件。
- 不更新 `harness/mutation-baseline.json`（判定器，由评审方在合并后更新）。

## 验收

| 编号 | 验收内容 | 证据类型 | 覆盖（测试名或验证步骤） |
|---|---|---|---|
| 不挂规格：补已有行为的边界测试 | 金额与最低阈值小于 1 时按阈值分级；新测试通过 | 单测 | `python3 -m unittest tests.test_daily_report`（新增用例名写在 PR） |
| 不挂规格：补已有行为的边界测试 | 两处存活变异被杀死 | CI 断言 | `python3 harness/mutate.py --only 热力金额分级`，杀死 21/21 |

## 步骤与提交顺序

| # | 改动 | 涉及文件 | 验证方式 | 对应验收 |
|---|---|---|---|---|
| 1 | 新增金额与最低阈值小于 1 的分级测试 | `tests/test_daily_report.py` | `bin/verify`；`python3 harness/mutate.py --only 热力金额分级` 无存活 | 两行均是 |
