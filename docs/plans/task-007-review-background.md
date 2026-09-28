---
task: T007
class: K7
risk: R3
designer: claude-code
size: medium
architecture: false
spec_refs: []
no_spec_reason: 护栏与流程改动，验收写在 docs/specs/delivery-harness.md 的约定与验证状态中，没有产品规格验收编号
budget:
  wall_clock_min: 240
  ci_rounds: 3
  retries: 2
  tokens: null
rollback: git revert（评审方回到 PR #63 合并时的状态）
---

# 任务：独立评审补齐、只读锁定与后台自动评审

状态：设计评审方自行实现（2026-09-29，方案 B）。来源：PR #63 合并时漏掉的提交 `87acfcf`；#62 试行中发现的评审方可执行 bash；用户 2026-09-29 同意「只读锁定 + 撤回材料写进提示词 + 后台自动评审」。

## 目标终态

- PR #63 合并的是 `83972c9`，此后推送的 `87acfcf`（OpenCode 评审方、已合并 PR 的评审基点、CI 材料、B33、试行 1/3 记录）未进入 main；本任务把它补上。
- OpenCode 评审方真正只读：关掉 bash、子代理、网页与写入类工具（`plan` 代理本身只禁止编辑，2026-09-28 实测它在评审中运行了 PR 的测试与 `gh`）。
- 评审移到后台：`bin/dispatch review --pending` 逐个评审「带 needs-independent-review、CI 已全部通过、当前 head 尚无独立评审结论」的开着的 PR；`--watch` 每隔若干分钟评审一轮，派发的停机标记存在时退出。不并行（多个 OpenCode 同时运行会冲突）。
- 材料仍由评审方按文件读取（「材料写进提示词」实测没有变快，已撤回）。

## 非目标与禁止动作

- 不把评审放进 GitHub Actions（评审方与模型账号在本机）；不在本任务中安装 launchd 常驻（持久的本机配置，需用户另行决定）。
- 不改变评审结论的地位：仍只供用户参考。

## 前置条件（不满足就停下报告）

- OpenCode 能在本机无界面运行（标准输入关闭时，2026-09-28 实测可用）。

## 验收

| 编号 | 验收内容 | 证据类型 | 覆盖（测试名或验证步骤） |
|---|---|---|---|
| 不挂规格：护栏 | OpenCode 评审方关掉命令与写入类工具 | 单测 | `tests.test_harness_review_independent.ReviewerTest.test_opencode_tools_are_locked_to_read_only` |
| 不挂规格：护栏 | 待评审的判定：CI 全部通过、当前 head 未评过；逐个评审；停机标记即退出 | 单测 | `tests.test_harness_review_independent.BackgroundTest` |
| 不挂规格：护栏 | 已合并 PR 的评审基点与 CI 材料（随 87acfcf 补上） | 单测 | `tests.test_harness_review_independent.ReviewBaseTest` |
| 不挂规格：护栏 | 实际查询线上待评审 PR | 真实数据 | `bin/dispatch review --pending` |

## 步骤与提交顺序

| # | 改动 | 涉及文件 | 验证方式 | 对应验收 |
|---|---|---|---|---|
| 1 | 补上 87acfcf | `harness/review.py`、`harness/review_prompt.md`、`harness/rules.toml`、`harness/dispatch.py` | `bin/verify` | 第 3 行 |
| 2 | 只读锁定与后台评审 | `harness/review.py`、`harness/dispatch.py` | `bin/verify` | 第 1、2、4 行 |
| 3 | 规范、README、待办同步 | `docs/specs/delivery-harness.md`、`harness/README.md`、`docs/plans/backlog.md` | `bin/verify` | — |
