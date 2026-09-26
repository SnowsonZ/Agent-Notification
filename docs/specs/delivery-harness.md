# 可验证交付 harness

状态：现役（2026-09-25 起）。方案与阶段见 [可验证交付方案](../plans/verifiable-delivery.md)，基线见 [2026-09-25 基线评审](../review/2026-09-25-harness-baseline.md)。

harness 只依赖 Python 标准库，放在仓库顶层 `harness/`，不进发布包（构建只拷 `scripts/` 与 `bin/session-manager`）。改动 `harness/`、`.githooks/`、`.github/`、`.claude/`、`.opencode/`、`.pi/`、`.zcode/` 属于 R3，必须由用户批准。

## 1. 命令

| 命令 | 作用 | 谁在何时运行 |
|---|---|---|
| `bin/verify` | 工具版本、git 守卫、lint、仓库卫生、Python 测试、Swift 测试（仅 macOS） | 所有人；pre-push 自动运行 |
| `bin/verify --quick` | 工具版本、lint、仓库卫生 | pre-commit 自动运行 |
| `bin/verify --full` | 默认档 + 事故回放 | macOS CI（`--strict --full`）；改动测试或产品逻辑后 |
| `bin/verify --strict` | 被跳过的检查算失败 | macOS CI |
| `python3 harness/acceptance.py [--manual]` | 规格验收编号 ↔ 测试映射检查；列出人工验收清单 | verify 各档；写 PR 的人工验收部分时 |
| `python3 harness/review_pack.py --base origin/main` | 评审证据包：风险等级、修复证据、verify、涉及的验收编号 | 评审方评审前 |
| `python3 harness/quality.py [--update]` | 熵治理棘轮：复杂度超标函数数、超长文件数只降不升 | verify 默认档；每周 quality workflow |
| `python3 harness/metrics.py --base origin/main [--github]` | 交付度量：修复数、声称已修、修复带回放比例、新增测试、CI 轮次，并列 v0.8.0 基线 | CI harness job；试跑记录 |
| `python3 harness/replay.py [--list]` | 把历史缺陷注入工作区副本，对应测试必须失败；列出基线覆盖 | `verify --full` |
| `python3 harness/mutate.py [--check / --update]` | 定向变异测试，得分与 `harness/mutation-baseline.json` 比较（只升不降） | 每周 quality workflow；补测试后 |
| `python3 harness/evidence.py --base origin/main [--swift]` | 按 `Defect:` trailer 生成修复证据，验证修复前测试失败、修复后通过；`--swift` 同时编译运行引用编号的 Swift 测试（macOS，编译失败算出错） | CI harness job（Python）与 macOS build job（含 Swift）；实现方自查 |
| `python3 harness/risk.py --base origin/main` | 按改动路径判定 R0–R3 | CI harness job |
| `python3 harness/base_tests.py --base origin/main` | 用 base 版本的已有测试在 head 上重跑：追加进已有测试文件的代码禁用不了判定器（有意改动已有测试的 PR 按 R2 评审，此项只报告）；测试经 `harness/base_tests_runner.py` 运行，产品代码在运行中篡改 unittest 时一律失败 | CI harness job |
| `python3 harness/hygiene.py --staged / --range BASE` | 禁止路径、超大文件、凭据、新增行中的本机路径 | pre-commit、pre-push、CI |
| `python3 harness/git_guard.py install` | 把 `core.hooksPath` 指向 `.githooks`（幂等，不覆盖已有设置） | 每个新环境一次 |
| `python3 harness/release_check.py --tag vX.Y.Z` | tag 与构建版本一致、构建号递增、tag 在 main 上 | 推 tag 时 CI 自动运行 |

`verify` 终端只打印结论，完整输出在 `build/verify/<检查名>.log`，汇总在 `build/verify/summary.json`。判断通过只认退出码和 CI 上当前 head 的运行。

## 2. 约定

