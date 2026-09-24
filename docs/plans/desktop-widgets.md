# macOS 桌面组件（设计方案）

状态：**已定稿，现行合同见 [桌面组件规范](../specs/desktop-widgets.md)**，本文保留设计背景与 P0 结论，与规范冲突处以规范为准（例如 revision 不一致时照常打开、只跳过自动确认）。编写日期 2026-09-23，基线 v0.7.5 / `485228f`。
依赖：token 与金额的数据来自 [usage-cost-report.md](usage-cost-report.md)；消息与任务的状态语义以 [unified-inbox.md](../specs/unified-inbox.md) 为准。

## 需求

提供 macOS 桌面组件（WidgetKit，macOS 14+ 支持放在桌面和通知中心），覆盖三类信息：

1. **消息**：待查看数量、进行中数量、最新的待处理事项。
2. **token 及金额**：支持切换周期（日 / 周 / 月）和视角（model / harness / 项目）。
3. **最近任务**：最近活动的会话，点击可跳回。

## 结论

- **组件不运行 Python，也不读各来源的原始数据。** 主 App 负责生成一份快照 JSON，组件只读这份快照。WidgetKit 扩展必须运行在沙盒中，既不能执行 `bin/session-manager`，也读不到 `~/.claude` 等目录。现有架构里「App 调 CLI」的方式正好承担快照生成。
- **ad-hoc 签名可行，不需要开发者账号**（P0 实测）。App Group 需要开发者签名，所以改用沙盒的只读路径例外来读取快照。在 macOS 27.0 上，注册、组件库展示、读快照、reload（<1 秒）、点击唤起、重签后保留全部通过。macOS 14、15、26 尚未验证，是剩余风险。
- **跳转复用现有 `inbox open` 链路**：组件通过 URL 唤起 App，由 App 执行带 revision 校验的打开操作，组件本身不做任何定位或确认。

## 架构

```
各来源 ──► Python 采集 / 日报 / 计价 ──► App（3 秒轮询，已有）
                                         │  生成快照（节流）
                                         ▼
                ~/.local/state/session-manager/widget/snapshot.json（0600，原子写）
                                         │  WidgetCenter.reloadTimelines（按变化触发）
                                         ▼
                           Widget 扩展（沙盒，只读快照）
                                         │  点击 → agentnotification://open?...
                                         ▼
                           App → inbox open <id> --revision N
```

### 快照

生成方：App 新增 `WidgetSnapshotWriter`。数据来自现有的 `rows --all`，以及新增的 `inbox usage --json`（见金额方案）。

```json
{
  "schema": 1,
  "generated_at": 1790000000,
  "currency_default": "CNY",
  "fx": {"USD_CNY": 7.10, "as_of": "2026-09-23"},
  "inbox": {
    "pending": 3, "running": 2,
    "items": [{"id": "...", "revision": 12, "provider": "claude", "title": "...",
               "project": "session-manager", "state": "waiting", "at": 1790000000}]
  },
  "usage": {
    "day":   {"totals": {...}, "by": {"harness": [...], "model": [...], "project": [...]}, "series": [...]},
    "week":  {...},
    "month": {...}
  },
  "recent": [{"id": "...", "revision": 5, "provider": "codex", "title": "...", "state": "idle",
              "at": 1790000000, "total_tokens": 1234567, "cost": {"USD": 1.23, "CNY": 0}}]
}
```

- 每个 `by` 维度只保留 Top 5，其余合并为「其他」。
- `cost` 按原币种分别存放；组件按用户选择的币种和快照里的汇率自行换算，与日报界面的算法一致。金额附带 `unpriced_tokens`，组件据此显示「部分未定价」标记。
- 三个周期都放进快照，组件切换周期时无需等待 App 生成新数据。
- 快照只放界面展示所需的元数据，即标题摘要、项目名、状态和数字，与收件箱已保存的字段一致，不新增任何正文内容。

### 刷新策略

WidgetKit 对后台刷新有频率预算，不能跟着 3 秒轮询一起刷新。

| 触发 | 动作 |
|---|---|
| 待查看或进行中数量变化，或最新待处理事项变化 | 立即写快照，并 reload 消息类组件 |
| 最近任务列表的顺序或状态变化 | 写快照，reload 最近任务组件，最小间隔 60 秒 |
| token / 金额 | 每 15 分钟写一次快照，并 reload 用量组件；打开日报窗口时顺带刷新 |
| 时间线兜底 | 组件的 `TimelineProvider` 每 30 分钟请求一次，只重读快照 |

- App 未运行时，快照会逐渐过时。`generated_at` 超过 30 分钟的，组件角落显示「更新于 HH:MM」并淡化数字；超过 24 小时则显示「请打开会话通知」。
- 刷新判定以内容签名为准（数量、ID、revision 组成的哈希）；签名没变就不写文件、不 reload。

## 组件清单

| 组件 | 尺寸 | 内容 | 配置项（AppIntent） |
|---|---|---|---|
| 消息 | 小 | 待查看数量大字、进行中数量、最新一条的来源图标 | — |
| | 中 | 最近 3 条待处理：图标、标题、状态、相对时间 | — |
| | 大 | 待处理最多 6 条 + 进行中最多 3 条 | — |
| 用量 | 小 | 所选周期的金额大字、token 合计、环比箭头 | 周期、币种 |
| | 中 | 左侧合计，右侧按所选视角的 Top 4 横向条 | 周期、视角（model / harness / 项目）、度量（金额 / token）、币种 |
| | 大 | 中尺寸内容 + 周期内逐天柱图（Swift Charts） | 同上 |
| 最近任务 | 中 / 大 | 最近 4 / 8 个任务：图标、标题、状态、时间、token 与金额 | 来源过滤（全部或指定 harness） |

