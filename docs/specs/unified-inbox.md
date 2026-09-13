# 统一会话收件箱 v0.2.2

状态：v0.2.2 已构建并通过完整 bundle 签名校验；新增 Pi 标题、应用图标、分页、成功打开后自动处理和系统通知；收件箱为单实例（Window 场景），重复点通知或托盘按钮只聚焦已有窗口。列表翻页与通知提交已实测；Zcode 辅助功能授权已生效，模拟事项经 App「打开会话」完成一次真实导航并按点击时 revision 自动确认，完整 GUI 闭环已验收（2026-09-14，证据见文末）。

## 启动与日常使用

```sh
/Users/snowson/workspace/agent/tools/session-manager/bin/session-manager app
```

应用为 `build/SessionInbox.app`。窗口显示“待处理 / 全部会话”，菜单栏图标显示待处理数量，可重新打开列表。窗口关闭后，App 未退出时仍每 3 秒刷新。支持系统通知；没有配置登录自启动或全局热键。全部会话每页 20 条，按最新活动时间倒序；搜索或切换列表重置页码。待处理列表保留等待/错误优先级。

Pi/Kimi 继续使用现有入口启动，之后的会话状态自动进入列表：

```sh
bin/session-manager pi
bin/session-manager kimi
```

Claude 已安装用户级观察 hooks；Kimi 的原有两条绑定 hooks 扩展为八种生命周期事件。hooks 不返回审批决定；Kimi 普通非受管理启动直接返回，不收集数据。既有会话不保证热加载新 hooks，完整实时覆盖从之后启动的会话开始。

“打开会话”成功后自动标记已处理，仍可手动点“已处理”。打开动作携带点击时的 revision；失败保留未读，打开期间到达的新回复不会被旧动作清除。Pi/Kimi/Zcode 使用适配器成功结果；Claude/Codex URL 以 OS 接受分发为成功，不能据此证明目标 UI 已显示。

App 运行时通知新的待处理事件；首次快照静默，不补发历史堆积。铃铛按钮控制通知，系统需允许通知。通知按事项和事件 token 去重；点击通知仅将收件箱窗口置前，不自动打开目标会话，也不改变未读状态（2026-09-14 用户决定，替代原先的"点击即打开目标会话"）。通知内容只包含来源、状态和展示标题。

Pi 标题优先使用自定义会话名，否则截取首条用户消息最多 80 字符；空会话使用项目名。只保存展示摘要，不保存完整正文。既有受管理记录可从身份核对后的本地会话文件限量补标题，后续变更由扩展事件更新。

## 来源与定位

| 来源 | 状态与元数据 | 打开方式 |
|---|---|---|
| Claude Desktop | 本地元数据补标题/工作区/桌面 ID，hooks 更新运行、结束和等待；旧会话没有可靠状态时明确未知 | 已验证的 claude://code/continue 链接 |
| Codex Desktop | rollout 完整行增量读取，session_index 补标题，按会话与 turn ID 去重 | codex://threads 链接；OS 分发不等同于本轮 UI 验证 |
| Zcode | 任务索引提供元数据，运行数据库 turn_usage 提供最新轮次的开始、完成、失败与取消；原生未读标记仅补充 | 已验证的原生 AX 搜索 + 复制任务路径核验 |
| Pi | 原生扩展事件，agent_settled 才标本轮结束 | run/session/lease/前台组验证后的 iTerm2 聚焦 |
| Kimi | 生命周期 hooks；正常/错误/等待分别处理 | 同 Pi |

本轮已导入 480 个 Codex、76 个 Claude、64 个 Zcode 非隐藏会话，初始未读为 0；Pi/Kimi 会在受管理运行后出现。初次刷新约 7.5 秒，增量刷新约 0.12 秒（本机样本，不是性能保证）。

有 13 个 Codex 文件在目标头之后混入其他 session_meta；当前无法无歧义归属，因此保留跳过并显示“历史记录暂未接入”，不把父会话历史当成当前子会话。目标头之前的祖先前缀可跳过，目标头之后出现不同身份仍拒绝。失败文件的签名缓存避免每次重复解析，文件变化后重试。未来 Codex 格式变化仍需维护适配器。

第一次导入不会把所有历史完成记录变成待处理。Codex 和 Zcode 的完成事件均按首次监控时间作为基线；Zcode 的新完成/失败不依赖原生蓝点，原 App 清除蓝点不会替本收件箱确认已处理。CLI 进程退出后入口禁用，但未处理的结果可保留。