- **修复提交**：说明中加一行 `Defect: <编号>`（放在哪一段都行，不依赖 git trailer 规则）；纯规格或文档修复写 `Defect: <编号> doc`。测试里用同一个编号标注（注释或说明均可），evidence 据此定位测试。给早已修复的缺陷补回归测试或护栏（例如把运行时缺陷改成编译错误、抽出可测的纯函数）不是修复，不带 `Defect:`：此时退回改动只会让测试编译不过，得不到「修复前失败」的证据；覆盖由评审方加入的回放用例证明（任务 003）。
- **缺陷编号全局唯一**：`<来源>-<序号>`，例如 `V080-R17`（v0.8.0 交付评审第 17 项）、`REV0921-R2`（2026-09-21 评估第 2 项）、`H0925-4`（2026-09-25 harness 建设中的发现）。不再使用裸 `R17`。
- **每个修复都要能被回放**：修复评审发现的缺陷时，要有 `harness/replay_cases.py` 的注入用例（或守卫测试）；无法回放的写进 `DEFERRED` 并说明原因。回放用例是判定器：执行方在 PR 里写明注入点，由评审方加入（H0926-2）。
- **执行方可编辑的 harness 数据**：`harness/**` 对执行方整体禁改，唯一例外是 `harness/acceptance-gaps.txt`（补完测试后删行）。它只能缩减，新增条目 risk.py 判 R3。
- **已知缺陷登记**：发现但暂不修的缺陷写成确定性测试并标 `@unittest.expectedFailure`，说明里写编号与待决事项；修好后它会「意外通过」并报错，逼着移除登记。
- **验收编号**：每份规格的验收表含「证据类型」「覆盖」两列，编号前缀按规格区分——W（桌面组件）、U（用量金额）、DR（工作日报）、IN（统一收件箱）、CB（CLI 会话绑定）、ZN（Zcode 导航）。可自动化条目必须有真实存在的测试；暂缺的登记在 `harness/acceptance-gaps.txt`（带原因，只能缩减）。
- **任务与计划**：任务按 [task.md](../templates/task.md) 写（终态、非目标、编号验收、风险、预算、升级包）；R2 及以上先按 [plan.md](../templates/plan.md) 写计划交评审。
- **独立评审**：评审方按 [review-prompt.md](../templates/review-prompt.md) 工作，对照 [review-checklist.md](../templates/review-checklist.md)（由失败分类生成，机器已判定的只核对，评审时间花在机器判定不了的部分）；发现编号 `PR<编号>-R<序号>`，修复以证据表为准。
- **测试的几种形态**：种子固定的性质测试（`tests/test_properties.py`，`PROPTEST_SEEDS=N` 放大搜索）、架构适应度（`tests/test_architecture.py`，同一口径只实现一次）、CLI 黄金快照（`tests/test_golden.py`，有意改变时 `UPDATE_GOLDEN=1` 重新生成，按 R2 评审）。
- **护栏规则先合并**：本机 git 守卫按 origin/main 上的 `harness/rules.toml` 执行（评审 PR7-R3），改规则的 PR 合并前，依赖新规则的文件在本机提交会被拒。放宽类规则（如新增 `[hygiene] allowed` 例外）与依赖它的文件分两个 PR：先合并规则，再提交文件。
- **行为不变的重构**：每个提交带 `Risk: R1`；risk.py 核对只改产品代码、已有测试与黄金快照零改动，否则按 R2。
- **不手写通过状态**：PR 与交付说明里的「测试通过」「CI 通过」「已修复」一律由 CI 的 harness job summary 与 run 链接代替。

## 3. 风险等级

| 等级 | 判定（`harness/rules.toml [risk]`） | 合并 |
|---|---|---|
| R0 | 说明性文档；只新增测试 | 门禁全绿即可自动合并 |
| R1 | 声明 `Risk: R1` 且机器核对通过 | 自动合并 + 抽样审计 |
| R2 | 产品代码、现役规格、AGENTS.md；改动或删除已有测试；改动黄金快照 | 评审方评审 + 用户看证据包后合并 |
| R3 | 护栏、CI 与发布、依赖、报告迁移、快照与隐私、用户配置安装、运行时入口 | 用户批准 |

