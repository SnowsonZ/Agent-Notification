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
            let isDailyReport = (response.notification.request.content.userInfo["kind"] as? String) == "daily_report"
            Task { @MainActor in
                NSApplication.shared.activate(ignoringOtherApps: true)
                if isDailyReport {
                    // 日报通知点击只进日报窗口，不打扰收件箱。
                    if let window = NSApplication.shared.windows.first(where: { $0.identifier?.rawValue == "dailyReport" && $0.isVisible }) {
                        window.makeKeyAndOrderFront(nil)
                    } else {
                        NotificationCenter.default.post(name: .reopenDailyReport, object: nil)
                    }
                } else if let window = NSApplication.shared.windows.first(where: { $0.canBecomeMain && $0.isVisible }) {
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
    static let reopenDailyReport = Notification.Name("SessionInboxReopenDailyReport")
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
    // 橙底白字与托盘徽标、行内未读点同一视觉语言；白描边与浅色瓦片分隔。
    NSColor(red: 1, green: 0.54, blue: 0.10, alpha: 1).setFill()
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
    var id: String { provider + "/" + sessionId }
}
struct DayReport: Decodable {
    let date: String
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
    static let reportVersion = 2
    private var generating = false
    private var timer: Timer?
    init(root: String? = nil) {
        self.root = root ?? Bundle.main.object(forInfoDictionaryKey: "SessionManagerRoot") as? String ?? ""
        // 20:00 触发用 60 秒粒度判定即可；App 晚于 20:00 启动时首查即补跑。
        let scheduler = Timer(timeInterval: 60, repeats: true) { [weak self] _ in
            guard let self else { return }
            Task { @MainActor in self.tick() }
        }
        RunLoop.main.add(scheduler, forMode: .common)
        timer = scheduler
        tick()
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
    func tick(now: Date = Date()) {
        guard !generating else { return }
        let stored = UserDefaults.standard.string(forKey: "dailyReportLastGeneratedDay")
        guard shouldGenerateDailyReport(now: now, lastGeneratedStamp: stored, version: Self.reportVersion) else { return }
        generating = true
        Task {
            // 生成失败静默等下个 tick 重试；成功才记键、通知并刷新总览。
            let result = await run(["daily-report"])
            generating = false
            guard result.0 == 0 else { return }
            UserDefaults.standard.set(
                "\(dailyReportDayKey(Date()))#\(Self.reportVersion)", forKey: "dailyReportLastGeneratedDay")
            notifyGenerated(result.1)
            loadOverview()
        }
    }
    private func notifyGenerated(_ data: Data) {
        var body = "今天的工作日报已生成"
        if let report = try? Self.decode(DayReport.self, from: data) {
            body = "今日 token 合计 \(tokenText(report.totals.totalTokens)) · \(report.totals.tasks) 个任务"
        }
        let content = UNMutableNotificationContent()
        content.title = "日报已生成"
        content.body = body
        content.sound = .default
        content.userInfo = ["kind": "daily_report", "date": dailyReportDayKey(Date())]
        UNUserNotificationCenter.current().add(
            UNNotificationRequest(identifier: "daily-report-" + dailyReportDayKey(Date()), content: content, trigger: nil))
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
func dayFraction(_ stamp: Double) -> Double {
    let components = Calendar.current.dateComponents([.hour, .minute], from: Date(timeIntervalSince1970: stamp))
    return Double(components.hour ?? 0) + Double(components.minute ?? 0) / 60
}
func rhythmRange(_ tasks: [ReportTask]) -> (Double, Double) {
    var low = 24.0, high = 0.0
    for task in tasks where task.firstAt > 0 && task.lastAt > task.firstAt {
        low = min(low, dayFraction(task.firstAt))
        high = max(high, dayFraction(task.lastAt))
    }
    guard high > low else { return (8, 24) }
    let start = max(0, (floor(low / 2) * 2))
    let end = min(24, max(start + 2, ceil(high / 2) * 2))
    return (start, end)
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
                        Text("数据来自本地会话元数据 · 每天 20:00 自动生成").font(.subheadline).foregroundStyle(.secondary)
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
                            TodayChipsView(today: today)
                        }
                        HeatmapView(overview: overview)
                        if let today = overview.today, let current = reportDate(today.date) {
                            WeekTrendView(days: trendDays(overview, endingAt: current))
                        }
                        HStack(alignment: .top, spacing: 14) {
                            ProviderShareCard(sources: overview.weekSources)
                            TopProjectsCard(projects: overview.topProjects)
                        }
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
        VStack(alignment: .leading, spacing: 8) {
            if columns.isEmpty {
                Text("还没有可统计的数据").font(.subheadline).foregroundStyle(.secondary)
            } else {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(alignment: .top, spacing: 4) {
                        VStack(alignment: .leading, spacing: 3) {
                            Text("").frame(height: 15)
                            ForEach(["一", "二", "三", "四", "五", "六", "日"], id: \.self) { label in
                                Text(label).font(.system(size: 8)).foregroundStyle(.tertiary)
                                    .frame(width: 13, height: 13, alignment: .trailing)
                            }
                        }
                        VStack(alignment: .leading, spacing: 3) {
                            HStack(spacing: 3) {
                                ForEach(marks.indices, id: \.self) { index in
                                    Text(marks[index] ?? "")
                                        .font(.system(size: 8)).foregroundStyle(.tertiary)
                                        .frame(width: 13, alignment: .leading)
                                }
                            }
                            HStack(alignment: .top, spacing: 3) {
                                ForEach(columns.indices, id: \.self) { column in
                                    VStack(spacing: 3) {
                                        ForEach(columns[column].indices, id: \.self) { row in
                                            if let day = columns[column][row] {
                                                NavigationLink(value: day.date) {
                                                    RoundedRectangle(cornerRadius: 3)
                                                        .fill(heatColor(day.level))
                                                        .frame(width: 13, height: 13)
                                                        .overlay {
                                                            if day.date == todayKey {
                                                                RoundedRectangle(cornerRadius: 3)
                                                                    .strokeBorder(Color.primary.opacity(0.5), lineWidth: 1)
                                                            }
                                                        }
                                                }
                                                .buttonStyle(.plain)
                                                .dailyHoverTip(title: reportDayDisplay(day.date), lines: [])
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
    }
}

enum TokenClass { case input, cache, output }
func tokenClassColor(_ cls: TokenClass) -> Color {
    switch cls {
    case .input: return Color.accentColor.opacity(0.95)
    case .cache: return Color.accentColor.opacity(0.35)
    case .output: return Color.green.opacity(0.9)
    }
}
// 日报自定义悬浮：多行卡片，鼠标进入即显（不走系统 tooltip 的长延迟通道）。
// 黑色 80% 不透明底、白色文字（用户指定），尺寸随内容自适应、文本不折行。
struct HoverTipCard: View {
    let title: String
    let lines: [String]
    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(Color.white)
                .lineLimit(1)
            ForEach(lines.indices, id: \.self) { index in
                Text(lines[index])
                    .font(.system(size: 11))
                    .foregroundStyle(Color.white.opacity(0.85))
                    .lineLimit(1)
            }
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .background(Color.black.opacity(0.8), in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(Color.white.opacity(0.12)))
        .shadow(color: .black.opacity(0.25), radius: 8, y: 3)
        .fixedSize()
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
            .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Color.primary.opacity(0.05)))
    }
}
extension View {
    func dailyReportCard() -> some View { modifier(DailyReportCardBackground()) }
}

struct TodayChipsView: View {
    let today: TodaySummary
    var body: some View {
        HStack(alignment: .center, spacing: 16) {
            VStack(alignment: .leading, spacing: 2) {
                Text(tokenText(today.totalTokens))
                    .font(.system(size: 26, weight: .bold)).monospacedDigit()
                Text("今日 token 合计").font(.caption).foregroundStyle(.secondary)
                Text(usageLine(input: today.inputTokens, cache: today.cacheTokens, output: today.outputTokens))
                    .font(.caption2).foregroundStyle(.tertiary)
            }
            Spacer()
            statBlock("\(today.tasks)", "任务")
            statBlock("\(today.turns)", "轮次")
            statBlock("\(today.sources)", "活跃来源")
        }
        .dailyReportCard()
    }
    private func statBlock(_ value: String, _ label: String) -> some View {
        VStack(alignment: .trailing, spacing: 2) {
            Text(value).font(.system(size: 17, weight: .semibold)).monospacedDigit()
            Text(label).font(.caption).foregroundStyle(.secondary)
        }
    }
}

struct WeekTrendView: View {
    let days: [OverviewDay]
    var body: some View {
        let longest = max(days.map(\.totalTokens).max() ?? 1, 1)
        let todayKey = dailyReportDayKey(Date())
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                Text("最近 7 天 · token 合计").font(.headline)
                Spacer()
                ForEach([(tokenClassColor(.input), "输入"), (tokenClassColor(.cache), "缓存"),
                         (tokenClassColor(.output), "输出")], id: \.1) { color, label in
                    HStack(spacing: 3) {
                        Circle().fill(color).frame(width: 6, height: 6)
                        Text(label).font(.system(size: 9)).foregroundStyle(.secondary)
                    }
                }
            }
            HStack(alignment: .bottom, spacing: 10) {
                ForEach(days) { day in
                    VStack(spacing: 3) {
                        Text(tokenText(day.totalTokens))
                            .font(.system(size: 8)).foregroundStyle(.secondary).lineLimit(1)
                        stackedBar(day, longest: longest)
                            .frame(maxWidth: .infinity)
                            .overlay {
                                if day.date == todayKey {
                                    RoundedRectangle(cornerRadius: 3)
                                        .strokeBorder(Color.accentColor, lineWidth: 1.5)
                                }
                            }
                        Text(reportShortDay(day.date))
                            .font(.system(size: 8)).foregroundStyle(.tertiary)
                    }
                    .dailyHoverTip(title: reportDayDisplay(day.date),
                                   lines: classTipLines(input: day.inputTokens,
                                                        cache: day.cacheTokens,
                                                        output: day.outputTokens)
                                       + ["合计：\(tokenText(day.totalTokens))",
                                          "\(day.tasks) 个任务"])
                }
            }
            .frame(height: 84, alignment: .bottom)
        }
        .dailyReportCard()
    }
    // 三类层叠：自下而上 输入/缓存/输出，高度按合计相对最长日。
    private func stackedBar(_ day: OverviewDay, longest: Int) -> some View {
        let total = max(day.totalTokens, 1)
        let barHeight = max(3, CGFloat(day.totalTokens) / CGFloat(longest) * 52)
        let segments: [(value: Int, color: Color)] = [
            (day.inputTokens, tokenClassColor(.input)),
            (day.cacheTokens, tokenClassColor(.cache)),
            (day.outputTokens, tokenClassColor(.output)),
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

struct ProviderShareCard: View {
    let sources: [String: SourceUsage]
    var body: some View {
        let entries: [(key: String, usage: SourceUsage)] =
            sources.map { (key: $0.key, usage: $0.value) }
                .sorted { $0.usage.totalTokens > $1.usage.totalTokens }
                .filter { $0.usage.totalTokens > 0 }
        let total = max(entries.reduce(0) { $0 + $1.usage.totalTokens }, 1)
        VStack(alignment: .leading, spacing: 8) {
            Text("来源占比 · 近 7 天").font(.headline)
            if entries.isEmpty {
                Text("近 7 天没有可统计数据").font(.subheadline).foregroundStyle(.secondary)
            } else {
                GeometryReader { proxy in
                    HStack(spacing: 1) {
                        ForEach(Array(entries.enumerated()), id: \.offset) { _, entry in
                            RoundedRectangle(cornerRadius: 2)
                                .fill(providerReportColor(entry.key))
                                .frame(width: max(2, proxy.size.width * Double(entry.usage.totalTokens) / Double(total)))
                                .dailyHoverTip(title: providerName(entry.key),
                                               lines: classTipLines(entry.usage)
                                                   + ["合计：\(tokenText(entry.usage.totalTokens))"])
                        }
                    }
                }.frame(height: 10)
                VStack(alignment: .leading, spacing: 5) {
                    ForEach(entries, id: \.key) { entry in
                        VStack(alignment: .leading, spacing: 1) {
                            HStack(spacing: 4) {
                                Circle().fill(providerReportColor(entry.key)).frame(width: 7, height: 7)
                                Text(providerName(entry.key)).font(.system(size: 11))
                                Spacer()
                                Text(tokenText(entry.usage.totalTokens)).font(.system(size: 11)).monospacedDigit()
                                Text("\(Int((Double(entry.usage.totalTokens) / Double(total) * 100).rounded()))%")
                                    .font(.system(size: 10)).foregroundStyle(.tertiary)
                                    .frame(width: 32, alignment: .trailing)
                            }
                            Text(usageLine(entry.usage))
                                .font(.system(size: 9)).foregroundStyle(.tertiary)
                        }
                        .dailyHoverTip(title: providerName(entry.key),
                                       lines: classTipLines(entry.usage)
                                           + ["合计：\(tokenText(entry.usage.totalTokens))"])
                    }
                }
            }
        }
        .dailyReportCard()
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct TopProjectsCard: View {
    let projects: [TopProject]
    var body: some View {
        let longest = max(projects.first?.totalTokens ?? 1, 1)
        VStack(alignment: .leading, spacing: 8) {
            Text("Top 5 活跃项目 · 近 7 天").font(.headline)
            if projects.isEmpty {
                Text("近 7 天没有可统计的项目").font(.subheadline).foregroundStyle(.secondary)
            } else {
                ForEach(projects) { project in
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: 8) {
                            Text(project.name)
                                .font(.system(size: 12)).lineLimit(1)
                                .help(project.project)
                            Spacer()
                            Text(tokenText(project.totalTokens))
                                .font(.system(size: 11, weight: .medium)).monospacedDigit()
                            Text("\(Int((project.share * 100).rounded()))%")
                                .font(.system(size: 10)).foregroundStyle(.tertiary)
                                .frame(width: 32, alignment: .trailing)
                        }
                        Text(usageLine(input: project.inputTokens, cache: project.cacheTokens, output: project.outputTokens))
                            .font(.system(size: 9)).foregroundStyle(.tertiary)
                        GeometryReader { proxy in
                            ZStack(alignment: .leading) {
                                Capsule().fill(Color.primary.opacity(0.06))
                                Capsule().fill(Color.accentColor.opacity(0.8))
                                    .frame(width: max(4, proxy.size.width * Double(project.totalTokens) / Double(longest)))
                            }
                        }.frame(height: 5)
                    }
                    .dailyHoverTip(title: project.name,
                                   lines: classTipLines(input: project.inputTokens,
                                                        cache: project.cacheTokens,
                                                        output: project.outputTokens)
                                       + ["合计：\(tokenText(project.totalTokens))"])
                }
            }
        }
        .dailyReportCard()
        .frame(maxWidth: .infinity, alignment: .leading)
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
                        SummaryCardView(report: report, overview: model.overview)
                        RhythmBandView(tasks: report.tasks)
                        ProviderShareView(report: report)
                        ProjectBarsView(report: report)
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
    let overview: ReportOverview?
    var body: some View {
        HStack(alignment: .center, spacing: 18) {
            ZStack {
                Circle().stroke(Color.primary.opacity(0.07), lineWidth: 11)
                ringSegments
            }
            .frame(width: 92, height: 92)
            VStack(alignment: .leading, spacing: 4) {
                Text(tokenText(report.totals.totalTokens))
                    .font(.system(size: 26, weight: .bold)).monospacedDigit()
                Text("token 合计 · \(report.totals.tasks) 个任务 · \(report.totals.turns) 轮 · \(activeSourceCount) 来源")
                    .font(.subheadline).foregroundStyle(.secondary)
                Text(usageLine(input: report.totals.inputTokens, cache: report.totals.cacheTokens,
                               output: report.totals.outputTokens))
                    .font(.caption).foregroundStyle(.tertiary)
            }
            Spacer()
            if let week = weekBars {
                VStack(alignment: .trailing, spacing: 4) {
                    Text("最近 7 天").font(.caption2).foregroundStyle(.tertiary)
                    HStack(alignment: .bottom, spacing: 3) {
                        ForEach(week) { day in
                            RoundedRectangle(cornerRadius: 2)
                                .fill(day.date == report.date ? Color.accentColor : heatColor(day.level))
                                .frame(width: 9, height: max(3, CGFloat(day.totalTokens) / maxWeek * 30))
                        }
                    }
                }
            }
        }
        .dailyReportCard()
    }
    private var activeSourceCount: Int {
        report.totals.sources.values.filter { $0.totalTokens > 0 }.count
    }
    private var ringSegments: some View {
        let entries = report.totals.sources.sorted { $0.value.totalTokens > $1.value.totalTokens }
        let total = max(report.totals.totalTokens, 1)
        var cursor = 0.0
        var segments: [(color: Color, start: Double, end: Double)] = []
        for entry in entries {
            let start = cursor
            cursor = min(cursor + Double(entry.value.totalTokens) / Double(total), 1)
            segments.append((providerReportColor(entry.key), start, cursor))
        }
        return ForEach(Array(segments.enumerated()), id: \.offset) { _, segment in
            Circle()
                .trim(from: segment.start, to: segment.end)
                .stroke(segment.color, style: StrokeStyle(lineWidth: 11, lineCap: .butt))
        }
    }
    private var weekBars: [OverviewDay]? {
        guard let overview, let current = reportDate(report.date) else { return nil }
        let prior = overview.days.filter { day in
            guard let date = reportDate(day.date) else { return false }
            return date <= current
        }
        return prior.isEmpty ? nil : Array(prior.suffix(7))
    }
    private var maxWeek: Double {
        Double(weekBars?.map(\.totalTokens).max() ?? 1)
    }
}

struct RhythmBandView: View {
    let tasks: [ReportTask]
    var body: some View {
        let range = rhythmRange(tasks)
        VStack(alignment: .leading, spacing: 5) {
            Text("一天节奏 · \(Int(range.0))–\(Int(range.1)) 时").font(.headline)
            GeometryReader { proxy in
                let width = proxy.size.width
                ZStack(alignment: .topLeading) {
                    RoundedRectangle(cornerRadius: 6).fill(Color.primary.opacity(0.045))
                    ForEach(tasks) { task in
                        let first = max(task.firstAt, 0)
                        let last = max(task.lastAt, first)
                        let left = (dayFraction(first) - range.0) / (range.1 - range.0)
                        let span = (dayFraction(last) - dayFraction(first)) / (range.1 - range.0)
                        RoundedRectangle(cornerRadius: 2)
                            .fill(providerReportColor(task.provider).opacity(0.85))
                            .frame(width: max(3, span * width), height: proxy.size.height - 8)
                            .offset(x: max(0, left * width), y: 4)
                            .dailyHoverTip(title: task.title,
                                           lines: ["\(providerName(task.provider)) · \(reportTimeRange(task)) · \(task.turns) 轮"]
                                               + classTipLines(input: task.inputTokens, cache: task.cacheTokens,
                                                               output: task.outputTokens)
                                               + ["合计：\(tokenText(task.totalTokens))"])
                    }
                }
            }
            .frame(height: 26)
            .help("按任务首末时间的来源着色节奏带")
            GeometryReader { proxy in
                let width = proxy.size.width
                ZStack(alignment: .topLeading) {
                    ForEach(Array(stride(from: range.0, through: range.1, by: 2)), id: \.self) { hour in
                        Text(String(format: "%02d", Int(hour)))
                            .font(.system(size: 8)).foregroundStyle(.tertiary)
                            .position(x: min(max((hour - range.0) / (range.1 - range.0) * width, 8), width - 8), y: 6)
                    }
                }
            }
            .frame(height: 12)
            legend
        }
    }
    private var legend: some View {
        let providers = Array(Set(tasks.map(\.provider))).sorted()
        return HStack(spacing: 10) {
            ForEach(providers, id: \.self) { provider in
                HStack(spacing: 3) {
                    Circle().fill(providerReportColor(provider)).frame(width: 7, height: 7)
                    Text(providerName(provider)).font(.system(size: 9)).foregroundStyle(.secondary)
                }
            }
        }
    }
}

struct ProviderShareView: View {
    let report: DayReport
    var body: some View {
        let entries: [(key: String, usage: SourceUsage)] =
            report.totals.sources.map { (key: $0.key, usage: $0.value) }
                .sorted { $0.usage.totalTokens > $1.usage.totalTokens }
                .filter { $0.usage.totalTokens > 0 }
        let total = max(report.totals.totalTokens, 1)
        let unavailable = Set(report.tasks.filter { $0.fidelity == "unavailable" }.map(\.provider))
            .subtracting(entries.map(\.key))
        VStack(alignment: .leading, spacing: 8) {
            Text("来源占比").font(.headline)
            GeometryReader { proxy in
                HStack(spacing: 1) {
                    ForEach(Array(entries.enumerated()), id: \.offset) { _, entry in
                        RoundedRectangle(cornerRadius: 2)
                            .fill(providerReportColor(entry.key))
                            .frame(width: max(2, proxy.size.width * Double(entry.usage.totalTokens) / Double(total)))
                            .dailyHoverTip(title: providerName(entry.key),
                                           lines: classTipLines(entry.usage)
                                               + ["合计：\(tokenText(entry.usage.totalTokens))"])
                    }
                }
            }.frame(height: 10)
            VStack(alignment: .leading, spacing: 5) {
                ForEach(entries, id: \.key) { entry in
                    VStack(alignment: .leading, spacing: 1) {
                        HStack(spacing: 4) {
                            Circle().fill(providerReportColor(entry.key)).frame(width: 7, height: 7)
                            Text(providerName(entry.key)).font(.system(size: 11))
                            Spacer()
                            Text(tokenText(entry.usage.totalTokens)).font(.system(size: 11)).monospacedDigit()
                            Text("\(Int((Double(entry.usage.totalTokens) / Double(total) * 100).rounded()))%")
                                .font(.system(size: 10)).foregroundStyle(.tertiary)
                                .frame(width: 32, alignment: .trailing)
                        }
                        Text(usageLine(entry.usage))
                            .font(.system(size: 9)).foregroundStyle(.tertiary)
                    }
                    .dailyHoverTip(title: providerName(entry.key),
                                   lines: classTipLines(entry.usage)
                                       + ["合计：\(tokenText(entry.usage.totalTokens))"])
                }
            }
            if !unavailable.isEmpty {
                Text(unavailable.map { providerName($0) }.joined(separator: "、") + " 无 token 统计，不计入")
                    .font(.system(size: 10)).foregroundStyle(.tertiary)
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
        if !entries.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                Text("项目 · \(entries.count)").font(.headline)
                ForEach(entries.prefix(6), id: \.key) { entry in
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: 8) {
                            Text((entry.key as NSString).lastPathComponent)
                                .font(.system(size: 12)).lineLimit(1)
                                .help(entry.key)
                            Spacer()
                            Text(tokenText(entry.usage.totalTokens))
                                .font(.system(size: 11, weight: .medium)).monospacedDigit()
                        }
                        GeometryReader { proxy in
                            ZStack(alignment: .leading) {
                                Capsule().fill(Color.primary.opacity(0.06))
                                Capsule().fill(Color.accentColor.opacity(0.65))
                                    .frame(width: max(4, proxy.size.width * Double(entry.usage.totalTokens) / Double(longest)))
                            }
                        }.frame(height: 6)
                        Text(usageLine(entry.usage))
                            .font(.system(size: 9)).foregroundStyle(.tertiary)
                    }
                    .dailyHoverTip(title: (entry.key as NSString).lastPathComponent,
                                   lines: classTipLines(entry.usage)
                                       + ["合计：\(tokenText(entry.usage.totalTokens))"])
                }
            }
        }
    }
}

struct TaskListView: View {
    let report: DayReport
    var body: some View {
        let longest = max(report.tasks.map(\.totalTokens).max() ?? 1, 1)
        VStack(alignment: .leading, spacing: 8) {
            Text("任务 · \(report.tasks.count)").font(.headline)
            ForEach(report.tasks) { task in
                VStack(alignment: .leading, spacing: 6) {
                    HStack(spacing: 6) {
                        Circle().fill(providerReportColor(task.provider)).frame(width: 8, height: 8)
                        Text(task.title)
                            .font(.system(size: 13, weight: .medium)).lineLimit(1)
                            .help(task.title)
                        Spacer()
                        Text(task.fidelity == "unavailable" ? "无 token" : tokenText(task.totalTokens))
                            .font(.system(size: 12, weight: .semibold)).monospacedDigit()
                            .foregroundStyle(task.fidelity == "unavailable" ? AnyShapeStyle(.secondary) : AnyShapeStyle(.primary))
                    }
                    HStack(spacing: 5) {
                        Text(providerName(task.provider)).foregroundStyle(.secondary)
                        if !task.project.isEmpty {
                            Text("·").foregroundStyle(.tertiary)
                            Text((task.project as NSString).lastPathComponent)
                                .foregroundStyle(.secondary).lineLimit(1).help(task.project)
                        }
                        if task.turns > 0 {
                            Text("·").foregroundStyle(.tertiary)
                            Text("\(task.turns) 轮").foregroundStyle(.secondary)
                        }
                        Text("·").foregroundStyle(.tertiary)
                        Text(reportTimeRange(task)).foregroundStyle(.secondary)
                    }.font(.system(size: 11))
                    GeometryReader { proxy in
                        ZStack(alignment: .leading) {
                            Capsule().fill(Color.primary.opacity(0.05))
                            Capsule().fill(providerReportColor(task.provider).opacity(0.8))
                                .frame(width: max(3, proxy.size.width * Double(task.totalTokens) / Double(longest)))
                        }
                    }.frame(height: 5)
                    HStack(spacing: 5) {
                        Circle().fill(stateDotColor(task.state)).frame(width: 7, height: 7)
                        Text(stateName(task.state)).foregroundStyle(.secondary)
                        Text("· " + usageLine(task)).foregroundStyle(.tertiary)
                            .lineLimit(1)
                        Spacer()
                    }.font(.system(size: 10))
                }
                .padding(11)
                .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 10))
                .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(Color.primary.opacity(0.05)))
                .dailyHoverTip(title: task.title,
                               lines: ["\(providerName(task.provider)) · \(task.turns) 轮 · \(reportTimeRange(task))"]
                                   + classTipLines(input: task.inputTokens, cache: task.cacheTokens,
                                                   output: task.outputTokens)
                                   + ["合计：\(tokenText(task.totalTokens))", stateName(task.state)])
            }
        }
    }
}


@MainActor final class InboxModel: ObservableObject {
    @Published var rows: [InboxRow] = []
    @Published var showAll = false { didSet { page = 0 } }
    @Published var query = "" { didSet { page = 0 } }
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
    var unreadCount: Int { rows.filter(\.unread).count }
    var filtered: [InboxRow] {
        rows.filter { row in
            // 已退出且原会话入口不可用的行没有可执行的后续，直接不展示。
            guard !(row.state == "closed" && !row.openAvailable) else { return false }
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
                    notificationStatus = granted ? "通知已开启" : "请在系统设置 → 通知中允许 Agent Notification"
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
        process.executableURL = URL(fileURLWithPath: root + "/scratch/iterm-probe-venv/bin/python")
        process.arguments = [root + "/scripts/inbox.py"] + arguments
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
    // 缺失时画品牌色字符兜底。
    let glyphs = ["pi": ("π", NSColor(srgbRed: 0.42, green: 0.48, blue: 0.55, alpha: 1)),
                  "kimi": ("K", NSColor(srgbRed: 0.30, green: 0.43, blue: 0.96, alpha: 1)),
                  "codex": (">_", NSColor(srgbRed: 0.35, green: 0.45, blue: 0.95, alpha: 1)),
                  "zcode": ("Z", NSColor(srgbRed: 0.22, green: 0.25, blue: 0.30, alpha: 1)),
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

        let title = NSTextField(labelWithString: "Agent Notification")
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
                .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 10))
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
                .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 10))
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
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Agent Notification").font(.system(size: 24, weight: .bold))
                    Text(model.unreadCount == 0 ? "暂无新通知" : "\(model.unreadCount) 条会话有新动态")
                        .font(.subheadline).foregroundStyle(.secondary)
                }
                Spacer()
                Button {
                    openWindow(id: "dailyReport")
                    NSApplication.shared.activate(ignoringOtherApps: true)
                } label: {
                    Image(systemName: "chart.bar.doc.horizontal").foregroundStyle(.secondary)
                }
                .buttonStyle(.plain)
                .help("工作日报")
                Button { model.toggleNotifications() } label: {
                    Image(systemName: model.notificationsEnabled && model.notificationsAllowed ? "bell.badge.fill" : "bell.slash")
                        .foregroundStyle(.secondary)
                }
                .buttonStyle(.plain)
                .help(model.notificationsEnabled ? model.notificationStatus + "（点击关闭）" : "点击开启消息通知")
                Button { model.refresh() } label: {
                    Image(systemName: "arrow.clockwise").foregroundStyle(.secondary)
                }
                .buttonStyle(.plain)
                .help("刷新").disabled(model.loading)
            }
            if !model.agents.isEmpty {
                NewSessionLauncherRow(model: model)
            }
            VStack(spacing: 10) {
                Picker("显示范围", selection: $model.showAll) {
                    Text("待查看（\(model.unreadCount)）").tag(false)
                    Text("全部会话（\(model.rows.count)）").tag(true)
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                HStack(spacing: 6) {
                    Image(systemName: "magnifyingglass").font(.caption).foregroundStyle(.secondary)
                    TextField("搜索会话或项目", text: $model.query)
                        .textFieldStyle(.plain)
                }
                .padding(.horizontal, 10).padding(.vertical, 8)
                .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 7))
                .overlay(RoundedRectangle(cornerRadius: 7).strokeBorder(Color.primary.opacity(0.06)))
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
        .onAppear { model.refresh() }
    }
}

struct InboxActionButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .foregroundStyle(isEnabled ? Color.primary.opacity(0.75) : Color.secondary.opacity(0.4))
            .background {
                RoundedRectangle(cornerRadius: 7)
                    .fill(Color.primary.opacity(isEnabled && configuration.isPressed ? 0.18 : 0.035))
            }
            .contentShape(RoundedRectangle(cornerRadius: 7))
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
                            .fill(Color.orange)
                            .frame(width: 9, height: 9)
                            .overlay(Circle().strokeBorder(Color(nsColor: .windowBackgroundColor), lineWidth: 1.5))
                            .offset(x: 3, y: -3)
                    }
                }
                .help(providerName(row.provider))
                .padding(.top, 1)
            VStack(alignment: .leading, spacing: 5) {
                Text(row.title)
                    .font(.system(size: 13, weight: row.unread ? .semibold : .medium))
                    .lineLimit(2)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .help(row.title)
                HStack(spacing: 5) {
                    Text(stateName(row.state))
                        .foregroundStyle(row.unread ? AnyShapeStyle(.orange) : AnyShapeStyle(.secondary))
                        .fixedSize()
                    if !row.project.isEmpty {
                        Text("·").foregroundStyle(.tertiary)
                        Text((row.project as NSString).lastPathComponent)
                            .foregroundStyle(.secondary)
                            .lineLimit(1).help(row.project)
                    }
                }.font(.system(size: 11))
                // 会话时长只在未处理期间展示：从最近一次通知起实时累计（随刷新周期）；
                // 已处理即不再展示，来源侧清未读（如 Interrupt）同样不展示。
                if row.unread, let start = row.attentionAt, start > 0 {
                    Text(inboxDurationText(from: start, to: Date().timeIntervalSince1970))
                        .font(.system(size: 10)).foregroundStyle(.secondary)
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
        .background(row.unread ? Color.orange.opacity(0.045) : Color.clear, in: RoundedRectangle(cornerRadius: 8))
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
                        .background(Circle().fill(.orange))
                        .offset(x: 5, y: -2)
                }
            }
            .padding(.top, 2)
            .padding(.trailing, 5)
            .onReceive(NotificationCenter.default.publisher(for: .reopenInbox)) { _ in
                openWindow(id: "inbox")
                NSApplication.shared.activate()
            }
            .onReceive(NotificationCenter.default.publisher(for: .reopenDailyReport)) { _ in
                openWindow(id: "dailyReport")
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
        Window("Agent Notification", id: "inbox") { InboxView(model: model) }
            .defaultSize(width: 400, height: 620)
            .windowResizability(.contentMinSize)
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
