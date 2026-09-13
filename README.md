# Session Manager

管理本机 Codex、Claude Code、Zcode 桌面会话，以及 iTerm2 中的 Pi、Kimi CLI 会话：汇总需要介入的状态，并定位到原会话。用户已恢复优先接入 iTerm2，并反馈 API 枚举成功。

已有可运行的原生会话收件箱 v0.1：待处理列表、全部会话、搜索、标记已处理、菜单栏计数和现有会话跳转。用 `bin/session-manager app` 打开。完整使用与验收边界见 [统一收件箱](docs/specs/unified-inbox.md)。

## 新环境准备

当前构建目标为 Apple Silicon、macOS 14+，需要 Python 3.11+ 和 Xcode Command Line Tools；本机验证使用 Python 3.12。在项目根目录执行：

```sh
python3 -m venv scratch/iterm-probe-venv
scratch/iterm-probe-venv/bin/python -m pip install -r requirements.txt
mkdir -p build
xcrun swiftc native/ZcodeFocus.swift -o build/zcode-focus
python3 scripts/build_inbox_app.py
```

完成后，`bin/session-manager inbox setup` 会追加本项目 Claude/Kimi 观察 hooks，保留其他配置；`bin/session-manager app` 打开列表。iTerm2 API 和 Zcode 辅助功能权限需在实际运行环境中授权。已有本机安装不需要重复初始化；构建前先退出本项目 App。

## 文档入口

| 文档 | 用途 | 状态 |
|---|---|---|
| [统一收件箱 v0.1](docs/specs/unified-inbox.md) | 当前使用入口、数据来源、配置和已知边界 | App 已编译；真实导入、列表与已处理 UI 验证通过 |
| [市场调研](docs/research/2026-09-13-market-survey.md) | 现成工具比较与选型依据 | 前期调研，早于宿主范围最终确认 |
| [会话收件箱方案](docs/plans/session-inbox.md) | 五路接入、通知抓取评估、实现建议和验收 | 当前方案，待接入验证；不是已验证规格 |
| [本机接入验证](docs/research/2026-09-13-integration-probe.md) | 当前版本证据、可复现检查与联调缺口 | Claude 正常结束闭环通过一次；其余状态和来源待测 |
| [剩余接入排查](docs/research/2026-09-14-integration-findings.md) | Pi/Kimi 运行时、Codex/Zcode 兼容读取、Zcode UI 身份比对 | iTerm 枚举和跨标签页聚焦经用户实测通过；待 agent 归属绑定 |
| [系统 Terminal 备选](docs/plans/terminal-migration.md) | 无额外依赖的备用终端探针 | 暂缓，当前优先 iTerm2 |
| [CLI 会话绑定](docs/specs/cli-session-binding.md) | 受管理启动、存活锁、会话 ID 和前台进程组校验 | Pi/Kimi 正常聚焦和退出后拒绝均获用户实测 |
| [跨终端误判修复](docs/research/2026-09-14-cross-terminal-ownership.md) | 首次 managed focus 报错的原因与回归 | 32 项检查通过，用户重试成功 |
| [Zcode 原生导航](docs/specs/zcode-native-navigation.md) | task ID 查标题、AX 搜索、复制任务路径校验 | 首次真实导航及 ID 后置核验通过，用户和本地日志共同确认 |

当前可用入口：`bin/session-manager pi`、`bin/session-manager kimi` 启动受管理会话，`bin/session-manager list` 查看绑定，`bin/session-manager focus RUN_ID SESSION_ID` 校验并跳转。须在 iTerm2 内由用户执行。Kimi 已配置生命周期 observer hooks，Claude 已配置收件箱观察 hooks；Pi 无全局配置修改。

Zcode 入口为 `bin/session-manager zcode-focus TASK_ID`，已通过一次真实导航及 ID 核验；`--describe` 只解析任务元数据。

运行 `python3 scripts/probe_capabilities.py` 可重新检查安装包静态能力；该脚本不修改 agent 配置。

运行 `python3 scripts/claude_locator.py <runtime-or-desktop-session-id>` 可只读解析已有 Claude Desktop 会话链接。解析失败或歧义会明确返回，不猜测最近会话。运行 `python3 -m unittest discover -s tests -v` 验证解析和事件接收脚本。

`scripts/capture_hook.py <provider> --output-dir <directory>` 从 stdin 接收诊断事件，只保存会话 ID、事件类型及可选 turn ID，不保存正文，不自动安装 hooks。已验证 Claude 真实会话与 Kimi 本地协议 fixture 的投递；provider 参数不代表全部适配完成。隔离测试配置和原始事件保留在 `scratch/`。

## 文档归档约定

所有后续项目文档沿用下列约定，优先更新现有主题，避免在根目录散放报告。

| 目录 | 内容 | 命名 |
|---|---|---|
| `docs/research/` | 市场调研、接口调查、实验结论与来源 | `YYYY-MM-DD-topic.md`，注明调研日期与证据边界 |
| `docs/plans/` | 设计方案、取舍、未决项和实施路径 | `topic.md`，注明当前阶段，持续更新 |
| `docs/specs/` | 经确认、足以执行的需求和接口规格 | `topic.md`，关联来源方案与验收项 |
| `docs/decisions/` | 已采纳的重要架构决策及理由 | `NNNN-topic.md`，注明状态和被替代关系 |

后两类按需创建；不为空目录提前搭架子。一次性输出放 `scratch/`，有长期价值的证据整理后进入对应文档。新增、迁移或替代文档时同步更新本索引及相关链接；历史文档保留背景，但以索引标明的当前方案为准。不使用 `final-v2` 等文件名表达状态。
