import AppKit
import ApplicationServices
import SwiftUI
import UserNotifications

struct InboxRow: Decodable, Identifiable, Sendable {
    let id: String
    let provider: String
    let title: String
    let project: String
    let state: String
    let unread: Bool
    let revision: Int
    let eventAt: Double
    let activityAt: Double?
    let attentionToken: String?
    // 可选：App 新于脚本时旧 JSON 缺这两键，非可选会让整行解码失败。
    let attentionAt: Double?
    let openAvailable: Bool
}
struct SourceHealth: Decodable { let status: String; let errors: Int? }
struct Health: Decodable { let sources: [String: SourceHealth]? }
struct Envelope: Decodable { let sessions: [InboxRow]; let health: Health? }

struct AgentEntry: Decodable, Identifiable {
    let id: String
    let name: String
    let installed: Bool
    let iterm: Bool
}
struct AgentList: Decodable { let agents: [AgentEntry] }

final class InboxAppDelegate: NSObject, NSApplicationDelegate, UNUserNotificationCenterDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        UNUserNotificationCenter.current().delegate = self
        applyAppearanceIcon()
        // 系统 tooltip 默认延迟约 1.5s：日报悬浮要求即触即显，压到 120ms。
        UserDefaults.standard.register(defaults: ["NSInitialToolTipDelay": 120])
        // Apply the compact default once; later user resizing remains persistent.
        if !UserDefaults.standard.bool(forKey: "compactWindowV1") {
            if let window = NSApp.windows.first(where: { $0.identifier?.rawValue == "inbox" }) {
                window.setContentSize(NSSize(width: 400, height: 620))
                UserDefaults.standard.set(true, forKey: "compactWindowV1")
            }
        }
        DistributedNotificationCenter.default().addObserver(forName: Notification.Name("AppleInterfaceThemeChangedNotification"),
                                                            object: nil, queue: .main) { [weak self] _ in
            self?.applyAppearanceIcon()
        }
    }
    // macOS 不为 icns 做外观切换：随系统明暗手动换 Dock 图标（并叠加当前未读角标）。
    func applyAppearanceIcon() {
        let dark = NSApp.effectiveAppearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
        applyDockIcon(dark: dark, unread: DockBadge.unread)
    }
    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .sound])
    }
    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
                                withCompletionHandler completionHandler: @escaping () -> Void) {
        if response.actionIdentifier == UNNotificationDefaultActionIdentifier {
            Task { @MainActor in
                NSApplication.shared.activate(ignoringOtherApps: true)
                if let window = NSApplication.shared.windows.first(where: { $0.canBecomeMain && $0.isVisible }) {
                    window.makeKeyAndOrderFront(nil)
                } else {
                    // Window 已被关闭时 SwiftUI 已释放它，只能经 openWindow 重建；
                    // 常驻的菜单栏图标视图监听该事件并调用 openWindow(id: "inbox")。
                    NotificationCenter.default.post(name: .reopenInbox, object: nil)
                }
            }
        }
        completionHandler()
    }
}

extension Notification.Name {
    static let reopenInbox = Notification.Name("SessionInboxReopenInbox")
}

// Dock 未读角标：dockTile.badgeLabel 在本应用不渲染（见 InboxModel.updateDockBadge 注），
// 把角标直接画进应用图标。主题切换重绘底图时要带上同一数值，故未读数存全局。
enum DockBadge {
    static var unread = 0
}

func applyDockIcon(dark: Bool, unread: Int) {
    guard let url = Bundle.main.url(forResource: dark ? "AppIconDark" : "AppIcon", withExtension: "icns"),
          let base = NSImage(contentsOf: url) else { return }
    guard unread > 0 else {
        NSApplication.shared.applicationIconImage = base
        return
    }
    let canvas = NSSize(width: 1024, height: 1024)
    let image = NSImage(size: canvas)
    image.lockFocus()
    base.draw(in: NSRect(origin: .zero, size: canvas))
    let text = unread > 99 ? "99+" : String(unread)
    let fontSize: CGFloat = text.count >= 3 ? 140 : (text.count == 2 ? 180 : 230)
    let string = NSAttributedString(string: text, attributes: [
        .font: NSFont.systemFont(ofSize: fontSize, weight: .bold),
        .foregroundColor: NSColor.white,
    ])
    let bounds = string.boundingRect(with: canvas, options: [.usesLineFragmentOrigin])
    let center = NSPoint(x: 852, y: 852)
    let radius: CGFloat = text.count >= 3 ? 176 : 158
    let circle = NSRect(x: center.x - radius, y: center.y - radius, width: radius * 2, height: radius * 2)
    // 橙底白字与托盘徽标、行内未读点同一视觉语言（统一 systemOrange，明暗自适应）；白描边与浅色瓦片分隔。
    NSColor.systemOrange.setFill()
    NSBezierPath(ovalIn: circle).fill()
    NSColor.white.setStroke()
    let border = NSBezierPath(ovalIn: circle)
    border.lineWidth = 16
    border.stroke()
    string.draw(at: NSPoint(x: center.x - bounds.width / 2, y: center.y - bounds.height / 2))
    image.unlockFocus()
    NSApplication.shared.applicationIconImage = image
}

// MARK: - 工作日报

struct SourceUsage: Decodable {
    let inputTokens: Int
    let cacheTokens: Int
    let outputTokens: Int
    let totalTokens: Int
}
struct ReportTotals: Decodable {
    let tasks: Int
    let turns: Int
    let inputTokens: Int
    let cacheTokens: Int
    let outputTokens: Int
    let totalTokens: Int
    let sources: [String: SourceUsage]
    let projects: [String: SourceUsage]
}
struct ReportTask: Decodable, Identifiable {
    let provider: String
    let sessionId: String
    let title: String
    let project: String
    let firstAt: Double
    let lastAt: Double
    let inputTokens: Int
    let cacheTokens: Int
    let outputTokens: Int
    let totalTokens: Int
    let turns: Int
    let state: String
    let fidelity: String
    let segments: [[Double]]?  // 真实活动区间（Zcode 轮区间 / 逐条消息聚类），节奏带只画这些
    var id: String { provider + "/" + sessionId }
}
struct DayReport: Decodable {
    let date: String
    let generatedAt: Double?
    let totals: ReportTotals
    let tasks: [ReportTask]
}
struct OverviewDay: Decodable, Identifiable {
    let date: String
    let inputTokens: Int
    let cacheTokens: Int
    let outputTokens: Int
    let totalTokens: Int
    let tasks: Int
    let level: Int
    var id: String { date }
}
struct TodaySummary: Decodable, Identifiable {
    let date: String
    let inputTokens: Int
    let cacheTokens: Int
    let outputTokens: Int
    let totalTokens: Int
    let tasks: Int
    let turns: Int
    let sources: Int
    let level: Int
    var id: String { date }
}
struct TopProject: Decodable, Identifiable {
    let project: String
    let name: String
    let inputTokens: Int
    let cacheTokens: Int
    let outputTokens: Int
    let totalTokens: Int
    let share: Double
    var id: String { project }
}
struct ReportOverview: Decodable {
    let days: [OverviewDay]
    let topProjects: [TopProject]
    let weekSources: [String: SourceUsage]
    let today: TodaySummary?
}

@MainActor final class DailyReportModel: ObservableObject {
    @Published var overview: ReportOverview?
    @Published var selectedDate = ""
    @Published var selectedDay: DayReport?
    @Published var path: [String] = []
    @Published var loading = false
    @Published var error: String?
    let root: String
    init(root: String? = nil) {
        self.root = root ?? Bundle.main.object(forInfoDictionaryKey: "SessionManagerRoot") as? String ?? ""
    }
    nonisolated static func decode<T: Decodable>(_ type: T.Type, from data: Data) throws -> T {
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(type, from: data)
    }
    nonisolated static func message(_ result: (Int32, Data, String)) -> String {
        if let data = result.2.data(using: .utf8),
           let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let reason = object["reason"] as? String { return reason }
        let raw = result.2.isEmpty ? String(data: result.1, encoding: .utf8) ?? "" : result.2
        return String(raw.prefix(300))
    }
    private func run(_ arguments: [String]) async -> (Int32, Data, String) {
        let directory = root
        return await Task.detached { InboxModel.call(root: directory, arguments: arguments) }.value
    }
    func loadOverview() {
        guard !loading else { return }
        loading = true
        error = nil
        Task {
            let result = await run(["daily-report", "--overview"])
            loading = false
            guard result.0 == 0 else { error = Self.message(result); return }
            do { overview = try Self.decode(ReportOverview.self, from: result.1) }
            catch { self.error = "日报总览读取失败：" + error.localizedDescription }
        }
    }
    func loadDay(_ date: String) {
        selectedDate = date
        selectedDay = nil
        error = nil
        Task {
            let result = await run(["daily-report", "--date", date])
            guard result.0 == 0 else { error = Self.message(result); return }
            do { selectedDay = try Self.decode(DayReport.self, from: result.1) }
            catch { self.error = "当日报告读取失败：" + error.localizedDescription }
        }
    }
}

func providerReportColor(_ provider: String) -> Color {
    switch provider {
    case "zcode": return Color(red: 0.0, green: 0.478, blue: 1.0)      // #007AFF
    case "codex": return Color(red: 0.063, green: 0.639, blue: 0.498)  // #10A37F
    case "claude": return Color(red: 0.851, green: 0.467, blue: 0.341) // #D97757
    case "pi": return Color(red: 0.392, green: 0.824, blue: 1.0)       // #64D2FF
    case "kimi": return Color(red: 0.749, green: 0.353, blue: 0.949)   // #BF5AF2
    case "agy": return Color(red: 0.259, green: 0.522, blue: 0.957)    // #4285F4 Google 蓝
    case "opencode": return Color(red: 0.961, green: 0.620, blue: 0.043) // #F59E0B 暂定
    default: return Color.secondary
    }
}
func heatColor(_ level: Int) -> Color {
    switch level {
    case 1: return Color.accentColor.opacity(0.22)
    case 2: return Color.accentColor.opacity(0.45)
    case 3: return Color.accentColor.opacity(0.70)
    case 4: return Color.accentColor
    default: return Color.primary.opacity(0.07)
    }
}
// 需要用户关注的统一橙：未读点、未读文字、未读行底色、托盘与 Dock 角标同一色。
// 状态点（stateDotColor）保留语义化系统色，不走这里。
extension Color {
    static let attention = Color(nsColor: .systemOrange)
}