R0/R1 的自动合并由 `.github/workflows/auto-merge.yml` 执行：build 完成后以 `workflow_run` 触发，检出 main、用 main 上的 `risk.py` 对 PR 的 diff 重新判级（只读 diff，不执行 PR 的代码），R0/R1 才以 `--match-head-commit` 合并本次评估过的提交；fork 与失败的运行不处理。判定放在这里而不是 PR 自己的 CI 里，是因为 `pull_request` 事件执行的是 PR 分支里的 workflow 定义。

## 4. 三层护栏

| 层 | 内容 | 能否被绕过 |
|---|---|---|
| 服务端 | `.github/rulesets/main.json`：main 禁删除与改写，必须经 PR，`build` 与 `harness` 检查必须通过；`release-tags.json`：`v*` tag 禁移动与删除；release job 走 environment `release`，由用户批准 | 本机 Agent 无法绕过 |
| git | `.githooks/pre-commit`（保护分支上禁止提交、暂存区卫生、快速 verify）；`pre-push`（禁推 main 与 tag、禁强制推送、本次推送的改动卫生、完整 verify）；`reference-transaction`（禁本地改写或删除 main、移动或删除 tag，含 filter-repo） | 防误操作，不防有意绕过：`--no-verify`、改 `core.hooksPath`、改守卫代码或运行时解释器都能绕过（规则文件已改为读 origin/main）；Agent 层拒绝其中能识别的命令 |
| Agent | `harness/command_guard.py`：拒绝改写历史、强推、推 main 与 tag、建删 tag、跳过钩子、设置覆盖变量、`reset --hard`、不带 venv 排除的 `git clean -x`、删除工作区外路径、`gh release` 与删除 CI 记录、GitHub API 写请求（`gh api` 写方法与对 api.github.com 的 curl 写请求）；引号或反斜杠拆写的命令另按去掉引号的形式再查一遍；Agent 自行合并 PR（`gh pr merge` 与 MCP 的合并、开启自动合并工具，用户决定 D4）；`--role implementer` 另禁编辑判定器与护栏 | 取决于各家 hook 能力 |

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
| Claude Code | 设计与评审 | 项目级 `.claude/settings.json` 的 PreToolUse（Bash 与 MCP 合并工具），自动生效 |
| Codex | 设计与评审 | 项目级 `.codex/hooks.json` 的 `PreToolUse`（不设 matcher，所有工具都交给守卫），从 git 仓库根调用 `command_guard.py --format claude --role designer`。**须经用户信任后才运行**：未信任时 `codex exec` 静默跳过、命令照常执行；交互界面启动时提示「Hooks need review」，信任后记入用户级 `~/.codex/config.toml`（§5 第 4 步）。`hooks.json` 内容一改就要重新信任；只改守卫脚本不用。不用用户级钩子，以免影响其他项目。`.codex/` 其余内容禁止入库 |
| OpenCode | 执行 | 项目级插件 `.opencode/plugin/harness-guard.js`，`--role implementer` |
| Pi | 执行 | 项目级扩展 `.pi/extensions/harness-guard.ts`（`tool_call` 事件拦截，`--role implementer`）。**只在项目被信任后加载**：未信任时（含 `pi -p` 且无保存的信任）扩展不加载、命令照常执行，只剩 git 与服务端两层；须按 §5 第 5 步信任本项目 |
| Zcode | 执行 | 项目级 `.zcode/config.json` 的 `PreToolUse`（Bash、Edit、Write 与 MCP 合并工具），调用 `command_guard.py --format claude --role implementer`，退出码 2 即拦截。工作区钩子须经用户信任后才执行（§5 第 6 步），未信任时状态为 `pending_trust`、不执行。**只在桌面版 ZCode.app（协议服务端宿主）生效**（2026-09-26 实测拦截）；**CLI 与 TUI 不执行工作区钩子**，且没有参数可以打开（用户决定不改 Zcode、不用用户级钩子，以免影响其他项目）。用 Zcode 当执行方时用桌面版。`.zcode/` 其余内容是 Zcode 运行数据，仍禁止入库（`[hygiene] allowed` 只放行这一个文件） |

