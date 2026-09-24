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
// 2026-09-24 重设计：token 为主线、金额（USD）是伴随指标；配置精简为 周期·视角；
// 官方来源图标（WidgetIcons.swift，native/agent-icons 资源由构建脚本复制进 appex）。

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
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try? decoder.decode(WidgetSnapshot.self, from: data)
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

// MARK: - usage kind（配置式：周期 / 视角；降级：读 prefs.fallback）
// 2026-09-24 重设计：度量与币种配置取消——token 恒为主线、金额（USD）恒为伴随。

struct UsageConfiguration: Equatable {
    var period = "day"
    var dimension = "harness"
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
            dimension: defaults.string(forKey: "widgetFallbackDimension") ?? "harness"
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

struct UsageIntent: WidgetConfigurationIntent {
    static var title: LocalizedStringResource = "用量视图"
    static var description = IntentDescription("选择周期与视角；token 为主线，金额（USD）伴随展示。")

    @Parameter(title: "周期", default: .day) var period: UsagePeriodKind
    @Parameter(title: "视角", default: .harness) var dimension: UsageDimensionKind

    var resolved: UsageConfiguration {
        UsageConfiguration(period: period.rawValue, dimension: dimension.rawValue)
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
        .description("token 消耗与估算金额（USD）")
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

// MARK: - 组件视图（数据全部来自快照；金额 USD-only；官方图标见 WidgetIcons.swift）

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

// 环比文字：涨红降绿（花钱视角），与日报同一语义。
@ViewBuilder
func usageDeltaText(_ change: Double) -> some View {
    let up = change >= 0
    Text((up ? "▲ " : "▼ ") + String(format: "%.0f%%", abs(change) * 100))
        .font(.system(size: 9, weight: .semibold)).monospacedDigit()
        .foregroundStyle(up ? Color(red: 1.0, green: 0.271, blue: 0.227) : Color(red: 0.188, green: 0.820, blue: 0.345))
}

struct InboxWidgetView: View {
    @Environment(\.widgetFamily) private var family
    let entry: InboxEntry

    var body: some View {
        Group {
            if let snapshot = entry.box.snapshot {
                switch family {
                case .systemSmall: small(snapshot)
                case .systemLarge: large(snapshot)
                default: medium(snapshot)
                }
            } else {
                Text("请打开会话通知").font(.caption)
            }
        }
        .widgetURL(URL(string: "agentnotification://inbox"))
    }

    // 小：待查看大字 + 进行中 + 最新一条来源
    private func small(_ snapshot: WidgetSnapshot) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text("\(snapshot.inbox.pending)")
                    .font(.system(size: 38, weight: .bold))
                    .foregroundStyle(snapshot.inbox.pending > 0 ? Color.primary : Color.secondary)
                Text("待查看").font(.caption2).foregroundStyle(.secondary)
            }
            Spacer()
            HStack(spacing: 5) {
                Text("进行中 \(snapshot.inbox.running)").font(.caption2).foregroundStyle(.secondary)
                Spacer()
                if let first = snapshot.inbox.pendingItems.first {
                    AgentWidgetIcon(id: first.provider, size: 13)
                    Text(widgetProviderName(first.provider)).font(.caption2).foregroundStyle(.tertiary)
                }
            }
            UsageStaleHint(box: entry.box)
        }
    }