// Liquid Glass（macOS 26+）只用于浮层：窗口工具栏由系统自动套玻璃，日报悬浮卡见 HoverTipSurface。
// 内容层（新建会话按钮、列表行、日报卡片、搜索框）保持实色——玻璃落在实色窗口底上
// 只会变成一块块凸起的暗色方砖，尤其深色模式下与轻量的页面语言冲突。
func stateDotColor(_ state: String) -> Color {
    switch state {
    case "running": return .green
    case "waiting": return .orange
    case "failed": return .red
    default: return Color.secondary
    }
}
let reportDayFormatter: DateFormatter = {
    let formatter = DateFormatter()
    formatter.dateFormat = "yyyy-MM-dd"
    formatter.locale = Locale(identifier: "en_US_POSIX")
    return formatter
}()
func reportDate(_ text: String) -> Date? { reportDayFormatter.date(from: text) }
func reportDayDisplay(_ text: String) -> String {
    guard let date = reportDate(text) else { return text }
    let components = Calendar.current.dateComponents([.month, .day, .weekday], from: date)
    let weekdays = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"]
    return "\(components.month ?? 0)月\(components.day ?? 0)日 \(weekdays[(components.weekday ?? 1) - 1])"
}
func reportShortDay(_ text: String) -> String {
    guard let date = reportDate(text) else { return text }
    let components = Calendar.current.dateComponents([.month, .day], from: date)
    return "\(components.month ?? 0)/\(components.day ?? 0)"
}
func trendDays(_ overview: ReportOverview, endingAt current: Date) -> [OverviewDay] {
    let prior = overview.days.filter { day in
        guard let date = reportDate(day.date) else { return false }
        return date <= current
    }
    return prior.isEmpty ? [] : Array(prior.suffix(7))
}
func usageLine(input: Int, cache: Int, output: Int) -> String {
    "输入 \(tokenText(input)) · 缓存 \(tokenText(cache)) · 输出 \(tokenText(output))"
}
func usageLine(_ usage: SourceUsage) -> String {
    usageLine(input: usage.inputTokens, cache: usage.cacheTokens, output: usage.outputTokens)
}
func usageLine(_ task: ReportTask) -> String {
    if task.fidelity == "unavailable" { return "无 token 记录，不计入统计" }
    return usageLine(input: task.inputTokens, cache: task.cacheTokens, output: task.outputTokens)
}
func reportClock(_ stamp: Double) -> String {
    let components = Calendar.current.dateComponents([.hour, .minute], from: Date(timeIntervalSince1970: stamp))
    return String(format: "%02d:%02d", components.hour ?? 0, components.minute ?? 0)
}
func reportTimeRange(_ task: ReportTask) -> String {
    let first = reportClock(task.firstAt)
    let last = reportClock(task.lastAt)
    return first == last ? first : "\(first)–\(last)"
}
func heatWeekColumns(_ days: [OverviewDay], calendar: Calendar = .current) -> [[OverviewDay?]] {
    guard let first = days.first, let startDate = reportDate(first.date) else { return [] }
    // GitHub 布局：列=周、行=周一至周日；首列按周几留空补位。
    let leading = (calendar.component(.weekday, from: startDate) + 5) % 7
    var cells: [OverviewDay?] = Array(repeating: nil, count: leading) + days.map { Optional($0) }
    while cells.count % 7 != 0 { cells.append(nil) }
    return stride(from: 0, to: cells.count, by: 7).map { Array(cells[$0..<min($0 + 7, cells.count)]) }
}
func heatMonthMarks(_ columns: [[OverviewDay?]], calendar: Calendar = .current) -> [String?] {
    var result: [String?] = []
    var lastMonth = -1
    for column in columns {
        guard let day = column.compactMap({ $0 }).first, let date = reportDate(day.date) else {
            result.append(nil)
            continue
        }
        let month = calendar.component(.month, from: date)
        if month != lastMonth {
            result.append("\(month)月")
            lastMonth = month
        } else {
            result.append(nil)
        }
    }
    return result
}

struct DailyReportView: View {
    @ObservedObject var model: DailyReportModel
    @ObservedObject private var hoverCenter = HoverTipCenter.shared
    var body: some View {
        NavigationStack(path: $model.path) {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("工作日报").font(.system(size: 24, weight: .bold))
                        Text("数据来自本地会话元数据 · 今日随时实时汇总 · 次日首次查看定稿").font(.subheadline).foregroundStyle(.secondary)
                    }
                    if let error = model.error {
                        HStack(alignment: .top, spacing: 8) {
                            Image(systemName: "exclamationmark.triangle.fill").font(.caption).foregroundStyle(.red)
                            Text(error).font(.caption).foregroundStyle(.red).textSelection(.enabled)
                            Spacer()
                            Button { model.loadOverview() } label: { Text("重试").font(.caption) }
                                .buttonStyle(InboxActionButtonStyle())
                        }
                        .padding(10)
                        .background(Color.red.opacity(0.06), in: RoundedRectangle(cornerRadius: 8))
                    }
                    if let overview = model.overview {
                        if let today = overview.today {
                            TodayChipsView(today: today, overview: overview)
                        }
                        HeatmapView(overview: overview)
                        if let today = overview.today, let current = reportDate(today.date) {
                            WeekTrendView(days: trendDays(overview, endingAt: current))
                        }
                        HStack(alignment: .top, spacing: 14) {
                            SourceShareView(title: "来源占比 · 近 7 天", sources: overview.weekSources)
                            TopProjectsCard(projects: overview.topProjects)
                        }
                        .fixedSize(horizontal: false, vertical: true)
                    } else if model.loading {
                        HStack(spacing: 10) {
                            ProgressView().controlSize(.small)
                            Text("正在汇总近半年会话…").font(.subheadline).foregroundStyle(.secondary)
                        }.frame(maxWidth: .infinity).padding(.vertical, 40)
                    }
                    Text("口径：token 三类之和参与全部统计——输入=新鲜输入+缓存写入；缓存=读取回放；输出=含 reasoning。取消/出错轮次照计。")
                        .font(.caption2).foregroundStyle(.tertiary)
                }
                .padding(16)
            }
            .navigationDestination(for: String.self) { date in
                DayDetailView(model: model, date: date)
            }
        }
        // 悬浮卡片渲染在根层覆盖：不受相邻单元格/分区卡片遮挡影响。
        .overlay(alignment: .topLeading) {
            GeometryReader { proxy in
                if let tip = hoverCenter.active {
                    HoverTipCard(title: tip.title, lines: tip.lines)
                        .fixedSize()
                        .allowsHitTesting(false)
                        .offset(
                            x: min(max(hoverCenter.activeFrame.minX + 14, 8), max(proxy.size.width - 230, 8)),
                            y: min(hoverCenter.activeFrame.maxY + 6, max(proxy.size.height - 120, 8)))
                        .zIndex(9999)
                }
            }
            .allowsHitTesting(false)
        }
        .coordinateSpace(name: HoverTipCenter.space)
        .frame(minWidth: 560, idealWidth: 600, minHeight: 520, idealHeight: 780)
        .background(Color(nsColor: .windowBackgroundColor))
        .onAppear { model.loadOverview() }
    }
}

