# 剩余接入问题排查

日期：2026-09-14。当前结论：五种来源均已有状态采集线索，但只有 Claude 完成过真实事件到原会话跳转的一次闭环；不要将状态读取与导航覆盖合并成“全部支持”。

## 当前进展：用户执行 iTerm2 枚举成功

后续聚焦验证已通过（用户实测）：关闭旧标签页后，重新枚举取得 `F141A302-938C-454C-9970-D0391FCCA4E0`；从第二页 `C33A518B-96F2-4253-A620-A854B5E636F6` 执行带 --activate 的探针，返回 matched_session_id 与目标一致、activation_call_completed=true，用户确认实际切回第一页。旧的三枚 ID 已失效或不再作为当前测试依据。

当前状态：API 枚举与一次跨标签页聚焦通过。agent_ownership_verified=false 仍属实，Pi/Kimi 会话与 pane 的归属绑定、退出和复用失效处理尚待实现验证。无需重复让用户跑基础聚焦测试。

用户恢复优先使用 iTerm2，并贴回探针输出：live_session_ids 为 `AF2CC3D2-F1C5-4A4A-AD86-2873CED2B599`、`4443D2EC-9F29-4BF1-B3CF-25297F8D4110`、`29D9FB17-3F3A-40D3-A725-676E0A4F287F`。证据来源是用户本机执行并提供结果，不是 agent 绕过工具限制执行。

API 连接与枚举已通过；activation_requested=false，agent_ownership_verified=false 是该次探针的预期结果，分别表示没有请求聚焦、尚未校验 agent 归属，不是连接失败。下一步从另一标签页以明确 ID 调用 --activate，并核对实际选中的页；随后验证 Pi/Kimi session ID 与 pane 的绑定。下方早期“等待只读探针”的描述已由本结果更新。

## 后续进展：Zcode 身份核验与 iTerm 环境

Zcode 的任务 ID 搜索本次已成功显示实际结果：完整 task ID（sess_afcd78bb…）返回“暂无相关结果”。因此该样例不能直接用 ID 搜索定位。

在已选择的目标任务菜单中，“复制会话 ID”禁用，“复制任务路径”可用。将 UI 复制的值粘贴到临时 TextEdit 文稿，读到路径末段为 `<task_id>.zcode-session`，与索引中的目标 task_id、workspace_path 完全一致。该步骤验证了所选任务身份，但尚未形成稳定的跨任务自动导航器，也没有完成同名候选遍历测试。

临时证据存于 `scratch/zcode-task-path-verification.rtf`，没有保存到 iCloud。特别注意：这个 UI 复制路径实际不存在于磁盘，它是可用于比对身份的逻辑路径，不能用于直接打开文件，也不能将文件不存在误判为当前任务不存在。

`zcode_task_state.py` 新增 `--copied-task-path`，仅将 UI 获取的路径与索引中的 task_id/workspace_path 精确比较，返回 `selection_identity_matches`；依然保持 `exact_session_navigation=false`。调用方必须实际从 UI 获取路径，人工构造一个匹配字符串不构成导航证据。新增两个回归检查，总计 21 项检查通过。

iTerm2 专用环境已准备并验证 import/API 签名：`scratch/iterm-probe-venv/`，iterm2 2.23、protobuf 7.36.1、websockets 17.1。未连接 iTerm API。已请求用户在 iTerm2 执行下列只读命令并返回输出，等待结果：

```sh
scratch/iterm-probe-venv/bin/python scripts/iterm_probe.py
```

该步骤仍需用户或允许的环境执行，因为现有 Computer Use 明确拒绝 iTerm 访问。无需用户自行找 Python 依赖，不关闭 iTerm API 认证，不以另一技术绕过工具拒绝。后续取得真实 pane ID 后才能继续核验 Pi/Kimi 的绑定和聚焦。

## 本轮实测

