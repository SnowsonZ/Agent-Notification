# macOS 桌面组件（规范）

状态：**终版设计，待实施**（2026-09-23 定稿）。执行步骤见 [执行计划](../plans/usage-cost-widgets-execution.md)；设计背景见 [方案](../plans/desktop-widgets.md)，签名可行性证据见 [探针记录](../research/2026-09-23-widget-adhoc-probe.md)。本文与方案冲突时以本文为准。
依赖：用量数据来自 [用量金额规范](usage-cost.md) 的 `inbox usage`；消息与状态语义以 [统一收件箱](unified-inbox.md) 为准。

## 1. 架构约束

- 组件是 WidgetKit 扩展，运行在沙盒里，**只读一份快照文件**。它不运行 Python，不读任何来源的原始数据，也不做定位或确认。
- 快照由主 App 生成。点击组件时通过 URL 唤起 App，由 App 走现有的 `inbox open` 链路。
- 签名方式：ad-hoc 签名，不使用 App Group。组件通过沙盒只读例外读取快照，这条路径已由探针验证。
- 平台：macOS 14+，与 App 一致。

## 2. 快照

- 路径：`~/.local/state/session-manager/widget/snapshot.json`。目录权限 0700，文件权限 0600，采用「临时文件 + rename」的原子写入。
- 写入方：App 中的 `WidgetSnapshotWriter`。
- 数据结构的 Swift 定义放在 `native/Shared/WidgetSnapshot.swift`，App 和组件编译同一份文件。

```json
{
  "schema": 1,
  "generated_at": 1790000000,
  "prefs": {"currency": "USD", "hide_titles": false, "fallback": {"period": "day", "dimension": "harness"}},
  "fx": {"USD_CNY": 7.10, "as_of": "2026-09-23"},
  "inbox": {
    "pending": 3, "running": 2,
    "pending_items": [{"id": "…", "revision": 12, "provider": "claude", "title": "…", "project": "session-manager", "state": "waiting", "at": 1790000000}],
    "running_items": [ ]
  },
  "usage": {"day": {"…": "inbox usage 单周期输出，by 各维度截为 Top 5 + 其他"}, "week": {}, "month": {}},
  "usage_at": 1790000000,
  "recent": [{"id": "…", "revision": 5, "provider": "codex", "title": "…", "project": "…", "state": "idle", "at": 1790000000,
              "today_tokens": 1234567, "today_cost": {"input": {}, "cache": {}, "output": {}, "unpriced_tokens": 0}}]
}
```

字段规则：

- `pending_items` 最多 6 条，`running_items` 最多 3 条，`recent` 最多 8 条。
- 所有条目都排除 agent 拉起的会话（按有效来源 `origin == "agent"` 判断，即使 App 打开了 agent 审计开关也要排除）。`pending` 数量与 `pending_items` 的口径和 Dock 角标完全一致：`inboxNotifyEligible && inboxRowListed`（`native/InboxModels.swift`）。
- `recent` 按最近活动时间倒序。`today_*` 取自今日报告中 `(provider, session_id)` 匹配的任务；今天没有用量的任务，这两个字段为 null，组件显示 `—`。
- `hide_titles` 为 true 时，App 写入的 `title` 为空字符串。组件只显示来源和项目名；标题根本不写入文件，而不只是在显示时隐藏。
- `usage.*.by` 的截断：每个维度保留前 5 名，其余合并为 `{"key": "__other__"}`。
- 快照只包含收件箱已经保存的元数据和数字，不新增任何正文内容。

## 3. 写入与刷新

内容签名：对快照的各分区分别计算哈希。

| 分区 | 签名内容 | 写入与 reload |
|---|---|---|
| inbox | pending、running 数量，以及各条目的 `id:revision:state` | 签名变化时，立即写快照并 reload `inbox` kind |
| recent | 各条目的 `id:revision:state` | 签名变化时写快照；`recent` kind 的 reload 最小间隔 60 秒，间隔内的变化合并到下一次 |
| usage | 无签名，按时间刷新 | 每 15 分钟执行一次 `inbox usage --period all --json`，写快照并 reload `usage` kind；打开日报窗口时也立即刷新 |
| prefs / fx | 值本身 | 变化时写快照，reload 全部 kind |

- **签名没变就不写文件、不 reload。** 这段判断写成纯函数 `WidgetRefreshPolicy`，放在 `native/InboxPolicy.swift` 或同级文件，并编写单测。
- 组件的 `TimelineProvider` 以 30 分钟为周期（`policy: .after(+30min)`），到期只重读快照。
- **过时提示**：`now − generated_at > 30 分钟`时，右上角显示「更新于 HH:MM」，数字降低透明度；超过 24 小时，组件显示「请打开会话通知」。用量分区看 `usage_at`，其余分区看 `generated_at`。
- 快照不存在或无法解析时，组件显示「请打开会话通知」，不崩溃。