struct HeatmapView: View {
    let overview: ReportOverview
    var body: some View {
        let columns = heatWeekColumns(overview.days)
        let marks = heatMonthMarks(columns)
        let todayKey = dailyReportDayKey(Date())
        let activeDays = overview.days.filter { $0.totalTokens > 0 }.count
        let streak = activeStreak(overview.days, todayKey: todayKey)
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                Text("活跃热力 · 近半年").font(.headline)
                Spacer()
                Text("活跃 \(activeDays) 天").font(.caption).foregroundStyle(.secondary).monospacedDigit()
                if streak > 1 {
                    Text("·").font(.caption).foregroundStyle(.tertiary)
                    Text("连续 \(streak) 天").font(.caption).foregroundStyle(Color.accentColor).monospacedDigit()
                }
            }
            if columns.isEmpty {
                Text("还没有可统计的数据").font(.subheadline).foregroundStyle(.secondary)
            } else {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(alignment: .top, spacing: 4) {
                        VStack(alignment: .leading, spacing: 3) {
                            Text("").frame(height: 15)
                            ForEach(["一", "二", "三", "四", "五", "六", "日"], id: \.self) { label in
                                Text(label).font(.system(size: 9)).foregroundStyle(.tertiary)
                                    .frame(width: 13, height: 13, alignment: .trailing)
                            }
                        }
                        VStack(alignment: .leading, spacing: 3) {
                            HStack(spacing: 3) {
                                ForEach(marks.indices, id: \.self) { index in
                                    Text(marks[index] ?? "")
                                        .font(.system(size: 9)).foregroundStyle(.tertiary)
                                        .frame(width: 13, alignment: .leading)
                                }
                            }
                            HStack(alignment: .top, spacing: 3) {
                                ForEach(columns.indices, id: \.self) { column in
                                    VStack(spacing: 3) {
                                        ForEach(columns[column].indices, id: \.self) { row in
                                            if let day = columns[column][row], day.totalTokens > 0 || day.date == todayKey {
                                                // 有记录（或今天）才可点进详情；空白日只是占位。
                                                NavigationLink(value: day.date) {
                                                    RoundedRectangle(cornerRadius: 3)
                                                        .fill(heatColor(day.level))
                                                        .frame(width: 13, height: 13)
                                                        .overlay {
                                                            if day.date == todayKey {
                                                                RoundedRectangle(cornerRadius: 3)
                                                                    .strokeBorder(Color.attention, lineWidth: 1.5)
                                                            }
                                                        }
                                                }
                                                .buttonStyle(.plain)
                                                .chartHover(scale: 1.3)
                                                .dailyHoverTip(title: reportDayDisplay(day.date),
                                                               lines: day.totalTokens > 0
                                                                   ? ["合计：\(tokenText(day.totalTokens))", "\(day.tasks) 个任务"]
                                                                   : ["暂无记录"])
                                            } else if let day = columns[column][row] {
                                                RoundedRectangle(cornerRadius: 3)
                                                    .fill(heatColor(day.level))
                                                    .frame(width: 13, height: 13)
                                                    .dailyHoverTip(title: reportDayDisplay(day.date), lines: ["无记录"])
                                            } else {
                                                Rectangle().fill(.clear).frame(width: 13, height: 13)
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                HStack(spacing: 4) {
                    Text("少").font(.caption2).foregroundStyle(.tertiary)
                    ForEach(0..<5, id: \.self) { level in
                        RoundedRectangle(cornerRadius: 2).fill(heatColor(level)).frame(width: 10, height: 10)
                    }
                    Text("多").font(.caption2).foregroundStyle(.tertiary)
                    Spacer()
                    Text("点击任意日期查看当日详情").font(.caption2).foregroundStyle(.tertiary)
                }
            }
        }
        .dailyReportCard()
    }
}
// 连续活跃天数：从今天（或今天无记录时从昨天）往前数连续有 token 的日子。
func activeStreak(_ days: [OverviewDay], todayKey: String) -> Int {
    let ordered = days.sorted { $0.date < $1.date }
    guard var index = ordered.lastIndex(where: { $0.date <= todayKey }) else { return 0 }
    if ordered[index].date == todayKey, ordered[index].totalTokens <= 0 { index -= 1 }
    var streak = 0
    var expected = index >= 0 ? reportDate(ordered[index].date) : nil
    while index >= 0, let date = expected, ordered[index].totalTokens > 0,
          reportDate(ordered[index].date) == date {
        streak += 1
        index -= 1
        expected = Calendar.current.date(byAdding: .day, value: -1, to: date)
    }
    return streak
}

enum TokenClass { case input, cache, output }
func tokenClassColor(_ cls: TokenClass) -> Color {
    switch cls {
    // 缓存是读取回放、通常占九成以上：用中性灰压住，让真正的输入/输出两端跳出来。
    // 输入=主题蓝，输出=青绿（systemTeal），与来源色系不冲突，明暗模式各自自适应。
    case .input: return Color.accentColor
    case .cache: return Color.primary.opacity(0.13)
    case .output: return Color(nsColor: .systemTeal)
    }
}
// 日报自定义悬浮：多行卡片，鼠标进入即显（不走系统 tooltip 的长延迟通道）。
// 黑色 80% 不透明底、白色文字（用户指定），尺寸随内容自适应、文本不折行。
struct HoverTipCard: View {
    let title: String
    let lines: [String]
    private var tipTitleStyle: AnyShapeStyle {
        if #available(macOS 26, *) { return AnyShapeStyle(.primary) }
        return AnyShapeStyle(Color.white)
    }
    private var tipLineStyle: AnyShapeStyle {
        if #available(macOS 26, *) { return AnyShapeStyle(.secondary) }
        return AnyShapeStyle(Color.white.opacity(0.85))
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(tipTitleStyle)
                .lineLimit(1)
            ForEach(lines.indices, id: \.self) { index in
                Text(lines[index])
                    .font(.system(size: 11))
                    .foregroundStyle(tipLineStyle)
                    .lineLimit(1)
            }
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .modifier(HoverTipSurface())
        .fixedSize()
    }
}

// 悬浮卡是典型浮层：macOS 26 起用玻璃承托并沿用系统前景色；旧系统保留深色实底。
struct HoverTipSurface: ViewModifier {
    func body(content: Content) -> some View {
        if #available(macOS 26, *) {
            content
                .foregroundStyle(.primary)
                .glassEffect(.regular, in: RoundedRectangle(cornerRadius: 10))
                .shadow(color: .black.opacity(0.12), radius: 10, y: 4)
        } else {
            content
                .background(Color.black.opacity(0.8), in: RoundedRectangle(cornerRadius: 8))
                .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(Color.white.opacity(0.12)))
                .shadow(color: .black.opacity(0.25), radius: 8, y: 3)
        }
    }
}

/// 悬浮内容统一渲染在日报根视图的覆盖层上，避免被相邻单元格或后续卡片遮挡。
/// （SwiftUI zIndex 只在同层兄弟间生效，单元格内悬浮无法盖过其他分区的卡片。）
@MainActor
final class HoverTipCenter: ObservableObject {
    static let shared = HoverTipCenter()
    static let space = "dailyReportRoot"
    struct Tip: Equatable {
        let title: String
        let lines: [String]
    }
    @Published var active: Tip?
    @Published var activeFrame: CGRect = .zero
    private var activeId: UUID?
    private var frames: [UUID: CGRect] = [:]
    func move(_ id: UUID, frame: CGRect) {
        frames[id] = frame
        if activeId == id { activeFrame = frame }  // 悬浮期间内容滚动时卡片跟随。
    }
    func hover(_ id: UUID, inside: Bool, tip: Tip) {
        guard inside else {
            if activeId == id { active = nil }
            return
        }
        activeId = id
        active = tip
        activeFrame = frames[id] ?? activeFrame
    }
}
// 悬浮身份必须按视图身份稳定：帧登记只发生在 onAppear/帧变化，若键随父视图重建换新
// （悬浮本身就会触发 HoverTipCenter 发布→日报根视图重算），新键永远查不到帧，
// hover() 回退 activeFrame 就继承上一次悬浮位置。@StateObject 每个视图身份只建一次。
final class HoverTipIdentity: ObservableObject { let id = UUID() }
struct DailyHoverTip: ViewModifier {
    let title: String
    let lines: [String]
    @StateObject private var identity = HoverTipIdentity()
    @ObservedObject private var center = HoverTipCenter.shared
    func body(content: Content) -> some View {
        content
            .background(
                GeometryReader { geo in
                    Color.clear
                        .onAppear { center.move(identity.id, frame: geo.frame(in: .named(HoverTipCenter.space))) }
                        .onChange(of: geo.frame(in: .named(HoverTipCenter.space))) { _, newFrame in
                            center.move(identity.id, frame: newFrame)
                        }
                }
            )
            .onHover { inside in
                center.hover(identity.id, inside: inside, tip: HoverTipCenter.Tip(title: title, lines: lines))
            }
    }
}
extension View {
    func dailyHoverTip(title: String, lines: [String]) -> some View {
        modifier(DailyHoverTip(title: title, lines: lines))
    }
}
struct HoverTipModifier<Tip: View>: ViewModifier {
    @ViewBuilder let tip: () -> Tip
    @StateObject private var model = HoverModel()
    final class HoverModel: ObservableObject { @Published var hovering = false }
    func body(content: Content) -> some View {
        content
            .overlay(alignment: .topLeading) {
                if model.hovering {
                    tip().allowsHitTesting(false).transition(.opacity).zIndex(10)
                }
            }
            .zIndex(model.hovering ? 999 : 0)
            .onHover { entering in
                withAnimation(.easeIn(duration: 0.06)) { model.hovering = entering }
            }
    }
}
extension View {
    func hoverTip<Tip: View>(@ViewBuilder tip: @escaping () -> Tip) -> some View {
        modifier(HoverTipModifier(tip: tip))
    }
}
// 图表 hover 动效：色块轻微放大/提亮，行式条目底色浮现；与悬浮提示互不影响。
struct ChartHoverEffect: ViewModifier {
    let scale: CGFloat
    let highlight: Bool
    // 与 HoverTipModifier 同理：本仓库用裸 swiftc 编译，@State 宏插件不可用，用 @StateObject 承载状态。
    @StateObject private var model = HoverModel()
    final class HoverModel: ObservableObject { @Published var hovering = false }
    func body(content: Content) -> some View {
        content
            .scaleEffect(model.hovering ? scale : 1)
            .brightness(model.hovering && !highlight ? 0.08 : 0)
            .background {
                if highlight {
                    RoundedRectangle(cornerRadius: 6)
                        .fill(Color.primary.opacity(model.hovering ? 0.05 : 0))
                        .padding(-4)
                }
            }
            .animation(.easeOut(duration: 0.15), value: model.hovering)
            .onHover { model.hovering = $0 }
    }
}
extension View {
    /// 色块类：放大 + 提亮。
    func chartHover(scale: CGFloat = 1.06) -> some View {
        modifier(ChartHoverEffect(scale: scale, highlight: false))
    }
    /// 行式条目：底色浮现，不缩放。
    func rowHover() -> some View {
        modifier(ChartHoverEffect(scale: 1, highlight: true))
    }
}
func classTipLines(input: Int, cache: Int, output: Int) -> [String] {
    // 展示顺序遵循用户模板：缓存 / 输入 / 输出。
    ["缓存：\(tokenText(cache))", "输入：\(tokenText(input))", "输出：\(tokenText(output))"]
}
func classTipLines(_ usage: SourceUsage) -> [String] {
    classTipLines(input: usage.inputTokens, cache: usage.cacheTokens, output: usage.outputTokens)
}
struct DailyReportCardBackground: ViewModifier {
    func body(content: Content) -> some View {
        content
            .padding(14)
            .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Color.primary.opacity(0.06)))
    }
}
extension View {
    func dailyReportCard() -> some View { modifier(DailyReportCardBackground()) }
}

// 总览/详情共用的头卡：大数字 + 三类 token 分段条 + 右侧 2×2 数据瓦片。
struct UsageHeroView: View {
    let total: Int
    let input: Int
    let cache: Int
    let output: Int
    let caption: String
    let accent: String?
    let tiles: [(value: String, label: String)]
    var body: some View {
        HStack(alignment: .top, spacing: 18) {
            VStack(alignment: .leading, spacing: 6) {
                Text(tokenText(total))
                    .font(.system(size: 30, weight: .bold)).monospacedDigit()
                HStack(spacing: 6) {
                    Text(caption).font(.caption).foregroundStyle(.secondary)
                    if let accent {
                        Text("·").foregroundStyle(.tertiary).font(.caption)
                        Text(accent).font(.caption).foregroundStyle(Color.accentColor)
                    }
                }
                classBar.frame(height: 8).padding(.top, 4)
                HStack(spacing: 10) {
                    classLegend(.output, "输出", output)
                    classLegend(.input, "输入", input)
                    classLegend(.cache, "缓存", cache)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Grid(alignment: .leading, horizontalSpacing: 8, verticalSpacing: 8) {
                ForEach(Array(stride(from: 0, to: tiles.count, by: 2)), id: \.self) { start in
                    GridRow {
                        ForEach(start..<min(start + 2, tiles.count), id: \.self) { index in
                            statTile(tiles[index].value, tiles[index].label)
                        }
                    }
                }
            }
        }
        .dailyReportCard()
    }
    private var classBar: some View {
        let sum = max(total, 1)
        // 三类统一顺序：输出 / 输入 / 缓存（横条从左到右，柱图从上到下）。
        let parts: [(Int, Color)] = [(output, tokenClassColor(.output)), (input, tokenClassColor(.input)),
                                     (cache, tokenClassColor(.cache))]
        return GeometryReader { proxy in
            HStack(spacing: 1) {
                ForEach(Array(parts.enumerated()), id: \.offset) { _, part in
                    if part.0 > 0 {
                        Rectangle().fill(part.1)
                            .frame(width: max(2, proxy.size.width * Double(part.0) / Double(sum)))
                    }
                }
            }
            .clipShape(RoundedRectangle(cornerRadius: 3))
        }
    }
    private func classLegend(_ cls: TokenClass, _ label: String, _ value: Int) -> some View {
        HStack(spacing: 4) {
            RoundedRectangle(cornerRadius: 1.5).fill(tokenClassColor(cls)).frame(width: 6, height: 6)
            Text(label).font(.caption2).foregroundStyle(.secondary)
            Text(tokenText(value)).font(.caption2.weight(.medium)).monospacedDigit()
        }
    }
    private func statTile(_ value: String, _ label: String) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(value).font(.system(size: 16, weight: .semibold)).monospacedDigit().lineLimit(1)
            Text(label).font(.caption2).foregroundStyle(.secondary)
        }
        .frame(width: 66, alignment: .leading)
        .padding(.horizontal, 9).padding(.vertical, 6)
        .background(Color.primary.opacity(0.04), in: RoundedRectangle(cornerRadius: 8))
    }
}

struct TodayChipsView: View {
    let today: TodaySummary
    let overview: ReportOverview
    var body: some View {
        UsageHeroView(total: today.totalTokens, input: today.inputTokens, cache: today.cacheTokens,
                      output: today.outputTokens, caption: "今日 token 合计", accent: "实时汇总",
                      tiles: [("\(today.tasks)", "任务"), ("\(today.turns)", "轮次"),
                              ("\(today.sources)", "活跃来源"), (deltaText, "较昨日")])
    }
    // 较昨日：昨日无记录显示「—」，避免除零后的夸张百分比。
    private var deltaText: String {
        guard let date = reportDate(today.date),
              let yesterday = Calendar.current.date(byAdding: .day, value: -1, to: date),
              let previous = overview.days.first(where: { $0.date == dailyReportDayKey(yesterday) }),
              previous.totalTokens > 0 else { return "—" }
        let ratio = Double(today.totalTokens - previous.totalTokens) / Double(previous.totalTokens)
        let percent = Int((abs(ratio) * 100).rounded())
        return (ratio >= 0 ? "▲ " : "▼ ") + "\(percent)%"
    }
}

struct WeekTrendView: View {
    let days: [OverviewDay]
    var body: some View {
        let longest = max(days.map(\.totalTokens).max() ?? 1, 1)
        let average = days.isEmpty ? 0 : days.reduce(0) { $0 + $1.totalTokens } / days.count
        let todayKey = dailyReportDayKey(Date())
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                Text("最近 7 天 · token 合计").font(.headline)
                if average > 0 {
                    Text("日均 \(tokenText(average))").font(.caption).monospacedDigit().foregroundStyle(.secondary)
                }
                Spacer()
                ForEach([(tokenClassColor(.output), "输出"), (tokenClassColor(.input), "输入"),
                         (tokenClassColor(.cache), "缓存")], id: \.1) { color, label in
                    HStack(spacing: 3) {
                        RoundedRectangle(cornerRadius: 1.5).fill(color).frame(width: 6, height: 6)
                        Text(label).font(.system(size: 9)).foregroundStyle(.secondary)
                    }
                }
            }
            ZStack(alignment: .bottomLeading) {
                // 7 日均值虚线画在柱子后面，数值放标题行，避免与柱顶数字打架。
                if average > 0 {
                    Line().stroke(style: StrokeStyle(lineWidth: 1, dash: [3, 3]))
                        .foregroundStyle(Color.primary.opacity(0.28))
                        .frame(height: 1)
                        .offset(y: -(12 + 3 + CGFloat(average) / CGFloat(longest) * 52))
                        .allowsHitTesting(false)
                }
                HStack(alignment: .bottom, spacing: 10) {
                    ForEach(days) { day in
                        VStack(spacing: 3) {
                            Text(tokenText(day.totalTokens))
                                .font(.system(size: 9)).monospacedDigit()
                                .foregroundStyle(day.date == todayKey ? AnyShapeStyle(Color.accentColor) : AnyShapeStyle(.secondary))
                                .lineLimit(1)
                            stackedBar(day, longest: longest)
                                .frame(maxWidth: .infinity)
                                .clipShape(RoundedRectangle(cornerRadius: 3))
                                .overlay {
                                    if day.date == todayKey {
                                        RoundedRectangle(cornerRadius: 3)
                                            .strokeBorder(Color.attention, lineWidth: 1.5)
                                    }
                                }
                            Text(reportShortDay(day.date))
                                .font(.system(size: 9)).monospacedDigit()
                                .foregroundStyle(day.date == todayKey ? AnyShapeStyle(.primary) : AnyShapeStyle(.tertiary))
                        }
                        .chartHover(scale: 1.04)
                        .dailyHoverTip(title: reportDayDisplay(day.date),
                                       lines: classTipLines(input: day.inputTokens,
                                                            cache: day.cacheTokens,
                                                            output: day.outputTokens)
                                           + ["合计：\(tokenText(day.totalTokens))",
                                              "\(day.tasks) 个任务"])
                    }
                }
            }
            .frame(height: 84, alignment: .bottom)
        }
        .dailyReportCard()
    }
    // 三类层叠：自上而下 输出/输入/缓存，高度按合计相对最长日。
    private func stackedBar(_ day: OverviewDay, longest: Int) -> some View {
        let total = max(day.totalTokens, 1)
        let barHeight = max(3, CGFloat(day.totalTokens) / CGFloat(longest) * 52)
        let segments: [(value: Int, color: Color)] = [
            (day.outputTokens, tokenClassColor(.output)),
            (day.inputTokens, tokenClassColor(.input)),
            (day.cacheTokens, tokenClassColor(.cache)),
        ]
        return VStack(spacing: 0) {
            ForEach(Array(segments.enumerated()), id: \.offset) { _, segment in
                Rectangle()
                    .fill(segment.color)
                    .frame(height: barHeight * CGFloat(segment.value) / CGFloat(total))
            }
        }
    }
}
struct Line: Shape {
    func path(in rect: CGRect) -> Path {
        var path = Path()
        path.move(to: CGPoint(x: rect.minX, y: rect.midY))
        path.addLine(to: CGPoint(x: rect.maxX, y: rect.midY))
        return path
    }
}