Zcode 会用最新轮次的真实时间作事件时钟，任务的改名或查看时间不产生完成事件。旧版以 updated_at 作时钟的数据会一次性迁移，回补监控开始后漏掉的完成；仅匹配任务索引中的 session ID，子 agent 不冒充主任务。修复证据见 [Zcode 待处理遗漏](../research/2026-09-14-zcode-inbox-fix.md)。

## 数据与状态语义

本地状态位于 `~/.local/state/session-manager/inbox.sqlite`，与现有绑定库并存。保存会话标识、标题摘要、项目、状态、活动时间、事件去重 ID、未读版本和定位信息；不保存提示词全文、回复全文、工具参数或凭据。

状态：running、waiting、idle、failed、interrupted、closed、unknown。“本轮已结束”不表示业务目标成功。Stop 后如果继续运行，后续运行事件会清除过期的待处理状态。来源没有事件时不能凭一段时间无输出判断完成。

事件有稳定 ID 时去重；旧时间戳事件不能覆盖较新的状态。CLI hooks 没有可靠 turn ID 的情况按接收顺序处理，尚未承诺异常重排情况下的精确顺序。快照元数据更新不单独构成新注意事项。activity_at 用于排序，与状态事件时钟分离，查看或改名不冒充任务完成。

## CLI 与维护

```sh
bin/session-manager inbox rows --refresh
bin/session-manager inbox rows --all --refresh
bin/session-manager inbox ack ITEM_ID --revision REVISION
bin/session-manager inbox open ITEM_ID
bin/session-manager inbox sync
bin/session-manager inbox setup
```

setup 可重复运行，保留既有配置与 hooks，不重复添加本项目相同命令。自定义 CLAUDE_CONFIG_DIR / KIMI_CODE_HOME 需要在对应环境执行 setup。App 固定关联当前工作目录，移动项目后应重建并更新 hooks。

重建 App：

```sh
python3 scripts/build_inbox_app.py
```

当前产物面向 Apple Silicon、macOS 14+。先退出应用再重建，构建脚本会拒绝覆盖运行中的应用。图标由 AppKit 生成并打包，完整 bundle 使用本地 ad-hoc 签名并校验；这不是分发公证。新签名可能需要重新添加辅助功能授权，旧开关显示开启不代表新版已获授权。

## 验证证据

- 当前 55 项 Python 检查通过，包括重复完成事件不恢复已处理项、旧 UI 不能清掉新回复、迟到事件不覆盖新运行、相同 unread marker 不重复提醒、源配置安装幂等，以及 Zcode 无蓝点完成、旧时钟迁移和独立已处理状态。
- 原生 App 已实际打开，显示真实多来源会话，“全部 / 待处理”过滤工作正常。
- 注入明确标注“模拟事件”的测试项后，UI 待处理计数变成 1；点击已处理后变成 0，数据库确认未读已清除。该模拟项随后隐藏，未操作任何真实待办。
- 未通过本轮 UI 操作去绕过对 Codex/iTerm2 的 Computer Use 限制；对应导航沿用此前已验证的入口，最终从 App 点击的权限行为由用户使用时确认。

实现：[状态库](../../scripts/inbox_store.py)、[来源采集](../../scripts/inbox_sources.py)、[命令入口](../../scripts/inbox.py)、[原生界面](../../native/SessionInbox.swift)、[队列测试](../../tests/test_inbox.py)。

### v0.2 补充验收

- 新增测试覆盖打开成功自动确认、失败保留、新回复竞态、活动时间排序和 Pi 标题身份核验；Swift 纯策略测试覆盖分页边界和通知去重/启动静默。
- GUI 已验证全部会话从第 1 页切换到第 2 页；应用图标已渲染检查。
- 系统设置与 App 均显示通知开启，模拟事件的通知请求被系统接受（App 的通知去重记录含 token）。点击通知的行为改为仅置前收件箱；原先的自动打开实现（pendingNotificationOpen）已移除。用户实际点击验证：收件箱打开，无 Zcode 导航（原生助手日志无新记录），模拟项未读保留；该模拟项验收后已清理（2026-09-14）。
- Zcode 辅助功能授权经用户此前重新添加后已生效（2026-09-14 验收）：模拟事项经 App「打开会话」首次尝试因搜索框焦点竞态被助手拒绝且保留未读、错误行内显示；重试后原生助手完成搜索、粘贴、候选选中与复制任务路径身份核验（selection_identity_matches=true）并置前目标任务「现在的模型」，App 按点击时 revision 自动确认（unread 1→0），界面与数据库一致回到"暂时没有待处理事项"。
