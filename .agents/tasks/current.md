---
task: t-2026-09-14-inbox-gui-acceptance
status: in_progress
hop: 2
owner:
ack: 接手统一收件箱 v0.2 的 GUI 验收闭环：Zcode 辅助功能授权与"成功打开自动标已处理"已验收通过；通知点击语义经用户决定改为仅置前收件箱，待用户点击一次验证新行为后收尾。
updated: 2026-09-14T04:18:08+08:00
---

## Intent

完成统一收件箱 v0.2 剩余 GUI 验收：恢复 Zcode 辅助功能授权后验收"成功打开自动标已处理"的完整闭环，并验收点击系统通知后的行为。

## Constraints

- 自动和手动标记已处理必须携带点击时的 revision 校验；成功打开自动确认，失败保留未读，打开期间新回复不被旧动作吞掉（AGENTS.md、docs/specs/unified-inbox.md）。
- AXPress 返回成功不等于界面动作完成，必须核对后置状态；AppKit 前台状态更新需主 run loop，不用阻塞 sleep 轮询。
- 工具拒绝某 App 时不用另一控制技术绕过；完成独立工作，准备可审阅探针交用户或允许环境验证。
- Claude/Codex 的 URL 打开以 OS 接受分发为成功，不能写成目标 UI 已显示。
- 诊断日志不输出会话正文、输入内容、原剪贴板或凭据；不读取通知数据库、不监听私人通知。
- 模拟测试项必须明确标注"模拟事件"，验收后隐藏，不当真实待办处理。

## Done

- [x] 统一收件箱 v0.2 已交付并提交（68eec7f）：分页、Pi 标题、应用图标、成功打开自动处理、系统通知已实现；55 项 Python 检查通过（产物：scripts/inbox*.py、native/SessionInbox.swift、tests/）。
- [x] Zcode 辅助功能授权验收通过（2026-09-14）：模拟事项经 App「打开会话」首次因搜索框焦点竞态被拒且保留未读（失败路径 ✓），重试后助手完成搜索→选中→复制任务路径身份核验（selection_identity_matches=true）并置前目标任务，unread 1→0 自动确认。证据：scratch/zcode-focus-latest.json（20:04:52Z，exit_code 0）+ inbox.sqlite。已写入 docs/specs/unified-inbox.md。
- [x] 通知点击语义按用户决定改为"仅置前收件箱，不自动打开目标会话、不改变未读"：移除 pendingNotificationOpen 机制，经菜单栏图标视图桥接 openWindow 兼容窗口已关场景；版本升 0.2.1 并重建。55 项测试仍通过。通知投递在重建后仍被系统接受（notificationSeen 含新 token）。

## Next

- [ ] 用户点击一次通知（横幅或通知中心里的"zcode-sim · 等待输入"）验证新行为：收件箱窗口置前、不发生 Zcode 导航（scratch/zcode-focus-latest.json 无新记录）、模拟项未读保留。通过后把模拟行（inbox.sqlite id=13c2ef7e0201276e0e702590，provider=zcode-sim）置 hidden=1 清理，并确认收件箱回到无待处理。
- [ ] 注意：重建后 ad-hoc 重签可能使辅助功能授权再次失效；若「打开会话」报 accessibility_permission_required，在系统设置重新添加一次即可，不影响通知点击验证。

## Decisions

- 建新棒而非恢复旧棒：仓库无任何历史任务棒（git 历史无 .agents/ 路径、无 trailer），用户确认此前的接入缺口与方案验证项均已解决，当前待办以 68eec7f 的 spec 遗留为准。（2026-09-14, zcode）
- 通知点击只打开收件箱、不再自动跳转目标会话：用户明确决定（"通知点击打开SessionInbox就好"），与 spec 既有原则"仅将 App 置前不应自动标已读"一致；自动打开路径整体移除而非加开关。（2026-09-14, zcode 执行）
- 通知点击验收不追求横幅 5 秒存活窗口内的自动化点击：computer-use 工具往返 8–10 秒赢不了竞态，通知中心面板也无法自动打开（控制中心 AX 不可读）；改由用户手点，符合"工具到不了的动作由用户验证"边界。（2026-09-14, zcode）

## Open questions

- 重建后辅助功能授权是否需要用户重新添加一次待实测（见 Next 第 2 条）。

## Do not

- 不用另一控制技术绕过 computer-use 对某 App 的拒绝。
- 不关闭 iTerm API 认证、不设置允许任意应用访问。
- 不把 OS 分发成功或 AXPress 返回值写成 UI 验收通过。
- 不提交会话正文、凭据或用户私人配置。
