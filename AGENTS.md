# Session Manager

本地 macOS 会话收件箱：汇总 Claude/Codex/Zcode 桌面任务和受管理的 Pi/Kimi CLI 会话，并定位回原会话。当前实现与使用入口见 README.zh-CN.md（README.md 为英文简版），能力边界以 docs/specs/ 为准。

## 开发入口

- Python 后端和适配器在 `scripts/`，Swift 原生界面与 Zcode 导航在 `native/`，统一 CLI 在 `bin/session-manager`。
- Python 检查：`python3 -m unittest discover -s tests -v`。
- Zcode 辅助程序：`xcrun swiftc native/ZcodeFocus.swift -o build/zcode-focus`；无 UI 自检：`build/zcode-focus --self-test`。
- 原生收件箱构建：`python3 scripts/build_inbox_app.py`；先退出正在运行的本项目 App，再覆盖可执行文件。
- 新环境依赖与启动步骤见 README.zh-CN.md；不要把现有本机虚拟环境视为仓库自带依赖。

## 文档与发布

- 长期调研进 `docs/research/YYYY-MM-DD-topic.md`；方案进 `docs/plans/`；现役合同及验收进 `docs/specs/`；重大已采纳决策按需放 `docs/decisions/`。
- 新增、迁移或替代文档时更新 README 索引；历史排错记录不能覆盖最新验收状态。
- 发布版本号必须先与用户确认后再打 tag，不自行指定 minor/patch 档位；tag 推送即触发远端发布。
- 打 tag 前同步 `scripts/build_inbox_app.py` 的 `CFBundleShortVersionString` 与 `CFBundleVersion`，两处不能只改其一。

## 本地环境与清理

- `scratch/` 放临时实验与运行证据，`build/` 放产物；两者、虚拟环境、数据库、凭据及完整用户会话正文不提交。
- 清理 `scratch/`（含 `git clean`）必须保留 `scratch/iterm-probe-venv`：它是开发包 App 与 CLI 的运行时解释器，不是实验产物。被删后收件箱报 "The file “python” doesn’t exist."、CLI 报 "Missing runtime"；按 README「快速上手」两条命令重建，数据在 `~/.local/state/session-manager` 不受影响。
- 不把用户本机的 Claude/Kimi 配置复制进仓库；安装观察 hooks 保留既有配置并保持幂等。

## 身份与定位

- 标题只用于展示或找候选；定位必须核对稳定会话 ID。Zcode 以复制出的任务路径作后置比对，不能把工作区打开当成精确定位。
- Pi/Kimi 跳转保留 run_id、session_id、运行锁、TTY 前台进程组和 live pane 校验；退出或复用后旧绑定必须拒绝。
- 来源身份混合、格式未知或定位有歧义时明确降级；不要猜测归属或弱化检查来消除报错。

## 状态与运行时

- macOS 跨会话查询前台组用 `ps` 的 pgid/tpgid；`tcgetpgrp` 只适用于调用方自己的控制终端。
- AppKit 前台状态更新需要服务主 run loop，不要用阻塞 sleep 轮询；AXPress 返回成功不等于界面动作已完成，检查后置状态。
- 本轮结束不等于需求成功；运行状态和未读状态独立保存。成功打开自动确认，打开失败不清未读；自动和手动标记已处理必须使用 revision 校验，避免吞掉新事件。

## 数据与隐私

- 默认只保留管理所需元数据；诊断日志不输出聊天正文、输入内容、原剪贴板或凭据。

## 验证边界

- 区分单元测试、模拟协议、真实 PTY、用户 UI 验收；编译通过和 OS 分发成功不能写成完整导航已验证。
- 新的回归测试覆盖实际失败拓扑；例如跨标签页问题不能只在目标终端内部测试。
- 工具拒绝某 App 时不通过另一控制技术绕过；完成独立工作，准备可审阅探针，由用户或允许的环境验证。
- 改 CLI 生命周期读 `docs/specs/cli-session-binding.md`；改 Zcode 导航读 `docs/specs/zcode-native-navigation.md`；改队列或来源读 `docs/specs/unified-inbox.md`。