## 4. 组件清单

| kind | 尺寸 | 内容 | 配置（AppIntent） |
|---|---|---|---|
| `inbox` 消息 | 小 | 待查看数量大字、进行中数量、最新一条的来源图标 | 无 |
| | 中 | 最近 3 条待处理：图标、标题、状态、相对时间 | 无 |
| | 大 | 待处理最多 6 条，加进行中最多 3 条 | 无 |
| `usage` 用量 | 小 | 所选周期 token 大字（中上）、金额伴随行（USD、放大无标签）、环比（涨红降绿） | 周期、视角 |
| | 中 | 左侧 token 大字 + $ 金额伴随 + 环比（上对齐）；右侧所选视角 Top 4（官方图标 + token + $，无横条——行宽不足）；行内不足五名按实有数 | 周期、视角（harness / model / 项目） |
| | 大 | 中尺寸内容，加上周期内逐天柱图（Swift Charts） | 同上 |
| `recent` 最近任务 | 中 / 大 | 最近 4 / 8 个任务：图标、标题、状态、相对时间、今日 token 与金额 | 来源过滤（全部 / 指定 harness） |

- **配置的默认值**：周期 = 今日，视角 = harness。度量与币种配置已取消（2026-09-24 重设计）：token 恒为主线、金额恒为 USD 伴随。App 设置里提供这两个默认项（组件默认设置面板）。
- **降级构建**：构建环境缺少 AppIntents 元数据工具时（见 §6），`usage` 与 `recent` 改为 `StaticConfiguration`，读取快照中的 `prefs.fallback`。App 设置里提供这三个默认项，并注明「当前构建不支持在组件上配置」。
- 组件上不放切换按钮：ad-hoc 签名下没有 App Group，切换状态无法写回。
- 来源图标和颜色复用 `ProviderStyles.swift`（Shared，主 App 与组件共用）与 `native/agent-icons`；构建脚本把图标资源复制进 appex，`zcode.png` 由已安装 Zcode.app 图标现场提取（缺席退化品牌色字牌）；暗色变体（pi/opencode）走 Shared/IconPipeline。
- 金额显示使用 [用量金额规范](usage-cost.md) §5 的 `money_text` 与换算规则。所有数字都要能从快照推出，组件不自行计算任何口径。

## 5. URL 与跳转

App 注册 URL scheme `agentnotification`，写在 `build_inbox_app.py` 的 Info.plist `CFBundleURLTypes` 中。

| URL | 行为 |
|---|---|
| `agentnotification://open?id=<row id>&revision=<int>` | 等价于在 App 中点击该行：执行 `inbox open <id> --revision <n>`。沿用现有语义：打开成功后，只有 revision 仍然一致时才自动确认；打开期间来了新事件，就不确认，保留未读；打开失败也保留未读。 |
| `agentnotification://report?period=day\|week\|month` | 打开日报窗口，并切到对应周期的本期。 |
| `agentnotification://inbox` | 打开收件箱窗口。 |

- **参数校验**：
  - host 必须在上表之中。
  - `id` 必须存在于 App 当前的行集合里。
  - `revision` 必须是非负整数。
  - `period` 必须是枚举值之一。
  - 不满足任一条件时不执行任何动作，只写一条不含参数原文的诊断日志。
- 组件只使用 `widgetURL`（小尺寸）或 `Link`（中、大尺寸的逐行链接）。

## 6. 构建

- **源码位置**：
  - `native/Widget/*.swift`：组件代码。放在子目录，是为了不被主程序构建脚本的 `native/*.swift` 通配编进 App。
  - `native/Shared/*.swift`：App 和组件共用的代码。构建脚本需要把这个目录显式加入主程序源码列表。
- **产物**：`Agent Notification.app/Contents/PlugIns/Agent Notification Widgets.appex`（目录名含空格，与主程序包命名一致；可执行文件为 `AgentNotificationWidgets`）。开发包和 `--standalone` 包都要带上。（2026-09-24 验收同步：按实际产物命名）
  - bundle id：`local.session-manager.inbox.widgets`。
  - `CFBundlePackageType = XPC!`。
  - `NSExtension.NSExtensionPointIdentifier = com.apple.widgetkit-extension`。
  - 补齐 `CFBundleSupportedPlatforms = [MacOSX]`、`CFBundleInfoDictionaryVersion`、`DTPlatformName`、`DTSDKName`。
  - 版本号与主程序同步：`CFBundleShortVersionString` 和 `CFBundleVersion` 都从同一个变量读取，满足「两处不能只改其一」的发布约束。
