# 可验证交付 harness

状态：现役（2026-09-25 起）。方案与阶段见 [可验证交付方案](../plans/verifiable-delivery.md)，基线见 [2026-09-25 基线评审](../review/2026-09-25-harness-baseline.md)。

harness 只依赖 Python 标准库，放在仓库顶层 `harness/`，不进发布包（构建只拷 `scripts/` 与 `bin/session-manager`）。改动 `harness/`、`.githooks/`、`.github/`、`.claude/`、`.opencode/` 属于 R3，必须由用户批准。

## 1. 命令

| 命令 | 作用 | 谁在何时运行 |
|---|---|---|
| `bin/verify` | 工具版本、git 守卫、lint、仓库卫生、Python 测试、Swift 测试（仅 macOS） | 所有人；pre-push 自动运行 |
| `bin/verify --quick` | 工具版本、lint、仓库卫生 | pre-commit 自动运行 |
| `bin/verify --full` | 默认档 + 事故回放 | macOS CI（`--strict --full`）；改动测试或产品逻辑后 |
| `bin/verify --strict` | 被跳过的检查算失败 | macOS CI |
| `python3 harness/acceptance.py [--manual]` | 规格验收编号 ↔ 测试映射检查；列出人工验收清单 | verify 各档；写 PR 的人工验收部分时 |
| `python3 harness/replay.py [--list]` | 把历史缺陷注入工作区副本，对应测试必须失败；列出基线覆盖 | `verify --full` |
| `python3 harness/mutate.py [--check / --update]` | 定向变异测试，得分与 `harness/mutation-baseline.json` 比较（只升不降） | 每周 quality workflow；补测试后 |
| `python3 harness/evidence.py --base origin/main` | 按 `Defect:` trailer 生成修复证据，验证修复前测试失败、修复后通过 | CI harness job；实现方自查 |
| `python3 harness/risk.py --base origin/main` | 按改动路径判定 R0–R3 | CI harness job |
| `python3 harness/hygiene.py --staged / --range BASE` | 禁止路径、超大文件、凭据、新增行中的本机路径 | pre-commit、pre-push、CI |
| `python3 harness/git_guard.py install` | 把 `core.hooksPath` 指向 `.githooks`（幂等，不覆盖已有设置） | 每个新环境一次 |
| `python3 harness/release_check.py --tag vX.Y.Z` | tag 与构建版本一致、构建号递增、tag 在 main 上 | 推 tag 时 CI 自动运行 |

`verify` 终端只打印结论，完整输出在 `build/verify/<检查名>.log`，汇总在 `build/verify/summary.json`。判断通过只认退出码和 CI 上当前 head 的运行。

## 2. 约定

- **修复提交**：说明中加一行 `Defect: <编号>`（放在哪一段都行，不依赖 git trailer 规则）；纯规格或文档修复写 `Defect: <编号> doc`。测试里用同一个编号标注（注释或说明均可），evidence 据此定位测试。
- **缺陷编号全局唯一**：`<来源>-<序号>`，例如 `V080-R17`（v0.8.0 交付评审第 17 项）、`REV0921-R2`（2026-09-21 评估第 2 项）、`H0925-4`（2026-09-25 harness 建设中的发现）。不再使用裸 `R17`。
- **每个修复都要能被回放**：修复评审发现的缺陷时，在 `harness/replay_cases.py` 加注入用例（或守卫测试）；无法回放的写进 `DEFERRED` 并说明原因。
- **已知缺陷登记**：发现但暂不修的缺陷写成确定性测试并标 `@unittest.expectedFailure`，说明里写编号与待决事项；修好后它会「意外通过」并报错，逼着移除登记。
- **验收编号**：每份规格的验收表含「证据类型」「覆盖」两列，编号前缀按规格区分——W（桌面组件）、U（用量金额）、DR（工作日报）、IN（统一收件箱）、CB（CLI 会话绑定）、ZN（Zcode 导航）。可自动化条目必须有真实存在的测试；暂缺的登记在 `harness/acceptance-gaps.txt`（带原因，只能缩减）。
- **任务与计划**：任务按 [task.md](../templates/task.md) 写（终态、非目标、编号验收、风险、预算、升级包）；R2 及以上先按 [plan.md](../templates/plan.md) 写计划交评审。
- **测试的几种形态**：种子固定的性质测试（`tests/test_properties.py`，`PROPTEST_SEEDS=N` 放大搜索）、架构适应度（`tests/test_architecture.py`，同一口径只实现一次）、CLI 黄金快照（`tests/test_golden.py`，有意改变时 `UPDATE_GOLDEN=1` 重新生成，按 R2 评审）。
- **行为不变的重构**：每个提交带 `Risk: R1`；risk.py 核对只改产品代码、已有测试与黄金快照零改动，否则按 R2。
- **不手写通过状态**：PR 与交付说明里的「测试通过」「CI 通过」「已修复」一律由 CI 的 harness job summary 与 run 链接代替。

