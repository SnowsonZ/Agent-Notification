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
        precondition([pendingRank("waiting"), pendingRank("failed"), pendingRank("running"), pendingRank("idle")] == [0, 1, 2, 2])
        // closed 且入口不可用的行（如 /clear 后被替换的受管理会话）不展示也不计入待查看。
        precondition(!inboxRowListed(state: "closed", openAvailable: false))
        precondition(inboxRowListed(state: "closed", openAvailable: true))
        precondition(inboxRowListed(state: "idle", openAvailable: false))
        precondition(inboxRowListed(state: "waiting", openAvailable: false))
        // 进行中分段只收「等待模型回复」的回合：waiting（等用户确认权限）、开着空闲、
        // 出错/中断/已结束都不算（2026-09-20 用户确认口径）。
        precondition(inboxActiveListed(state: "running", openAvailable: false))
        precondition(inboxActiveListed(state: "running", openAvailable: true))
        precondition(!inboxActiveListed(state: "waiting", openAvailable: true))
        precondition(!inboxActiveListed(state: "idle", openAvailable: true))
        precondition(!inboxActiveListed(state: "failed", openAvailable: true))
        precondition(!inboxActiveListed(state: "interrupted", openAvailable: true))
        precondition(!inboxActiveListed(state: "closed", openAvailable: true))
        // 刷新节拍（墙钟）：距上次全量发起 ≥15 秒才再扫，丢拍由下一个 tick 按经过时间自动补。
        precondition(!inboxTickShouldScan(elapsed: 0, fullEvery: 15))
        precondition(!inboxTickShouldScan(elapsed: 14.9, fullEvery: 15))
        precondition(inboxTickShouldScan(elapsed: 15, fullEvery: 15))
        precondition(inboxTickShouldScan(elapsed: 3600, fullEvery: 15))  // 唤醒/久置后立即补扫
        precondition(inboxTickShouldScan(elapsed: 1, fullEvery: 0))  // 非法参数退化为每 tick 都扫
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
