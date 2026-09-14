# Zcode 状态语义排查：等待权限与回复完毕均显示「本轮已结束」

调研日期：2026-09-14。证据边界：本机实测（Zcode CLI 运行库与任务索引数据库查询、Zcode 桌面应用包内容观察、收件箱库与采集器代码走读）。应用包内容观察反映当日安装版本，Zcode 更新后需复核；本文不是行为合同，规格以 [unified-inbox](../specs/unified-inbox.md) 为准。

## 问题

用户报告：Zcode 会话无论「等待权限批准」还是「回复完毕」，卡片状态都显示橙色的「本轮已结束」，无法区分。

## 数据链路

1. 展示层：`native/SessionInbox.swift` 的 `stateName` 把库内状态映射为中文文案——`running`→运行中、`waiting`→等待输入、`idle`→本轮已结束、`failed`→发生错误、`interrupted`→已中断、`closed`→已退出、`unknown`→状态待确认。卡片上的时长（如「1分钟40秒」）是 `max(activity_at, event_at)` 的 SwiftUI 相对时间，即距最近一次事件过了多久，不是回合耗时。
2. 存储：`~/.local/state/session-manager/inbox.sqlite` 的 `sessions.state`，由 `store.event()` 按「时间戳不早于现有事件才覆盖」更新。
3. 采集：`scripts/inbox_sources.py` 的 `collect_zcode`，数据来自两处——
   - `~/.zcode/v2/tasks-index.sqlite` 的 `tasks.task_status`（仅当会话在 `turn_usage` 无任何行时作快照）；
   - `~/.zcode/cli/db/db.sqlite` 的 `turn_usage`（有回合历史时以此为准）。

## 实测证据

### 1. `turn_usage` 只在回合结束后落行

表结构 `status` 列的 CHECK 约束只有 `running/completed/error/cancelled`，没有等待类状态。排查当时有一个正在运行的 Zcode 会话（任务索引 `task_status='running'`），而 `turn_usage` 中该会话 0 行，且全表不存在 `status='running'` 或 `completed_at IS NULL` 的行——回合运行期间没有行，结束时才写入。

```sql
sqlite3 ~/.zcode/cli/db/db.sqlite "SELECT session_id,turn_id,status,started_at,completed_at FROM turn_usage WHERE session_id='<运行中会话>'"
-- 结果为空
```

### 2. 任务索引的 `task_status` 词表只有三种

```sql
sqlite3 ~/.zcode/v2/tasks-index.sqlite "SELECT task_status, COUNT(*) FROM tasks GROUP BY task_status"
-- completed | 74; error | 2; running | 1（无 waiting）
```

因此 `inbox_sources.py` 中 `{'completed': 'idle', ..., 'waiting': 'waiting'}` 映射里的 `waiting` 分支对 Zcode 是死代码。

### 3. 桌面端把「等待权限」折叠进 running，不持久化

Zcode.app 的 `Contents/Resources/app.asar` 内可观察到：

- `statusFromZCodeSession`：内部状态 `running/waiting/paused` 一律映射为 `running` 再落库；持久化词表即 `["running","completed","error"]`。
- 内部状态词表 `["idle","running","waiting","paused","completed","error"]` 含 waiting，但「等待权限」体现为会话 projection 的 `pendingPermissions` 数组，只存在于桌面 UI 内存，不写入任务索引，也不写入 CLI 运行库。

### 4. CLI 运行库无替代信号

- `permission` 表：项目级配置（`project_id` 主键 + `data` JSON），排查时为空，不是待审批队列。
- `session.permission` 列：存权限模式（`{"mode":"yolo"}` / `plan` / `build` / `edit`），不是等待状态。
- `part` 表：工具调用行持久化为 `completed/error/running`，无 `pending` 或审批请求行。

### 5. 事件时序使 running 被同批覆盖

`collect_zcode` 从 `turn_usage` 取「最新一行」生成事件。由于该表只在回合结束后有行，回合 N 的 turn-start（running）与 turn-end（idle/failed/interrupted）必然在同一次刷新里先后写入，running 立即被终态覆盖；`task_status='running'` 的快照分支只在会话无任何回合历史时生效。结果：Zcode 会话整个回合运行期间（含等待权限）显示的是上一回合的终态，通常是「本轮已结束」。

## 结论

- 对 Zcode 来源：`waiting` 不可达（映射是死分支）、`running` 形同虚设（同批被覆盖）、`closed` 无事件源（靠任务索引 archived/deleted 隐藏）。
- 「等待权限批准」与「回复完毕」在当前数据源下不可区分，均呈现为「本轮已结束」。这是 Zcode 持久化边界，不是采集器缺陷。
- 「本轮已结束」的准确含义：Zcode 记录的最近一个回合以 completed 结束；等待权限期间显示的是上一个已记录回合的残影。

## 修复方向

1. 已实施（2026-09-14，运行中推断）：`collect_zcode` 在 task_status 为 running、更新时间晚于最新已记录回合结束、且该回合结束事件不是本批刚落库时，推断“运行中”；推断时间戳不超过该回合结束时刻，真实完成事件始终能覆盖推断。回归测试见 `tests/test_zcode_inbox.py`：活跃回合显示运行中且刷新幂等、真实完成覆盖推断并恢复待处理、滞后索引不能覆盖已记录完成。已用本机真实库副本验证完整生命周期：推断生效、真实完成后回到“本轮已结束”。残余边界：索引自身冻结在 completed 的运行中会话仍显示上一回合终态（实测存在，桌面端并非每个回合都回写索引）；“等待权限”显示“运行中”而非“等待输入”。
2. 彻底区分“等待权限”需 Zcode 将 `pendingPermissions`（或等价信号）持久化到可读位置后回补采集。