// 来源占比：环形图在上、图例在下；总览（近 7 天）与详情（当日）共用。
struct SourceShareView: View {
    let title: String
    let sources: [String: SourceUsage]
    var unavailable: Set<String> = []
    var body: some View {
        let entries: [(key: String, usage: SourceUsage)] =
            sources.map { (key: $0.key, usage: $0.value) }
                .sorted { $0.usage.totalTokens > $1.usage.totalTokens }
                .filter { $0.usage.totalTokens > 0 }
        let total = max(entries.reduce(0) { $0 + $1.usage.totalTokens }, 1)
        let missing = unavailable.subtracting(entries.map(\.key))
        VStack(alignment: .leading, spacing: 10) {
            Text(title).font(.headline)
            if entries.isEmpty {
                Text("没有可统计的来源").font(.subheadline).foregroundStyle(.secondary)
            } else {
                VStack(alignment: .leading, spacing: 12) {
                    donut(entries, total: total).frame(maxWidth: .infinity)
                    VStack(alignment: .leading, spacing: 7) {
                        ForEach(entries, id: \.key) { entry in
                            let share = Double(entry.usage.totalTokens) / Double(total)
                            VStack(alignment: .leading, spacing: 3) {
                                HStack(spacing: 5) {
                                    Circle().fill(providerReportColor(entry.key)).frame(width: 6, height: 6)
                                    Text(providerName(entry.key)).font(.footnote).lineLimit(1)
                                    Spacer(minLength: 4)
                                    Text(tokenText(entry.usage.totalTokens))
                                        .font(.footnote.weight(.medium)).monospacedDigit()
                                    Text("\(Int((share * 100).rounded()))%")
                                        .font(.system(size: 10)).monospacedDigit().foregroundStyle(.tertiary)
                                        .frame(width: 30, alignment: .trailing)
                                }
                                GeometryReader { proxy in
                                    ZStack(alignment: .leading) {
                                        Capsule().fill(Color.primary.opacity(0.05))
                                        Capsule().fill(providerReportColor(entry.key).opacity(0.75))
                                            .frame(width: max(3, proxy.size.width * share))
                                    }
                                }.frame(height: 3)
                            }
                            .rowHover()
                            .dailyHoverTip(title: providerName(entry.key),
                                           lines: classTipLines(entry.usage)
                                               + ["合计：\(tokenText(entry.usage.totalTokens))"])
                        }
                    }
                }
            }
            if !missing.isEmpty {
                Text(missing.map { providerName($0) }.joined(separator: "、") + " 无 token 统计，不计入")
                    .font(.system(size: 10)).foregroundStyle(.tertiary)
            }
        }
        .dailyReportCard()
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
    // 环形图：从 12 点方向顺时针；中心标最大来源的占比，段间留小缝。
    private func donut(_ entries: [(key: String, usage: SourceUsage)], total: Int) -> some View {
        var cursor = 0.0
        var segments: [(color: Color, start: Double, end: Double)] = []
        for entry in entries {
            let start = cursor
            cursor = min(cursor + Double(entry.usage.totalTokens) / Double(total), 1)
            segments.append((providerReportColor(entry.key), start, cursor))
        }
        let gap = segments.count > 1 ? 0.004 : 0
        let lead = entries[0]
        let leadShare = Int((Double(lead.usage.totalTokens) / Double(total) * 100).rounded())
        return ZStack {
            Circle().stroke(Color.primary.opacity(0.06), lineWidth: 14)
            ForEach(Array(segments.enumerated()), id: \.offset) { _, segment in
                Circle()
                    .trim(from: segment.start + gap, to: max(segment.start + gap, segment.end - gap))
                    .stroke(segment.color, style: StrokeStyle(lineWidth: 14, lineCap: .butt))
                    .rotationEffect(.degrees(-90))
            }
            VStack(spacing: 0) {
                Text("\(leadShare)%").font(.system(size: 20, weight: .bold)).monospacedDigit()
                Text(providerName(lead.key)).font(.system(size: 10)).foregroundStyle(.secondary).lineLimit(1)
            }
        }
        .frame(width: 112, height: 112)
    }
}

struct TopProjectsCard: View {
    let projects: [TopProject]
    var body: some View {
        let longest = max(projects.first?.totalTokens ?? 1, 1)
        VStack(alignment: .leading, spacing: 10) {
            Text("Top 5 活跃项目 · 近 7 天").font(.headline)
            if projects.isEmpty {
                Text("近 7 天没有可统计的项目").font(.subheadline).foregroundStyle(.secondary)
            } else {
              // 与左侧环形图卡等高时，列表在剩余空间里垂直居中。
              VStack(alignment: .leading, spacing: 10) {
                ForEach(Array(projects.enumerated()), id: \.element.id) { index, project in
                    VStack(alignment: .leading, spacing: 4) {
                        HStack(spacing: 6) {
                            Text("\(index + 1)")
                                .font(.system(size: 10, weight: .semibold)).monospacedDigit()
                                .foregroundStyle(.tertiary).frame(width: 12, alignment: .trailing)
                            Text(project.name)
                                .font(.footnote).lineLimit(1)
                                .help(project.project)
                            Spacer(minLength: 4)
                            Text(tokenText(project.totalTokens))
                                .font(.footnote.weight(.medium)).monospacedDigit()
                            Text("\(Int((project.share * 100).rounded()))%")
                                .font(.system(size: 10)).monospacedDigit().foregroundStyle(.tertiary)
                                .frame(width: 30, alignment: .trailing)
                        }
                        // 条按输入/缓存/输出分段，与头卡分段条同一套色。
                        GeometryReader { proxy in
                            let width = proxy.size.width * Double(project.totalTokens) / Double(longest)
                            let sum = max(project.totalTokens, 1)
                            let parts: [(Int, Color)] = [(project.outputTokens, tokenClassColor(.output)),
                                                         (project.inputTokens, tokenClassColor(.input)),
                                                         (project.cacheTokens, tokenClassColor(.cache))]
                            ZStack(alignment: .leading) {
                                RoundedRectangle(cornerRadius: 3).fill(Color.primary.opacity(0.05))
                                HStack(spacing: 1) {
                                    ForEach(Array(parts.enumerated()), id: \.offset) { _, part in
                                        if part.0 > 0 {
                                            Rectangle().fill(part.1)
                                                .frame(width: max(2, width * Double(part.0) / Double(sum)))
                                        }
                                    }
                                }
                                .frame(width: max(4, width))
                                .clipShape(RoundedRectangle(cornerRadius: 3))
                            }
                        }.frame(height: 8).padding(.leading, 18)
                    }
                    .rowHover()
                    .dailyHoverTip(title: project.name,
                                   lines: classTipLines(input: project.inputTokens,
                                                        cache: project.cacheTokens,
                                                        output: project.outputTokens)
                                       + ["合计：\(tokenText(project.totalTokens))"])
                }
              }
              .frame(maxHeight: .infinity, alignment: .center)
            }
        }
        .dailyReportCard()
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
}

struct DayDetailView: View {
    @ObservedObject var model: DailyReportModel
    let date: String
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if let report = model.selectedDay, model.selectedDate == date {
                    if report.totals.tasks == 0 {
                        VStack(spacing: 10) {
                            Image(systemName: "calendar.badge.clock").font(.system(size: 30)).foregroundStyle(.tertiary)
                            Text("这一天没有会话记录").font(.subheadline).foregroundStyle(.secondary)
                        }.frame(maxWidth: .infinity).padding(.vertical, 60)
                    } else {
                        // 各区块统一卡片内边距，比例条/节奏带左右边界对齐。
                        // 来源与项目并排成一行，和总览页的双栏节奏一致。
                        SummaryCardView(report: report)
                        RhythmBandView(report: report).dailyReportCard()
                        HStack(alignment: .top, spacing: 14) {
                            SourceShareView(title: "来源占比", sources: report.totals.sources,
                                            unavailable: Set(report.tasks.filter { $0.fidelity == "unavailable" }.map(\.provider)))
                            ProjectBarsView(report: report)
                                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                        }
                        .fixedSize(horizontal: false, vertical: true)
                        TaskListView(report: report)
                    }
                } else if let error = model.error {
                    HStack(alignment: .top, spacing: 8) {
                        Image(systemName: "exclamationmark.triangle.fill").font(.caption).foregroundStyle(.red)
                        Text(error).font(.caption).foregroundStyle(.red).textSelection(.enabled)
                        Spacer()
                        Button { model.loadDay(date) } label: { Text("重试").font(.caption) }
                            .buttonStyle(InboxActionButtonStyle())
                    }
                    .padding(10)
                    .background(Color.red.opacity(0.06), in: RoundedRectangle(cornerRadius: 8))
                } else {
                    HStack(spacing: 10) {
                        ProgressView().controlSize(.small)
                        Text("正在生成当日报告…").font(.subheadline).foregroundStyle(.secondary)
                    }.frame(maxWidth: .infinity).padding(.vertical, 60)
                }
            }
            .padding(16)
        }
        .navigationTitle(reportDayDisplay(date))
        .onAppear { model.loadDay(date) }
    }
}

struct SummaryCardView: View {
    let report: DayReport
    var body: some View {
        UsageHeroView(total: report.totals.totalTokens, input: report.totals.inputTokens,
                      cache: report.totals.cacheTokens, output: report.totals.outputTokens,
                      caption: "token 合计", accent: liveLabel,
                      tiles: [("\(report.totals.tasks)", "任务"), ("\(report.totals.turns)", "轮次"),
                              ("\(activeSourceCount)", "来源"), (activeSpanText, "活跃时长")])
    }
    // 今天不固化：每次打开即时重算，标明截止时刻，次日首次查看时补算定稿。
    private var liveLabel: String? {
        guard report.date == dailyReportDayKey(Date()) else { return nil }
        let until = reportClock(report.generatedAt ?? Date().timeIntervalSince1970)
        return "实时汇总 · 截至 \(until)"
    }
    private var activeSourceCount: Int {
        report.totals.sources.values.filter { $0.totalTokens > 0 }.count
    }
    // 活跃时长 = 全部任务真实活动段的并集长度；并行任务不重复计时。
    private var activeSpanText: String {
        let seconds = mergedActiveSeconds(report.tasks)
        let minutes = Int((seconds / 60).rounded())
        if minutes < 60 { return "\(minutes)m" }
        return minutes % 60 == 0 ? "\(minutes / 60)h" : "\(minutes / 60)h \(minutes % 60)m"
    }
}

func taskActivitySegments(_ task: ReportTask) -> [[Double]] {
    if let segments = task.segments, !segments.isEmpty { return segments }
    return task.firstAt > 0 ? [[task.firstAt, max(task.lastAt, task.firstAt)]] : []
}
func mergedActiveSeconds(_ tasks: [ReportTask]) -> Double {
    let segments = tasks.flatMap(taskActivitySegments)
        .filter { $0.count == 2 && $0[0] > 0 }
        .sorted { $0[0] < $1[0] }
    var total = 0.0
    var current: (Double, Double)?
    for segment in segments {
        let end = max(segment[1], segment[0])
        if let open = current, segment[0] <= open.1 {
            current = (open.0, max(open.1, end))
        } else {
            if let open = current { total += open.1 - open.0 }
            current = (segment[0], end)
        }
    }
    if let open = current { total += open.1 - open.0 }
    return total
}

