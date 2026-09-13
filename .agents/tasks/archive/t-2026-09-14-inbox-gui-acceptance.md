---
task: t-2026-09-14-inbox-gui-acceptance
status: done
hop: 3
owner:
ack: 接手统一收件箱 v0.2 的 GUI 验收闭环：Zcode 辅助功能授权与"成功打开自动标已处理"已验收；通知点击按用户决定改为仅置前收件箱并经验证，任务完成。
updated: 2026-09-14T04:26:00+08:00
---

## Intent

完成统一收件箱 v0.2 剩余 GUI 验收：恢复 Zcode 辅助功能授权后验收"成功打开自动标已处理"完整闭环，并验收点击系统通知后的行为。

## Constraints

- 标记已处理必须带点击时 revision；成功打开自动确认，失败保留未读（AGENTS.md）。
- AXPress 成功不等于动作完成，须核对后置状态；工具拒绝某 App 时不换技术绕过，留可审阅探针由用户验证。
- URL 打开以 OS 接受分发为成功，不写成 UI 已显示；不提交会话正文或凭据。
- 模拟项必须标注"模拟事件"，验收后清理。

## Done

- [x] Zcode 辅助功能授权验收通过（2026-09-14）：模拟事项经 App「打开会话」首次因搜索框焦点竞态被拒且保留未读，重试后原生助手完成搜索→选中→复制任务路径身份核验（selection_identity_matches=true）并置前目标任务，unread 1→0 自动确认。证据：scratch/zcode-focus-latest.json（2026-09-13T20:04:52Z，exit_code 0）。
- [x] 通知点击语义改为"仅置前收件箱，不自动打开目标会话、不改变未读"（用户决定，与"仅置前不自动标已读"原则一致）：移除 pendingNotificationOpen，经菜单栏图标视图桥接 openWindow 兼容窗口已关场景；版本 0.2.1 重建，55 项测试通过。用户点击验证：收件箱打开、无 Zcode 导航、未读保留。提交 d42d923。
- [x] spec 已同步（docs/specs/unified-inbox.md：状态、通知语义、v0.2 补充验收证据）；模拟项已标记已读并隐藏（App 自动撤回已投递模拟通知）。

## Next

- (none)

## Decisions

- 通知点击只打开收件箱、不跳转目标会话：用户明确决定（2026-09-14），自动打开路径整体移除而非加开关。
- 横幅点击验收交用户手点：computer-use 工具往返 8–10 秒赢不了横幅约 5 秒存活窗口，通知中心面板无法自动打开（控制中心 AX 不可读）；符合"工具到不了的动作由用户验证"边界。（2026-09-14, zcode）
- ad-hoc 重签可能使辅助功能授权失效：v0.2.1 重建后若「打开会话」报 accessibility_permission_required，在系统设置重新添加一次即可，属已知运维项而非回归。（2026-09-14, zcode）

## Open questions

- (none)

## Do not

- 不用另一控制技术绕过 computer-use 对某 App 的拒绝；不把 OS 分发成功或 AXPress 返回值写成 UI 验收通过。