| 来源 | 新证据 | 未完成项 |
|---|---|---|
| Pi 0.85.1 | 实际模型回复 OK，扩展收到 session_start、agent_start、agent_end、agent_settled(idle=true)、session_shutdown | 运行在工具启动的 CLI，不是 iTerm；pane 绑定和交互等待尚未实测 |
| Kimi 0.42.0 | 实际 CLI + 本地协议 fixture：正常收到 Stop；HTTP 400 收到 StopFailure，进程分别退出 0/1 | 不代表真实 Kimi 服务端验证；prompt 模式本轮未收到配置了的 TurnStarted、SessionEnd；iTerm 导航未测 |
| Codex Desktop / CLI 0.154.0-alpha.6.2 | 当前 App 会话 rollout 包含 10 个 task_started、9 个 task_complete，携带 turn_id；只读解析器成功消费 | 依赖非稳定日志格式；此次未通过 OS 链接导航验收，hooks 投递也未测 |
| Zcode 3.11.2 | 只读 tasks-index.sqlite 查询真实任务，得到 task_id、task_status、unread_at 等字段 | 仅快照验证，未测实时转换；外部精确会话定位仍未解决 |

[机器可读证据](2026-09-14-runtime-evidence.json)保存测试事件和汇总，不保存用户会话正文。

## Pi 扩展

[pi_capture.ts](../../scripts/pi_capture.ts)显式通过 `-e` 加载，不安装全局扩展。实际调用关闭工具、技能、上下文文件和自动扩展发现，使用单独 session 目录，模型为原本配置的 zai-coding-cn/glm-5.3-flash，提示仅要求回复 OK。

实验显示 agent_end 与 agent_settled 相邻但语义不同。主状态源使用 agent_settled 并检查 idle；只把它理解为运行已稳定空闲，不保证业务目标完成。扩展同时准备 ui_prompt_start/end，但本轮未触发交互 UI，不宣称已验证。

扩展只在确实存在 ITERM_SESSION_ID 时记录它；本轮没有生成假的 terminal ID。身份还需要与 iTerm API 的 live session inventory 对齐，再绑定 agent session 和进程生命周期。

## Kimi：配置隔离与事件兼容

