<!--
可验证交付（机制文档见 [delivery-harness](https://github.com/SnowsonZ/delivery-harness/blob/main/README.zh-CN.md)）：通过与否只认机器输出。
- 不要手写「测试通过」「CI 通过」「已修复」：CI 的 harness job 会在 job summary 里生成风险等级与修复证据。
- 修复缺陷的提交在说明末尾带 `Defect: <来源>-<序号>`（纯规格修复写 `Defect: <编号> doc`）。
- 声明行为不变的重构：每个提交带 `Risk: R1`，由 risk.py 核对，不满足按 R2。
-->

## 任务

<!-- 派发给执行方的任务先按 docs/templates/task.md 写任务书并合并（计划并入其「步骤与提交顺序」）。 -->

- 任务书：docs/plans/task-<编号>-<名字>.md（设计方自行实现时随本 PR 提交；只有纯记录类改动可以写「无」）

- 目标终态：
- 对应验收编号（docs/specs，新增条目写进规格验收表）：
- 非目标：
- 待办（docs/plans/backlog.md）：关闭 B?／新增 B?／无（关闭的在本 PR 里移到「已关闭」）

## 证据

- CI 运行（当前 head）：<!-- 粘贴本 PR 最新提交的 build / harness 运行链接 -->
- 风险等级与修复证据：见 harness job summary（机器生成）

## 需要人工验收的部分

<!-- 只列机器无法判定的项（真机 UI、系统授权等），写明步骤与预期；没有写「无」。
     `bin/harness acceptance --manual` 列出现役人工验收清单，从中挑出本 PR 涉及的编号。 -->