struct RhythmBandView: View {
    let report: DayReport
    var tasks: [ReportTask] { report.tasks }
    private var dayStart: Double { reportDate(report.date)?.timeIntervalSince1970 ?? 0 }
    private let laneHeight: CGFloat = 9
    private let laneGap: CGFloat = 3
    var body: some View {
        let range = rhythmRange(tasks.map(taskActivitySegments), dayStart: dayStart)
        let hours = range.1 - range.0
        let lanes = providerLanes
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text("一天节奏 · \(Int(range.0))–\(Int(range.1)) 时").font(.headline)
                Spacer()
                legend
            }
            // 每个来源一条泳道：并行任务不再互相覆盖，一眼能看出几个 agent 同时在跑。
            GeometryReader { proxy in
                let width = proxy.size.width
                ZStack(alignment: .topLeading) {
                    ForEach(Array(stride(from: range.0, through: range.1, by: 2)), id: \.self) { hour in
                        Rectangle().fill(Color.primary.opacity(0.06)).frame(width: 1)
                            .offset(x: min(max((hour - range.0) / hours * width, 0), width - 1))
                    }
                    ForEach(Array(lanes.enumerated()), id: \.offset) { index, provider in
                        let y = CGFloat(index) * (laneHeight + laneGap)
                        RoundedRectangle(cornerRadius: 3)
                            .fill(providerReportColor(provider).opacity(0.10))
                            .frame(height: laneHeight).offset(y: y)
                        ForEach(tasks.filter { $0.provider == provider }) { task in
                            ForEach(Array(taskActivitySegments(task).enumerated()), id: \.offset) { _, segment in
                                let left = (rhythmHour(segment[0], dayStart: dayStart) - range.0) / hours
                                let span = (rhythmHour(segment[1], dayStart: dayStart)
                                            - rhythmHour(segment[0], dayStart: dayStart)) / hours
                                RoundedRectangle(cornerRadius: 3)
                                    .fill(providerReportColor(task.provider))
                                    .frame(width: max(4, span * width), height: laneHeight)
                                    .offset(x: min(max(0, left * width), width - 4), y: y)
                                    .chartHover(scale: 1.15)
                                    .dailyHoverTip(title: task.title,
                                                   lines: ["\(providerName(task.provider)) · \(reportClock(segment[0]))–\(reportClock(segment[1])) · 全天 \(reportTimeRange(task)) · \(task.turns) 轮"]
                                                       + classTipLines(input: task.inputTokens, cache: task.cacheTokens,
                                                                       output: task.outputTokens)
                                                       + ["合计：\(tokenText(task.totalTokens))"])
                            }
                        }
                    }
                    if let now = nowFraction(range: range) {
                        Rectangle().fill(Color.attention).frame(width: 1.5)
                            .offset(x: now * width)
                    }
                }
            }
            .frame(height: CGFloat(max(lanes.count, 1)) * (laneHeight + laneGap) - laneGap)
            .help("按来源分泳道的节奏带；色块为任务真实活动时段，空闲留白")
            GeometryReader { proxy in
                let width = proxy.size.width
                ZStack(alignment: .topLeading) {
                    ForEach(Array(stride(from: range.0, through: range.1, by: 2)), id: \.self) { hour in
                        Text(String(format: "%02d", Int(hour)))
                            .font(.system(size: 9)).monospacedDigit().foregroundStyle(.tertiary)
                            .position(x: min(max((hour - range.0) / hours * width, 8), width - 8), y: 6)
                    }
                }
            }
            .frame(height: 12)
        }
    }
    // 泳道按来源 token 量降序，最活跃的来源在最上面。
    private var providerLanes: [String] {
        var totals: [String: Int] = [:]
        for task in tasks { totals[task.provider, default: 0] += task.totalTokens }
        return totals.keys.sorted { (totals[$0]!, $1) > (totals[$1]!, $0) }
    }
    // 今天画一条「现在」竖线，让实时汇总的截止点落在时间轴上。
    private func nowFraction(range: (Double, Double)) -> Double? {
        guard report.date == dailyReportDayKey(Date()) else { return nil }
        let hour = rhythmHour(Date().timeIntervalSince1970, dayStart: dayStart)
        guard hour >= range.0, hour <= range.1 else { return nil }
        return (hour - range.0) / (range.1 - range.0)
    }
    private var legend: some View {
        HStack(spacing: 8) {
            ForEach(providerLanes, id: \.self) { provider in
                HStack(spacing: 3) {
                    Circle().fill(providerReportColor(provider)).frame(width: 6, height: 6)
                    Text(providerName(provider)).font(.system(size: 9)).foregroundStyle(.secondary)
                }
            }
        }
    }
}



struct ProjectBarsView: View {
    let report: DayReport
    var body: some View {
        let entries: [(key: String, usage: SourceUsage)] =
            report.totals.projects.map { (key: $0.key, usage: $0.value) }
                .sorted { $0.usage.totalTokens > $1.usage.totalTokens }
        let longest = max(entries.first?.usage.totalTokens ?? 1, 1)
        let split = providerSplit
        VStack(alignment: .leading, spacing: 10) {
            Text("项目 · \(entries.count)").font(.headline)
            if entries.isEmpty {
                Text("没有可统计的项目").font(.subheadline).foregroundStyle(.secondary)
            }
            VStack(alignment: .leading, spacing: 10) {
            ForEach(Array(entries.prefix(6).enumerated()), id: \.element.key) { index, entry in
                VStack(alignment: .leading, spacing: 4) {
                    HStack(spacing: 6) {
                        Text("\(index + 1)")
                            .font(.system(size: 10, weight: .semibold)).monospacedDigit()
                            .foregroundStyle(.tertiary).frame(width: 12, alignment: .trailing)
                        Text((entry.key as NSString).lastPathComponent)
                            .font(.footnote).lineLimit(1)
                            .help(entry.key)
                        Spacer(minLength: 4)
                        Text(tokenText(entry.usage.totalTokens))
                            .font(.footnote.weight(.medium)).monospacedDigit()
                    }
                    // 条按来源分段着色：同一项目里哪个 agent 用得多，和上方环形图同一套色。
                    GeometryReader { proxy in
                        let width = proxy.size.width * Double(entry.usage.totalTokens) / Double(longest)
                        let parts = split[entry.key] ?? []
                        let partTotal = max(parts.reduce(0) { $0 + $1.tokens }, 1)
                        ZStack(alignment: .leading) {
                            RoundedRectangle(cornerRadius: 3).fill(Color.primary.opacity(0.05))
                            HStack(spacing: 1) {
                                if parts.isEmpty {
                                    Rectangle().fill(Color.accentColor.opacity(0.65))
                                } else {
                                    ForEach(parts, id: \.provider) { part in
                                        Rectangle().fill(providerReportColor(part.provider).opacity(0.85))
                                            .frame(width: max(2, width * Double(part.tokens) / Double(partTotal)))
                                    }
                                }
                            }
                            .frame(width: max(4, width))
                            .clipShape(RoundedRectangle(cornerRadius: 3))
                        }
                    }.frame(height: 8).padding(.leading, 18)
                }
                .rowHover()
                .dailyHoverTip(title: (entry.key as NSString).lastPathComponent,
                               lines: classTipLines(entry.usage)
                                   + ["合计：\(tokenText(entry.usage.totalTokens))"]
                                   + (split[entry.key] ?? []).map { "\(providerName($0.provider))：\(tokenText($0.tokens))" })
            }
            }
            .frame(maxHeight: .infinity, alignment: .center)
        }
        .dailyReportCard()
        .frame(maxWidth: .infinity, alignment: .leading)
    }
    // 项目 × 来源的 token 拆分来自任务列表；无 token 记录的任务不参与。
    private var providerSplit: [String: [(provider: String, tokens: Int)]] {
        var totals: [String: [String: Int]] = [:]
        for task in report.tasks where task.fidelity != "unavailable" && task.totalTokens > 0 {
            let key = task.project.isEmpty ? "(无项目)" : task.project
            totals[key, default: [:]][task.provider, default: 0] += task.totalTokens
        }
        return totals.mapValues { byProvider in
            byProvider.map { (provider: $0.key, tokens: $0.value) }
                .sorted { ($0.tokens, $1.provider) > ($1.tokens, $0.provider) }
        }
    }
}

struct TaskListView: View {
    let report: DayReport
    var body: some View {
        let dayTotal = max(report.totals.totalTokens, 1)
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text("任务 · \(report.tasks.count)").font(.headline)
                Spacer()
                Text("底色长度 = token 占今日合计比例").font(.system(size: 9)).foregroundStyle(.tertiary)
            }
            .padding(.bottom, 8)
            ForEach(Array(report.tasks.enumerated()), id: \.element.id) { index, task in
                taskRow(task, dayTotal: dayTotal)
                if index < report.tasks.count - 1 {
                    Divider().opacity(0.5)
                }
            }
        }
        .dailyReportCard()
    }
    // 一行一任务：左侧来源色条 + 行内底色按 token 占今日合计的比例变长，取代逐张卡片和胶囊条。
    // 行内已列全所有字段，不再挂悬浮提示。
    private func taskRow(_ task: ReportTask, dayTotal: Int) -> some View {
        let color = providerReportColor(task.provider)
        let share = task.fidelity == "unavailable" ? 0 : Double(task.totalTokens) / Double(dayTotal)
        return HStack(alignment: .top, spacing: 10) {
            RoundedRectangle(cornerRadius: 1.5).fill(color).frame(width: 3)
                .padding(.vertical, 2)
            VStack(alignment: .leading, spacing: 4) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(task.title)
                        .font(.body.weight(.medium)).lineLimit(1)
                        .help(task.title)
                    Spacer(minLength: 8)
                    Text(task.fidelity == "unavailable" ? "无 token" : tokenText(task.totalTokens))
                        .font(.system(size: 12, weight: .semibold)).monospacedDigit()
                        .foregroundStyle(task.fidelity == "unavailable" ? AnyShapeStyle(.secondary) : AnyShapeStyle(.primary))
                }
                HStack(spacing: 6) {
                    Text(providerName(task.provider))
                        .font(.system(size: 10, weight: .medium))
                        .foregroundStyle(color)
                        .padding(.horizontal, 5).padding(.vertical, 1)
                        .background(color.opacity(0.13), in: Capsule())
                    if !task.project.isEmpty {
                        Text((task.project as NSString).lastPathComponent)
                            .foregroundStyle(.secondary).lineLimit(1).help(task.project)
                    }
                    if task.turns > 0 {
                        Text("\(task.turns) 轮").foregroundStyle(.secondary)
                    }
                    Text(reportTimeRange(task)).monospacedDigit().foregroundStyle(.secondary)
                    Spacer(minLength: 4)
                    Circle().fill(stateDotColor(task.state)).frame(width: 6, height: 6)
                    Text(stateName(task.state)).foregroundStyle(.secondary)
                }
                .font(.footnote)
                Text(usageLine(task)).font(.caption2).foregroundStyle(.tertiary).lineLimit(1)
            }
        }
        .padding(.vertical, 8)
        .padding(.horizontal, 6)
        .background(alignment: .leading) {
            GeometryReader { proxy in
                RoundedRectangle(cornerRadius: 6)
                    .fill(color.opacity(0.07))
                    .frame(width: max(0, proxy.size.width * share))
            }
        }
        .rowHover()
    }
}