    // 中：最近 3 条待处理（官方图标、标题、状态、相对时间）
    private func medium(_ snapshot: WidgetSnapshot) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text("\(snapshot.inbox.pending) 待查看").font(.caption).foregroundStyle(.secondary)
                Spacer()
                Text("进行中 \(snapshot.inbox.running)").font(.caption2).foregroundStyle(.secondary)
            }
            // R14：中/大尺寸逐条 Link（§5），点击直接打开对应会话。
            ForEach(snapshot.inbox.pendingItems.prefix(3), id: \.id) { item in
                Link(destination: itemDestination(item)) {
                    HStack(spacing: 6) {
                        AgentWidgetIcon(id: item.provider, size: 15)
                        Text(item.title.isEmpty ? item.project : item.title)
                            .font(.caption).lineLimit(1)
                        Spacer()
                        Text(relative(item.at)).font(.caption2).foregroundStyle(.tertiary)
                    }
                }
            }
            if snapshot.inbox.pendingItems.isEmpty {
                Text("没有待处理事项").font(.caption).foregroundStyle(.tertiary)
            }
            Spacer()
            UsageStaleHint(box: entry.box)
        }
    }

    // 大：待处理最多 6 条 + 进行中最多 3 条
    private func large(_ snapshot: WidgetSnapshot) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("待查看 \(snapshot.inbox.pending)").font(.caption).foregroundStyle(.secondary)
            ForEach(snapshot.inbox.pendingItems.prefix(6), id: \.id) { item in
                itemRow(item)
            }
            if !snapshot.inbox.runningItems.isEmpty {
                Text("进行中 \(snapshot.inbox.running)").font(.caption).foregroundStyle(.secondary)
                    .padding(.top, 2)
                ForEach(snapshot.inbox.runningItems.prefix(3), id: \.id) { item in
                    itemRow(item)
                }
            }
            Spacer()
            UsageStaleHint(box: entry.box)
        }
    }

    private func itemRow(_ item: WidgetSnapshot.Inbox.Item) -> some View {
        Link(destination: itemDestination(item)) {
            HStack(spacing: 6) {
                AgentWidgetIcon(id: item.provider, size: 15)
                Text(item.title.isEmpty ? item.project : item.title)
                    .font(.caption).lineLimit(1)
                Spacer()
                Text(item.state).font(.caption2).foregroundStyle(.tertiary)
                Text(relative(item.at)).font(.caption2).foregroundStyle(.tertiary)
            }
        }
    }

    private func itemDestination(_ item: WidgetSnapshot.Inbox.Item) -> URL {
        URL(string: "agentnotification://open?id=\(item.id)&revision=\(item.revision)")
            ?? URL(string: "agentnotification://inbox")!
    }

    private func relative(_ at: Double) -> String {
        let interval = Date().timeIntervalSince1970 - at
        if interval < 60 { return "刚刚" }
        if interval < 3600 { return "\(Int(interval / 60))分" }
        if interval < 86400 { return "\(Int(interval / 3600))时" }
        return "\(Int(interval / 86400))天"
    }
}

struct UsageWidgetView: View {
    @Environment(\.widgetFamily) private var family
    let entry: UsageEntry

    private var snapshot: WidgetSnapshot? { entry.box.snapshot }
    private var rate: Double { snapshot?.fx.usdCny ?? 7.10 }

    private var usage: WidgetUsagePayload? { snapshot?.usage[entry.configuration.period] }

    private var dimensionRows: [WidgetUsagePayload.By.Row] {
        guard let by = usage?.by else { return [] }
        switch entry.configuration.dimension {
        case "model": return by.model
        case "project": return by.project
        default: return by.harness
        }
    }

    private var tokenTotalText: String {
        guard let usage, usage.totals.totalTokens > 0 else { return "—" }
        return tokenText(usage.totals.totalTokens)
    }

    private var money: Double? { usdTotal(usage?.totals.cost, rate: rate) }

    private var environmentChange: Double? {
        guard let usage, let previous = usage.previous else { return nil }
        guard previous.totalTokens > 0, usage.totals.totalTokens > 0 else { return nil }
        return Double(usage.totals.totalTokens - previous.totalTokens) / Double(previous.totalTokens)
    }

    var body: some View {
        Group {
            if snapshot == nil {
                Text("请打开会话通知").font(.caption)
            } else if let usage {
                switch family {
                case .systemSmall: small(usage)
                case .systemLarge: large(usage)
                default: medium(usage)
                }
            } else {
                Text("用量待刷新").font(.caption)
            }
        }
        .widgetURL(URL(string: "agentnotification://report?period=" + entry.configuration.period))
    }