## 5. 一次性设置（用户）

1. GitHub ruleset（先做：它是唯一不能被本机绕过的一层）：仓库 Settings → Rules → Rulesets → New ruleset → Import a ruleset，依次导入 `.github/rulesets/main.json` 与 `.github/rulesets/release-tags.json`。
2. 本机环境：由 Agent 按 AGENTS.md「新环境准备」自行完成（重建 venv、装开发依赖、`python3 harness/git_guard.py install`、`bin/verify`），不需要人执行。
3. 发版审批：Settings → Environments → New environment，名称 `release`，勾选 Required reviewers 并加上自己；只有一个维护者时不要勾选 Prevent self-review。
4. Codex（用 Codex 时必做，`.codex/hooks.json` 改动后需重做）：在仓库根启动交互式 `codex`，出现「Hooks need review」时先选 Review hooks，确认命令是调用 `harness/command_guard.py --role designer`，再信任。也可以启动后执行 `/hooks` 信任。信任只能由用户做；`--dangerously-bypass-hook-trust` 不用于本仓库。
5. Pi（执行方用 Pi 时必做）：在仓库根目录启动 `pi`，执行 `/trust` 保存对本项目的信任（写入用户级 `~/.pi/agent/trust.json`，由用户自行执行），重启 pi 后项目扩展才会加载；或每次运行都加 `-a`。
6. Zcode（执行方用 Zcode 时必做，每个克隆一次，`.zcode/config.json` 改动后需重做）：`zcode hooks trust status --workspace <仓库根>` 查看，确认声明内容后由用户执行它提示的 `zcode hooks trust grant --workspace <仓库根> --hook-digest <sha256>`。
7. 设置后自检（只看、不改）：Rulesets 列表中两条规则均为 Active；任一 PR 页面上 `build` 与 `harness` 标为 Required。不要用真实推送 main 的方式测试：规则没生效时会真的改掉 main。
8. Agent 身份（方案 §13 D3，用户执行一次）：
   - `Snowson` 账号在仓库 Settings → Collaborators 中的角色设为 **Write**。
   - 本机让 gh 同时登录 `Snowson`：`gh auth login --hostname github.com` 以 `Snowson` 登录，然后 `gh auth switch --user SnowsonZ` 切回自己的账号。
   - 之后 Agent 推送、开 PR、派发执行方都经 `bin/as-agent <命令>`：它只对这条命令改用 `Snowson` 的令牌与提交身份，不改 gh 当前账号和 git 配置。
9. R0/R1 的批准 App（方案 §13 D3，用户执行一次）：
   - 新建 GitHub App（个人账号 Settings → Developer settings → GitHub Apps）：不需要 Webhook；Repository permissions 给 **Contents: Read and write** 与 **Pull requests: Read and write**，其余不给；只允许安装在自己的账号上。GitHub 只把有仓库写权限的批准计入必需批准，对 App 而言即 Contents 写权限；只给 Pull requests 时 App 能批准，但批准不算数，合并仍被拒（H0926-6）。已安装后再改权限，需在安装处接受新权限。
   - 安装到本仓库（Only select repositories → `Agent-Notification`），并生成一把私钥。
   - 仓库 Settings → Environments → New environment，名称 `auto-merge`：Deployment branches 选 **Selected branches** 并只加 `main`；不设审批人。
   - 在该 environment 中添加 variable `AUTO_MERGE_APP_CLIENT_ID`（App 的 Client ID）和 secret `AUTO_MERGE_APP_PRIVATE_KEY`（私钥文件全文）。
   - 确认仓库 Settings → Actions → General 中「Allow GitHub Actions to create and approve pull requests」保持未勾选。
   - 以上完成、且引入本步骤的 PR 合并后，再按第 1 步重新导入 `.github/rulesets/main.json`，覆盖现有的 main 规则。顺序反过来，引入本步骤的 PR 自己就会因缺少批准而无法合并。
   - 自检：由 `Snowson` 开一个 R0 的 PR，auto-merge 运行后，PR 上应出现 App 的批准并被合并；由 `Snowson` 开一个 R2 的 PR，不点批准就无法合并。

