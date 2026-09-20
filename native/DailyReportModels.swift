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
