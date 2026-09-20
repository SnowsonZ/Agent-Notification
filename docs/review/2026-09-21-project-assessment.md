# 项目整体评估（2026-09-21）

## 结论

Session Manager 当前主干整体健康，已经达到稳定自用水平：核心状态机、会话身份校验、CAS 已读语义、受管理 CLI 绑定与回归测试均有较强的真实故障驱动痕迹。

下一次发布前建议优先处理三项：一个已复现的 Claude Desktop 定位缺陷、发布包验证缺口，以及 Swift 子进程管道潜在死锁。其余问题主要属于安全加固、隐私文案校准和维护性债务。

本评估按正确性、可读性与简单性、架构、安全、性能五个维度检查。结论基于 2026-09-21 的 `main`（`5ebfe8b`，v0.7.3）；这是一次只读评估，没有修改产品实现。

## 主要发现

### P1：单个损坏的 Claude 元数据文件会让所有正常 Desktop 会话失去定位能力

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

### P2：CI 没有按实际发布形态验证 standalone 包

位置：[.github/workflows/build.yml:24](../../.github/workflows/build.yml#L24)、[requirements.txt](../../requirements.txt)、[requirements-standalone.txt](../../requirements-standalone.txt)

CI 使用 Python 3.12 与 `requirements.txt` 创建测试环境；standalone 包则内置另一组 protobuf/websockets 版本，并声明支持本机 Python 3.9。构建完成后，测试仍通过开发虚拟环境运行，没有执行 bundle 内的 CLI 和 `pylib`。

因此，开发依赖下 186 项测试全部通过，并不能证明发布包的依赖组合、解释器选择和资源路径可运行。发布构建成功、签名验证成功也不能覆盖这一差异。

建议至少增加：

- 使用 bundle 内 `Contents/Resources/bin/session-manager` 的 `help` smoke test；
- 临时状态目录上的 `inbox rows --all` smoke test；
- Python 3.9 + `requirements-standalone.txt` 的关键绑定测试；
- 发布构建后再打包，避免只验证开发环境。

### P2：Swift 子进程 stdout/stderr 串行读取存在管道死锁风险

位置：[native/InboxModels.swift:245](../../native/InboxModels.swift#L245)

`InboxModel.call()` 在子进程运行后，先阻塞读取 stdout 到 EOF，再读取 stderr。如果子进程先写满 stderr 管道，它会等待父进程读取 stderr；父进程则仍在等待 stdout EOF，双方互相等待。收件箱刷新、日报读取和操作动作都共用这个入口，命中后 `loading` 可能永久不恢复。

正常路径输出较小，当前测试没有触发该风险；大量历史警告、依赖诊断或异常回溯会扩大触发概率。

建议抽取统一 Process runner，并发排空两个 Pipe，并增加超过系统管道容量的 stderr 压力测试。

### P2：发布工作流权限大于普通构建所需

位置：[.github/workflows/build.yml:10](../../.github/workflows/build.yml#L10)

整个 build job 获得 `contents: write`，但写权限实际只在 tag 发布时需要。普通 push、PR 构建、依赖安装和编译步骤不需要仓库写权限。

建议普通构建默认 `contents: read`，将 release 拆为仅 tag 触发且拥有写权限的独立 job。进一步加固可将 GitHub Actions 固定到 commit SHA，并为发布依赖引入哈希校验。这里记录的是供应链暴露面，不代表已发现可利用漏洞。

### P3：隐私文案比实际实现更绝对

位置：[docs/specs/daily-report.md:54](../specs/daily-report.md#L54)、[scripts/daily_report.py:315](../../scripts/daily_report.py#L315)、[scripts/inbox_sources.py:393](../../scripts/inbox_sources.py#L393)

规范写明“转写/会话文件中的正文不解析”，但日报会对包含正文的完整 JSON 行执行 `json.loads()`；Claude CLI 标题采集还会读取第一条合格的用户文本并保存为标题摘要。

当前实现没有把完整正文写入 inbox 数据库或日报，也没有输出完整正文，这一核心隐私边界仍然成立。建议将合同改成更准确的表述：正文会随本地记录在内存中反序列化，除标题摘要外不提取、不持久化、不输出；token 报告只消费 usage 与时间字段。

### P3：存在可重复的未关闭文件警告

位置：[scripts/migrations.py:49](../../scripts/migrations.py#L49)、[scripts/migrations.py:90](../../scripts/migrations.py#L90)、[scripts/migrations.py:161](../../scripts/migrations.py#L161)、[scripts/inbox_sources.py:50](../../scripts/inbox_sources.py#L50)、[scripts/daily_report.py:244](../../scripts/daily_report.py#L244)、[scripts/daily_report.py:268](../../scripts/daily_report.py#L268)

完整测试虽然通过，但会稳定出现 `ResourceWarning: unclosed file`。CPython 当前通常会在引用计数归零时关闭这些句柄，因此未观察到功能失败；不过批量扫描历史文件时不应依赖解释器回收时机。

建议统一改为 `with path.open(...) as file`，并在对应测试模块启用 ResourceWarning 检查，防止回归。

## 架构与代码质量

### 做得好的部分

- `Store` 将事件幂等、revision CAS、未读状态和元数据补丁集中在一个边界内；并发首次建行和新活动竞态有专门测试。
- 定位链路坚持稳定会话 ID、运行锁、前台进程组和后置状态校验，遇到歧义时明确拒绝，不通过标题或“最近会话”猜测。
- 自动确认使用 UI 快照 revision，新活动到达后旧动作不会吞掉未读事件。
- 各来源适配器对格式漂移和缺失数据普遍采取降级语义，并把健康状态带回界面。
- 规格和调研记录包含真实失败拓扑、证据边界和已知限制，便于后续维护者理解为何存在当前约束。
- Python 测试覆盖状态机、迁移、日报口径、CLI 聚焦和隐私字段；Swift 纯策略测试覆盖分页、通知、刷新节拍与日报格式。

### 维护性风险

- [scripts/daily_report.py](../../scripts/daily_report.py) 806 行、[scripts/inbox_sources.py](../../scripts/inbox_sources.py) 685 行、[scripts/inbox.py](../../scripts/inbox.py) 611 行，采集、策略、迁移接线和 CLI 编排开始聚集在少数模块中。
- 当前规模下仍可理解，不建议为了行数立即拆分；下一次新增 provider 时，应先按 provider 拆 collector，并抽取统一的子进程执行边界，避免继续扩大条件分支。
- 现役规格持续追加历史修复过程，标题仍停留在 `统一会话收件箱 v0.4.0`、`工作日报 v3 / schema v6`，正文实际已描述 v0.7.3 与 schema v7。历史证据有价值，但“当前合同”和“演进记录”正逐渐混在一起。
- README 开头仍写“五类来源”，后文和实现已经覆盖更多来源；这不影响运行，但会降低新维护者建立准确项目地图的速度。

## 安全与隐私

没有发现命令拼接注入、SQL 参数拼接注入、正文落库或凭据输出等已确认高危问题。值得保留的现有防线包括：

- 外部会话 URL 只接受已知 scheme 和路径前缀；
- Zcode 查询拒绝控制字符并在执行前核对目标元数据；
- Claude settings 安装拒绝符号链接，写入采用临时文件替换并检测并发修改；
- hook 输入有大小上限，存储只保留管理所需字段；
- SQLite 外部来源普遍使用只读 URI 打开；
- 本项目明确是协作式本机工具，不把恶意本机进程纳入安全边界。

本轮没有进行联网依赖漏洞审计，也没有核对第三方 action 或 Python 包的最新安全公告，因此不能把“未发现代码级高危问题”外推为“供应链无风险”。

## 性能

现有双节拍设计合理：3 秒只读 store，15 秒执行来源扫描，避免 UI 每个 tick 重扫全部来源。Codex 游标签名缓存、Claude 标题负缓存和日报历史固化都针对真实热点做了优化。

目前未发现已确认的性能回归。可关注两处长期成本：

- 每次全量刷新仍需枚举各来源历史文件或表；来源规模继续增长后，应以实测耗时决定是否增加目录级增量索引；
- `_sweep_managed_directories()` 每轮会更新全部匹配行，即使 hidden 值没有变化，可在后续性能整理时改为仅更新发生变化的行。

## 验证记录

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

## 建议处理顺序

1. 修复 Claude 全局错误污染并增加回归测试；这是已确认的用户可见正确性缺陷。
2. 并发读取 Swift 子进程 stdout/stderr，并加入大输出压力测试。
3. 给 standalone bundle 增加发布形态 smoke test，再收紧 CI 写权限。
4. 清理未关闭文件警告，并让 ResourceWarning 进入自动验证。
5. 校准隐私合同和 README 来源数量；下一次新增 provider 前再拆 collector/process runner，不单独启动无行为收益的大重构。