    // 小：token 大字（中上）+ 金额伴随行（放大、无标签）+ 环比
    private func small(_ usage: WidgetUsagePayload) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text("用量").font(.system(size: 11, weight: .semibold)).foregroundStyle(.secondary)
                Spacer()
                Text(periodLabel(usage.period)).font(.caption2).foregroundStyle(.tertiary)
            }
            Text(tokenTotalText)
                .font(.system(size: 27, weight: .bold)).minimumScaleFactor(0.7)
                .padding(.top, 18)
            Text(usdText(money))
                .font(.system(size: 15, weight: .semibold)).monospacedDigit()
                .padding(.top, 4)
            if let change = environmentChange {
                usageDeltaText(change).padding(.top, 8)
            }
            Spacer(minLength: 0)
            UsageStaleHint(box: entry.box)
        }
    }

    // 中：左侧合计（上对齐），右侧所选视角 Top 4（图标 + token + $）
    private func medium(_ usage: WidgetUsagePayload) -> some View {
        HStack(alignment: .top, spacing: 14) {
            VStack(alignment: .leading, spacing: 0) {
                Text("用量 · " + periodLabel(usage.period))
                    .font(.system(size: 11, weight: .semibold)).foregroundStyle(.secondary)
                Text(tokenTotalText)
                    .font(.system(size: 23, weight: .bold)).minimumScaleFactor(0.7)
                    .padding(.top, 12)
                Text(usdText(money))
                    .font(.system(size: 14, weight: .semibold)).monospacedDigit()
                    .padding(.top, 5)
                if let change = environmentChange {
                    usageDeltaText(change).padding(.top, 6)
                }
                Spacer(minLength: 0)
                UsageStaleHint(box: entry.box)
            }
            .frame(width: 96, alignment: .leading)
            VStack(alignment: .leading, spacing: 0) {
                let rows = dimensionRows.filter { $0.totalTokens > 0 && $0.key != "__other__" }
                    .sorted { $0.totalTokens > $1.totalTokens }
                if rows.isEmpty {
                    Text("暂无来源数据").font(.caption2).foregroundStyle(.tertiary)
                }
                ForEach(rows.prefix(4), id: \.key) { row in
                    HStack(spacing: 6) {
                        dimensionIcon(row.key)
                        Text(displayName(row.key))
                            .font(.caption2).lineLimit(1)
                        Spacer(minLength: 6)
                        Text(tokenText(row.totalTokens))
                            .font(.caption2.weight(.semibold)).monospacedDigit()
                        Text(usdText(usdTotal(row.cost, rate: rate)))
                            .font(.caption2).monospacedDigit().foregroundStyle(.secondary)
                            .frame(minWidth: 52, alignment: .trailing)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    // 大：中尺寸内容 + 周期内逐天三类堆叠柱 + 口径注脚
    private func large(_ usage: WidgetUsagePayload) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("用量 · " + periodLabel(usage.period))
                    .font(.system(size: 11, weight: .semibold)).foregroundStyle(.secondary)
                Spacer()
                Text("token · 来源").font(.caption2).foregroundStyle(.tertiary)
            }
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(tokenTotalText).font(.system(size: 24, weight: .bold)).minimumScaleFactor(0.7)
                Text(usdText(money)).font(.system(size: 14, weight: .semibold)).monospacedDigit()
                Spacer()
                if let change = environmentChange {
                    usageDeltaText(change)
                }
            }
            if let series = usage.series, !series.isEmpty {
                Text("近 7 天 · 三类").font(.system(size: 9)).foregroundStyle(.tertiary)
                miniBars(series)
                Text("来源 Top 4").font(.system(size: 9)).foregroundStyle(.tertiary).padding(.top, 4)
                let rows = dimensionRows.filter { $0.totalTokens > 0 && $0.key != "__other__" }
                    .sorted { $0.totalTokens > $1.totalTokens }
                ForEach(rows.prefix(4), id: \.key) { row in
                    HStack(spacing: 6) {
                        dimensionIcon(row.key)
                        Text(displayName(row.key)).font(.caption2).lineLimit(1)
                        Spacer(minLength: 6)
                        GeometryReader { proxy in
                            ZStack(alignment: .leading) {
                                RoundedRectangle(cornerRadius: 2).fill(Color.secondary.opacity(0.15))
                                RoundedRectangle(cornerRadius: 2)
                                    .fill(Color.accentColor.opacity(0.7))
                                    .frame(width: max(2, proxy.size.width * share(row.totalTokens, total: usage.totals.totalTokens)))
                            }
                        }
                        .frame(height: 5)
                        Text(tokenText(row.totalTokens))
                            .font(.caption2.weight(.semibold)).monospacedDigit()
                        Text(usdText(usdTotal(row.cost, rate: rate)))
                            .font(.caption2).monospacedDigit().foregroundStyle(.secondary)
                            .frame(minWidth: 56, alignment: .trailing)
                    }
                }
            }
            Spacer(minLength: 0)
            HStack {
                Text("金额为估算值 · USD · 汇率 \(String(format: "%.2f", rate))")
                    .font(.caption2).foregroundStyle(.tertiary)
                Spacer()
                UsageStaleHint(box: entry.box)
            }
        }
    }

    // 逐天三类堆叠迷你柱（手绘，替换 Swift Charts）：自上而下 输出/缓存/输入。
    private func miniBars(_ series: [WidgetUsagePayload.SeriesRow]) -> some View {
        let longest = max(series.map(\.totalTokens).max() ?? 1, 1)
        return HStack(alignment: .bottom, spacing: 5) {
            ForEach(series) { row in
                let total = max(row.totalTokens, 1)
                let barHeight = max(4, CGFloat(row.totalTokens) / CGFloat(longest) * 52)
                VStack(spacing: 0) {
                    ForEach([(row.outputTokens ?? 0, tokenClassColor(.output)),
                             (row.cacheTokens ?? 0, tokenClassColor(.cache)),
                             (row.inputTokens ?? 0, tokenClassColor(.input))], id: \.0) { pair in
                        Rectangle().fill(pair.1)
                            .frame(height: barHeight * CGFloat(pair.0) / CGFloat(total))
                    }
                }
                .frame(height: barHeight)
            }
        }
        .frame(height: 52, alignment: .bottom)
    }

    @ViewBuilder
    private func dimensionIcon(_ key: String) -> some View {
        if entry.configuration.dimension == "model" {
            ModelWidgetIcon(name: key)
        } else if entry.configuration.dimension == "project" {
            Image(systemName: "folder.fill").font(.system(size: 10)).foregroundStyle(.tertiary)
        } else {
            AgentWidgetIcon(id: key, size: 14)
        }
    }

    private func share(_ tokens: Int, total: Int) -> Double {
        total > 0 ? Double(tokens) / Double(total) : 0
    }

    private func periodLabel(_ period: String) -> String {
        ["day": "今日", "week": "本周", "month": "本月"][period] ?? period
    }

    private func displayName(_ key: String) -> String {
        if entry.configuration.dimension == "project" {
            return (key as NSString).lastPathComponent
        }
        if entry.configuration.dimension == "model" {
            return key
        }
        return widgetProviderName(key)
    }
}