@MainActor final class InboxModel: ObservableObject {
    @Published var rows: [InboxRow] = []
    @Published var showAll = false { didSet { page = 0 } }
    @Published var query = "" { didSet { page = 0 } }
    @Published var searchExpanded = false
    func setSearchExpanded(_ expanded: Bool) {
        searchExpanded = expanded
        if !expanded { query = "" }
    }
    @Published var page = 0
    let pageSize = 20
    @Published var loading = false
    @Published var opening = false
    @Published var error: String?
    @Published var degraded: [String] = []
    @Published var notificationsEnabled = UserDefaults.standard.object(forKey: "notificationsEnabled") as? Bool ?? true
    @Published var notificationsAllowed = false
    @Published var notificationStatus = "正在检查通知权限"
    @Published var agents: [AgentEntry] = []
    private var notificationSeen = UserDefaults.standard.dictionary(forKey: "notificationSeen") as? [String: String] ?? [:]
    private var lastLaunchDirectory = UserDefaults.standard.string(forKey: "lastLaunchDirectory")
    private var loadingAgents = false
    private var initialNotificationSnapshot = true
    private var notificationInFlight = Set<String>()
    private var notificationRetry: [String: Date] = [:]
    private var lastDockBadge = -1
    let root: String
    private var timer: Timer?
    init() {
        root = Bundle.main.object(forInfoDictionaryKey: "SessionManagerRoot") as? String ?? ""
        let tick = Timer(timeInterval: 3, repeats: true) { [weak self] _ in
            guard let self else { return }
            Task { @MainActor in self.refresh() }
        }
        RunLoop.main.add(tick, forMode: .common)
        timer = tick
        checkNotificationPermission()
        loadAgents()
    }
    var unreadCount: Int { rows.filter { $0.unread && inboxRowListed(state: $0.state, openAvailable: $0.openAvailable) }.count }
    var filtered: [InboxRow] {
        rows.filter { row in
            guard inboxRowListed(state: row.state, openAvailable: row.openAvailable) else { return false }
            return (showAll || row.unread) && (query.isEmpty ||
                (row.title + " " + row.project + " " + row.provider).localizedCaseInsensitiveContains(query))
        }.sorted {
            if !showAll {
                let rank: (InboxRow) -> Int = { $0.state == "waiting" ? 0 : ($0.state == "failed" ? 1 : 2) }
                if rank($0) != rank($1) { return rank($0) < rank($1) }
            }
            return max($0.activityAt ?? 0, $0.eventAt) > max($1.activityAt ?? 0, $1.eventAt)
        }
    }
    var totalPages: Int { inboxPageCount(total: filtered.count, size: pageSize) }
    var visible: [InboxRow] {
        let values = filtered
        guard showAll else { return values }
        // 懒加载：随滚动逐步放开已加载的行，到底自动追加下一页。
        return Array(values.prefix(min((page + 1) * pageSize, values.count)))
    }
    func loadMoreIfNeeded(for row: InboxRow) {
        guard showAll, page + 1 < totalPages, row.id == visible.last?.id else { return }
        page += 1
    }
    func toggleNotifications() {
        notificationsEnabled.toggle()
        UserDefaults.standard.set(notificationsEnabled, forKey: "notificationsEnabled")
        if notificationsEnabled { checkNotificationPermission() }
    }
    func loadAgents() {
        guard !loadingAgents else { return }
        loadingAgents = true
        let directory = root
        Task {
            let result = await Task.detached { Self.call(root: directory, arguments: ["agents"]) }.value
            loadingAgents = false
            guard result.0 == 0 else { return }
            let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
            if let list = try? decoder.decode(AgentList.self, from: result.1) {
                agents = list.agents.filter(\.installed)
            }
        }
    }
    func launch(_ agent: AgentEntry) {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.canCreateDirectories = true
        panel.allowsMultipleSelection = false
        panel.directoryURL = URL(fileURLWithPath: lastLaunchDirectory ?? NSHomeDirectory())
        panel.message = "选择启动 \(agent.name) 的工作目录"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        lastLaunchDirectory = url.path
        UserDefaults.standard.set(url.path, forKey: "lastLaunchDirectory")
        action(["launch", "--agent", agent.id, "--dir", url.path], isOpen: false)
    }
    func checkNotificationPermission() {
        Task {
            let center = UNUserNotificationCenter.current()
            let settings = await center.notificationSettings()
            notificationsAllowed = settings.authorizationStatus == .authorized || settings.authorizationStatus == .provisional
            notificationStatus = notificationsAllowed ? "通知已开启" : "系统尚未允许通知"
            if settings.authorizationStatus == .notDetermined && notificationsEnabled {
                do {
                    let granted = try await center.requestAuthorization(options: [.alert, .sound])
                    notificationsAllowed = granted
                    notificationStatus = granted ? "通知已开启" : "请在系统设置 → 通知中允许「会话通知」"
                } catch {
                    let detail = error as NSError
                    notificationStatus = "通知授权请求失败（\(detail.domain) \(detail.code)）"
                }
            }
        }
    }
    private func notifyNewItems(_ values: [InboxRow]) {
        let center = UNUserNotificationCenter.current()
        let previousSeen = notificationSeen
        let readIDs = values.filter { !$0.unread && notificationSeen[$0.id] != nil }.map(\.id)
        if !readIDs.isEmpty { center.removeDeliveredNotifications(withIdentifiers: readIDs) }
        for row in values {
            let token = row.attentionToken ?? ""
            let enabled = notificationsEnabled && notificationsAllowed
            if initialNotificationSnapshot || !enabled || !row.unread {
                notificationSeen[row.id] = token
                continue
            }
            guard needsNotification(unread: row.unread, token: token, seen: notificationSeen[row.id],
                                    initialSnapshot: false, enabled: enabled), !notificationInFlight.contains(row.id),
                  (notificationRetry[row.id] ?? .distantPast) <= Date() else { continue }
            notificationInFlight.insert(row.id)
            let content = UNMutableNotificationContent()
            content.title = "\(providerName(row.provider)) · \(stateName(row.state))"
            content.body = row.title
            content.sound = .default
            content.threadIdentifier = row.id
            content.userInfo = ["item_id": row.id, "revision": row.revision]
            center.add(UNNotificationRequest(identifier: row.id, content: content, trigger: nil)) { [weak self] failure in
                Task { @MainActor in
                    guard let self else { return }
                    self.notificationInFlight.remove(row.id)
                    if failure == nil {
                        self.notificationSeen[row.id] = token
                        UserDefaults.standard.set(self.notificationSeen, forKey: "notificationSeen")
                    } else { self.notificationRetry[row.id] = Date().addingTimeInterval(60) }
                }
            }
        }
        initialNotificationSnapshot = false
        if previousSeen != notificationSeen { UserDefaults.standard.set(notificationSeen, forKey: "notificationSeen") }
    }
    // Dock 角标与菜单栏托盘同口径（未读会话数）；refresh 每 3 秒跑一次，仅数值变化时改写。
    // 不用 dockTile.badgeLabel：本应用启动时替换 applicationIconImage 后系统角标不再渲染
    // （实测 badgeLabel 有值而 Dock 不画、邻居应用角标正常），改为把角标画进应用图标。
    private func updateDockBadge() {
        let count = unreadCount
        guard count != lastDockBadge else { return }
        lastDockBadge = count
        DockBadge.unread = count
        let dark = NSApp.effectiveAppearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
        applyDockIcon(dark: dark, unread: count)
    }
    nonisolated static func call(root: String, arguments: [String]) -> (Int32, Data, String) {
        let process = Process()
        if let resources = Bundle.main.resourceURL,
           FileManager.default.isExecutableFile(atPath: resources.appendingPathComponent("bin/session-manager").path),
           FileManager.default.fileExists(atPath: resources.appendingPathComponent("pylib").path) {
            // 发布包：脚本与依赖随包，解释器选择和 PYTHONPATH 由随包的 CLI 统一处理。
            process.executableURL = resources.appendingPathComponent("bin/session-manager")
            process.arguments = ["inbox"] + arguments
        } else {
            // 开发包：直接跑仓库里的脚本和 venv。
            process.executableURL = URL(fileURLWithPath: root + "/scratch/iterm-probe-venv/bin/python")
            process.arguments = [root + "/scripts/inbox.py"] + arguments
        }
        let output = Pipe(); let errors = Pipe()
        process.standardOutput = output; process.standardError = errors
        do {
            try process.run()
            let data = output.fileHandleForReading.readDataToEndOfFile()
            let diagnostic = errors.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            return (process.terminationStatus, data, String(data: diagnostic, encoding: .utf8) ?? "")
        } catch { return (1, Data(), error.localizedDescription) }
    }
    func refresh() {
        guard !loading else { return }
        loading = true
        let directory = root
        Task {
            let result = await Task.detached { Self.call(root: directory, arguments: ["rows", "--all", "--refresh"]) }.value
            loading = false
            guard result.0 == 0 else { error = result.2; return }
            do {
                let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
                let payload = try decoder.decode(Envelope.self, from: result.1)
                rows = payload.sessions
                page = max(0, min(page, totalPages - 1))
                if agents.isEmpty { loadAgents() } // 启动时检测偶发失败会在后续刷新里自动补试。
                notifyNewItems(rows)
                updateDockBadge()
                degraded = (payload.health?.sources ?? [:]).filter { $0.value.status != "ok" }.map { name, info in
                    if let count = info.errors, count > 0 { return "\(providerName(name))：\(count) 个历史记录暂未接入" }
                    return "\(providerName(name))：来源暂不可用"
                }.sorted()
            } catch { self.error = "列表读取失败：" + error.localizedDescription }
        }
    }
    func acknowledge(_ row: InboxRow) { action(["ack", row.id, "--revision", String(row.revision)], isOpen: false) }
    func open(_ row: InboxRow) {
        // Zcode 跳转依赖辅助功能授权；缺失时走拖拽授权悬浮窗，不发起会失败的开销、不动未读状态。
        if row.provider == "zcode" && !AccessibilitySetupController.shared.isGranted {
            AccessibilitySetupController.shared.present()
            return
        }
        action(["open", row.id, "--revision", String(row.revision)], isOpen: true)
    }
    private func action(_ arguments: [String], isOpen: Bool) {
        if isOpen { opening = true }
        error = nil
        let directory = root
        Task {
            let result = await Task.detached { Self.call(root: directory, arguments: arguments) }.value
            if isOpen { opening = false }
            if result.0 != 0 {
                let raw = result.2.isEmpty ? String(data: result.1, encoding: .utf8) ?? "操作未完成" : result.2
                if let data = raw.data(using: .utf8), let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any], let reason = object["reason"] as? String {
                    error = reason
                } else { error = String(raw.prefix(500)) }
                if raw.contains("accessibility_permission_required") {
                    AccessibilitySetupController.shared.present()
                }
            }
            refresh()
        }
    }
}

private var agentIconCache: [String: NSImage] = [:]

// 统一来源标志：每个标志先按透明边界裁掉自带留白，再等比缩进目标方框居中，
// 各家标志在列表和入口里占的框一致；按目标点数光栅化（Retina 下 2x），不做二次放大。
func agentIcon(_ id: String, size points: CGFloat = 16) -> NSImage {
    let key = "\(id)@\(points)"
    if let cached = agentIconCache[key] { return cached }
    let image = renderAgentIcon(id, size: NSSize(width: points, height: points))
    agentIconCache[key] = image
    return image
}

private func agentIconSource(_ id: String) -> NSImage? {
    // 有桌面 App 的来源用应用图标；CLI 用官方标志。
    // claude 指 Claude Code，用其 Clawd 标志而非 Claude 桌面版图标，两者是不同产品。
    let bundleIds = ["zcode": "dev.zcode.app"]
    if let bundleId = bundleIds[id], let url = NSWorkspace.shared.urlForApplication(withBundleIdentifier: bundleId) {
        return NSWorkspace.shared.icon(forFile: url.path)
    }
    // 官方标志收进 agent-icons 随本 App 打包：claude.svg 取自 Claude Code VS Code 扩展的 clawd.svg；
    // codex.png、agy.png 分别由 ChatGPT.app 的 icon-codex-dark-color.png 和 Antigravity.app 图标抠掉瓦片得到，
    // 不直接用应用图标，避免出现白色方框；kimi.svg 按官方 favicon 几何重绘，避免 64px 位图放大发糊。
    let official: [String: (ext: String, fallbackPath: String)] = [
        "pi": ("svg", "native/agent-icons/pi.svg"),
        "claude": ("svg", "native/agent-icons/claude.svg"),
        "kimi": ("svg", "native/agent-icons/kimi.svg"),
        "codex": ("png", "native/agent-icons/codex.png"),
        "agy": ("png", "native/agent-icons/agy.png"),
    ]
    guard let entry = official[id] else { return nil }
    var urls = [Bundle.main.url(forResource: id, withExtension: entry.ext)].compactMap { $0 }
    if let root = Bundle.main.object(forInfoDictionaryKey: "SessionManagerRoot") as? String {
        urls.append(URL(fileURLWithPath: root + "/" + entry.fallbackPath))
    }
    for url in urls where FileManager.default.fileExists(atPath: url.path) {
        if let icon = NSImage(contentsOf: url) { return icon }
    }
    return nil
}

