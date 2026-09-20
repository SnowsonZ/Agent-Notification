# 各来源能否走推送（替代 3 秒全量轮询）——2026-09-20 调研

## 背景与问题

App 每 3 秒 tick 一次 `rows --all --refresh`（`native/InboxModels.swift` 的 `Timer(timeInterval: 3)`），每轮都对五个采集器做全量扫描（`scripts/inbox_sources.py: refresh()`）。问题：能否像 Claude/Kimi hooks 那样改为事件推送。

## 现状盘点：七家的消息到达通道

| 来源 | 通道 | 性质 | 证据 |
|---|---|---|---|
| Claude CLI | 用户级观察 hooks（Notification/PermissionRequest/SessionEnd 等） | 推送 | spec「Claude 已安装用户级观察 hooks」 |
| Kimi | 8 种生命周期 hooks（`install_kimi_hooks`） | 推送 | spec、session_binding.py |
| pi（受管） | 包装器 `pi -e pi_capture.ts` 挂 pi 扩展上报（`ui_prompt_start`/`session_shutdown` 等） | 推送 | session_binding.py run 分支 `command += ["-e", pi_capture.ts]` |
| opencode（受管） | 插件事件 `opencode_capture.js` → `session_binding event` | 推送 | spec 2026-09-16 受管理化记录 |
| agy（受管） | 官方插件 hooks 五事件（PreInvocation→running、Stop→待查看），包装器兜底 SessionEnd | 推送 | spec、`agy-hook` 分支 |
| **codex** | rollout 文件被动扫描 | **仅轮询** | `collect_codex`；无任何 hook 接入 |
| **zcode** | `~/.zcode/v2/tasks-index.sqlite` + `~/.zcode/cli/db/db.sqlite` 读库推断 | **仅轮询** | `collect_zcode`；宿主无任务生命周期 hook API（`~/.zcode/hooks/` 只有编辑期 lint-dispatch） |

标题补充采集（`pi_titles`、agy summaries 库）是被动的只读补齐（只补已有行），设计上跟随刷新，不属于本议题。

## codex 实测（本日，沙箱 CODEX_HOME，未动真实配置）

环境：codex-cli 0.154.0（homebrew npm 包，主二进制 `@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex`）。

### 通道一：hooks.json（Claude 风格，推荐）

- 二进制 strings 与官方文档一致：配置键 `hooks`，事件枚举 `PreToolUse / PermissionRequest / PostToolUse / PreCompact / PostCompact / UserPromptSubmit / SessionStart / SessionEnd / SubagentStart / SubagentStop / Stop / Interrupt`；默认启用（`[features] hooks = false` 可关）。
- 配置位置：`~/.codex/hooks.json`（用户级）或 config.toml 内联 `[hooks]`；载荷走 stdin 单个 JSON，含 `session_id / turn_id / transcript_path / cwd / hook_event_name / model / permission_mode`。
- **正测**（沙箱 `CODEX_HOME` + `--dangerously-bypass-hook-trust` + `codex exec`）：一次会话依次收到 `SessionStart(source=startup)` → `UserPromptSubmit(prompt=…)` → `Stop(last_assistant_message=…)` → `SessionEnd(reason=other)`，与现有 claude hooks 管线（`inbox.py hook` → `receive`）载荷模型同构。
- **反测（关键边界）**：不带信任绕过时，**hooks 被静默跳过**——exec 模式无任何警告输出，事件一条不来。非 managed hooks 需用户在 codex TUI 里 `/hooks` 逐个信任，信任按 hook 哈希记录，**每次改 hook 脚本都要重新信任**。这是接入的主要摩擦，性质与 claude「hooks 不保证热加载」同级，需写入安装说明。

### 通道二：notify（对照，不推荐首选）

- `config.toml` 的 `notify = [程序, 参数…]`，回合完成时以末位 argv 传入 kebab-case JSON：`{type: agent-turn-complete, thread-id, turn-id, cwd, client, input-messages, last-assistant-message}`（exec 模式实测触发；文档另称有 approval-requested）。
- 无信任门禁，但**单槽位**：本机已被 computer-use 客户端占用（`SkyComputerUseClient turn-ended`），要接需替换为分发脚本再链回原程序；且事件覆盖远窄于 hooks（无 SessionStart/Interrupt）。

### 结论：codex 可以推送