## 6. 验证状态

按 AGENTS.md「验证边界」区分证据类型。

| 能力 | 证据 | 状态 |
|---|---|---|
| verify 三处同口径 | 云端 Linux 实跑；macOS CI `--strict` 实跑 Swift 检查（run 36148783352）；2026-09-26 本机 macOS `bin/verify --strict --full` 10 项全过 | ✅ |
| evidence / risk / hygiene | 单测 + 临时 git 仓库场景（`tests/test_harness.py`） | ✅ |
| CI harness job | 首次运行即拦下测试数据里的真实用户目录（run 36148783352） | ✅ |
| git 钩子 | 临时仓库真实 git 回放：main 上提交、amend、移动与删除 tag、filter-repo、推 main、推 tag、强推、推送卫生（`tests/test_harness_guard.py`） | ✅ Linux；macOS 由 CI 覆盖 |
| Claude Code PreToolUse | 2026-09-25 本仓库会话中真实拦截 3 次（均为命令文本含危险字样的误报，拦截本身生效） | ✅ 真实会话 |
| OpenCode 插件 | node 加载插件的单测；2026-09-26 真实 OpenCode（`opencode run`）中实测：设置覆盖变量的命令被拒、编辑 `harness/rules.toml` 被拒且文件未改 | ✅ 真实会话 |
| Pi 扩展 | node 加载扩展的单测（`PiExtensionTest`）；2026-09-26 真实 Pi（`pi -a -p`）中实测：设置覆盖变量的命令被拒、编辑 `harness/rules.toml` 被拒且文件未改；**未信任项目时（`pi -p` 不带 `-a`）同一命令照常执行**，扩展未加载 | ✅ 已信任时；⚠️ 依赖用户按 §5 第 5 步信任项目 |
| Zcode 钩子 | `.zcode/config.json` 按声明运行钩子命令的单测（`ZcodeHookConfigTest`）。2026-09-26 真实 Zcode 0.16.9 实测：CLI（`zcode -p`）识别到声明并可授予信任，但**信任后两项探针仍放行**。日志为 `workspace_hook.feature_disabled`：源码中只有协议服务端（桌面 ZCode.app 等宿主）显式打开工作区钩子（`workspaceHookTrustEnabled: true`，注释称灰度开关），CLI 与 TUI 未打开 2026-09-26 桌面版 ZCode.app 实测（主仓库授信任后由用户发两项探针）：设置覆盖变量的命令、编辑 `harness/rules.toml` 均被「harness 守卫」拒绝，文件未改。CLI 没有启用该功能的参数：开关只由协议服务端注入，不读项目配置或环境变量 | ✅ 桌面版；❌ CLI 与 TUI（只剩 git 与服务端两层） |
| Codex hooks | 载荷解析单测（含列表形式命令），`CodexHookConfigTest` 按 `.codex/hooks.json` 的声明运行钩子。2026-09-26 Codex 0.157.1 在临时仓库实测：项目级 hooks.json 被读取；未信任时 `codex exec` 静默不运行、命令照常执行；用户信任后，探针钩子以 JSON 拒绝拦下命令（`Command blocked by PreToolUse hook`）；载荷为 `tool_name: Bash`、`tool_input.command` 字符串，与守卫的 Claude 格式一致 | ✅ 加载、信任门禁与载荷；⚠️ 本仓库钩子以退出码 2 拒绝，待用户信任后实测 |
| ruleset | 配置文件与 workflow 一致性单测（`tests/test_harness_release.py`）；2026-09-25 导入后经 GitHub API 核对：两条均 Active、规则与文件一致（GitHub 为 PR 规则补了默认参数）、无绕过名单，main 上生效的规则为禁删、禁强推、必须经 PR、`build` 与 `harness` 必须通过 | ✅ |
| environment `release` | 2026-09-25 创建。本会话代理禁止读取 environments 接口，由用户转贴 API 输出核对：`required_reviewers` 为用户本人、`prevent_self_review` 为 false、`can_admins_bypass` 为 false（管理员即与 Agent 共用的身份也不能跳过审批）、部署限制为只允许 tag `v*` | ✅ 配置；实际拦停待首次发版确认（方案 §13 V6） |
| 单独身份与批准（D3） | workflow 与 ruleset 一致性单测（`test_auto_merge_approval_needs_main_only_environment`）、批准守卫单测。2026-09-26 GitHub 实测：API 核对 ruleset（1 个批准、非最后推送者、新推送作废）、environment `auto-merge` 只允许 main、Actions 不能批准；R0 的 PR #32 由 `Snowson` 推送后显示需要批准、不可合并，auto-merge 用 App 批准并由 `github-actions` 合并（首次因 App 缺 Contents 写权限批准不计入，H0926-6，调整后重跑）；R2 以上不经用户批准不可合并，见引入本行的 PR | ✅ R0；R2 以本行所在 PR 为证 |
| 发版核对 | 临时仓库单测：版本不一致、构建号未递增、tag 不在 main | ✅ 单测；首次真实发版时再确认 |
| 事故回放 | 30 个注入用例（含评审 PR7-R1..R9 的 11 个）：Linux 实跑 27 个，Swift 3 个由 macOS CI `--strict --full` 运行（run 36152060546）；回放自检（注入点未过期、基线全覆盖）在默认档 | ✅ |
| 修复证据 | 本 PR 的 H0925 与 PR7-R1..R6 修复提交由 evidence 生成「修复前失败、修复后通过」；修复前以出错结束不算证据，只退回修复提交自身的改动 | ✅ |
| 已有测试按 base 版本重跑 | 临时仓库单测四个场景（`tests/test_harness.py` BaseTestsTest）；本 PR 上 main 的 267 个测试在 head 通过 | ✅ 本地；CI 步骤随本 PR 首次运行 |
| 变异测试 | 4 个目标的基线得分（见基线评审 §6） | ✅ Linux 实跑 |
| 验收映射 | 6 份规格 69 条编号：可自动化 54 条中 53 条有测试、1 条登记缺口，19 条进入人工清单；检查器在 verify 各档运行 | ✅ |
| Swift 回放 | macOS CI `--strict --full`（run 36152060546）通过，Swift 注入在 strict 下不可跳过 | ✅ |
| zcode 自检、质量棘轮、交付度量 | 本地实跑；zcode 自检与 CI 中的度量步骤随本 PR 首次在 CI 运行 | ⚠️ 待本 PR 的 CI |
| 每周 quality workflow | 本地实跑变异 `--check`、回放、质量棘轮 | ⚠️ 定时与手动触发只对默认分支生效，合并后首次运行 |

