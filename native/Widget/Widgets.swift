import WidgetKit
import SwiftUI
#if WIDGET_APPINTENTS
import AppIntents
#endif

// 桌面组件（desktop-widgets.md）：三个 kind——inbox / usage / recent，
// kind 字符串与 App 侧 WidgetSnapshotWriter.reloadTimelines 一致。
// 组件运行在沙盒里，只读一份快照文件（~/.local/state/session-manager/widget/
// snapshot.json），不运行 Python、不做定位或确认；点击经 agentnotification://
// 唤起主 App 走现有 inbox open 链路（§1、§5）。
// 构建环境有 appintentsmetadataprocessor 时 -D WIDGET_APPINTENTS 编配置式；
// 缺失时（仅 CLT 的本机）编降级静态组件，读取快照里的 prefs.fallback（§4、W8）。
// 组件不自行计算任何口径：数字全部来自快照，金额文字用共享的 moneyText。

// 沙盒内 NSHomeDirectory() 是容器目录，按真实 uid 取 home（探针结论
// docs/research/2026-09-23-widget-adhoc-probe.md）。
enum WidgetSnapshotReader {
    static func homeDirectory() -> String {
        let entry = getpwuid(getuid())
        guard let dir = entry?.pointee.pw_dir else { return NSHomeDirectory() }
        return String(cString: dir)
    }

    static func load() -> WidgetSnapshot? {
        let path = homeDirectory() + "/.local/state/session-manager/widget/snapshot.json"
        guard let data = FileManager.default.contents(atPath: path) else { return nil }
        return try? JSONDecoder().decode(WidgetSnapshot.self, from: data)
    }
}

// 快照整体状态：不存在或无法解析时组件显示「请打开会话通知」（§3）；
// 过时（>30 分钟）数字降低透明度并显示更新时间，>24 小时只显示提示文字。
struct WidgetSnapshotBox {
    var snapshot: WidgetSnapshot?
    var referenceAt: Double = 0  // inbox/recent 看 generated_at，usage 看 usage_at
    var tooOld: Bool = false

    var stale: Bool { referenceAt > 0 && (Date().timeIntervalSince1970 - referenceAt) > 30 * 60 }
    var updatedText: String? {
        guard stale, referenceAt > 0, !tooOld else { return nil }
        let formatter = DateFormatter()
        formatter.dateFormat = "HH:mm"
        return "更新于 " + formatter.string(from: Date(timeIntervalSince1970: referenceAt))
    }
}

func widgetBox(_ snapshot: WidgetSnapshot?, usageScoped: Bool = false) -> WidgetSnapshotBox {
    guard let snapshot else { return WidgetSnapshotBox() }
    let reference = usageScoped ? snapshot.usageAt : snapshot.generatedAt
    let age = Date().timeIntervalSince1970 - reference
    return WidgetSnapshotBox(snapshot: snapshot, referenceAt: reference, tooOld: age > 24 * 3600)
}

let widgetProviderNames = [
    "claude": "Claude", "codex": "Codex", "zcode": "Zcode",
    "pi": "Pi", "kimi": "Kimi", "opencode": "OpenCode", "agy": "AGY",
]

func widgetProviderName(_ key: String) -> String { widgetProviderNames[key] ?? key }

@main
struct AgentNotificationWidgets: WidgetBundle {
    var body: some Widget {
        InboxWidget()
        UsageWidget()
        RecentWidget()
    }
}

// MARK: - inbox kind（无配置；小/中/大）

struct InboxEntry: TimelineEntry {
    let date: Date
    let box: WidgetSnapshotBox
}

struct InboxProvider: TimelineProvider {
    func placeholder(in context: Context) -> InboxEntry {
        InboxEntry(date: Date(), box: WidgetSnapshotBox())
    }

    func getSnapshot(in context: Context, completion: @escaping (InboxEntry) -> Void) {
        completion(InboxEntry(date: Date(), box: widgetBox(WidgetSnapshotReader.load())))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<InboxEntry>) -> Void) {
        let entry = InboxEntry(date: Date(), box: widgetBox(WidgetSnapshotReader.load()))
        completion(Timeline(entries: [entry], policy: .after(Date().addingTimeInterval(30 * 60))))
    }
}

struct InboxWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "inbox", provider: InboxProvider()) { entry in
            InboxWidgetView(entry: entry)
        }
        .configurationDisplayName("会话通知")
        .description("待查看与进行中的 agent 会话")
        .supportedFamilies([.systemSmall, .systemMedium, .systemLarge])
    }
}

// MARK: - usage kind（配置式：周期 / 视角 / 度量 / 币种；降级：读 prefs.fallback）