// 在 256px 画布上光栅化后扫描 alpha，得到标志实际占据的区域（源图坐标）。
private func agentIconContentRect(_ image: NSImage) -> NSRect {
    let full = NSRect(origin: .zero, size: image.size)
    let px = 256
    guard image.size.width > 0, image.size.height > 0,
          let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: px, pixelsHigh: px, bitsPerSample: 8,
                                     samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                                     colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0),
          let data = rep.bitmapData else { return full }
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    image.draw(in: NSRect(x: 0, y: 0, width: px, height: px), from: full, operation: .sourceOver, fraction: 1)
    NSGraphicsContext.restoreGraphicsState()
    var minX = px, minY = px, maxX = -1, maxY = -1
    let rowBytes = rep.bytesPerRow
    for y in 0..<px {
        for x in 0..<px where data[y * rowBytes + x * 4 + 3] > 24 {
            minX = min(minX, x); maxX = max(maxX, x); minY = min(minY, y); maxY = max(maxY, y)
        }
    }
    guard maxX >= minX, maxY >= minY else { return full }
    // 位图 y 向下，NSImage 坐标 y 向上。
    let sx = image.size.width / CGFloat(px), sy = image.size.height / CGFloat(px)
    return NSRect(x: CGFloat(minX) * sx, y: CGFloat(px - 1 - maxY) * sy,
                  width: CGFloat(maxX - minX + 1) * sx, height: CGFloat(maxY - minY + 1) * sy)
}

private func renderAgentIcon(_ id: String, size: NSSize) -> NSImage {
    if let source = agentIconSource(id) {
        let content = agentIconContentRect(source)
        // 方框内再按视觉分量微调：横宽形的 Kimi 本就矮一截保持满框，其余标志统一收一档，
        // 实心方块的 Pi 和撑满方框的 Antigravity 再多收一点。
        let emphasis: CGFloat = ["kimi": 1, "pi": 0.68, "agy": 0.78][id] ?? 0.88
        let scale = min(size.width / max(content.width, 1), size.height / max(content.height, 1)) * emphasis
        let fitted = NSSize(width: content.width * scale, height: content.height * scale)
        // 用绘制闭包而非 lockFocus：按实际输出的缩放因子按需绘制，Retina 下矢量与高清位图保持清晰。
        return NSImage(size: size, flipped: false) { _ in
            NSGraphicsContext.current?.imageInterpolation = .high
            source.draw(in: NSRect(x: (size.width - fitted.width) / 2, y: (size.height - fitted.height) / 2,
                                   width: fitted.width, height: fitted.height),
                        from: content, operation: .sourceOver, fraction: 1)
            return true
        }
    }
    // 缺失时画品牌色字符兜底；色值与 providerReportColor 逐一对应，保持同一来源同一色。
    let glyphs = ["pi": ("π", NSColor(srgbRed: 0.392, green: 0.824, blue: 1.0, alpha: 1)),
                  "kimi": ("K", NSColor(srgbRed: 0.749, green: 0.353, blue: 0.949, alpha: 1)),
                  "codex": (">_", NSColor(srgbRed: 0.063, green: 0.639, blue: 0.498, alpha: 1)),
                  "zcode": ("Z", NSColor(srgbRed: 0.0, green: 0.478, blue: 1.0, alpha: 1)),
                  "claude": ("C", NSColor(srgbRed: 0.851, green: 0.467, blue: 0.341, alpha: 1)),
                  "agy": ("A", NSColor(srgbRed: 0.259, green: 0.522, blue: 0.957, alpha: 1)),
                  "opencode": ("OC", NSColor(srgbRed: 0.961, green: 0.620, blue: 0.043, alpha: 1))]
    let (glyph, color) = glyphs[id] ?? ("?", NSColor.systemGray)
    return NSImage(size: size, flipped: false) { rect in
        color.setFill()
        NSBezierPath(roundedRect: rect, xRadius: size.width / 4, yRadius: size.height / 4).fill()
        let text = NSAttributedString(string: glyph, attributes: [
            .font: NSFont.systemFont(ofSize: size.height * 0.7, weight: .semibold),
            .foregroundColor: NSColor.white,
        ])
        let bounds = text.boundingRect(with: size, options: [.usesLineFragmentOrigin])
        text.draw(at: NSPoint(x: (size.width - bounds.width) / 2, y: (size.height - bounds.height) / 2))
        return true
    }
}

func stateName(_ state: String) -> String {    ["running": "运行中", "waiting": "等待输入", "idle": "本轮已结束", "failed": "发生错误",
     "interrupted": "已中断", "closed": "已退出", "unknown": "状态待确认"][state] ?? state
}
func providerName(_ provider: String) -> String {
    ["claude": "Claude", "codex": "Codex", "zcode": "Zcode", "pi": "Pi", "kimi": "Kimi",
     "agy": "Antigravity CLI", "opencode": "OpenCode"][provider] ?? provider
}

// —— 辅助功能拖拽授权 ——
// Zcode「前往会话」依赖 AX 权限。缺权限时不让用户去 Finder 翻目录：
// 自动打开系统设置对应面板，并浮出一个可拖拽的本应用徽章，拖进列表即完成授权。
@MainActor
final class AccessibilitySetupController {
    static let shared = AccessibilitySetupController()
    private var panel: NSPanel?
    private var watchdog: Timer?

    var isGranted: Bool { AXIsProcessTrusted() }

    func present() {
        if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility") {
            NSWorkspace.shared.open(url)
        }
        if panel == nil { panel = makePanel() }
        panel?.orderFrontRegardless()
        startWatchdog()
    }

    func dismiss() {
        watchdog?.invalidate()
        watchdog = nil
        panel?.orderOut(nil)
        panel = nil
    }

    private func makePanel() -> NSPanel {
        let panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 268, height: 92),
                            styleMask: [.borderless, .nonactivatingPanel],
                            backing: .buffered, defer: false)
        panel.level = .floating
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.hidesOnDeactivate = false
        panel.isOpaque = false
        panel.backgroundColor = .clear
        let badge = DragBadgeView(frame: NSRect(x: 0, y: 0, width: 268, height: 92))
        badge.onClose = { [weak self] in self?.dismiss() }
        panel.contentView = badge
        if let screen = NSScreen.main {
            panel.setFrameOrigin(NSPoint(x: screen.frame.midX - 134, y: screen.frame.minY + 96))
        }
        return panel
    }

    // 拖进去之后无需用户回报：轮询到已授权就自动收起悬浮窗。
    private func startWatchdog() {
        watchdog?.invalidate()
        let timer = Timer(timeInterval: 1, repeats: true) { [weak self] _ in
            guard let self else { return }
            Task { @MainActor in
                if self.isGranted { self.dismiss() }
            }
        }
        RunLoop.main.add(timer, forMode: .common)
        watchdog = timer
    }
}

// 悬浮徽章：应用图标 + 提示文案；按住任意位置即发起携带本应用 URL 的拖拽。
final class DragBadgeView: NSView, NSDraggingSource {
    var onClose: (() -> Void)?
    private let icon: NSImage

    override init(frame frameRect: NSRect) {
        icon = NSApp.applicationIconImage
        super.init(frame: frameRect)
        wantsLayer = true
        layer?.cornerRadius = 14
        layer?.backgroundColor = NSColor.controlBackgroundColor.cgColor
        layer?.borderColor = NSColor.separatorColor.cgColor
        layer?.borderWidth = 1
        layer?.shadowOpacity = 0.3
        layer?.shadowColor = NSColor.black.cgColor
        layer?.shadowOffset = NSSize(width: 0, height: -4)
        layer?.shadowRadius = 8

        let iconView = NSImageView(frame: NSRect(x: 14, y: 20, width: 52, height: 52))
        iconView.image = icon
        iconView.imageScaling = .scaleProportionallyUpOrDown
        addSubview(iconView)

        let title = NSTextField(labelWithString: "会话通知")
        title.font = .systemFont(ofSize: 13, weight: .semibold)
        title.frame = NSRect(x: 76, y: 46, width: 176, height: 18)
        addSubview(title)

        let hint = NSTextField(labelWithString: "把我拖进「辅助功能」列表")
        hint.font = .systemFont(ofSize: 11)
        hint.textColor = .secondaryLabelColor
        hint.frame = NSRect(x: 76, y: 26, width: 176, height: 16)
        addSubview(hint)

        let close = ClosureButton(frame: NSRect(x: 242, y: 66, width: 18, height: 18))
        close.title = "✕"
        close.isBordered = false
        close.font = .systemFont(ofSize: 11)
        close.contentTintColor = .secondaryLabelColor
        close.handler = { [weak self] in self?.onClose?() }
        addSubview(close)
    }

    required init?(coder: NSCoder) { fatalError("unsupported") }

    override func mouseDown(with event: NSEvent) {
        let item = NSDraggingItem(pasteboardWriter: Bundle.main.bundleURL as NSURL)
        icon.size = NSSize(width: 52, height: 52)
        item.setDraggingFrame(NSRect(x: 14, y: frame.height - 72, width: 52, height: 52), contents: icon)
        beginDraggingSession(with: [item], event: event, source: self)
    }

    func draggingSession(_ session: NSDraggingSession, sourceOperationMaskForDraggingAt location: NSPoint) -> NSDragOperation {
        .copy
    }

    func draggingSession(_ session: NSDraggingSession, sourceOperationMaskFor context: NSDraggingContext) -> NSDragOperation {
        .copy
    }
}

final class ClosureButton: NSButton {
    var handler: (() -> Void)?
    override func mouseDown(with event: NSEvent) { handler?() }
}

// 「新建会话」行：已安装 agent 图标按钮平铺；放不下时行尾收敛为「+N」菜单，点击列出剩余 agent。
// GeometryReader 独占整行拿可用宽度，按预算常数折算容量——不做子视图测量，避免布局提案耦合
// （裸 swiftc 构建没有 SwiftUIMacros，视图里用不了 @State）。
struct NewSessionLauncherRow: View {
    @ObservedObject var model: InboxModel
    // 标签「新建会话」自然宽度 ≤ 48pt，加一处 12pt 间距与 8pt 保险；宁可提前出「+N」也不裁切按钮。
    private let labelBudget: CGFloat = 68

