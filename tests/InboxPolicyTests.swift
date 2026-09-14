import Foundation

@main struct PolicyTests {
    static func main() {
        precondition(inboxPageCount(total: 41, size: 20) == 3)
        precondition(inboxPageRange(total: 41, page: 2, size: 20) == 40..<41)
        precondition(inboxPageRange(total: 0, page: 0, size: 20).isEmpty)
        precondition(inboxPageRange(total: 5, page: 2, size: 20).isEmpty)
        precondition(!needsNotification(unread: true, token: "a", seen: nil, initialSnapshot: true, enabled: true))
        precondition(!needsNotification(unread: true, token: "a", seen: "a", initialSnapshot: false, enabled: true))
        precondition(!needsNotification(unread: false, token: "b", seen: "a", initialSnapshot: false, enabled: true))
        precondition(!needsNotification(unread: true, token: "b", seen: "a", initialSnapshot: false, enabled: false))
        precondition(needsNotification(unread: true, token: "b", seen: "a", initialSnapshot: false, enabled: true))
        precondition(inboxDurationText(from: 0, to: 59) == "59秒")
        precondition(inboxDurationText(from: 100, to: 200) == "1分40秒")
        precondition(inboxDurationText(from: 0, to: 3700) == "1小时1分")
        precondition(inboxDurationText(from: 0, to: 90000) == "1天1小时")
        precondition(inboxDurationText(from: 200, to: 100) == "0秒")
        precondition(tokenText(0) == "0")
        precondition(tokenText(895) == "895")
        precondition(tokenText(6_594) == "6.6k")
        precondition(tokenText(456_700) == "457k")
        precondition(tokenText(613_613) == "614k")
        precondition(tokenText(1_000_000) == "1M")
        precondition(tokenText(55_703_404) == "55.7M")
        precondition(tokenText(143_168_263) == "143M")
        precondition(tokenText(553_010_996) == "553M")
        precondition(tokenText(1_000_000_000) == "1B")
        precondition(tokenText(1_230_000_000) == "1.23B")
        var clock = Calendar.current.date(from: DateComponents(year: 2026, month: 9, day: 14, hour: 19, minute: 59))!
        let todayKey = dailyReportDayKey(clock)
        precondition(!shouldGenerateDailyReport(now: clock, lastGeneratedStamp: nil, version: 2))
        clock = Calendar.current.date(byAdding: .minute, value: 1, to: clock)!
        precondition(shouldGenerateDailyReport(now: clock, lastGeneratedStamp: nil, version: 2))
        precondition(!shouldGenerateDailyReport(now: clock, lastGeneratedStamp: todayKey + "#2", version: 2))
        precondition(shouldGenerateDailyReport(now: clock, lastGeneratedStamp: todayKey + "#1", version: 2))
        let nextMorning = Calendar.current.date(byAdding: DateComponents(day: 1, hour: -11), to: clock)!
        precondition(!shouldGenerateDailyReport(now: nextMorning, lastGeneratedStamp: todayKey + "#2", version: 2))
        let nextEvening = Calendar.current.date(byAdding: DateComponents(day: 1, minute: 1), to: clock)!
        precondition(shouldGenerateDailyReport(now: nextEvening, lastGeneratedStamp: todayKey + "#2", version: 2))
        precondition(!shouldGenerateDailyReport(now: nextEvening, lastGeneratedStamp: dailyReportDayKey(nextEvening) + "#2", version: 2))
        precondition([0, 999_999, 1_000_000, 9_999_999, 10_000_000, 49_999_999, 50_000_000].map { heatLevel(tokens: $0) } == [0, 1, 2, 2, 3, 3, 4])
        precondition(heatLevel(tokens: -5) == 0)
        precondition(heatLevel(activeSeconds: 100) == 1)  // 兼容旧调用名
        print("Pagination, notification, daily report policy checks passed")
    }
}
