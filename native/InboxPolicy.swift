import Foundation

func inboxPageCount(total: Int, size: Int) -> Int { max(1, (total + size - 1) / size) }
// closed 且入口不可用的行没有可执行的后续，列表不展示，待查看计数与列表同口径。
func inboxRowListed(state: String, openAvailable: Bool) -> Bool {
    state != "closed" || openAvailable
}
// 进行中分段：正在等待模型回复的回合（state==running）才算。waiting 等的是用户确认
// 权限、idle 是开着空闲，都不算；口径见 docs/specs/unified-inbox.md「进行中视图」。
func inboxActiveListed(state: String, openAvailable: Bool) -> Bool {
    inboxRowListed(state: state, openAvailable: openAvailable) && state == "running"
}
func inboxPageRange(total: Int, page: Int, size: Int) -> Range<Int> {
    let start = min(max(0, page) * size, total)
    return start..<min(start + size, total)
}
// 待查看排序优先级：等待输入 > 发生错误 > 其余。与 inbox_store.rows 的 SQL CASE 同规则
// （跨语言双实现，两测试同守）；App 拉 --all 全量后在客户端按此重排待查看视图。
func pendingRank(_ state: String) -> Int {
    switch state {
    case "waiting": return 0
    case "failed": return 1
    default: return 2
    }
}
func needsNotification(unread: Bool, token: String, seen: String?, initialSnapshot: Bool, enabled: Bool) -> Bool {
    enabled && !initialSnapshot && unread && !token.isEmpty && token != seen
}
func inboxDurationText(from: Double, to: Double) -> String {
    let span = max(0, Int(to - from))
    if span < 60 { return "\(span)秒" }
    if span < 3600 { return "\(span / 60)分\(span % 60)秒" }
    if span < 86400 { return "\(span / 3600)小时\(span % 3600 / 60)分" }
    return "\(span / 86400)天\(span % 86400 / 3600)小时"
}
// token 计数 k/M/B：<1k 原值、≥1k x.k、≥1M x.xM（<10M 两位小数）、≥1B x.xxB，末尾零去除。
func trimTrailingZeros(_ text: String) -> String {
    var result = text
    if result.contains(".") {
        while result.hasSuffix("0") { result.removeLast() }
        if result.hasSuffix(".") { result.removeLast() }
    }
    return result
}
func tokenText(_ value: Int) -> String {
    if value <= 0 { return "0" }
    if value < 1_000 { return String(value) }
    let units: [(factor: Double, symbol: String, decimals: Int)] =
        [(1_000_000_000, "B", 2), (1_000_000, "M", 1), (1_000, "k", 1)]
    for unit in units where Double(value) >= unit.factor {
        let mantissa = Double(value) / unit.factor
        let text = mantissa < 100
            ? trimTrailingZeros(String(format: "%.\(unit.decimals)f", mantissa))
            : String(format: "%.0f", mantissa)
        return text + unit.symbol
    }
    return String(value)
}
// 日报日期键「YYYY-MM-DD」：今日高亮与实时标注按它比对报告日期。
func dailyReportDayKey(_ date: Date, calendar: Calendar = .current) -> String {
    let components = calendar.dateComponents([.year, .month, .day], from: date)
    return String(format: "%04d-%02d-%02d", components.year ?? 0, components.month ?? 0, components.day ?? 0)
}
// 节奏带时间轴：时间戳相对当天 0 点的小时数，截到 [0, 24]。
// 跨零点任务的末次时间是次日 00:00，按"时:分"取值会变成 0 而画出负宽度，这里按偏移算即为 24。
func rhythmHour(_ stamp: Double, dayStart: Double) -> Double {
    min(max((stamp - dayStart) / 3600, 0), 24)
}
// 按所有活动段的最早/最晚小时取偶数刻度区间；无有效段回退 8–24。
func rhythmRange(_ segments: [[[Double]]], dayStart: Double) -> (Double, Double) {
    var low = 24.0, high = 0.0
    for segment in segments.joined() where segment.count == 2 && segment[0] > 0 {
        low = min(low, rhythmHour(segment[0], dayStart: dayStart))
        high = max(high, rhythmHour(max(segment[1], segment[0]), dayStart: dayStart))
    }
    guard high >= low else { return (8, 24) }  // 单条消息也是活动段（零长度）
    let start = max(0, floor(low / 2) * 2)
    let end = min(24, max(start + 2, ceil(high / 2) * 2))
    return (start, end)
}
// 热力分级阈值只在 Python（daily_report.heat_level）实现，界面直接使用载荷里的 level；
// Swift 不留第二份实现，避免双语言口径漂移（旧副本阈值曾与 Python 不一致）。