    var body: some View {
        GeometryReader { proxy in
            let total = model.agents.count
            let visible = visibleCount(available: proxy.size.width, total: total)
            HStack(spacing: 12) {
                Text("新建会话").font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: true, vertical: false)
                ForEach(model.agents.prefix(visible)) { agent in
                    launcherButton(agent)
                }
                if visible < total {
                    overflowMenu(hidden: Array(model.agents.suffix(total - visible)))
                }
                Spacer()
            }
        }
        .frame(height: 36)
    }

    // 每个按钮占 36pt 图标 + 12pt 间距；全部放得下就不显示「+N」。
    private func visibleCount(available: CGFloat, total: Int) -> Int {
        let usable = max(0, available - labelBudget)
        if CGFloat(48 * total) - 12 <= usable + 0.5 { return total }
        return max(1, Int((usable - 36) / 48))
    }

    @ViewBuilder
    private func launcherButton(_ agent: AgentEntry) -> some View {
        Button {
            model.launch(agent)
        } label: {
            Image(nsImage: agentIcon(agent.id, size: 24))
                .frame(width: 24, height: 24)
                .frame(width: 36, height: 36)
                .background(Color.primary.opacity(0.05), in: RoundedRectangle(cornerRadius: 10))
        }
        .buttonStyle(.plain)
        .disabled(!agent.iterm)
        .accessibilityLabel("新建 \(agent.name) 会话")
        .help(agent.iterm ? "选择目录并在 iTerm2 新标签中启动 \(agent.name)" : "未检测到 iTerm2，无法在此启动")
    }

    private func overflowMenu(hidden: [AgentEntry]) -> some View {
        Menu {
            ForEach(hidden) { agent in
                Button {
                    model.launch(agent)
                } label: {
                    Label {
                        Text(agent.name)
                    } icon: {
                        Image(nsImage: agentIcon(agent.id, size: 16))
                            .frame(width: 16, height: 16)
                    }
                }
                .disabled(!agent.iterm)
            }
        } label: {
            Text("+\(hidden.count)")
                .font(.system(size: 12, weight: .semibold)).monospacedDigit()
                .foregroundStyle(.secondary)
                .frame(width: 36, height: 36)
                .background(Color.primary.opacity(0.05), in: RoundedRectangle(cornerRadius: 10))
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.hidden)
        .fixedSize()
        .accessibilityLabel("其余 \(hidden.count) 个 agent")
        .help("其余 \(hidden.count) 个 agent：" + hidden.map { $0.name }.joined(separator: "、"))
    }
}
struct InboxView: View {
    @ObservedObject var model: InboxModel
    @Environment(\.openWindow) private var openWindow
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("会话通知").font(.system(size: 22, weight: .bold))
            if !model.agents.isEmpty {
                NewSessionLauncherRow(model: model)
            }
            VStack(spacing: 8) {
                // 分段筛选居中；搜索默认只是行尾一个图标，点开才展开输入框。
                HStack {
                    Spacer()
                    Picker("显示范围", selection: $model.showAll) {
                        Text("待查看（\(model.unreadCount)）").tag(false)
                        Text("全部会话（\(model.rows.count)）").tag(true)
                    }
                    .pickerStyle(.segmented)
                    .labelsHidden()
                    Spacer()
                }
                .overlay(alignment: .trailing) {
                    Button { model.setSearchExpanded(true) } label: {
                        Image(systemName: "magnifyingglass")
                            .frame(width: 28, height: 28)
                            .foregroundStyle(model.searchExpanded ? Color.accentColor : Color.secondary)
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .help("搜索会话或项目")
                    .accessibilityLabel("搜索")
                }
                if model.searchExpanded {
                    InboxSearchField(model: model)
                }
            }
            if model.visible.isEmpty {
                Spacer()
                VStack(spacing: 10) {
                    Image(systemName: model.query.isEmpty ? "tray" : "magnifyingglass")
                        .font(.system(size: 30)).foregroundStyle(.tertiary)
                    Text(model.loading ? "正在加载会话…" : (!model.query.isEmpty ? "没有匹配的会话" : (model.showAll ? "还没有会话" : "暂无新通知")))
                        .font(.subheadline).foregroundStyle(.secondary)
                }.frame(maxWidth: .infinity)
                Spacer()
            } else {
                ScrollView {
                    LazyVStack(spacing: 0) {
                        ForEach(model.visible) { row in
                            InboxRowView(model: model, row: row)
                            Divider().padding(.leading, 54)
                        }
                        if model.showAll && model.page + 1 < model.totalPages {
                            HStack(spacing: 8) {
                                ProgressView().controlSize(.small)
                                Text("继续加载").font(.caption2).foregroundStyle(.tertiary)
                            }
                            .frame(maxWidth: .infinity).padding(.vertical, 6)
                            .onAppear { model.page += 1 }
                        }
                    }.padding(.vertical, 2)
                }
            }
            if let error = model.error {
                HStack(alignment: .top, spacing: 8) {
                    Image(systemName: "exclamationmark.triangle.fill").font(.caption).foregroundStyle(.red)
                    Text(error).font(.caption).foregroundStyle(.red).textSelection(.enabled)
                    Spacer()
                    Button { model.error = nil } label: { Image(systemName: "xmark").font(.caption2) }
                        .buttonStyle(.plain).foregroundStyle(.secondary)
                }
                .padding(10)
                .background(Color.red.opacity(0.06), in: RoundedRectangle(cornerRadius: 8))
            }
            if !model.degraded.isEmpty {
                Text(model.degraded.joined(separator: " · ")).font(.caption).foregroundStyle(.orange)
            }
            if model.notificationsEnabled && !model.notificationsAllowed {
                Text(model.notificationStatus).font(.caption).foregroundStyle(.secondary)
            }
            Text("打开会话后自动标记已读")
                .font(.caption2).foregroundStyle(.tertiary)
        }
        .padding(16)
        .frame(minWidth: 360, idealWidth: 400, minHeight: 480, idealHeight: 620)
        .background(Color(nsColor: .windowBackgroundColor))
        // 头部动作进窗口工具栏：macOS 26 起系统自动给工具栏项套 Liquid Glass，
        // 内容标题留在页内（工具栏用 unifiedCompact 且不显示标题，避免重复）。
        .toolbar {
            // 紧凑工具栏无标题时项目从左排起：先放弹性空白，把动作组推到右侧。
            ToolbarItem(placement: .automatic) { Spacer() }
            ToolbarItemGroup(placement: .primaryAction) {
                Button {
                    openWindow(id: "dailyReport")
                    NSApplication.shared.activate(ignoringOtherApps: true)
                } label: {
                    Label("工作日报", systemImage: "chart.bar.doc.horizontal")
                }
                .help("工作日报")
                Button { model.toggleNotifications() } label: {
                    Label("通知", systemImage: model.notificationsEnabled && model.notificationsAllowed ? "bell.badge.fill" : "bell.slash")
                }
                .help(model.notificationsEnabled ? model.notificationStatus + "（点击关闭）" : "点击开启消息通知")
                Button { model.refresh() } label: {
                    Label("刷新", systemImage: "arrow.clockwise")
                }
                .help("刷新").disabled(model.loading)
            }
        }
        .onAppear { model.refresh() }
    }
}

// 展开态搜索框：出现即聚焦；关闭清空关键词，让列表回到未过滤状态。
struct InboxSearchField: View {
    @ObservedObject var model: InboxModel
    @FocusState private var focused: Bool
    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: "magnifyingglass").font(.caption).foregroundStyle(.secondary)
            TextField("搜索会话或项目", text: $model.query)
                .textFieldStyle(.plain)
                .focused($focused)
                .onExitCommand { model.setSearchExpanded(false) }
            Button { model.setSearchExpanded(false) } label: {
                Image(systemName: "xmark.circle.fill").font(.caption).foregroundStyle(.tertiary)
            }
            .buttonStyle(.plain)
            .help("关闭搜索")
            .accessibilityLabel("关闭搜索")
        }
        .padding(.horizontal, 10).padding(.vertical, 8)
        .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(Color.primary.opacity(0.06)))
        .onAppear { focused = true }
    }
}

struct InboxActionButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .foregroundStyle(isEnabled ? Color.primary.opacity(0.75) : Color.secondary.opacity(0.4))
            .background {
                RoundedRectangle(cornerRadius: 8)
                    .fill(Color.primary.opacity(isEnabled && configuration.isPressed ? 0.12 : 0.035))
            }
            .contentShape(RoundedRectangle(cornerRadius: 8))
            .scaleEffect(isEnabled && configuration.isPressed ? 0.90 : 1)
            .animation(.easeOut(duration: 0.12), value: configuration.isPressed)
    }
}

struct InboxRowView: View {
    @ObservedObject var model: InboxModel
    let row: InboxRow
    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            // 主内容为 agent 图标；待处理时右上角橙色圆点，描边取窗口背景色，
            // 亮色/暗色模式自动适配。
            // 已退出与已结束对用户含义一致（都可重开查看），外观保持一致，不做置灰。
            Image(nsImage: agentIcon(row.provider, size: 30))
                .frame(width: 30, height: 30)
                .overlay(alignment: .topTrailing) {
                    if row.unread {
                        Circle()
                            .fill(Color.attention)
                            .frame(width: 9, height: 9)
                            .overlay(Circle().strokeBorder(Color(nsColor: .windowBackgroundColor), lineWidth: 1.5))
                            .offset(x: 3, y: -3)
                    }
                }
                .help(providerName(row.provider))
                .padding(.top, 1)
            VStack(alignment: .leading, spacing: 5) {
                Text(row.title)
                    .font(.body.weight(row.unread ? .semibold : .medium))
                    .lineLimit(2)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .help(row.title)
                HStack(spacing: 5) {
                    Text(stateName(row.state))
                        .foregroundStyle(row.unread ? AnyShapeStyle(Color.attention) : AnyShapeStyle(.secondary))
                        .fixedSize()
                    if !row.project.isEmpty {
                        Text("·").foregroundStyle(.tertiary)
                        Text((row.project as NSString).lastPathComponent)
                            .foregroundStyle(.secondary)
                            .lineLimit(1).help(row.project)
                    }
                }.font(.subheadline)
                // 会话时长只在未处理期间展示：从最近一次通知起实时累计（随刷新周期）；
                // 已处理即不再展示，来源侧清未读（如 Interrupt）同样不展示。
                if row.unread, let start = row.attentionAt, start > 0 {
                    Text(inboxDurationText(from: start, to: Date().timeIntervalSince1970))
                        .font(.footnote).foregroundStyle(.secondary)
                        .help("会话时长：从最近一次通知起累计")
                }
            }
            HStack(spacing: 4) {
                if row.unread {
                    Button { model.acknowledge(row) } label: {
                        Image(systemName: "checkmark.circle").frame(width: 28, height: 30)
                    }
                    .help("标记已读").accessibilityLabel("标记已读")
                }
                Button { model.open(row) } label: {
                    Image(systemName: "arrow.up.forward.app").frame(width: 28, height: 30)
                }
                .help("前往会话").accessibilityLabel("前往会话")
                .disabled(!row.openAvailable || model.opening)
            }
            .font(.system(size: 15)).foregroundStyle(.secondary)
            .buttonStyle(InboxActionButtonStyle())
        }
        .padding(.horizontal, 4).padding(.vertical, 12)
        .background(row.unread ? Color.attention.opacity(0.045) : Color.clear, in: RoundedRectangle(cornerRadius: 8))
        .onAppear { model.loadMoreIfNeeded(for: row) }
    }
}

struct TrayMenu: View {
    @ObservedObject var model: InboxModel
    @Environment(\.openWindow) var openWindow
    var body: some View {
        Button("查看会话通知（\(model.unreadCount) 条待查看）") {
            openWindow(id: "inbox")
            NSApplication.shared.activate()
        }
        Button("查看工作日报") {
            openWindow(id: "dailyReport")
            NSApplication.shared.activate()
        }
        Button("刷新") { model.refresh() }
        Divider()
        Button("退出") { NSApplication.shared.terminate(nil) }
    }
}

struct TrayIcon: View {
    @ObservedObject var model: InboxModel
    @Environment(\.openWindow) private var openWindow
    @Environment(\.colorScheme) private var colorScheme
    var body: some View {
        // 徽标必须显式配色：模板渲染会把橙底白字拍平成单色，数字不可读；
        // 图标本体跟随菜单栏明暗手动着色。padding 与 offset 配合保证徽标在状态项边界内。
        Image(systemName: model.unreadCount > 0 ? "tray.fill" : "tray")
            .foregroundStyle(colorScheme == .dark ? Color.white : Color.black)
            .overlay(alignment: .topTrailing) {
                if model.unreadCount > 0 {
                    Text(model.unreadCount > 99 ? "99+" : String(model.unreadCount))
                        .font(.system(size: 8, weight: .bold))
                        .foregroundStyle(.white)
                        .padding(.horizontal, 3)
                        .frame(minWidth: 12, minHeight: 12)
                        .background(Circle().fill(Color.attention))
                        .offset(x: 5, y: -2)
                }
            }
            .padding(.top, 2)
            .padding(.trailing, 5)
            .onReceive(NotificationCenter.default.publisher(for: .reopenInbox)) { _ in
                openWindow(id: "inbox")
                NSApplication.shared.activate()
            }
    }
}

@main struct SessionInboxApp: App {
    @NSApplicationDelegateAdaptor(InboxAppDelegate.self) var appDelegate
    @StateObject private var model = InboxModel()
    @StateObject private var reportModel = DailyReportModel()
    var body: some Scene {
        // Window（而非 WindowGroup）：收件箱只允许一个实例，openWindow 聚焦已有窗口；
        // WindowGroup 的 openWindow 每次调用都会新建窗口。
        Window("会话通知", id: "inbox") { InboxView(model: model) }
            .defaultSize(width: 400, height: 620)
            .windowResizability(.contentMinSize)
            .windowToolbarStyle(.unifiedCompact(showsTitle: false))
        Window("日报", id: "dailyReport") { DailyReportView(model: reportModel) }
            .defaultSize(width: 600, height: 780)
            .windowResizability(.contentMinSize)
        MenuBarExtra {
            TrayMenu(model: model)
        } label: {
            TrayIcon(model: model)
        }
    }
}