struct UsageConfiguration: Equatable {
    var period = "day"
    var dimension = "harness"
    var metric = "cost"
    var currency: String? = nil  // nil = 跟随 App（§4 配置默认值）
}

struct UsageEntry: TimelineEntry {
    let date: Date
    let box: WidgetSnapshotBox
    let configuration: UsageConfiguration
}

struct UsageSnapshotProvider {
    static func read(configuration: UsageConfiguration) -> UsageEntry {
        UsageEntry(date: Date(), box: widgetBox(WidgetSnapshotReader.load(), usageScoped: true), configuration: configuration)
    }

    // 降级形态的配置来源：App 设置写入的 prefs.fallback 同款 UserDefaults 键。
    static func fallbackConfiguration() -> UsageConfiguration {
        let defaults = UserDefaults.standard
        return UsageConfiguration(
            period: defaults.string(forKey: "widgetFallbackPeriod") ?? "day",
            dimension: defaults.string(forKey: "widgetFallbackDimension") ?? "harness",
            metric: defaults.string(forKey: "widgetFallbackMetric") ?? "cost",
            currency: nil
        )
    }
}

#if WIDGET_APPINTENTS
// AppEnum 提供选项菜单且 macOS 14 可用（DynamicOptionsProvider 形态的
// @Parameter(optionsProvider:) 要 macOS 26）。
enum UsagePeriodKind: String, AppEnum {
    case day, week, month
    static var typeDisplayRepresentation: TypeDisplayRepresentation = "周期"
    static var caseDisplayRepresentations: [UsagePeriodKind: DisplayRepresentation] = [
        .day: "今日", .week: "本周", .month: "本月"
    ]
}

enum UsageDimensionKind: String, AppEnum {
    case harness, model, project
    static var typeDisplayRepresentation: TypeDisplayRepresentation = "视角"
    static var caseDisplayRepresentations: [UsageDimensionKind: DisplayRepresentation] = [
        .harness: "来源", .model: "模型", .project: "项目"
    ]
}

enum UsageMetricKind: String, AppEnum {
    case cost, tokens
    static var typeDisplayRepresentation: TypeDisplayRepresentation = "度量"
    static var caseDisplayRepresentations: [UsageMetricKind: DisplayRepresentation] = [
        .cost: "金额", .tokens: "token"
    ]
}

enum UsageCurrencyKind: String, AppEnum {
    case app, usd = "USD", cny = "CNY"
    static var typeDisplayRepresentation: TypeDisplayRepresentation = "币种"
    static var caseDisplayRepresentations: [UsageCurrencyKind: DisplayRepresentation] = [
        .app: "跟随 App", .usd: "USD", .cny: "CNY"
    ]
}

struct UsageIntent: WidgetConfigurationIntent {
    static var title: LocalizedStringResource = "用量视图"
    static var description = IntentDescription("选择周期、视角、度量与币种。")

    @Parameter(title: "周期", default: .day) var period: UsagePeriodKind
    @Parameter(title: "视角", default: .harness) var dimension: UsageDimensionKind
    @Parameter(title: "度量", default: .cost) var metric: UsageMetricKind
    @Parameter(title: "币种", default: .app) var currency: UsageCurrencyKind

    var resolved: UsageConfiguration {
        UsageConfiguration(
            period: period.rawValue,
            dimension: dimension.rawValue,
            metric: metric.rawValue,
            currency: currency == .app ? nil : currency.rawValue
        )
    }
}

struct UsageIntentProvider: AppIntentTimelineProvider {
    func placeholder(in context: Context) -> UsageEntry {
        UsageSnapshotProvider.read(configuration: UsageConfiguration())
    }

    func snapshot(for configuration: UsageIntent, in context: Context) async -> UsageEntry {
        UsageSnapshotProvider.read(configuration: configuration.resolved)
    }

    func timeline(for configuration: UsageIntent, in context: Context) async -> Timeline<UsageEntry> {
        let entry = UsageSnapshotProvider.read(configuration: configuration.resolved)
        return Timeline(entries: [entry], policy: .after(Date().addingTimeInterval(30 * 60)))
    }
}

struct UsageWidget: Widget {
    var body: some WidgetConfiguration {
        AppIntentConfiguration(kind: "usage", intent: UsageIntent.self, provider: UsageIntentProvider()) { entry in
            UsageWidgetView(entry: entry)
        }
        .configurationDisplayName("用量金额")
        .description("按 API 标价估算的 token 消耗与金额")
        .supportedFamilies([.systemSmall, .systemMedium, .systemLarge])
    }
}
#else
struct UsageWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "usage", provider: UsageStaticProvider()) { entry in
            UsageWidgetView(entry: entry)
        }
        .configurationDisplayName("用量金额")
        .description("当前构建不支持在组件上配置")
        .supportedFamilies([.systemSmall, .systemMedium, .systemLarge])
    }
}