struct RecentWidgetView: View {
    @Environment(\.widgetFamily) private var family
    let entry: RecentEntry

    private var limit: Int { family == .systemLarge ? 8 : 4 }

    private var items: [WidgetSnapshot.RecentItem] {
        guard let snapshot = entry.box.snapshot else { return [] }
        return Array(
            snapshot.recent
                .filter { entry.providerFilter == nil || $0.provider == entry.providerFilter }
                .prefix(limit)
        )
    }

    var body: some View {
        Group {
            if entry.box.snapshot == nil {
                Text("请打开会话通知").font(.caption)
            } else if items.isEmpty {
                Text("暂无最近任务").font(.caption).foregroundStyle(.secondary)
            } else {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(items, id: \.id) { item in
                        Link(destination: destination(item)) {
                            HStack(alignment: .top, spacing: 7) {
                                AgentWidgetIcon(id: item.provider, size: 15)
                                VStack(alignment: .leading, spacing: 1) {
                                    Text(item.title.isEmpty ? item.project : item.title)
                                        .font(.caption).lineLimit(1)
                                    Text(relative(item.at)).font(.caption2).foregroundStyle(.tertiary)
                                }
                                Spacer(minLength: 8)
                                if let tokens = item.todayTokens {
                                    let money = item.todayCost.flatMap {
                                        usdTotal($0, rate: entry.box.snapshot?.fx.usdCny ?? 7.10)
                                    }
                                    VStack(alignment: .trailing, spacing: 1) {
                                        Text(tokenText(tokens))
                                            .font(.caption2.weight(.semibold)).monospacedDigit()
                                        Text(usdText(money))
                                            .font(.caption2).monospacedDigit().foregroundStyle(.tertiary)
                                    }
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

    // 组件只走 widgetURL/Link 唤起 App（§5）；点击 recent 条目按 id+revision 打开原会话。
    private func destination(_ item: WidgetSnapshot.RecentItem) -> URL {
        URL(string: "agentnotification://open?id=\(item.id)&revision=\(item.revision)")
            ?? URL(string: "agentnotification://inbox")!
    }

    private func relative(_ at: Double) -> String {
        let interval = Date().timeIntervalSince1970 - at
        if interval < 60 { return "刚刚" }
        if interval < 3600 { return "\(Int(interval / 3600))分钟前" }
        if interval < 86400 { return "\(Int(interval / 3600))小时前" }
        return "\(Int(interval / 86400))天前"
    }
}
