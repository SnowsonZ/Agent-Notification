import Foundation
import SwiftUI

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
    // v8：逐 model 用量与金额（金额为读取时派生值，§3/§5）
    let models: [String: ModelUsage]?
    let cost: WidgetSnapshotMoney?
}
struct ModelUsage: Decodable, Identifiable {
    let freshInput: Int
    let cacheWrite: Int
    let cacheRead: Int
    let output: Int
    let nativeCostUsd: Double?
    let rawNames: [String]?
    var id: String { rawNames?.first ?? freshInput.description }
    var totalTokens: Int { freshInput + cacheWrite + cacheRead + output }
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
    let models: [String: ModelUsage]?
    let cost: WidgetSnapshotMoney?
    var id: String { provider + "/" + sessionId }
}
struct AgentExcluded: Decodable {
    let tasks: Int
    let totalTokens: Int
}
struct DayReport: Decodable {
    let date: String
    let generatedAt: Double?
    let totals: ReportTotals
    let tasks: [ReportTask]
    let agentExcluded: AgentExcluded?  // agent 拉起的会话不进合计，只在此披露（2026-09-17）
}
struct OverviewDay: Decodable, Identifiable {
    let date: String
    let inputTokens: Int
    let cacheTokens: Int
    let outputTokens: Int
    let totalTokens: Int
    let tasks: Int
    let level: Int
    let cost: WidgetSnapshotMoney?
    let costLevel: Int?
    var id: String { date }

    enum CodingKeys: String, CodingKey {
        case date, inputTokens, cacheTokens, outputTokens, totalTokens, tasks, level, cost
        case costLevel = "cost_level"
    }
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
    let cost: WidgetSnapshotMoney?
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
    // 近 7 天 model 合计与金额（usage-cost.md §6 overview 新增 week_models）
    let weekModels: WeekModels?
    struct WeekModels: Decodable {
        let models: [String: WeekModelEntry]
        let cost: WidgetSnapshotMoney?
    }
    struct WeekModelEntry: Decodable {
        let freshInput: Int
        let cacheWrite: Int
        let cacheRead: Int
        let output: Int
        let nativeCostUsd: Double?
        let rawNames: [String]?
    }
}

// 日报周期与展示币种（usage-cost.md §7）：选择存 UserDefaults，币种默认 CNY。
enum ReportPeriod: String, CaseIterable {
    case day, week, month
}
@MainActor final class DailyReportModel: ObservableObject {
    @Published var overview: ReportOverview?
    @Published var selectedDate = ""
    @Published var selectedDay: DayReport?
    @Published var path: [String] = []
    @Published var loading = false
    @Published var error: String?
    // 组件 report URL 的周期切换请求（day/week/month）：日报视图挂载/收到时消费。
    @Published var pendingPeriod: String?
    // 周/月视图载荷：`inbox usage --period all --json`（含 day/week/month 三周期 + fx）
    @Published var usageByPeriod: [String: WidgetUsagePayload] = [:]
    @Published var usageError: String?
    @Published var period: ReportPeriod {
        didSet {
            UserDefaults.standard.set(period.rawValue, forKey: "reportPeriod")
            if oldValue != period { loadUsage(force: false) }
        }
    }
    // 热力图着色开关：token（默认）或按金额（§7）
    @Published var heatmapMetric: String {
        didSet { UserDefaults.standard.set(heatmapMetric, forKey: "reportHeatmapMetric") }
    }
    @Published var usageMetric: String {
        didSet { UserDefaults.standard.set(usageMetric, forKey: "reportUsageMetric") }
    }
    @Published var currency: String {
        didSet {
            UserDefaults.standard.set(currency, forKey: "reportCurrency")
            if oldValue != currency { WidgetSnapshotWriter.shared.refreshUsageNow() }
        }
    }
    // usage --period all 的锚点日期：切换周期导航后重拉（过去周期读缓存，快）。
    private var usageAnchor = ""
    private var usageLoading = false
    let root: String
    private var pricingTimer: Timer?
    init(root: String? = nil) {
        self.root = root ?? Bundle.main.object(forInfoDictionaryKey: "SessionManagerRoot") as? String ?? ""
        period = ReportPeriod(rawValue: UserDefaults.standard.string(forKey: "reportPeriod") ?? "") ?? .day
        currency = UserDefaults.standard.string(forKey: "reportCurrency") ?? "CNY"
        usageMetric = UserDefaults.standard.string(forKey: "reportUsageMetric") ?? "cost"
        heatmapMetric = UserDefaults.standard.string(forKey: "reportHeatmapMetric") ?? "tokens"
        // §4.5：App 启动时与每 6 小时后台执行 pricing update --auto，
        // 是否请求由命令自行判断到期；不阻塞主 run loop。
        runPricingUpdateAuto()
        let tick = Timer(timeInterval: 6 * 3600, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.runPricingUpdateAuto() }
        }
        RunLoop.main.add(tick, forMode: .common)
        pricingTimer = tick
    }

    private func runPricingUpdateAuto() {
        let directory = root
        Task.detached {
            _ = InboxModel.call(root: directory, arguments: ["pricing", "update", "--auto"])
        }
    }

    /// 拉取周期聚合（--period all）；force 用于周期导航跨越后重拉。
    func loadUsage(force: Bool) {
        guard !usageLoading else { return }
        usageLoading = true
        usageError = nil
        let directory = root
        let anchor = usageAnchor
        var arguments = ["usage", "--period", "all", "--json"]
        if !anchor.isEmpty { arguments += ["--date", anchor] }
        Task {
            let result = await Task.detached { InboxModel.call(root: directory, arguments: arguments) }.value
            usageLoading = false
            guard result.0 == 0,
                  let payload = try? Self.decode([String: WidgetUsagePayload].self, from: result.1)
            else {
                usageError = Self.message(result)
                return
            }
            usageByPeriod = payload
        }
    }

    func setUsageAnchor(_ date: String) {
        usageAnchor = date
        loadUsage(force: true)
    }

    // 周期导航（§7.5）：delta -1=上一期、0=本期、1=下一期；下一期不超本期。
    func shiftPeriod(_ delta: Int) {
        let calendar = Calendar.current
        if delta == 0 {
            usageAnchor = ""
            loadUsage(force: true)
            return
        }
        let base: Date
        if usageAnchor.isEmpty {
            base = Date()
        } else {
            base = calendar.date(from: calendar.dateComponents([.year, .month, .day], from: Self.anchorFormatter.date(from: usageAnchor) ?? Date())) ?? Date()
        }
        let shifted: Date
        switch period {
        case .day: shifted = calendar.date(byAdding: .day, value: delta, to: base) ?? base
        case .week: shifted = calendar.date(byAdding: .day, value: delta * 7, to: base) ?? base
        case .month: shifted = calendar.date(byAdding: .month, value: delta, to: base) ?? base
        }
        if shifted > Date() {  // 下期不能超过本期
            usageAnchor = ""
        } else {
            usageAnchor = Self.anchorFormatter.string(from: shifted)
        }
        loadUsage(force: true)
    }

    var atCurrentPeriod: Bool { usageAnchor.isEmpty }

    /// 展示汇率：来自最近一次 usage 载荷；缺省 7.10（§5）。
    var fxRate: Double {
        usageByPeriod.values.first?.fx?.usdCny ?? 7.10
    }

    static let anchorFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter
    }()
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