struct UsageStaticProvider: TimelineProvider {
    func placeholder(in context: Context) -> UsageEntry {
        UsageSnapshotProvider.read(configuration: UsageConfiguration())
    }

    func getSnapshot(in context: Context, completion: @escaping (UsageEntry) -> Void) {
        completion(UsageSnapshotProvider.read(configuration: UsageSnapshotProvider.fallbackConfiguration()))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<UsageEntry>) -> Void) {
        let entry = UsageSnapshotProvider.read(configuration: UsageSnapshotProvider.fallbackConfiguration())
        completion(Timeline(entries: [entry], policy: .after(Date().addingTimeInterval(30 * 60))))
    }
}
#endif

// MARK: - recent kind（中/大；§4 来源过滤为配置式专属，降级显示全部）

struct RecentEntry: TimelineEntry {
    let date: Date
    let box: WidgetSnapshotBox
    let providerFilter: String?  // nil = 全部
}

struct RecentProvider: TimelineProvider {
    func placeholder(in context: Context) -> RecentEntry {
        RecentEntry(date: Date(), box: WidgetSnapshotBox(), providerFilter: nil)
    }

    func getSnapshot(in context: Context, completion: @escaping (RecentEntry) -> Void) {
        completion(RecentEntry(date: Date(), box: widgetBox(WidgetSnapshotReader.load()), providerFilter: nil))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<RecentEntry>) -> Void) {
        let entry = RecentEntry(date: Date(), box: widgetBox(WidgetSnapshotReader.load()), providerFilter: nil)
        completion(Timeline(entries: [entry], policy: .after(Date().addingTimeInterval(30 * 60))))
    }
}

struct RecentWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "recent", provider: RecentProvider()) { entry in
            RecentWidgetView(entry: entry)
        }
        .configurationDisplayName("最近任务")
        .description("最近会话与今日用量")
        .supportedFamilies([.systemMedium, .systemLarge])
    }
}

// MARK: - 组件视图（D4 细化视觉；数据全部来自快照，金额走共享 moneyText）

struct UsageStaleHint: View {
    let box: WidgetSnapshotBox

    var body: some View {
        if box.tooOld {
            Text("请打开会话通知").font(.caption2).foregroundStyle(.orange)
        } else if let updated = box.updatedText {
            Text(updated).font(.caption2).foregroundStyle(.secondary)
        }
    }
}

struct InboxWidgetView: View {
    let entry: InboxEntry

    var body: some View {
        Group {
            if let snapshot = entry.box.snapshot {
                VStack(alignment: .leading, spacing: 4) {
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        Text("\(snapshot.inbox.pending)")
                            .font(.system(size: 36, weight: .bold))
                            .foregroundStyle(snapshot.inbox.pending > 0 ? Color.primary : Color.secondary)
                        Text("待查看").font(.caption).foregroundStyle(.secondary)
                        Spacer()
                        Text("进行中 \(snapshot.inbox.running)").font(.caption2).foregroundStyle(.secondary)
                    }
                    Spacer()
                    if let first = snapshot.inbox.pendingItems.first {
                        let title = first.title.isEmpty ? first.project : first.title
                        Text(widgetProviderName(first.provider) + " · " + title)
                            .font(.caption2).lineLimit(2).foregroundStyle(.secondary)
                    }
                    UsageStaleHint(box: entry.box)
                }
            } else {
                Text("请打开会话通知").font(.caption)
            }
        }
        .widgetURL(URL(string: "agentnotification://inbox"))
    }
}

struct UsageWidgetView: View {
    let entry: UsageEntry

    private var snapshot: WidgetSnapshot? { entry.box.snapshot }
    private var currency: String {
        entry.configuration.currency ?? snapshot?.prefs.currency ?? "CNY"
    }
    private var rate: Double { snapshot?.fx.usdCny ?? 7.10 }

    private var usage: WidgetUsagePayload? {
        snapshot?.usage[entry.configuration.period]
    }

    private var dimensionRows: [WidgetUsagePayload.By.Row] {
        guard let by = usage?.by else { return [] }
        switch entry.configuration.dimension {
        case "model": return by.model
        case "project": return by.project
        default: return by.harness
        }
    }

    private var tokenTotal: Int? {
        guard let usage else { return nil }
        let tokens = usage.totals.totalTokens
        return tokens > 0 ? tokens : nil
    }

    private var money: Double? {
        guard let cost = usage?.totals.cost else { return nil }
        let total = widgetMoneyTotal(cost, currency: currency, rate: rate)
        return total > 0 ? total : nil
    }