说明：

- 「视角」和「周期」是用户在编辑组件时选择的配置，而不是组件上的切换按钮。组件内可以放 AppIntent 按钮做切换（macOS 14 支持可交互组件），但切换状态需要写回共享存储，而这又依赖 App Group；ad-hoc 签名下没有 App Group，首版不做（见待决策项 3）。
- agent 拉起的会话按收件箱规则过滤，不出现在消息和最近任务中；用量合计口径与日报一致，也不包含这部分。
- 桌面上的内容别人也能看到。提供一个「隐藏标题」开关，打开后组件只显示来源和项目名。

## 点击跳转

- App 注册 URL scheme `agentnotification`；需要在 `build_inbox_app.py` 的 Info.plist 中增加 `CFBundleURLTypes`。
- 点击消息或任务条目：`agentnotification://open?id=<row id>&revision=<n>`。App 收到后走现有的 `inbox open`，成功才自动标记已处理，失败保留未读。revision 过期时（打开前已有新事件）同样按现有校验处理，组件不绕过这一步。
- 点击用量组件：`agentnotification://report?period=week`，打开日报窗口并定位到对应周期。
- 这个 URL 只接受 App 自己生成的参数形态；参数不合法一律拒绝，不做猜测。

## 构建与签名

- 现在的构建方式是 `swiftc` 直接编出单个可执行文件。组件需要额外生成 `Contents/PlugIns/AgentNotificationWidgets.appex`：Info.plist 中 `NSExtension.NSExtensionPointIdentifier = com.apple.widgetkit-extension`，代码仍用 `@main WidgetBundle` 声明组件集合，但进程入口按下一条链接；链接 WidgetKit、SwiftUI、AppIntents。
- 扩展的 entitlements：`com.apple.security.app-sandbox = true`，并用 `com.apple.security.temporary-exception.files.home-relative-path.read-only` 放行 `/.local/state/session-manager/widget/`。
- **入口必须链接为 `_NSExtensionMain`**（`-Xlinker -e -Xlinker _NSExtensionMain`），否则扩展一被拉起就会在 ExtensionFoundation 初始化时崩溃，组件库里也看不到它（P0 实测）。appex 的 Info.plist 要补上 `CFBundleSupportedPlatforms`、`CFBundleInfoDictionaryVersion`、`DTPlatformName`、`DTSDKName`。
- 签名顺序：先签 appex，再签 app，不使用 `--deep`；验证命令 `codesign --verify --strict` 保持不变。
- **配置式组件需要 Xcode**：`AppIntentConfiguration` 依赖只随 Xcode 提供的 `appintentsmetadataprocessor`。CI（macos-26，Xcode 26）具备这个工具；只装了 Command Line Tools 的本地环境没有。构建脚本检测不到时，降级为静态组件（周期 = 今日，视角 = harness，币种沿用 App 设置），并打印提示，做法与 Liquid Glass 图标检测不到 `actool` 时回退相同。
- 构建脚本继续拒绝覆盖正在运行的 App；App 启动过一次即完成组件注册，放在仓库的 `build/` 目录也可以（P0 实测），不要求位于 `/Applications`。
- CI 使用 macos-26 与 Xcode 26，工具链足够；dmg 的 standalone 包同样带上扩展。

## 实施分期

| 阶段 | 内容 | 验收 |
|---|---|---|
| **P0 可行性探针（已完成）** | 最小组件（显示快照里的一个数字）+ 只读路径例外 + ad-hoc 签名 | macOS 27.0 全部通过，见[探针记录](../research/2026-09-23-widget-adhoc-probe.md)；macOS 14 / 15 / 26 待有设备时补测 |
| P1 快照与跳转 | `WidgetSnapshotWriter`、内容签名节流、URL scheme、`open` 链路 | 单测：签名不变不写文件；revision 过期被拒；非法 URL 被拒 |
| P2 消息与最近任务组件 | 两类组件三种尺寸、过时提示、隐藏标题 | 真实数据截图；App 退出后显示过时提示 |
| P3 用量组件 | 周期、视角、度量、币种配置，Swift Charts | 与日报同周期同视角的数字逐项一致 |

P3 依赖金额方案的 P1、P2 完成；P1、P2 可以与金额方案并行推进。

## 待决策项

1. **签名**：已解决，继续使用 ad-hoc 签名，不加入 Apple Developer Program（P0 结论）。
2. **本地构建是否装 Xcode**。推荐装 Xcode（免费，不需要开发者账号），这样本地也能构建可配置的用量组件；不装时本地降级为静态组件，Release 不受影响。
3. **组件内切换**：首版只做配置式选择。ad-hoc 签名下没有 App Group，组件内切换按钮的状态无法写回共享存储，所以暂不做。

## 已知边界

- App 未运行时，组件内容不会更新，只会显示过时提示。
- WidgetKit 刷新频率受系统预算约束，组件状态可能比 App 晚几十秒；「进行中」的实时性以 App 为准。
- ad-hoc 重新签名（CDHash 变化）后，已放置的组件仍然保留，不需要重新添加（P0 在 macOS 27.0 上实测）；其他系统版本未验证。