- **编译**：
  - 使用 `swiftc -parse-as-library -application-extension -target arm64-apple-macosx14.0`，链接 SwiftUI、WidgetKit、AppIntents、Foundation。
  - **必须加 `-Xlinker -e -Xlinker _NSExtensionMain`**。探针证实缺了这一项，组件一被拉起就会崩溃。
- **entitlements**：只有两项。
  - `com.apple.security.app-sandbox = true`
  - `com.apple.security.temporary-exception.files.home-relative-path.read-only = ["/.local/state/session-manager/widget/"]`
- **签名**：先对 appex 做 ad-hoc 签名并带上 entitlements，再签主程序，不使用 `--deep`。现有的 `codesign --verify --deep --strict` 校验保留。
- **AppIntents 元数据**：
  - 检测 `xcrun -f appintentsmetadataprocessor`。存在时，用 `-emit-const-values` 编译，再生成 `Metadata.appintents` 放进 appex 的 Resources，并构建配置式组件（`-D WIDGET_APPINTENTS`）。
  - 不存在时，构建降级的静态组件，并打印提示。
  - 做法与 Liquid Glass 图标检测不到 `actool` 时回退一致。CI 的 macos-26 带有 Xcode，Release 必须是配置式；**CI 在 standalone 构建后要断言 appex 内存在 `Metadata.appintents`。**
- **CI 新增检查**：appex 存在且签名有效；`plutil` 断言上述 plist 键；`otool -l` 断言入口符号为 `_NSExtensionMain`，可用 `nm -u` 检查引用；`codesign -d --entitlements` 断言 entitlements 恰好是上面两项。

## 7. 验收

证据类型与覆盖列由 `harness/acceptance.py` 检查（见 [delivery-harness.md](delivery-harness.md)）。

| # | 项 | 方法 | 证据类型 | 覆盖 |
|---|---|---|---|---|
| W1 | 注册与组件库展示 | 构建开发包并启动 App 后，`pluginkit -m -p com.apple.widgetkit-extension` 能列出组件；组件库里 3 个 kind 的各尺寸预览正常（用户 UI 验收） | 真机 UI | 用户 UI 验收 |
| W2 | 快照读取 | 组件显示的数字与快照一致；删除快照后显示「请打开会话通知」，没有新的崩溃报告 | 真机 UI | 用户 UI 验收 |
| W3 | 刷新 | 制造一条新的待处理事项，组件在 5 秒内更新；签名不变时文件的 mtime 不变（单测覆盖 `WidgetRefreshPolicy`） | 单测 + 真机 UI | `tests/InboxPolicyTests.swift#WidgetRefreshPolicy.evaluate` |
| W4 | 跳转 | 点击待处理条目后，打开原会话并自动确认；打开前先制造新事件，打开后仍为未读；非法 URL 不执行动作（URL 解析写成纯函数并单测，端到端由用户 UI 验收） | 单测 + 真机 UI | `tests/InboxPolicyTests.swift#WidgetURLRouter.parse`、`tests/InboxPolicyTests.swift#WidgetURLQueue()` |
| W5 | 用量一致 | 同一周期、同一视角下，组件数字与 `inbox usage` 和日报界面一致 | 真实数据 + 真机 UI | 用户对照组件、CLI 与日报界面 |
| W6 | 隐私 | `hide_titles` 打开后，快照文件中没有标题；诊断日志中没有 URL 参数原文 | 单测 + 架构 | `tests/InboxPolicyTests.swift#widgetEntryText(hideTitles: true`、`test_architecture.WidgetWriterUsesPolicyTest` |
| W7 | 过时提示 | 退出 App 超过 30 分钟后显示「更新于」 | 真机 UI | 用户 UI 验收 |
| W8 | 构建两种形态 | 本机只有 CLT 时构建出降级组件；CI 构建出配置式组件，且 Metadata 断言通过 | CI 断言 + 人工 | `.github/workflows/build.yml#Metadata.appintents` |
| W9 | 重签保留 | 重新构建（CDHash 变化）后，已放置的组件仍在并能正常刷新 | 真机 UI | 用户 UI 验收 |

**验证边界**：W1、W4 端到端、W7、W9 需要用户在真机上做 UI 验收。macOS 14、15、26 的验收在有设备时补做，结果记入探针调研文档；没有覆盖到的系统不得写成「已验证」。
