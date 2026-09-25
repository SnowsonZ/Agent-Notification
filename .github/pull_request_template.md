<!--
可验证交付（docs/plans/verifiable-delivery.md）：通过与否只认机器输出。
- 不要手写「测试通过」「CI 通过」「已修复」：CI 的 harness job 会在 job summary 里生成风险等级与修复证据。
- 修复缺陷的提交在说明末尾带 `Defect: <来源>-<序号>`（纯规格修复写 `Defect: <编号> doc`）。
- 声明行为不变的重构：每个提交带 `Risk: R1`，由 risk.py 核对，不满足按 R2。
-->

## 任务

- 目标终态：
- 对应验收编号（docs/specs）：
- 非目标：

## 证据

- CI 运行（当前 head）：<!-- 粘贴本 PR 最新提交的 build / harness 运行链接 -->
- 风险等级与修复证据：见 harness job summary（机器生成）

## 需要人工验收的部分

<!-- 只列机器无法判定的项（真机 UI、系统授权等），写明步骤与预期；没有写「无」。 -->