## 3. 风险等级

| 等级 | 判定（`harness/rules.toml [risk]`） | 合并 |
|---|---|---|
| R0 | 说明性文档；只新增测试 | 门禁全绿即可自动合并 |
| R1 | 声明 `Risk: R1` 且机器核对通过 | 自动合并 + 抽样审计 |
| R2 | 产品代码、现役规格、AGENTS.md；改动或删除已有测试；改动黄金快照 | 评审方评审 + 用户看证据包后合并 |
| R3 | 护栏、CI 与发布、依赖、报告迁移、快照与隐私、用户配置安装、运行时入口 | 用户批准 |

## 4. 三层护栏

| 层 | 内容 | 能否被绕过 |
|---|---|---|
| 服务端 | `.github/rulesets/main.json`：main 禁删除与改写，必须经 PR，`build` 与 `harness` 检查必须通过；`release-tags.json`：`v*` tag 禁移动与删除；release job 走 environment `release`，由用户批准 | 本机 Agent 无法绕过 |
| git | `.githooks/pre-commit`（保护分支上禁止提交、暂存区卫生、快速 verify）；`pre-push`（禁推 main 与 tag、禁强制推送、本次推送的改动卫生、完整 verify）；`reference-transaction`（禁本地改写或删除 main、移动或删除 tag，含 filter-repo） | `--no-verify`、改 `core.hooksPath` 可绕过；Agent 层拒绝这些命令 |
| Agent | `harness/command_guard.py`：拒绝改写历史、强推、推 main 与 tag、建删 tag、跳过钩子、设置覆盖变量、`reset --hard`、不带 venv 排除的 `git clean -x`、删除工作区外路径、`gh release` 与删除 CI 记录；`--role implementer` 另禁编辑判定器与护栏 | 取决于各家 hook 能力 |

覆盖变量只供人使用（Agent 层会拒绝设置它们的命令）：

| 变量 | 放开 |
|---|---|
| `HARNESS_ALLOW_MAIN=1` | 在 main 上提交、推送 main |
| `HARNESS_ALLOW_TAG=1` | 推送 tag（发版） |
| `HARNESS_ALLOW_REWRITE=1` | 本地改写或删除 main、移动或删除 tag、强制推送 |
| `HARNESS_SKIP_VERIFY=1` | 钩子中跳过 verify |

Agent 层按角色接入：

| Agent | 角色 | 接入 |
|---|---|---|
| Claude Code | 设计与评审 | 项目级 `.claude/settings.json` 的 PreToolUse（Bash），自动生效 |
| Codex | 设计与评审 | 用户级配置，需手动添加（见 §5），不自动改用户配置 |
| OpenCode | 执行 | 项目级插件 `.opencode/plugin/harness-guard.js`，`--role implementer` |
| Pi | 执行 | 未接入：扩展的拦截 API 未实测，靠 git 与服务端两层 |
| Zcode | 执行 | 宿主没有工具调用 hook，靠 git 与服务端两层 |

## 5. 一次性设置（用户）