官方 [配置覆盖说明](https://www.kimi.com/code/docs/en/kimi-code-cli/configuration/overrides.html)确认当前只读单个用户配置，通过 KIMI_CODE_HOME 隔离；[环境变量文档](https://www.kimi.com/code/docs/en/kimi-code-cli/configuration/env-vars.html)说明该变量同时影响凭据和数据目录。

[probe_kimi_hooks.py](../../scripts/probe_kimi_hooks.py)创建独立 home、配置本机临时 HTTP 服务和固定协议响应，用虚构 key，未复制任何原账号凭据。它运行的是真实 Kimi CLI 和 hook runner；模拟的是模型服务。正常与失败两条路径均捕获 session_id / hook_event_name，与 [官方 hooks 输入格式](https://www.kimi.com/code/docs/en/kimi-code-cli/customization/hooks.html)一致。

复现命令：

```sh
python3 scripts/probe_kimi_hooks.py
python3 scripts/probe_kimi_hooks.py --failure
```

结果保存在每次独立的 `scratch/kimi-probe-*/result.json`。测试只证明该版本这些事件的运行时投递，不将源码中出现的事件名字或新文档条目当作所有模式均已实现。

## Codex：真实 App 日志作为兼容来源

当前任务 header 的 source 是 `vscode`，但 originator 是 `Codex Desktop`。不能只按 source==desktop 做识别。

[codex_rollout_events.py](../../scripts/codex_rollout_events.py)接收一个明确 rollout 路径与预期 session ID，校验 header，仅输出 task_started / task_complete / turn_aborted 的最小元数据。完整行才消费，支持 `--watch`；未完整写入的末行等待下次轮询。文件替换/缩短后重新验证身份，缺少 turn_id 就报不支持；不猜测其他会话。

```sh
python3 scripts/codex_rollout_events.py /absolute/path/rollout.jsonl --session-id <id>
```

每次重新运行会重放文件内历史事件；使用输出的 event_id 去重。它不是完整的跨文件发现与持久游标服务。task_complete 仅代表这一轮完成，不能据此宣称整个需求成功。异常/中断映射需要真实样例继续补充。

导航工具成功切到一个已有空闲任务，之后已恢复当前任务。尝试用 Computer Use 核验 App 时被明确拒绝访问 `com.openai.codex`；未改走其他 UI 控制技术绕过。厂商已记录的 `codex://threads/<id>` 与当前 agent 专用导航工具可用，仍不等于本轮完成了独立程序打开链接的 UI 实测。

## Zcode：状态可读，精确导航保留缺口

[zcode_task_state.py](../../scripts/zcode_task_state.py)以 SQLite mode=ro 和 query_only 查询一个 task_id，不读取 meta_json/searchable_text/凭据。表内 task_status、unread_at 可以成为版本适配的快照源；不要将 updated_at 变动本身当作完成事件。

```sh
python3 scripts/zcode_task_state.py <task-id>
```

脚本明确返回 exact_session_navigation=false；工作区链接单独标记，不能当作原任务链接。查到重复 ID 时不任意选取。真实任务查询成功，但未宣称实际状态转换监听通过。

此前检查的原生通知 click 回调具有 taskId，renderer 用 taskListCache/activeTaskId 找项目并切换任务；这个能力仍位于 App 内部 IPC，没有发现稳定外部入口。UI 搜索验证没有成功提交 ID：文本节点点击无导航，输入框设置未生效，剪贴板读取超时。未把这类工具交互失败解释为“Zcode 搜索不支持 ID”。按止损规则停止这条 UI 路径，没有循环尝试猜测链接或注入内部 IPC。

另外存在版本资料差异：随包 diagnosing-hooks 文档声称项目 hooks 可执行，而 [当前官方文档](https://zcode.z.ai/en/docs/hooks)明确说项目 hooks 被忽略。不能照旧资料复制 Claude 项目级配置到 Zcode。当前用户级 config 文件也不在文档示例位置；本轮未创建全局配置或修改现有任务来试错。

建议：第一版 Zcode 显示状态并明确提供工作区级入口；“五种全部精确跳转”继续作为未完成验收项。若必须一次性全覆盖，需要厂商公开 taskId 路由，或在允许的环境下另验证可按 ID 核对结果的 AX 路径。

## iTerm：明确的外部验证项

准备了 [iterm_probe.py](../../scripts/iterm_probe.py)，基于 [官方 App API](https://iterm2.com/python-api/app.html)和 [Session API](https://iterm2.com/python-api/session.html)。默认只列 live session IDs；指定 --session-id 匹配，--activate 才聚焦，不读写终端内容。

```sh
python3 scripts/iterm_probe.py
python3 scripts/iterm_probe.py --session-id '<observed-id>' --activate
```

需要含官方 iterm2 包的 Python 和已授权的 iTerm API。依赖环境现已准备，使用本文顶部的命令（项目根目录相对路径）。当前 Computer Use 曾明确拒绝 iTerm App 访问，因此 agent 没有执行这个 API 脚本来替代被拒绝的操作；留给用户或允许访问的测试环境执行。不要关闭 API 认证或设置允许任意应用访问以图方便。

live ID 精确匹配只解决 pane 身份，不能证明原 agent 仍占用该 pane。正式实现还需绑定 agent session、进程启动标识及终止事件；同 pane 启动第二个 agent 后必须使旧绑定失效。

## 验证与后续范围

19 项单元/脚本检查通过：Claude 歧义处理、接收器最小化、Codex 半行/轮次/文件替换校验、iTerm 候选冲突和 Zcode 只读/参数化查询。iTerm 的测试仅是纯函数，不是 API 连接证明。

接下来可以在已经验证的来源上实现统一队列与身份模型，不必继续等待所有 UI 条件齐备。但正式展示必须逐来源标出“事件实测/快照读取/导航实测/尚不支持”，不把脚本数量或静态接口发现当作产品完成。