走用户级 `hooks.json` → 复用 `inbox.py hook` 同款 stdin 管线，事件映射对齐现有 claude 语义（UserPromptSubmit→running、Stop→本轮结束+待查看、PermissionRequest→waiting、SessionEnd→closed、Interrupt→interrupted）。rollout 扫描保留兜底（历史回填、悬置回合合成、未被信任前的会话）。

与 [2026-09-16 受管理论证](2026-09-16-codex-managed-feasibility.md)的关系：该论证针对「受管理绑定」并暂缓，其重估条件「其余生命周期事件（会话开始/等待权限）是否可达」本日已实测满足（hooks.json 全事件可达且无需动被占用的 notify 单槽）；本调研回答的是推送替代轮询，不改变受管理绑定本身的暂缓决策。

**受管模式澄清（同日用户纠偏）**：claude/codex「没有受管模式」是错误表述——准确说法是**未接入**。受管合同（包装器/绑定/运行锁/pane 校验）provider 无关，`session_binding.py` 的 run choices 限四家是范围决定；codex 受管化在 09-16 已论证可行并暂缓。且本日 hooks.json 实测显著降低其拼装成本：SessionStart（带 session_id/cwd/source）可替代「监听新 rollout 文件」的启发式登记，PermissionRequest/Interrupt 直接补齐等待/中断盲区，notify 单槽不用动。未变的增量只剩聚焦强度（挂起/pane 复用拒绝），是否重估由用户决定。claude 增量更小（事件靠用户级 hooks 已全集、聚焦已有确定性方案），维持现状的依据比 codex 更强。

## zcode：无官方推送，只有文件系统事件

宿主 App 不提供任务生命周期 hooks（用户级 hooks 目录仅编辑期 lint）。现实推送通道是 FSEvents/kqueue 监听 `~/.zcode/v2/tasks-index.sqlite*` 与 `~/.zcode/cli/db/db.sqlite*`（SQLite WAL 写入会在目录层产生事件）触发**定向** `collect_zcode`。可行性在 Swift 侧（DispatchSource/FSEvents）成立，但「事件≠状态语义」——触发后仍要跑采集器读库判定，只是省掉无变化时的全量扫描。

## 轮询的职责拆解（2026-09-20 与用户对齐修正）

初版此处把「非受管启动/旧会话/硬杀/回填/健康」笼统列为轮询不可全删的理由，经逐条盘问修正：

- **受管四家裸启动**：设计上不收集（kimi hook 见无绑定即返回、opencode/agy 忽略非受管、pi 无包装器无扩展），轮询也从没覆盖它们——不构成轮询职责（初版表述有误）。
- **claude「旧会话」**：仅指 hooks 安装/更新时刻已在跑的会话（Claude Code 不热加载 hooks），过渡态问题，collect_claude 被动补齐，与刷新频率无关。
- **我方进程死**：零丢失。hooks 独立进程直写 store；codex rollout / zcode sqlite 是源头自维护的落盘数据。重启时读 store + 启动全量扫描即完全恢复——启动扫描就够。
- **agent 进程被硬杀**：无任何事件产生（codex kill -9 后行滞留 running），需在我方持续运行期间周期检查（现实现：rollout mtime 超 24h 判 interrupted）。需要的是低频（几十秒级）兜底，不是 3 秒。
- **历史回填**：装收件箱之前已存在的会话（rollout/claude 本地存储/zcode sqlite 里的存量记录），启动 + 低频扫描覆盖即可。
- **健康状态**：refresh 写 `health` 元数据供 App 降级横幅，跟慢扫描走即可。

**修正结论**：没有任何职责需要 3 秒全量刷新，只需要「非零且低于实时」。合理终态：3 秒 tick 只读 store（渲染）；启动全量扫描；15–30 秒低频兜底扫描（历史回填 + agent 硬杀检查 + 健康状态）；hooks 保即时。真正离不开慢扫描的只有 zcode（无推送通道）与 codex 悬置兜底（若接 hooks 后仍留兜底）。该改动涉及 App 重建与 spec 修订，另立方案评审。

