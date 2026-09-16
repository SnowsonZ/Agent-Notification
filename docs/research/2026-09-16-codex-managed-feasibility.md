# Codex 受管理接入可行性论证（结论：暂缓）

状态：2026-09-16 论证完成。结论：**技术上可行（通道需拼装），但成本高于当前收益，暂缓实施**；
现有的进程/文件匹配聚焦已确定性覆盖实际场景。若出现挂起会话误聚焦的实际困扰，或 2026-09-19
codex 额度恢复后可完成 notify 端到端验证，再按本方案实施。

## 背景

claude/codex 的 CLI 会话以无绑定方式接入收件箱（claude 走用户级 hooks，codex 走 rollout 被动
扫描），跳转走「命令行匹配 → 会话文件 fd 匹配 → 新标签恢复」。与受管理四家（pi/kimi/
opencode/agy）的差别只在校验强度：受管理有运行锁 + 前台进程组校验，能拒绝挂起（Ctrl+Z）
或 pane 被复用的会话；无绑定的进程匹配只验证进程/文件存在性。

用户要求论证 codex 能否升级为受管理。初版结论「codex 无事件通道、硬性不可行」经复核**不成立**，
予以修正。

## 实测到的通道（v2.x，2026-09-16）

1. **notify 配置**：`config.toml` 的 `notify = [命令, 参数...]` 在回合结束时拉起外部命令（推送通道）。
   本机已在用：`turn-ended` → `SkyComputerUseClient`（Computer Use 集成）。**codex 只支持单条
   notify 命令**——接入需以链接脚本替换或转发，会触碰既有集成。
2. **plugin 子命令**：marketplace 插件体系（add/list/marketplace/remove），生命周期钩子能力未验证。
3. **app-server 守护进程**：`codex app-server` / `agents` 子命令暗示存在会话注册表（未深挖）。
4. **rollout 文件创建时机（关键实测）**：TUI 打开瞬间即创建 `rollout-<时间>-<会话UUID>.jsonl`，
   首行 session_meta 含 id/cwd/originator（camelCase 无关，字段为 snake_case protojson），**早于任何
   模型调用**——「包装器监听新 rollout 文件做会话登记」这条通道成立，无需模型调用即可验证。

## 可行的受管理方案（如实施）

- 启动：`bin/session-manager codex` 包装器（managed_run 同合同：pane/tty/pgid + 运行锁）。
- 会话登记：包装器内监听 CODEX_HOME sessions 目录的新 rollout 文件（创建时间晚于启动时刻且
  cwd 匹配即归属；同目录并发启动存在歧义边界）→ 注册绑定 session_id。
- 回合结束：notify 链接脚本——先上报 session-manager（Stop 事件），再转发给既有 notify 消费者
  （保住 Computer Use 集成）。需端到端验证 notify 载荷是否含 conversation id。
- 退出：包装器兜底 SessionEnd（managed_run 既有逻辑）。
- 跳转：iterm_probe 三重校验聚焦（与四家一致）。

## 成本与收益

- 成本：notify 链要改用户既有集成；包装器需常驻监听；notify 端到端验证被额度阻断至 09-19；
  相比四家「单一官方钩子」，这是拼装方案，长期维护面更大。
- 收益：仅「挂起会话的强校验拒绝」一个边缘场景——运行中会话的聚焦，现有 fd 匹配（lsof 实测
  codex 持有 rollout fd）已确定性覆盖；关窗后恢复两者行为一致。

## 决策

保持现状（无绑定 + 进程/fd 匹配聚焦）。触发重估的条件：挂起误聚焦成为实际困扰，或额度恢复
后完成 notify 验证且发现其余生命周期事件（会话开始/等待权限）也可达——后者将同时补齐 codex
的等待/失败状态盲区。

关联：[统一收件箱规格](../specs/unified-inbox.md)、[CLI 绑定规格](../specs/cli-session-binding.md)
