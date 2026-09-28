---
task: T006
class: K7
risk: R3
designer: claude-code
size: medium
architecture: false
spec_refs: []
no_spec_reason: 护栏与流程改动，验收写在 docs/specs/delivery-harness.md 的约定与验证状态中，没有产品规格验收编号
budget:
  wall_clock_min: 480
  ci_rounds: 3
  retries: 2
  tokens: null
rollback: git revert（删去独立评审工具与校准集；auto-merge 不再打 needs-independent-review 标签）
---

# 任务：独立评审试行——非设计方只读评审 R2 以上的 PR，并在校准集上量出可信度

状态：设计评审方自行实现（2026-09-28，方案 B：任务书与实现同一个 PR）。来源：目标态设计 8.3、第十六节阶段二 P7；待办 B27；用户决定 5（先在 3 个 R2 PR 上试行）。

## 目标终态

- `bin/dispatch review <PR>` 在只读的评审工作区（`<仓库名>-review`）中，由不是该任务设计方的评审方评审 PR：只给任务书、PR 描述、diff 与证据包，不给执行过程；结论（通过 / 不通过 / 需用户验收与发现列表）以固定格式评论到 PR，并去掉 `needs-independent-review` 标签。
- 评审方可选 Pi、Codex、Claude Code；默认由 `harness/rules.toml [review] reviewer` 指定（用户 2026-09-28 定为 Pi + glm-5.3），结构上只读，环境里没有 GitHub 凭据；与执行方同一宿主时在评论中标出。
- 评审方本身失败（报错、超时、额度用尽）记为「评审失败」：不评论为结论、不计入校准。
- auto-merge 对 R2 及以上的 PR 打 `needs-independent-review`。
- 校准集 `evals/review/samples.json`：已知有问题的改动（回放用例在 main 上重新注入）与已知良好的已合并 PR；`review.py calibrate` 逐个落盘、可续跑、连续失败即停，算出 TPR 与 TNR，结果入库。
- 设计方自行实现的任务（任务书随本 PR 提交）不要求运行记录，仍要求每个提交带 `Task:`（`run_check.py`）。

## 非目标与禁止动作

- 不让评审结论替代用户审批，不据此自动合并。
- 不在本 PR 中决定是否常态化：3 个 R2 PR 试行后由用户决定。
- 不接入 OpenCode 评审方：2026-09-28 实测 OpenCode 在本机无界面运行卡在初始化，待用户排查后再接。

## 前置条件（不满足就停下报告）

- 至少一种评审方能在本机无界面运行并返回可解析的结论。

## 验收

| 编号 | 验收内容 | 证据类型 | 覆盖（测试名或验证步骤） |
|---|---|---|---|
| 不挂规格：护栏 | 结论解析、分离规则、只读参数、无凭据环境、评论格式、失败处理、校准打分与续跑 | 单测 | `tests.test_harness_review_independent` |
| 不挂规格：护栏 | 设计方自行实现时不要求运行记录、仍要求 `Task:` | 单测 | `tests.test_harness_run_check.RunCheckTest.test_designer_self_implementation_needs_trailers_but_no_record` |
| 不挂规格：护栏 | R2 以上打待评审标签 | CI 断言 | `tests.test_harness_review_independent.WorkflowTest` |
| 不挂规格：护栏 | 在校准集上量出 TPR 与 TNR 并入库 | 真实数据 | `evals/review/results/` 下的结果文件 |
| 不挂规格：护栏 | 3 个 R2 以上 PR 有非设计方的评审结论 | 人工 | 合并后对后续 R2 以上 PR 运行 `bin/dispatch review`，由用户对照（待办 B27） |

## 步骤与提交顺序

| # | 改动 | 涉及文件 | 验证方式 | 对应验收 |
|---|---|---|---|---|
| 1 | 评审工具、提示词、分离规则、失败处理、校准与续跑 | `harness/review.py`、`harness/review_prompt.md`、`harness/dispatch.py`、`harness/rules.toml`、`evals/review/samples.json` | `bin/verify` | 第 1、4 行 |
| 2 | 自行实现的任务不要求运行记录 | `harness/run_check.py` | `bin/verify` | 第 2 行 |
| 3 | auto-merge 打待评审标签 | `.github/workflows/auto-merge.yml` | `bin/verify` | 第 3 行 |
| 4 | 校准结果、规范、README、待办同步 | `evals/review/results/`、`docs/specs/delivery-harness.md`、`harness/README.md`、`docs/plans/backlog.md` | `python3 harness/review.py calibrate --reviewer pi` | 第 4 行 |
