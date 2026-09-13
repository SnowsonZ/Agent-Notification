# 统一会话收件箱 v0.1

状态：本地原生 App 已编译，真实数据导入及列表 UI 验证完成。可查看、筛选和搜索会话，显式标记已处理，并调用已有定位入口。尚未重新逐一验收从新 App 发起五种宿主导航；首次使用可能需要系统授权。

## 启动与日常使用

```sh
/Users/snowson/workspace/agent/tools/session-manager/bin/session-manager app
```

应用为 `build/SessionInbox.app`。窗口显示“待处理 / 全部会话”，菜单栏图标显示待处理数量，可重新打开列表。窗口关闭后，App 未退出时仍每 3 秒刷新。没有配置登录自启动、全局热键或系统通知横幅。

Pi/Kimi 继续使用现有入口启动，之后的会话状态自动进入列表：

```sh
bin/session-manager pi
bin/session-manager kimi
```

Claude 已安装用户级观察 hooks；Kimi 的原有两条绑定 hooks 扩展为八种生命周期事件。hooks 不返回审批决定；Kimi 普通非受管理启动直接返回，不收集数据。既有会话不保证热加载新 hooks，完整实时覆盖从之后启动的会话开始。

“打开会话”不会清除未读。需要用户明确点“已处理”。旧窗口中的已处理动作携带 revision；如果新回复先到达，旧操作被拒绝并提示刷新，不吞掉新事件。

## 来源与定位

| 来源 | 状态与元数据 | 打开方式 |
|---|---|---|
| Claude Desktop | 本地元数据补标题/工作区/桌面 ID，hooks 更新运行、结束和等待；旧会话没有可靠状态时明确未知 | 已验证的 claude://code/continue 链接 |
| Codex Desktop | rollout 完整行增量读取，session_index 补标题，按会话与 turn ID 去重 | codex://threads 链接；OS 分发不等同于本轮 UI 验证 |
| Zcode | 只读任务索引中的状态和 unread_at | 已验证的原生 AX 搜索 + 复制任务路径核验 |
| Pi | 原生扩展事件，agent_settled 才标本轮结束 | run/session/lease/前台组验证后的 iTerm2 聚焦 |
| Kimi | 生命周期 hooks；正常/错误/等待分别处理 | 同 Pi |

本轮已导入 480 个 Codex、76 个 Claude、64 个 Zcode 非隐藏会话，初始未读为 0；Pi/Kimi 会在受管理运行后出现。初次刷新约 7.5 秒，增量刷新约 0.12 秒（本机样本，不是性能保证）。

有 13 个 Codex 文件在目标头之后混入其他 session_meta；当前无法无歧义归属，因此保留跳过并显示“历史记录暂未接入”，不把父会话历史当成当前子会话。目标头之前的祖先前缀可跳过，目标头之后出现不同身份仍拒绝。失败文件的签名缓存避免每次重复解析，文件变化后重试。未来 Codex 格式变化仍需维护适配器。

第一次导入不会把所有历史完成记录变成待处理。Codex 按首次监控时间作为基线；Zcode 尊重原 unread_at。CLI 进程退出后入口禁用，但未处理的结果可保留。

## 数据与状态语义

本地状态位于 `~/.local/state/session-manager/inbox.sqlite`，与现有绑定库并存。保存会话标识、标题、项目、状态、事件去重 ID、未读版本和定位信息；不保存提示词全文、回复全文、工具参数或凭据。

状态：running、waiting、idle、failed、interrupted、closed、unknown。“本轮已结束”不表示业务目标成功。Stop 后如果继续运行，后续运行事件会清除过期的待处理状态。来源没有事件时不能凭一段时间无输出判断完成。

事件有稳定 ID 时去重；旧时间戳事件不能覆盖较新的状态。CLI hooks 没有可靠 turn ID 的情况按接收顺序处理，尚未承诺异常重排情况下的精确顺序。快照元数据更新不单独构成新注意事项。

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

当前产物面向 Apple Silicon、macOS 14+。不要在 App 运行时覆盖可执行文件，先退出应用再重建。

## 验证证据

- 44 项 Python 检查通过，包括重复完成事件不恢复已处理项、旧 UI 不能清掉新回复、迟到事件不覆盖新运行、相同 unread marker 不重复提醒、源配置安装幂等。
- 原生 App 已实际打开，显示真实多来源会话，“全部 / 待处理”过滤工作正常。
- 注入明确标注“模拟事件”的测试项后，UI 待处理计数变成 1；点击已处理后变成 0，数据库确认未读已清除。该模拟项随后隐藏，未操作任何真实待办。
- 未通过本轮 UI 操作去绕过对 Codex/iTerm2 的 Computer Use 限制；对应导航沿用此前已验证的入口，最终从 App 点击的权限行为由用户使用时确认。

实现：[状态库](../../scripts/inbox_store.py)、[来源采集](../../scripts/inbox_sources.py)、[命令入口](../../scripts/inbox.py)、[原生界面](../../native/SessionInbox.swift)、[队列测试](../../tests/test_inbox.py)。
