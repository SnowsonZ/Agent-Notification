---
task: t-2026-09-14-inbox-gui-acceptance
status: in_progress
hop: 1
owner: zcode
ack: 接手统一收件箱 v0.2 的 GUI 验收闭环：先核对/恢复 SessionInbox 辅助功能授权并重跑 Zcode 打开路径，再验收成功打开自动标已处理与点击系统通知后的导航。
updated: 2026-09-14T03:57:30+08:00
---

## Intent

完成统一收件箱 v0.2 剩余 GUI 验收：恢复 Zcode 辅助功能授权后验收"成功打开自动标已处理"的完整闭环，并独立验收点击系统通知后的导航。

## Constraints

- 自动和手动标记已处理必须携带点击时的 revision 校验；成功打开自动确认，失败保留未读，打开期间新回复不被旧动作吞掉（AGENTS.md、docs/specs/unified-inbox.md）。
- AXPress 返回成功不等于界面动作完成，必须核对后置状态；AppKit 前台状态更新需主 run loop，不用阻塞 sleep 轮询。
- 工具拒绝某 App 时不用另一控制技术绕过；完成独立工作，准备可审阅探针交用户或允许环境验证。
- Claude/Codex 的 URL 打开以 OS 接受分发为成功，不能写成目标 UI 已显示。
- 诊断日志不输出会话正文、输入内容、原剪贴板或凭据；不读取通知数据库、不监听私人通知。
- 模拟测试项必须明确标注"模拟事件"，验收后隐藏，不当真实待办处理。
- 跨终端问题需在真实拓扑下测（如跨标签页），不能只在目标终端内部验证。

## Done

- [x] 统一收件箱 v0.2 已交付并提交（68eec7f）：全部分页、Pi 标题、应用图标、成功打开自动处理、系统通知已实现；55 项 Python 检查通过，Swift 策略测试覆盖分页与通知去重（产物：scripts/inbox*.py、native/SessionInbox.swift、native/InboxPolicy.swift、tests/test_inbox_improvements.py、tests/InboxPolicyTests.swift）。
- [x] GUI 已验收：列表翻页第 1→2 页、通知请求被系统接受、模拟事项手动已处理闭环（证据：docs/specs/unified-inbox.md「验证证据」「v0.2 补充验收」）。

## Next

- [ ] 核对并恢复 `build/SessionInbox.app` 的辅助功能授权。验收条件：系统设置 → 隐私与安全性 → 辅助功能中清除旧/重复 SessionInbox 条目（ad-hoc 重签可能使旧授权失效）并保留当前 bundle 授权后，`python3 scripts/zcode_focus.py <真实task_id>` 不再报辅助功能拒绝并完成一次真实导航；若仍拒绝，`build/zcode-focus --self-test` 与 `scratch/zcode-focus-latest.json` 的 stage 日志用于定位，必要时用 `tccutil` 重置后由用户重新授权。
- [ ] 注入明确标注"模拟事件"的测试项（经 InboxStore API，指向真实 Zcode 任务），验收：App 内打开成功后按点击时 revision 自动确认；打开期间新回复到达不确认；失败保留未读。验收后移除模拟项。
- [ ] App 运行中注入新的模拟项触发系统通知，点击通知核对导航动作与自动确认（spec 明确该路径未独立验收）。验收完成后更新 docs/specs/unified-inbox.md「v0.2 补充验收」状态，必要时同步 README。

## Decisions

- 建新棒而非恢复旧棒：仓库无任何历史任务棒（git 历史无 .agents/ 路径、无 trailer），用户确认此前的接入缺口与方案验证项均已解决，当前待办以 68eec7f 的 spec 遗留为准。（2026-09-14, zcode）
- 本任务范围限定为 v0.2 两条未验收项 + 模拟事项自动处理闭环；五路宿主全面复验、Codex/iTerm2 导航新验收不在本任务内。（2026-09-14, zcode）

## Open questions

- computer-use 对系统通知横幅的点击能力待实测；若无法操作通知中心，按禁区规则准备探针交用户点击确认。
- 辅助功能授权恢复后是否要求重新构建 App（重签改变授权归属）待核对。

## Do not

- 不用另一控制技术绕过 computer-use 对某 App 的拒绝。
- 不关闭 iTerm API 认证、不设置允许任意应用访问。
- 不把 OS 分发成功或 AXPress 返回值写成 UI 验收通过。
- 不提交会话正文、凭据或用户私人配置。
