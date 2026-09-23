# 桌面组件 ad-hoc 签名可行性探针（2026-09-23）

调研日期：2026-09-23。环境：macOS 27.0（26A428），Apple Silicon，只装了 Command Line Tools（Swift 6.4），没有 Xcode。
关联方案：[desktop-widgets.md](../plans/desktop-widgets.md) P0。探针源码与构建脚本在 `scratch/widget-probe/`，不提交。

## 结论

**不需要 Apple Developer 账号，ad-hoc 签名可以实现桌面组件。** 组件方案 P0 列出的关键项在本机全部通过：注册、出现在组件库、沙盒读快照、宿主触发刷新、点击唤起宿主，以及重新签名后已放置的组件不失效。

构建方式要满足两个硬性要求：

1. **组件入口必须链接为 `_NSExtensionMain`**（`-Xlinker -e -Xlinker _NSExtensionMain`）。如果用 `@main WidgetBundle` 生成的 `main` 作入口，扩展一被拉起就会在 `ExtensionFoundation` 的 `_EXRunningExtension._shared` 初始化处因空值断言崩溃（`EXC_BREAKPOINT`，没有错误信息），组件库也就看不到它。Xcode 默认会加这个链接参数，所以手写 `swiftc` 时容易漏掉。
2. **appex 的 Info.plist 要补上平台元数据**：`CFBundleSupportedPlatforms`、`CFBundleInfoDictionaryVersion`、`DTPlatformName`、`DTSDKName`，与 Xcode 产物保持一致。探针是和入口修正一起加的，没有单独验证这几项是否必需，按 Xcode 产物的做法保留。

## 探针结构

- 宿主 `local.session-manager.widget-probe`：ad-hoc 签名，不开沙盒，`LSUIElement`，注册 URL scheme `agentnotification-probe`。启动时原子写入 `~/.local/state/session-manager/widget-probe/snapshot.json`（0600，内容为计数加时间），然后调用 `WidgetCenter.reloadAllTimelines()` 和 `getCurrentConfigurations`，并把收到的 URL 记入 `host.log`。
- 组件 `local.session-manager.widget-probe.widgets`：ad-hoc 签名，entitlements 为 `app-sandbox`，外加 `temporary-exception.files.home-relative-path.read-only = ["/.local/state/session-manager/widget-probe/"]`；用 `StaticConfiguration` 显示快照计数，`widgetURL` 指向宿主 scheme。读取路径用 `getpwuid` 取真实 home，因为沙盒内的 `NSHomeDirectory()` 是容器目录。组件把每次读取结果写入自己容器中的 `probe.log`，作为证据。
- 签名顺序：先签 appex（带 entitlements），再签宿主，不使用 `--deep`；`codesign --verify --strict` 通过。

## 验证结果

| 项 | 结果 | 证据 |
|---|---|---|
| 系统注册 | 通过 | `pluginkit -m -p com.apple.widgetkit-extension` 列出探针；从 `scratch/` 路径直接运行即可，无需放进 `/Applications` |
| amfid 对 ad-hoc 签名的态度 | 只记录不阻止 | 日志显示 `adhoc signed or signed by an unknown certificate chain`，但扩展照常被拉起（`Successfully spawned`） |
| 组件描述查询 | 修正入口后通过 | chronod 日志：`query returned 1 widget descriptors`；修正前每次都崩溃，报告为 `ProbeWidget-*.ips` |
| 出现在组件库 | 通过 | 用户截图：搜索「WidgetProbe」可见小、中两种尺寸，预览中渲染出快照值 `#4` |
| 沙盒读快照 | 通过 | 容器日志 `home=~/Library/Containers/…widgets/Data text=#4`，与宿主写入值一致 |
| 放置到桌面 | 通过 | 宿主的 `getCurrentConfigurations` 返回 `probe/systemMedium` |
| 宿主触发刷新 | 通过，延迟 < 1 秒 | 宿主 14:48:34 写入 `#5` 并 reload，组件 14:48:34 读到 `#5` |
| 点击唤起宿主 | 通过 | 宿主收到 `agentnotification-probe://open?id=probe&revision=1`，用户确认显示正确 |
| ad-hoc 重签后保留 | 通过 | 改代码后 CDHash 从 `887d160f…` 变为 `91eb858f…`，桌面组件的配置仍在，并读到新快照 `#7` |

附带观察：

- 点击时 NotificationCenter 记录了一条错误级日志 `Tapped widget was not flagged as visible`，但 URL 仍然送达。桌面组件点击时都会出现，不影响功能，记录在此备查。
- 宿主注册后，chronod 会自动拉起扩展查询组件描述，不需要用户打开组件库。所以构建问题可以通过 `~/Library/Logs/DiagnosticReports/<扩展名>-*.ips` 和 chronod 日志自行诊断，不依赖 UI。
- zsh 中的 `log` 是内建命令，查询统一日志要写 `/usr/bin/log show`。

## 未覆盖范围

- **只在 macOS 27.0 上测过**。项目最低支持 macOS 14，而 14、15、26 上 ad-hoc 组件的行为没有验证。
- **没有测 Release 形态**：CI 在 macos-26 上构建的 standalone dmg 安装到 `/Applications` 之后的行为未测。
- **配置式组件**（`AppIntentConfiguration`，用于选择周期、视角、币种）需要 `appintentsmetadataprocessor` 提取元数据。这个工具只随 Xcode 提供，本机 Command Line Tools 里没有，所以没有测。CI 的 macos-26 带 Xcode 26，但本地开发构建会缺这一步。
- 没有测系统刷新预算：宿主作为常驻 accessory 应用时，频繁 reload 是否会被限流。
- 没有测只读例外之外的写入需求，例如组件内切换状态要写回共享存储。

## 清理

探针宿主会在 10 分钟后自动退出。移除方法：在桌面上把 Widget Probe 组件拖走或右键移除，删除 `scratch/widget-probe/build/`，再删除 `~/.local/state/session-manager/widget-probe/` 和 `~/Library/Containers/local.session-manager.widget-probe.widgets/`。
