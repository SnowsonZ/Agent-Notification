# 可验证交付 harness（本仓库使用说明）

状态：现役（2026-09-25 起）。引擎与机制设计（判定器、三层护栏、风险判级与合并路由、派发与独立评审、可观测性）属独立开源项目 [delivery-harness](https://github.com/SnowsonZ/delivery-harness)；理论调研快照、目标态设计、抽取决策与建设期评审记录自 2026-10-09 起归引擎仓库（[目标态设计](https://github.com/SnowsonZ/delivery-harness/blob/main/docs/plans/2026-09-27-target-state-design.md)、[调研快照](https://github.com/SnowsonZ/delivery-harness/blob/main/docs/research/2026-09-23-agent-delivery-theory.md)、[抽取决策](https://github.com/SnowsonZ/delivery-harness/blob/main/docs/decisions/0001-harness-extraction.md)），本仓库只留引用。本文件只记本仓库的实例信息：命令入口、本仓库约定与配置值、平台设置、验证状态与已知边界；命令语义与机制设计的正源是引擎的 README（双语）与 SECURITY.md。

harness 以带锁文件的内置副本装在 `.harness/engine/`（版本、引擎提交与目录树哈希见 `.harness/engine.lock`，`bin/verify` 的 integrity 检查拒绝任何绕过升级的改动），只依赖 Python 标准库。本仓库规则与配置在 `.harness/config/`（`rules.toml`、`autonomy.toml`、`checks.toml`），棘轮状态在 `.harness/state/`，事故回放用例在 `.harness/project/replay_cases.py`。所有命令经 `bin/harness <子命令>`（`bin/verify`、`bin/dispatch` 为快捷入口）。升级引擎：在 delivery-harness 的检出中运行 `python3 engine/cli.py upgrade --target <本仓库>`，经 PR 合并。改动 `.harness/`、`bin/`、`.githooks/`、`.github/`、`.claude/`、`.opencode/`、`.pi/`、`.zcode/` 属于 R3，必须由用户批准。

## 1. 命令

| 命令 | 作用 | 谁在何时运行 |
|---|---|---|
| `bin/verify` | 工具版本、git 守卫、lint、仓库卫生、质量棘轮、文档链接、Python 测试、Swift 测试（仅 macOS） | 所有人；pre-push 自动运行 |
| `bin/verify --quick` | 工具版本、lint、仓库卫生、验收映射、任务书准入 | pre-commit 自动运行 |
| `bin/verify --full` | 默认档 + 事故回放 | macOS CI（`--strict --full`）；改动测试或产品逻辑后 |
| `bin/verify --strict` | 被跳过的检查算失败 | macOS CI |
| `bin/harness acceptance [--manual]` | 规格验收编号 ↔ 测试映射检查；列出人工验收清单 | verify 各档；写 PR 的人工验收部分时 |
| `bin/harness taskbook [任务书 --on-main]` | 任务书准入（头部、类别与风险相容、验收挂编号、必备章节、文件交叉核对）；`--on-main` 另要求已合并到 main | verify 各档；派发前 |
| `bin/harness review-pack --base origin/main` | 评审证据包 | 评审方评审前 |
| `bin/harness quality [--update]` | 熵治理棘轮（Python 与 Swift，只降不升） | verify 默认档；每周 quality workflow |
| `bin/harness docs` | 文档链接与状态新鲜度检查 | verify 默认档与完整档 |
| `bin/harness metrics --base origin/main [--github]` | 交付度量 | CI harness job |
| `bin/harness replay [--list]` | 历史缺陷注入回放 | `verify --full` |
| `bin/harness mutate [--check / --update]` | 定向变异测试（与基线比较，只升不降） | 每周 quality workflow；补测试后 |
| `bin/harness evidence --base origin/main [--swift]` | 修复证据与回放覆盖核对 | CI harness job；实现方自查 |
| `bin/harness risk --base origin/main` | 按改动路径判定 R0–R3 | CI harness job |
| `bin/harness policy …` | 合并路由判定（风险、类别、预算、规模、运行记录，逐条写理由） | auto-merge 的判定步骤（main 上的代码） |
| `bin/harness run-check --base origin/main [--branch <分支>]` | 任务 PR 的运行记录复核 | CI harness job；合并路由第 5 条（权威） |
| `bin/harness weekly [--publish]` | 周报（指标、突增标红、误差预算、试跑汇总） | 每周一 quality 工作流 |
| `bin/harness base-tests --base origin/main` | base 版本已有测试在 head 重跑 | CI harness job |
| `bin/harness hygiene --staged / --range BASE` | 禁止路径、超大文件、凭据、本机路径 | pre-commit、pre-push、CI |
| `bin/harness guard-git install` | `core.hooksPath` 指向 `.githooks` | 每个新环境一次 |
| `bin/dispatch run <任务书> [--resume] [--model M]` | 派发（准入→认领→槽位→守卫预检→Pi 执行→本地判定→记录→推送开 PR→等 CI→升级） | 设计评审方，任务书合并后 |
| `bin/dispatch status` / `bin/dispatch stop --all` | 槽位；停机（§8） | 设计评审方、用户 |
| `bin/dispatch review <PR> [--reviewer …]` | 独立评审（只读工作区，结论评论到 PR） | 设计评审方；后台评审自动运行 |
| `bin/dispatch review --pending` / `--watch` | 后台评审常驻 | 本机常驻 |
| `bin/harness review calibrate --reviewer <评审方>` | 评审校准（TPR、TNR） | 改评审提示词或换评审模型后 |
| `bin/harness release-check --tag vX.Y.Z` | 发版核对 | 推 tag 时 CI 自动运行 |

`verify` 终端只打印结论，完整输出在 `build/verify/<检查名>.log`，汇总在 `build/verify/summary.json`。判断通过只认退出码和 CI 上当前 head 的运行。

## 2. 本仓库的约定

- **修复提交**：说明加 `Defect: <编号>`（纯规格或文档修复写 `Defect: <编号> doc`），测试用同一编号标注。修复前必须以断言失败结束；需新增函数或接口时分两个提交（先不带 `Defect` 抽出可测位置、行为保持旧的、带 `Risk: R1`）。编号全局唯一 `<来源>-<序号>`（本仓库历史来源如 `V080-R17`、`REV0921-R2`、`H0925-4`），写错用 `Defect-Withdrawn: <编号>` 撤销，不改写历史。
- **回放强制**：PR 中每个非 doc 类、未撤销的 `Defect` 编号，head 的 `replay_cases.py` 必须有注入用例、守卫测试（GUARDED）或写明原因的暂缓项（DEFERRED），否则 CI 失败；注入用例由评审方在同一个 PR 里加入。
- **PR 正文与评论用文件传入**（`--body-file`），不在双引号里写 Markdown。
- **执行方可编辑的 harness 数据**：仅 `.harness/state/acceptance-gaps.txt`（补完测试后删行，只能缩减，新增判 R3）。
- **已知缺陷登记**：写成确定性测试并标 `@unittest.expectedFailure`，说明写编号；修好后「意外通过」逼着移除登记。
- **验收编号**：前缀按规格区分——W（桌面组件）、U（用量金额）、DR（工作日报）、IN（统一收件箱）、CB（CLI 会话绑定）、ZN（Zcode 导航）；暂缺的登记 `acceptance-gaps.txt`。
- **任务书即合同**：按 [task.md](../templates/task.md) 写（YAML 头部 + 终态、非目标、验收、步骤与提交顺序、升级包）。审查按类别：K7、K8、`architecture: true`、头部不合格与删除任务书由用户审（R2），其余过准入即自动合并（R0）。执行方对任务书与规格只读，改合同写升级包。设计方自行实现的工作也写任务书（与实现同一个 PR，不经派发、不要求运行记录）。
- **独立评审**：R2 及以上 CI 通过后由 auto-merge 打 `needs-independent-review`，本机后台评审（`--watch`）自动评审、新推送自动重评；评审方不能是设计方。默认评审方 `rules.toml [review] reviewer`（OpenCode，`zai-coding-plan/glm-5.3`），可用 `--reviewer` 选 Pi、Codex、Claude Code。结论只是输入，不替代用户审批。
- **行为不变的重构**：每个提交带 `Risk: R1`，加强判定（签名不变、无新依赖、不超 400 行）由引擎执行；变异得分不降由 CI 核对。
- **逃逸与抽审登记**：合并后才发现、本应被拦住的缺陷，开议题加 `escape` 与 `class:<类别>` 标签，正文写「引入：#<PR>」；K3 抽中的 PR 合并后由 auto-merge 开 `audit` 议题。升级处理后加 `escalation:needed` 或 `escalation:unneeded`。
- **每周错误分析**：周报出来后由不是本周主要设计方的评审方看本周全部异常并写结论，存 `docs/review/weekly/<年-周>.md`（R0），沉淀项进待办。
- **不手写通过状态**：「测试通过」「CI 通过」「已修复」一律由 CI 的 job summary 与 run 链接代替。
- **派发**：执行方任务经 `bin/dispatch` 在槽位（仓库同级 `<仓库名>-slot-<n>`，3 个）运行，不在主目录；执行方以 `Snowson` 提交身份工作但拿不到 GitHub 凭据，推送、开 PR、评论由派发脚本经 `bin/as-agent` 完成；预算取任务书头部（`wall_clock_min`/`retries`/`ci_rounds`）。
- **运行记录**：每次推送带 `docs/runs/<任务书名>/<序号>.json` 与提示词快照（R0），执行方不能编辑；CI 由 `run-check` 复核。
- **减少审批次数**：记录类改动并入下一个实质性 PR；相关 R3 小改动按主题合成一个 PR。
- **护栏规则先合并**：改规则的 PR 合并前，依赖新规则的文件在本机提交会被拒；放宽类规则与依赖文件分两个 PR。

## 3. 风险等级（本仓库路由结果）

| 等级 | 判定（`.harness/config/rules.toml [risk]`） | 合并 |
|---|---|---|
| R0 | 说明性文档；业务修改与缺陷修复等类别的任务书（通过准入）；只新增测试 | 门禁全绿即可自动合并 |
| R1 | 声明 `Risk: R1` 且机器核对通过 | 自动合并 + 抽样审计 |
| R2 | 产品代码、现役规格、AGENTS.md；模板与待办清单；K7、K8、架构级或头部不合格的任务书；删除任务书；改动或删除已有测试；改动黄金快照 | 评审方评审 + 用户看证据包后合并 |
| R3 | 护栏、CI 与发布、依赖、报告迁移、快照与隐私、用户配置安装、运行时入口 | 用户批准 |

自动合并由 main 上的 auto-merge 工作流执行，五条判定（风险、类别、预算、规模、运行记录）的机制与理由输出见引擎 README「合并路由」；本仓库的路径映射与预算窗口在 `rules.toml`、`autonomy.toml`。

## 4. Agent 接入（本仓库实例）

三层护栏（服务端 ruleset、git 钩子、Agent 命令守卫）的设计与信任模型见引擎 README 与 SECURITY.md。本仓库各宿主的接入：

| Agent | 角色 | 接入 |
|---|---|---|
| Claude Code | 设计与评审 | 项目级 `.claude/settings.json` 的 PreToolUse，自动生效 |
| Codex | 设计与评审 | 项目级 `.codex/hooks.json` 的 `PreToolUse`，须用户信任后才运行；`.codex/` 其余内容禁止入库 |
| OpenCode | 执行 | 项目级插件 `.opencode/plugin/harness-guard.js`，`--role implementer` |
| Pi | 执行 | 项目级扩展 `.pi/extensions/harness-guard.ts`（只在项目被信任后加载）；经 `bin/dispatch` 派发时不依赖信任（`pi -na -e <守卫>`） |
| Zcode | 执行 | 项目级 `.zcode/config.json` 的 PreToolUse；只在桌面版 ZCode.app 生效，CLI 与 TUI 不执行工作区钩子；`.zcode/` 其余内容禁止入库 |

覆盖变量只供人使用（Agent 层拒绝设置）：`HARNESS_ALLOW_MAIN=1`、`HARNESS_ALLOW_TAG=1`、`HARNESS_ALLOW_REWRITE=1`、`HARNESS_SKIP_VERIFY=1`。

## 5. 一次性设置（用户）

1. GitHub ruleset（先做：唯一不能被本机绕过的一层）：Settings → Rules → Rulesets → Import，依次导入 `.github/rulesets/main.json` 与 `.github/rulesets/release-tags.json`。
2. 本机环境由 Agent 按 AGENTS.md「新环境准备」完成，不需要人执行。
3. 发版审批：Settings → Environments → New environment `release`，勾选 Required reviewers 并加上自己；单维护者不勾 Prevent self-review。
4. Codex：仓库根启动交互式 `codex`，出现「Hooks need review」时 Review hooks 后信任（或 `/hooks`）；`hooks.json` 改动后需重做。
5. Pi：仓库根 `pi` → `/trust`（写 `~/.pi/agent/trust.json`），重启后项目扩展加载。
6. Zcode：`zcode hooks trust status --workspace <仓库根>`，确认后按提示 `grant --hook-digest <sha256>`；`config.json` 改动后需重做。
7. 设置后自检（只看、不改）：两条 ruleset 为 Active；任一 PR 页 `build` 与 `harness` 为 Required。不要用真实推送 main 测试。
8. Agent 身份：`Snowson` 账号角色设 Write；本机 gh 同时登录 `Snowson`（`gh auth login` 后 `gh auth switch --user SnowsonZ` 切回）；Agent 推送、开 PR、派发都经 `bin/as-agent`。
9. R0/R1 批准 App：新建 GitHub App（只需 Contents 与 Pull requests 读写权限），安装到本仓库；environment `auto-merge` 只允许 main；添加 variable `AUTO_MERGE_APP_CLIENT_ID` 与 secret `AUTO_MERGE_APP_PRIVATE_KEY`；确认 Actions 不能批准 PR；完成后重新导入 `main.json`。自检：`Snowson` 开 R0 PR 应被 App 批准合并，R2 PR 不点批准不能合并。

## 6. 验证状态

按 AGENTS.md「验证边界」区分证据类型。历史证据表（verify 同口径、守卫实测、ruleset 核对、派发端到端、独立评审校准、熵治理等）见各建设期 PR 与[基线评审](https://github.com/SnowsonZ/delivery-harness/blob/main/docs/review/2026-09-25-harness-baseline.md)、[全链路审计](https://github.com/SnowsonZ/delivery-harness/blob/main/docs/review/2026-09-27-harness-audit.md)（2026-10-09 已迁 delivery-harness）；引擎自身检查的验证以 [delivery-harness](https://github.com/SnowsonZ/delivery-harness) 仓库的 CI 与文档为准。

## 7. 已知边界

- **身份与批准的残余**：Agent 与用户同 macOS 用户，技术上能读到用户 gh 凭据再以用户身份批准，只靠 Agent 层守卫拦；彻底隔离要单独系统用户。用户自己开的 PR 由 `Snowson` 账号批准或临时调整 ruleset。
- **PR 不能合并自己**：依赖 environment 分支限制（只允许 main）与 App 只装在本仓库。
- **命令守卫按命令结构判断**：解释器代码（`python -c`）、脚本文件内部、`xargs` 补的参数、变量间接展开识别不了，退回字符串规则。
- **Zcode 与 Pi 的拦截依赖用户信任**（见 §5 第 5、6 步），未信任时只剩 git 与服务端两层。
- **本机护栏的信任根在可写路径**：git 钩子按 origin/main 的规则执行，但本地 `refs/remotes/origin/main` 本身不受保护；守卫代码与运行时解释器仍可被改。本机两层定位为防误操作，防有意绕过靠服务端 ruleset 与发版审批。

## 8. 停机与恢复

停机（用户，网页或手机）：仓库 Actions → `auto-merge` → Disable workflow；正在运行的逐个 Cancel；本机执行方 `bin/dispatch stop --all`（标记存在期间新派发直接拒绝，恢复时删除标记）。

恢复（用户）：Enable workflow；停机期间通过 build 的 PR 在最新 build 运行上 Re-run all jobs，完成后按当前规则判定。

演练：进入无人值守前一次、之后每季度一次（停机 → R0 测试 PR 不被自动合并 → 恢复 → 重跑后被合并），结果记入引擎仓库验证状态。
