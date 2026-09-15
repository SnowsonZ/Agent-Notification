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
        // closed 且入口不可用的行（如 /clear 后被替换的受管理会话）不展示也不计入待查看。
        precondition(!inboxRowListed(state: "closed", openAvailable: false))
        precondition(inboxRowListed(state: "closed", openAvailable: true))
        precondition(inboxRowListed(state: "idle", openAvailable: false))
        precondition(inboxRowListed(state: "waiting", openAvailable: false))
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
        precondition(dailyReportDayKey(Calendar.current.date(from: DateComponents(year: 2026, month: 9, day: 14))!) == "2026-09-14")
        precondition([0, 999_999, 1_000_000, 9_999_999, 10_000_000, 49_999_999, 50_000_000].map { heatLevel(tokens: $0) } == [0, 1, 2, 2, 3, 3, 4])
        precondition(heatLevel(tokens: -5) == 0)
        precondition(heatLevel(activeSeconds: 100) == 1)  // 兼容旧调用名
        // 节奏带：跨零点末次时间（次日 00:00）算 24 而不是 0；范围取偶数刻度；无段回退 8–24。
        let dayStart = 1_000_000.0
        precondition(rhythmHour(dayStart + 24 * 3600, dayStart: dayStart) == 24)
        precondition(rhythmHour(dayStart - 3600, dayStart: dayStart) == 0)
        precondition(rhythmHour(dayStart + 9.5 * 3600, dayStart: dayStart) == 9.5)
        precondition(rhythmRange([[[dayStart + 9.5 * 3600, dayStart + 10 * 3600]],
                                  [[dayStart + 16 * 3600, dayStart + 24 * 3600]]], dayStart: dayStart) == (8, 24))
        precondition(rhythmRange([[[dayStart + 1 * 3600, dayStart + 1 * 3600]]], dayStart: dayStart) == (0, 2))
        precondition(rhythmRange([[]], dayStart: dayStart) == (8, 24))
        print("Pagination, notification, daily report policy checks passed")
    }
}