1. 本机环境：`scratch/iterm-probe-venv/bin/python -m pip install -r requirements-dev.txt`，然后 `python3 harness/git_guard.py install`。`bin/verify` 会检查这两步。
2. GitHub ruleset：仓库 Settings → Rules → Rulesets → New ruleset → Import a ruleset，依次导入 `.github/rulesets/main.json` 与 `.github/rulesets/release-tags.json`。
3. 发版审批：Settings → Environments → New environment，名称 `release`，勾选 Required reviewers 并加上自己；只有一个维护者时不要勾选 Prevent self-review。
4. Codex（可选）：在 `~/.codex/hooks.json` 的 `PreToolUse` 中加一项，命令为 `python3 <仓库>/harness/command_guard.py --format claude --role designer`，然后在 Codex 里执行 `/hooks` 信任它（改动脚本后需重新信任）。
5. 设置后自检（只看、不改）：Rulesets 列表中两条规则均为 Active；任一 PR 页面上 `build` 与 `harness` 标为 Required。不要用真实推送 main 的方式测试：规则没生效时会真的改掉 main。

## 6. 验证状态

按 AGENTS.md「验证边界」区分证据类型。

| 能力 | 证据 | 状态 |
|---|---|---|
| verify 三处同口径 | 云端 Linux 实跑；macOS CI `--strict` 实跑 Swift 检查（run 36148783352） | ✅ 本机 macOS 待用户首次运行 |
| evidence / risk / hygiene | 单测 + 临时 git 仓库场景（`tests/test_harness.py`） | ✅ |
| CI harness job | 首次运行即拦下测试数据里的真实用户目录（run 36148783352） | ✅ |
| git 钩子 | 临时仓库真实 git 回放：main 上提交、amend、移动与删除 tag、filter-repo、推 main、推 tag、强推、推送卫生（`tests/test_harness_guard.py`） | ✅ Linux；macOS 由 CI 覆盖 |
| Claude Code PreToolUse | 2026-09-25 本仓库会话中真实拦截 3 次（均为命令文本含危险字样的误报，拦截本身生效） | ✅ 真实会话 |
| OpenCode 插件 | node 加载插件并调用 `tool.execute.before` 的单测 | ⚠️ 真实 OpenCode 加载待实测 |
| Codex hooks | 载荷解析单测（含列表形式命令） | ⚠️ 真实 Codex 待实测 |
| ruleset 与 environment | 配置文件与 workflow 一致性单测（`tests/test_harness_release.py`） | ⚠️ 待用户导入后按 §5 第 5 步自检 |
| 发版核对 | 临时仓库单测：版本不一致、构建号未递增、tag 不在 main | ✅ 单测；首次真实发版时再确认 |
| 事故回放 | 18 个注入用例在 Linux 上实跑（15 个）、Swift 3 个由 macOS CI `--strict --full` 运行；回放自检（注入点未过期、基线全覆盖）在默认档 | ✅ Linux；Swift 部分待 macOS CI |
| 修复证据 | 本 PR 的 H0925-1、H0925-2、H0925-5 三个修复提交由 evidence 生成「修复前失败、修复后通过」 | ✅ |
| 变异测试 | 4 个目标的基线得分（见基线评审 §6） | ✅ Linux 实跑 |
| 验收映射 | 6 份规格 69 条编号：可自动化 54 条中 53 条有测试、1 条登记缺口，19 条进入人工清单；检查器在 verify 各档运行 | ✅ |
| Swift 回放与 zcode 自检 | macOS CI `--strict --full`（run 36152060546）通过，Swift 注入在 strict 下不可跳过 | ✅ |

## 7. 已知边界

- **身份不可区分**：Agent 与用户共用同一个 GitHub 身份时（本会话触发的 CI 记录的 actor 即为用户），服务端分不清谁在合并。R2 以上「由用户合并」目前靠约定，彻底解决需要给执行者单独身份（机器账号或 GitHub App），待用户决定。
- **命令守卫按字符串匹配**：命令文本里出现危险字样就会拒绝，哪怕只是被 echo 或写进注释。误报的处理方式是换一种写法（例如用编辑工具改文件），不是放宽规则。
- **Zcode 与 Pi 无 Agent 层拦截**：它们设置覆盖变量或使用 `--no-verify` 时，本机两层都挡不住，只有服务端兜底。