## 7. 已知边界

- **身份与批准**（2026-09-26 用户决定改为单独身份，方案 §13 D3）：
  - Agent 用单独的账号 `Snowson` 推送和开 PR（`bin/as-agent`），用户用自己的账号 `SnowsonZ` 批准。
  - main 的 ruleset 要求非推送者批准最后一次推送；有新推送时作废旧批准。
  - R0/R1 由 auto-merge 的专用 App 批准，App 凭据放在只允许 main 使用的 environment `auto-merge` 中，PR 分支的 workflow 取不到。
  - 残余：Agent 与用户在同一个 macOS 用户下运行，技术上能读到用户在 gh 里的登录凭据，再以用户身份批准。这一步只靠 Agent 层守卫拦（拒绝 `gh pr review --approve` 与 MCP 评审工具）；彻底隔离要给 Agent 单独的系统用户。
  - 用户自己开的 PR 同样需要别人批准，由 `Snowson` 账号批准，或临时调整 ruleset。
- **PR 在自己的 workflow 里合并自己**：`pull_request` 事件执行 PR 分支里的 workflow 定义。D3 之后合并需要非推送者批准，`GITHUB_TOKEN` 既不能批准（仓库已关闭「允许 Actions 批准 PR」），也取不到 environment `auto-merge` 中的 App 凭据，因此 PR 不能再合并自己。仍依赖的前提：environment 的分支限制只允许 main；App 只装在本仓库，只有 Contents 与 Pull requests 写权限。App 的 Contents 写权限是批准被计入的前提（H0926-6），它的令牌因此也能推送；令牌只在 main 上的 auto-merge、判定为 R0/R1 之后取得。
- **命令守卫按命令结构判断**（2026-09-26，方案 §13 E3，`harness/shell_structure.py`）：把命令拆成简单命令，只对会执行的程序（git、rm、gh、curl 与覆盖变量的赋值）按参数判断；echo、grep 模式、提交说明、Agent 提示词、heredoc 正文、注释里的文字不再误拒。会执行但看不到结构的地方退回字符串规则：`python -c` 等解释器代码、交给 shell 或解释器执行的 stdin（heredoc 与 here-string 只判断被执行的那段正文，管道整段判断）、无法解析的命令（引号不配对）；`bash -c`、`eval`、`$(...)`、反引号递归按结构判断。仍然识别不了的：脚本文件内部（`bash x.sh`、`python3 x.py`）、`xargs` 从 stdin 补的参数、变量间接展开出的命令名。实测探针不要再用 `echo 覆盖变量=1`（现在按数据放行），改用会真实执行、被放过也无害的命令，例如对不存在的分支强推。此前字符串匹配阶段记录了 6 次误报。
- **Zcode 与 Pi 的拦截依赖用户信任**：Zcode 的工作区钩子未经信任不执行，Pi 的项目扩展在未信任项目时不加载。此时它们设置覆盖变量或使用 `--no-verify`，本机两层都挡不住，只有服务端兜底。
- **本机护栏的信任根在可写路径**（评审 PR7-R3）：git 钩子的规则文件曾在执行者可写的工作区，改掉 `protected_branches` 即可让本机改写 main 不受拦截。现在钩子按 origin/main 上的 `harness/rules.toml` 执行，工作区版本不一致时告警；但守卫代码（`harness/git_guard.py`、`.githooks/`）与运行时解释器（`scratch/iterm-probe-venv`）仍可被改。本机两层定位为防误操作，防有意绕过只能靠服务端 ruleset 与发版审批，所以一次性设置里 ruleset 排在第一步。
  - 「读 origin/main」只在本地远端引用可信时成立（评审 PR7-R8）：本地的 `refs/remotes/origin/main` 不受保护，把它改指伪造的提交，或把 remote 指向伪造的源再 fetch，守卫就会读到伪造的规则并照常告警「按 origin/main 的规则执行」。
  - 本 PR 合并前 main 上没有 `harness/rules.toml`，钩子退回读工作区版本；更根本的是 main 上还没有 `.githooks/`，检出 main 时本机没有任何 git 守卫（2026-09-26 评审报告直接提交到 main 即因此未被拦截，见基线评审 H0926-1）。
- **base_tests 的篡改检查在同一进程内**（评审 PR7-R7）：base 版本的测试 import 的是 head 的产品代码。`base_tests_runner.py` 在运行前后比对 unittest 各类与模块的属性，并跑一组必然失败的哨兵用例；risk.py 对产品代码新增的测试框架引用加标记（取消 R1）。专门针对这两处检查的篡改（例如识别哨兵用例名、改后在检查前复原）仍可能漏过，由评审兜底；接上 L4 自动合并（方案 §13 E1）前需复核这一残余。