**实施记录（2026-09-21）**：上述终态已落地——3 秒 tick 只读 store（`rows --all` 不带 `--refresh`），全量采集降为每 15 秒（`inboxTickShouldScan` 纯函数门控 + 7 条 policy 断言）；启动/窗口重开/托盘刷新保持全量，开关切换与操作后回读走快读。真机验证 health `checked_at` 20 秒窗口恰好一次推进、间隔 ~15 秒；进程采样捕获到无 --refresh 的快读调用。codex hooks.json 接入与 zcode FSEvents 两个可选项仍待立项。

## 附：claude CLI 会话误判 agent 回归（同日晚间，已修复）

用户报告"codex 能监控、claude code cli 不行"。排查：hooks 与事件管线正常（行都在 store），根因是 origin 判定回归——claude 9 月 19 日 00:42 升级到原生安装器版本 2.1.277（二进制 ~/.local/share/claude/versions/<版本号>，ucomm 为版本号），hook 从"node 版经 sh 包装派生"变为"直接 exec 派生"，hook 的父进程成了 claude 本体（ucomm "2.1.277" 不在 SHELL_PARENTS）→ 终端会话全判 agent → 默认隐藏。store 自升级时刻起 cli 定位行全为 agent、无一条 user，即回归窗口证据。期间一度误归因于第三方 GUI 壳（Claude Code Haha.app），经用户纠正后以 pty 复现抓到真实链路。

修复：`claude_spawn_origin` 识别父进程为 claude 本体（ucomm 为 claude 或版本号形态）时，判定进程上移一层取本体的父进程做 shell 判定；tty 仍以本体为准；旧 sh 包装路径语义不变；ps 输出异常按人工保留。新增 6 项回归测试（终端/工具拉起/无头无登记/无头登记命中/旧 sh 包装/异常输出），全量 168 项通过；pty 真实拓扑（zsh fork → 原生 claude）端到端复现判定 user 通过。注意复现陷阱：`zsh -c` 单命令会 exec 优化使父进程变成 script，须用交互式/加尾命令强制 fork 才忠实于 iTerm。存量误判行不自愈（origin 落库于注册时），已手动翻正用户确认的两条，9/19 凌晨真实目录的候选行待用户确认后处理；探针行已归档（hidden=1）。

## 附二：claude CLI 标题锁死兜底格式（同日，已修复）

用户反馈可见行标题仍显示「claude · <id>」。根因：标题取转写首条用户消息，但转写文件在回合进行中才落盘（实测 `claude -p` 进程存活 6.5s 时才出现），早于落盘的查找被 `claude-cli-title-missing` **永久负缓存**，文件出现后永不再试——node 时代（9/16–9/17）有 7 条 cli 行取到真标题，此后为零。修复：负缓存按会话状态门控（未结束每轮重试、已结束仍缺才永久记忆），并优先用 hook 带来的 locator 转写路径而非按 sid 全目录 glob。数据修复：清除 26 条转写已存在的陈旧负缓存，一轮刷新后真标题行 7→19；`2e8f1fec` 取到「当前模型配置」。`b69d31f4` 转写零用户消息（开了即退），兜底标题为正确行为。新增 2 项测试（落盘竞争重试、locator 路径优先），全量 170 项通过。

## 附三：双轴 code-review 与收敛（2026-09-21）

对当晚修复跑了两轴评审（标准/spec 并行子代理），结论再经人工复核：12 项发现中 1 项前提被实验证伪、1 项与事实不符、2 项不可达/不可操作。成立的修了三处：负缓存规则与 locator 回填收敛进 `_claude_cli_title` 单点（原规则双处编码，漂移会复发标题锁死）；locator 路径失效回退按 sid glob 并纠正 locator 为真实路径（原实现"替代"而非"优先+回退"，路径过期会重新引入永久锁死）；spec 验收记录补条目、过时的「当前 102 项」计数改为历史口径。证伪记录：`claude -p --resume <sid>` 实测派生新 session id（新转写新 sessionId，旧文件仅被触碰）——「resume 复活旧 sid 行导致负缓存不失效」的前提不成立，未为此写代码；同理新代码下陈旧负缓存无再生路径（closed+转写缺失才缓存，该状态下转写不会再出现）。全量 171 项通过。

## 证据留存

沙箱与事件日志：`scratch/codex-hook-probe/`（`home/hooks.json`、`home/config.toml`、`events.jsonl`、`probe.py`；`auth.json` 符号链接已移除）；claude 回归复现：`scratch/claude-hook-probe/`（`probe.sh`、`tree.log`）。