    private var environmentChange: Double? {
        guard let usage, let previous = usage.previous else { return nil }
        let current = usage.totals.totalTokens
        guard previous.totalTokens > 0, current > 0 else { return nil }
        return Double(current - previous.totalTokens) / Double(previous.totalTokens)
    }

    var body: some View {
        Group {
            if snapshot == nil {
                Text("请打开会话通知").font(.caption)
            } else if let usage {
                VStack(alignment: .leading, spacing: 3) {
                    HStack(alignment: .firstTextBaseline, spacing: 4) {
                        if entry.configuration.metric == "tokens" {
                            Text(tokenTotal.map { tokenText($0) } ?? "—")
                                .font(.system(size: 26, weight: .bold))
                                .minimumScaleFactor(0.6)
                        } else {
                            Text(moneyText(money, currency: currency))
                                .font(.system(size: 26, weight: .bold))
                                .minimumScaleFactor(0.6)
                        }
                        if let change = environmentChange {
                            Image(systemName: change >= 0 ? "arrow.up.right" : "arrow.down.right")
                                .font(.caption2).foregroundStyle(change >= 0 ? Color.secondary : Color.green)
                        }
                    }
                    if entry.configuration.metric != "tokens" {
                        Text(tokenTotal.map { "\($0) tokens" } ?? "—").font(.caption2).foregroundStyle(.secondary)
                    }
                    ForEach(dimensionRows.prefix(2), id: \.key) { row in
                        Text(widgetProviderName(row.key) + " " + percent(row, usage))
                            .font(.caption2).lineLimit(1).foregroundStyle(.secondary)
                    }
                    Spacer()
                    HStack {
                        Text(periodLabel(usage.period)).font(.caption2).foregroundStyle(.secondary)
                        Spacer()
                        UsageStaleHint(box: entry.box)
                    }
                }
            } else {
                Text("用量待刷新").font(.caption)
            }
        }
        .widgetURL(URL(string: "agentnotification://report?period=" + entry.configuration.period))
    }

    private func percent(_ row: WidgetUsagePayload.By.Row, _ usage: WidgetUsagePayload) -> String {
        guard usage.totals.totalTokens > 0 else { return "" }
        let share = Double(row.totalTokens) / Double(usage.totals.totalTokens)
        return String(format: "%.0f%%", share * 100)
    }

    private func periodLabel(_ period: String) -> String {
        ["day": "今日", "week": "本周", "month": "本月"][period] ?? period
    }
}

struct RecentWidgetView: View {
    let entry: RecentEntry

    private var items: [WidgetSnapshot.RecentItem] {
        guard let snapshot = entry.box.snapshot else { return [] }
        return snapshot.recent.filter { entry.providerFilter == nil || $0.provider == entry.providerFilter }
    }

    var body: some View {
        Group {
            if entry.box.snapshot == nil {
                Text("请打开会话通知").font(.caption)
            } else if items.isEmpty {
                Text("暂无最近任务").font(.caption).foregroundStyle(.secondary)
            } else {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(items.prefix(6), id: \.id) { item in
                        Link(destination: destination(item)) {
                            VStack(alignment: .leading, spacing: 1) {
                                HStack(spacing: 4) {
                                    Text(widgetProviderName(item.provider)).font(.caption2).foregroundStyle(.secondary)
                                    if !item.title.isEmpty || !item.project.isEmpty {
                                        Text(item.title.isEmpty ? item.project : item.title)
                                            .font(.caption).lineLimit(1)
                                    }
                                    Spacer()
                                    Text(relative(item.at)).font(.caption2).foregroundStyle(.tertiary)
                                }
                                if let tokens = item.todayTokens {
                                    let money = item.todayCost.flatMap { widgetMoneyTotal($0, currency: "CNY", rate: entry.box.snapshot?.fx.usdCny ?? 7.10) }
                                    Text(tokenText(tokens) + (money.map { " · " + moneyText($0, currency: "CNY") } ?? ""))
                                        .font(.caption2).foregroundStyle(.tertiary)
                                }
                            }
                        }
                    }
                    Spacer()
                    UsageStaleHint(box: entry.box)
                }
            }
        }
    }

    // 组件只走 widgetURL/Link 唤起 App（§5）；点击 recent 条目进收件箱并打开该行。
    private func destination(_ item: WidgetSnapshot.RecentItem) -> URL {
        URL(string: "agentnotification://open?id=\(item.id)&revision=\(item.revision)")
            ?? URL(string: "agentnotification://inbox")!
    }

    private func relative(_ at: Double) -> String {
        let interval = Date().timeIntervalSince1970 - at
        if interval < 60 { return "刚刚" }
        if interval < 3600 { return "\(Int(interval / 60))分钟前" }
        if interval < 86400 { return "\(Int(interval / 3600))小时前" }
        return "\(Int(interval / 86400))天前"
    }
}
