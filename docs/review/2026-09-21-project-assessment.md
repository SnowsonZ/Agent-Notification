# 项目整体评估（2026-09-21）

## 当前结论：v0.7.4 复评

评估基线：`main` / `81dfbe3e87df8de367c2efd9a890f0005aa5cccf`，开始时工作树干净。检查范围包括 CLI、Python 状态库与来源采集、受管理绑定、日报、Swift 模型与执行器、构建发布和现役规范。只改评估文档，没有修改产品实现、真实用户数据或配置，没有执行真实会话导航。

**项目已经具备完整的自用工具形态，架构和回归基础较好；但仍有影响通知可靠性、恢复边界和发布兼容性的确定性问题，不能仅凭测试全绿认定已在声明支持范围内可靠交付。** 建议先修复下面三项 P1，再收敛来源分类和日报问题；目前没有推倒重写的必要。

P1 指应优先修复的核心行为缺陷；P2 指特定场景的正确性或可靠性缺陷。以下六项有隔离执行证据，一项由实际载荷与 Swift 调用路径确认，证据边界分别注明。

### R1 · P1：发布包接受 Python 3.9，但收件箱依赖 Python 3.11 的模块

位置：[session_binding.py:15](../../scripts/session_binding.py#L15)、[CLI:9](../../bin/session-manager#L9)、[CI:57](../../.github/workflows/build.yml#L57)。

发布入口接受 `>=3.9`，`display_rows()` 却会导入无条件 `import tomllib` 的绑定模块。`requirements-standalone.txt` 没有兼容依赖或 shim。只有系统 Python 3.9 的机器会通过启动器检查，随后连空收件箱查询都失败；绑定和 setup 入口也受影响。

本机 `/usr/bin/python3` 是 3.9.6，使用独立状态目录执行实际代码：

```sh
/usr/bin/python3 scripts/inbox.py --root /tmp/session-manager-review-python39 rows --all
```

结果：`ModuleNotFoundError: No module named 'tomllib'`。这不是 grammar check 可以覆盖的问题。当前 CI 已增加 bundle smoke，但没有固定或断言该 smoke 使用最低支持版本。

建议统一运行时合同：保留 3.9 时补齐兼容层并在真实 3.9 下执行发布依赖组合；若改为 3.11，则同时更改启动检查、依赖打包目标和说明。最低支持版本属于产品取舍，修复时应明确选择。

### R2 · P1：Zcode 延迟落库的结束事件会被采集墙钟挡住

位置：[inbox_sources.py:583](../../scripts/inbox_sources.py#L583)、[inbox_store.py:181](../../scripts/inbox_store.py#L181)。

流式活性使用 `timestamp=time.time()` 更新会话，而真正结束事件采用来源的 `completed_at`。两个时钟混用：如果扫描读到 part、尚未读到结束行，随后补到的结束时间早于扫描时间，就会被 Store 当成旧事件丢弃。Store 还会先登记其 event_id，后续刷新不能重试应用该结束事件。

隔离 SQLite 夹具复现：part 时间为 T+8；T+10 扫描推为 running；随后补入 `completed_at=T+9` 的结束行；T+11 连续扫描两次，状态仍为 running、unread=false。该场景不依赖系统时钟倒退，只需要数据库可见性延迟，或完成发生在一次扫描取数与状态写入之间。

建议让推断状态使用来源时间，并明确真实生命周期事件优先于推断事件。回归必须覆盖“先采活性，再补较早结束时间”的两阶段拓扑；现有测试把结束行预先放入同一轮扫描，未覆盖此交错。

### R3 · P1：受管理聚焦被拒绝后，仍可能重复恢复并标记已读

位置：[inbox.py:314](../../scripts/inbox.py#L314)、[inbox.py:329](../../scripts/inbox.py#L329)。

`open_session()` 将 probe 的任何非零结果都视为恢复候选；查其它绑定时又排除原 run_id。因此原绑定仍活着、只是挂起/前台进程组变化/元数据查询失败时，上层仍可能调用 `launch_agent()` 新建恢复会话，随后 `_open_ok()` 自动确认。低层 `validate()` 的拒绝无法约束上层恢复行为。

隔离夹具持有真实 flock 并注册原绑定；模拟前台组无法验证，`validate()` 明确拒绝。把该拒绝交给实际 `open_session()` 后，观察到 launch 被调用一次、返回 `managed_resume`、unread 从 true 变成 false。启动和 UI 操作均替换为 mock，没有向真实终端输入命令。

建议区分“确认绑定已死”和“绑定仍活或归属无法验证”；只有确认失效且没有其它活绑定时才恢复。其它失败保留未读并返回具体原因。为仍存活但校验拒绝的路径加入回归，避免只测成功聚焦和退出恢复。

### R4 · P2：打开 agent 审计开关，会把 agent 会话送入通知与角标路径

位置：[InboxModels.swift:5](../../native/InboxModels.swift#L5)、[InboxModels.swift:92](../../native/InboxModels.swift#L92)、[InboxModels.swift:185](../../native/InboxModels.swift#L185)。

开关启用后请求 `rows --include-agents`；后端返回真实的 `origin=agent`、unread 和 attention_token。Swift 的 `InboxRow` 没有解码 origin，`notifyNewItems(rows)`、待查看和角标计数也没有来源过滤。因此在已完成初始快照、通知获准时，审计中新增/未见过的 agent attention token 会满足发通知条件，与代码注释“审计仍不产生通知”和规范不一致。

证据：隔离后端载荷 + Swift 完整调用路径审查；本轮未向 macOS 通知中心实际投递通知。

建议在 API 中提供经过目录规则计算的有效来源或通知资格；审计可见性与通知/待处理资格分别判断，不能依赖列表默认隐藏代替通知策略。

### R5 · P2：单条手动来源改判会被自动采集覆盖

位置：[inbox.py:577](../../scripts/inbox.py#L577)、[inbox_sources.py:293](../../scripts/inbox_sources.py#L293)。

单条 `origin --set` 直接改自动分类使用的同一个 origin 字段，没有记录 override。Claude Desktop 的每轮采集强制写回 user；Claude hook 和发生变化的 Codex rollout 也会重写自动分类。

隔离复现：正常 Desktop 元数据入库 → 手动 patch 为 agent（与 CLI 相同写入）→ 再 collect_claude，结果从 agent 回到 user。用户选“只改本条”不能持久生效。目录规则可绕开部分问题，但会影响整个项目，不能代替单条改判。

建议分别保存自动来源与手动覆盖，统一有效来源计算；测试覆盖改判后刷新、下一轮事件和进程重启。还应统一“声明优于推断”的优先级，避免权威采集无条件覆盖人工意图。

### R6 · P2：仅安装 Claude CLI 时，日报会漏掉全部 Claude 用量

位置：[daily_report.py:401](../../scripts/daily_report.py#L401)、[inbox_sources.py:255](../../scripts/inbox_sources.py#L255)。

`_claude_data()` 同时要求 Desktop 登记目录和 `.claude/projects` 存在，任一缺失就直接返回。纯 CLI 用户有完整转写、也有 store 行，却因为没安装 Desktop 而不进入用量扫描。`collect_claude()` 的同类早返回还会跳过 CLI 标题补全。

隔离夹具仅放入 CLI 转写（输入 100、缓存 50、输出 25）：报告任务数为 0；只创建一个空 Desktop 登记目录，不改任何转写或 store 数据，报告立即出现 1 个任务、175 tokens。现有 `test_claude_cli_only_transcript_counted_once` 同时创建了另一个 Desktop 夹具，掩盖了这个前提。

建议把 Desktop 可选数据源与 CLI 转写扫描拆开；增加“完全不存在 Desktop 目录”的安装拓扑回归。

### R7 · P2：ProcessRunner 的超时无法终止继承管道的后代进程等待

位置：[ProcessRunner.swift:68](../../native/ProcessRunner.swift#L68)。

并发排空 stdout/stderr 已修复旧死锁，但当前超时只终止直接子进程，随后无条件 `group.wait()`。直接子进程退出不保证管道 EOF：后代进程可能仍持有 stdout/stderr。此时 `process.isRunning=false`，超时分支不再终止任何进程，等待仍取决于后代何时退出。

无 UI Swift 探针：`/bin/sh -c 'sleep 9 & exit 0'`，timeout=0.2 秒，实际约 **9.014 秒**后返回 exit=0，超过 0.2+5 秒的梯度期限。长寿命后代会把同一问题放大成长期挂起。现有大 stderr 和直接 sleep 测试均通过。

建议让进程退出和输出读取共同受绝对截止时间约束，必要时安全终止受管理进程树并取消管道读取；超时应有明确结果，不能最终以父进程 exit=0 掩盖超时。保留大输出测试，再补父进程先退、后代持有管道的场景。

## 整体质量判断

| 维度 | 判断与依据 |
|---|---|
| 产品完成度 | 聚合、待处理/进行中、定位、恢复、批量已读、日报已形成完整工作流；七来源的能力边界有记录。核心价值是减少漏看和准确回到会话，R2/R3 应先于新增来源处理。 |
| 状态与身份 | Store 集中幂等、未读和 revision CAS；绑定有锁、session ID、TTY 前台组与后置复核。底层约束较好，风险集中在跨层恢复策略和不同时间来源的组合。 |
| 架构与维护 | Python/Swift 经 JSON 交互、provider 注册表、Store 和执行器均有明确边界。约 8,167 行 Python/Swift（不含测试），daily_report.py 1,143 行、inbox_sources.py 689 行、inbox.py 611 行。下一次修改可沿 provider 采集、统计口径、CLI 编排拆分；优先消除重复策略，不以行数为由重写。 |
| 测试 | 189 项 Python 检查及两组 Swift 检查通过；有真实故障驱动的 CAS、flock、跨 PTY 回归。薄弱点是最低运行时、安装组合、异步交错和上层动作语义；测试数量不能替代这些拓扑。 |
| 隐私与安全 | 检查的核心路径未发现已确认的凭据输出或完整正文落库；状态目录权限、参数化 SQL、命令转义和稳定身份检查值得保留。本轮不是供应链漏洞审计，也未证明所有外部输入组合都安全。 |
| 性能 | 3 秒 store 读取、15 秒全量兜底，结合游标与历史日报缓存，设计方向合理。全部会话仍完整跨进程传输，分页主要减少 UI 展示；大历史量下的扫描与日报成本未实测，不能声称无性能瓶颈。 |
| 文档与分发 | 规范记录了很多有价值的实测边界，但现役合同与历史叙述混排，CLI 绑定和 Zcode 文档可同时读到相反的旧/新结论。应把当前合同置前、历史证据另列。发布已加入 smoke 和最小权限，最低 Python 版本仍未闭环。 |

## 对前次评估的复核

下面保留的 v0.7.3 评估属于历史基线，不应继续当作当前待办。

- Claude 无关损坏文件污染所有定位：已修复，回归通过。
- stdout/stderr 串行读取：已改并发且压力测试通过；本轮 R7 是剩余的后代进程超时问题。
- 发布形态 smoke、普通构建只读权限：均已加入；R1 是最低运行时仍未覆盖。
- 先前文件句柄警告：本轮 Python 3.12 全量运行日志未出现 ResourceWarning 或 Exception ignored；不外推到所有 Python 版本。
- README 来源数量、部分规范版本和隐私表述已更新；部分源码注释和历史规格仍旧。

## 本轮验证与后续顺序

已执行：

- `python3 -W error::ResourceWarning -m unittest discover -s tests -v`：189 项通过（Python 3.12.10）。首次沙箱内 3 failures/1 error，均在允许本机进程查询后消失。
- `ruff check scripts tests`：通过。
- Swift `InboxPolicyTests`、`ProcessRunnerTests`：通过。
- `ZcodeFocus --self-test`：通过，不访问 UI。
- 主 App 全部 Swift 源码按 `arm64-apple-macosx14.0` 编译：通过，输出在 `/tmp`，没有覆盖运行中 App。
- 编译默认缓存目录受沙箱限制；指定 `-module-cache-path /tmp/session-manager-review-swift-cache` 后通过，无需更换 SDK。
- R1/R2/R3/R5/R6/R7 的隔离执行证据如上；R4 为实际载荷与静态调用路径证据。

可复用的本机探针在 `scratch/assessment-2026-09-21/`，不提交。测试日志位于 `/tmp/session-manager-assessment-tests-unrestricted.log`。未覆盖 DMG 新机器安装、最低 macOS 真机运行、真实 iTerm2/Zcode/通知 UI、联网依赖漏洞审计和长时间性能压测。没有将编译、模拟协议或 OS 分发成功当作精确导航验收。

建议顺序：先统一运行时合同并修 R2/R3；再完成 origin 有效值与通知策略、CLI-only 采集、执行器超时；最后在这些修改触及的边界上小步整理模块与规格。每项修复补对应失败拓扑，不另起缺乏行为收益的大规模重构。

---

## 附：v0.7.3 首次评估（历史记录）

以下内容保留原基线和当时结论；当前状态以上面的 v0.7.4 复评为准。

### 结论

Session Manager 当前主干整体健康，已经达到稳定自用水平：核心状态机、会话身份校验、CAS 已读语义、受管理 CLI 绑定与回归测试均有较强的真实故障驱动痕迹。

下一次发布前建议优先处理三项：一个已复现的 Claude Desktop 定位缺陷、发布包验证缺口，以及 Swift 子进程管道潜在死锁。其余问题主要属于安全加固、隐私文案校准和维护性债务。

本评估按正确性、可读性与简单性、架构、安全、性能五个维度检查。结论基于 2026-09-21 的 `main`（`5ebfe8b`，v0.7.3）；这是一次只读评估，没有修改产品实现。

### 主要发现

#### P1：单个损坏的 Claude 元数据文件会让所有正常 Desktop 会话失去定位能力

位置：[scripts/inbox_sources.py:273](../../scripts/inbox_sources.py#L273)

`collect_claude()` 先累计整个目录的 `errors`，随后对每个会话使用：

```python
if len(records) != 1 or errors:
```

因此，目录里任意一个无关的超大、损坏或暂时不可读 JSON，都会让所有正常 `cliSessionId` 进入 `locator.kind=unavailable` 分支。15 秒全量刷新会持续覆盖原本有效的 Desktop 深链。

已用隔离临时目录复现：一个合法 Desktop 元数据文件加一个损坏文件，合法会话最终也被标记为 `desktop identity ambiguous or incomplete`。这不是测试环境限制，而是当前实现的确定性行为。

建议：

- `errors` 仅用于来源 health 的 degraded 状态；
- 单个会话是否歧义只依据该 `sid` 的候选数量；
- 增加“合法文件 + 无关损坏文件”的回归测试；
- 保留同一 `sid` 多候选时拒绝猜测的现有安全语义。

#### P2：CI 没有按实际发布形态验证 standalone 包

位置：[.github/workflows/build.yml:24](../../.github/workflows/build.yml#L24)、[requirements.txt](../../requirements.txt)、[requirements-standalone.txt](../../requirements-standalone.txt)

CI 使用 Python 3.12 与 `requirements.txt` 创建测试环境；standalone 包则内置另一组 protobuf/websockets 版本，并声明支持本机 Python 3.9。构建完成后，测试仍通过开发虚拟环境运行，没有执行 bundle 内的 CLI 和 `pylib`。

因此，开发依赖下 186 项测试全部通过，并不能证明发布包的依赖组合、解释器选择和资源路径可运行。发布构建成功、签名验证成功也不能覆盖这一差异。

建议至少增加：

- 使用 bundle 内 `Contents/Resources/bin/session-manager` 的 `help` smoke test；
- 临时状态目录上的 `inbox rows --all` smoke test；
- Python 3.9 + `requirements-standalone.txt` 的关键绑定测试；
- 发布构建后再打包，避免只验证开发环境。

#### P2：Swift 子进程 stdout/stderr 串行读取存在管道死锁风险

位置：[native/InboxModels.swift:245](../../native/InboxModels.swift#L245)

`InboxModel.call()` 在子进程运行后，先阻塞读取 stdout 到 EOF，再读取 stderr。如果子进程先写满 stderr 管道，它会等待父进程读取 stderr；父进程则仍在等待 stdout EOF，双方互相等待。收件箱刷新、日报读取和操作动作都共用这个入口，命中后 `loading` 可能永久不恢复。

正常路径输出较小，当前测试没有触发该风险；大量历史警告、依赖诊断或异常回溯会扩大触发概率。

建议抽取统一 Process runner，并发排空两个 Pipe，并增加超过系统管道容量的 stderr 压力测试。

#### P2：发布工作流权限大于普通构建所需

位置：[.github/workflows/build.yml:10](../../.github/workflows/build.yml#L10)

整个 build job 获得 `contents: write`，但写权限实际只在 tag 发布时需要。普通 push、PR 构建、依赖安装和编译步骤不需要仓库写权限。

建议普通构建默认 `contents: read`，将 release 拆为仅 tag 触发且拥有写权限的独立 job。进一步加固可将 GitHub Actions 固定到 commit SHA，并为发布依赖引入哈希校验。这里记录的是供应链暴露面，不代表已发现可利用漏洞。

#### P3：隐私文案比实际实现更绝对

位置：[docs/specs/daily-report.md:54](../specs/daily-report.md#L54)、[scripts/daily_report.py:315](../../scripts/daily_report.py#L315)、[scripts/inbox_sources.py:393](../../scripts/inbox_sources.py#L393)

规范写明“转写/会话文件中的正文不解析”，但日报会对包含正文的完整 JSON 行执行 `json.loads()`；Claude CLI 标题采集还会读取第一条合格的用户文本并保存为标题摘要。

当前实现没有把完整正文写入 inbox 数据库或日报，也没有输出完整正文，这一核心隐私边界仍然成立。建议将合同改成更准确的表述：正文会随本地记录在内存中反序列化，除标题摘要外不提取、不持久化、不输出；token 报告只消费 usage 与时间字段。

#### P3：存在可重复的未关闭文件警告

位置：[scripts/migrations.py:49](../../scripts/migrations.py#L49)、[scripts/migrations.py:90](../../scripts/migrations.py#L90)、[scripts/migrations.py:161](../../scripts/migrations.py#L161)、[scripts/inbox_sources.py:50](../../scripts/inbox_sources.py#L50)、[scripts/daily_report.py:244](../../scripts/daily_report.py#L244)、[scripts/daily_report.py:268](../../scripts/daily_report.py#L268)

完整测试虽然通过，但会稳定出现 `ResourceWarning: unclosed file`。CPython 当前通常会在引用计数归零时关闭这些句柄，因此未观察到功能失败；不过批量扫描历史文件时不应依赖解释器回收时机。

建议统一改为 `with path.open(...) as file`，并在对应测试模块启用 ResourceWarning 检查，防止回归。

### 架构与代码质量

#### 做得好的部分

- `Store` 将事件幂等、revision CAS、未读状态和元数据补丁集中在一个边界内；并发首次建行和新活动竞态有专门测试。
- 定位链路坚持稳定会话 ID、运行锁、前台进程组和后置状态校验，遇到歧义时明确拒绝，不通过标题或“最近会话”猜测。
- 自动确认使用 UI 快照 revision，新活动到达后旧动作不会吞掉未读事件。
- 各来源适配器对格式漂移和缺失数据普遍采取降级语义，并把健康状态带回界面。
- 规格和调研记录包含真实失败拓扑、证据边界和已知限制，便于后续维护者理解为何存在当前约束。
- Python 测试覆盖状态机、迁移、日报口径、CLI 聚焦和隐私字段；Swift 纯策略测试覆盖分页、通知、刷新节拍与日报格式。

#### 维护性风险

- [scripts/daily_report.py](../../scripts/daily_report.py) 806 行、[scripts/inbox_sources.py](../../scripts/inbox_sources.py) 685 行、[scripts/inbox.py](../../scripts/inbox.py) 611 行，采集、策略、迁移接线和 CLI 编排开始聚集在少数模块中。
- 当前规模下仍可理解，不建议为了行数立即拆分；下一次新增 provider 时，应先按 provider 拆 collector，并抽取统一的子进程执行边界，避免继续扩大条件分支。
- 现役规格持续追加历史修复过程，标题仍停留在 `统一会话收件箱 v0.4.0`、`工作日报 v3 / schema v6`，正文实际已描述 v0.7.3 与 schema v7。历史证据有价值，但“当前合同”和“演进记录”正逐渐混在一起。
- README 开头仍写“五类来源”，后文和实现已经覆盖更多来源；这不影响运行，但会降低新维护者建立准确项目地图的速度。

### 安全与隐私

没有发现命令拼接注入、SQL 参数拼接注入、正文落库或凭据输出等已确认高危问题。值得保留的现有防线包括：

- 外部会话 URL 只接受已知 scheme 和路径前缀；
- Zcode 查询拒绝控制字符并在执行前核对目标元数据；
- Claude settings 安装拒绝符号链接，写入采用临时文件替换并检测并发修改；
- hook 输入有大小上限，存储只保留管理所需字段；
- SQLite 外部来源普遍使用只读 URI 打开；
- 本项目明确是协作式本机工具，不把恶意本机进程纳入安全边界。

本轮没有进行联网依赖漏洞审计，也没有核对第三方 action 或 Python 包的最新安全公告，因此不能把“未发现代码级高危问题”外推为“供应链无风险”。

### 性能

现有双节拍设计合理：3 秒只读 store，15 秒执行来源扫描，避免 UI 每个 tick 重扫全部来源。Codex 游标签名缓存、Claude 标题负缓存和日报历史固化都针对真实热点做了优化。

目前未发现已确认的性能回归。可关注两处长期成本：

- 每次全量刷新仍需枚举各来源历史文件或表；来源规模继续增长后，应以实测耗时决定是否增加目录级增量索引；
- `_sweep_managed_directories()` 每轮会更新全部匹配行，即使 hidden 值没有变化，可在后续性能整理时改为仅更新发生变化的行。

### 验证记录

本轮完成：

- `python3 -m unittest discover -s tests -v`：186 项通过；依赖 `ps` 的测试在允许读取本机进程的环境重跑后通过；
- `ruff check scripts tests`：通过；
- Swift policy tests：通过；
- `ZcodeFocus --self-test`：通过，不访问真实 UI；
- 全部主 App Swift 源码编译：通过，产物写入临时目录；
- Python 3.9 grammar check：18 个脚本通过；
- Claude 全局错误污染：在临时目录稳定复现；
- 工作树检查：`main` 与 `origin/main` 同步，评估结束时无未提交改动。

未覆盖：

- 真实 Accessibility/Zcode UI 导航；
- DMG 安装后的完整端到端运行；
- standalone 包在 Python 3.9 下的真实执行；
- 网络依赖漏洞和 GitHub Actions 上游完整性审计；
- 长时运行下的文件描述符、内存和轮询性能分析。

### 建议处理顺序

1. 修复 Claude 全局错误污染并增加回归测试；这是已确认的用户可见正确性缺陷。
2. 并发读取 Swift 子进程 stdout/stderr，并加入大输出压力测试。
3. 给 standalone bundle 增加发布形态 smoke test，再收紧 CI 写权限。
4. 清理未关闭文件警告，并让 ResourceWarning 进入自动验证。
5. 校准隐私合同和 README 来源数量；下一次新增 provider 前再拆 collector/process runner，不单独启动无行为收益的大重构。
