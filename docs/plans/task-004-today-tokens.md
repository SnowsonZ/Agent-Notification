# 任务：组件「最近任务」的今日 token 从未显示（H0926-7、H0926-8）

状态：待执行（2026-09-26 由设计与评审方按[任务模板](../templates/task.md)写成，来源：方案 §13 V4 真机验收；用户 2026-09-26 同意修复）。执行方由评审方派发。

## 缺陷

2026-09-26 V4 验收：新版 App 19:09 启动。19:09 与 19:20 快照各写了一次，8 条最近任务里有 5 条在当天日报里有用量，但快照中带今日 token 的始终是 0 条。

- **H0926-7（根因）**：`native/InboxModels.swift` 的 `InboxRow.sessionID` 永远是 nil。
  - `rows` 的 JSON 字段是 `session_id`，解码用 `convertFromSnakeCase`，转出的键是 `sessionId`，与属性名 `sessionID` 对不上。
  - 可选属性对不上键时不报错，只是为空。因此用量键恒为 `"<provider>:"`，一条都查不到。
  - 评审方已用最小 Swift 程序复现（`{"session_id":"abc"}` 解码后 `sessionID` 为 nil）。
  - V080-R9 的原修复与任务 003 的测试都只测了纯函数，没有经过真实解码，所以没发现。
- **H0926-8（次要）**：最近任务的签名只由 `id:revision:state` 组成（`native/WidgetSnapshotWriter.swift` 的 `recentSignature`），不含今日用量。修好 H0926-7 后，今日用量异步加载完时签名不变，快照不重写；要等 15 分钟一次的 usage 刷新或条目变化才写入。规格的节拍是 5 分钟。

## 目标终态

- `InboxRow` 的会话 ID 能从 `rows` 的 `session_id` 正确解码。改属性名为 `sessionId` 并同步调用处，或显式写 CodingKeys，二选一，以改动小为准。
- 新增 Python 测试（例如 `tests/test_swift_models.py`）：扫描 `native/` 下用 `convertFromSnakeCase` 解码的 `Decodable` 结构体，已声明的属性名中不允许出现连续大写（如 `ID`、`URL`），除非该结构体显式声明了 `CodingKeys`。测试里标注 `H0926-7`。
- 最近任务的签名由 `native/InboxPolicy.swift` 中的纯函数生成：输入最近任务条目，签名包含每条的 `id`、`revision`、`state`、`todayTokens`，以及 `todayCost` 中决定显示的内容。写入器改用它。今日用量变化（从无到有、数值变化、从有到无）会改变签名，用量不变时签名不变。
- 其余刷新策略不变：recent 的 reload 最小间隔 60 秒，usage 15 分钟，prefs 变化 reload 全部。

## 提交顺序（三个提交）

1. **修复 H0926-7，带 `Defect: H0926-7`**：改解码，并新增上面的 Python 测试。退回代码改动后，测试以断言失败结束。
2. **重构，不带 `Defect`，带 `Risk: R1`**：新增签名纯函数，行为与现状完全一致（不含用量），写入器改用它。
3. **修复 H0926-8，带 `Defect: H0926-8`**：让该函数把用量计入签名，并在 `tests/InboxPolicyTests.swift` 加断言，标注 `H0926-8`。

第 2、3 个提交分开的原因：修复证据检查会把修复提交的代码改动退回，要求测试以断言失败结束。若签名函数随修复才出现，退回后测试只会编译失败，证据不成立（任务 003 的 PR #35 即卡在这里）。

## 非目标与禁止动作

- 不改快照格式（`native/Shared/WidgetSnapshot.swift` 的字段与编码）、`WidgetRefreshPolicy.evaluate` 的判定规则、Python 端 `rows` 与日报的输出。
- 不修改已有测试，只新增。
- 不编辑 `harness/`。回放用例由评审方加入：在 PR 里为 H0926-7、H0926-8 各写一个注入点，包括文件、修复后的原文（恰好出现一次的一行）、退回成的写法，以及应当失败的测试或检查。
- 不改写已推送历史，不推 tag，不合并、不批准 PR。

## 前置条件（不满足就停下报告）

- `python3 harness/git_guard.py status` 显示已安装；`bin/verify` 在当前 main 上通过（macOS，含 swift-policy）。

## 验收

| 编号 | 验收内容 | 证据类型 | 覆盖（测试名或验证步骤） |
|---|---|---|---|
| — | 会话 ID 能正确解码，同类命名问题被静态检查拦住 | 单测 | 新增的 Python 测试（标注 H0926-7） |
| — | 用量从无到有、数值变化、从有到无时签名改变；用量相同时签名相同 | 单测 | `tests/InboxPolicyTests.swift` 中标注 H0926-8 的断言 |
| — | 两个修复的修复前测试都以断言失败结束 | 修复证据 | 本地 `python3 harness/evidence.py --base origin/main --swift`；CI 的 macOS build job |
| — | App 能编译，现有检查通过 | verify | `bin/verify --full` |
| — | 真机上最近任务显示今日 token | 人工 | 由评审方构建后与用户确认（方案 §13 V4） |

## 风险等级

预判 R3，因为改到 `native/WidgetSnapshotWriter.swift`。以 CI 中 `harness/risk.py` 的判定为准。回滚方式：revert 该 PR。

## 预算（超出即停止，把升级包交给评审方）

- CI 轮次：最多 3 轮
- 同一失败的重试：最多 2 次，每次必须带新的信息

## 交付要求

- 提交前 `bin/verify` 与 `bin/verify --full` 通过；本地 `evidence.py` 显示 H0926-7、H0926-8 均为「修复前失败、修复后通过」，原样摘录进 PR。
- PR 按 `.github/pull_request_template.md` 填写，不手写「已通过」。

## 升级包（卡住时填写）

- 当前状态与目标差距：
- 已尝试的方案与结果：
- 证据（失败日志、测试输出）：
- 可选方案与推荐：
- 需要评审方或用户决定的具体问题：
