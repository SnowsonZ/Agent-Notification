import Foundation

func inboxPageCount(total: Int, size: Int) -> Int { max(1, (total + size - 1) / size) }
func inboxPageRange(total: Int, page: Int, size: Int) -> Range<Int> {
    let start = min(max(0, page) * size, total)
    return start..<min(start + size, total)
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
// 日报 20:00 定时语义：过了当天 20:00 且「日期#版本」标记不匹配即触发；
// App 晚于 20:00 启动时首查即补跑。标记带 schema 版本，报告结构升级后当天会重生成。
func dailyReportDayKey(_ date: Date, calendar: Calendar = .current) -> String {
    let components = calendar.dateComponents([.year, .month, .day], from: date)
    return String(format: "%04d-%02d-%02d", components.year ?? 0, components.month ?? 0, components.day ?? 0)
}
func shouldGenerateDailyReport(now: Date, lastGeneratedStamp: String?, version: Int, hour: Int = 20,
                               calendar: Calendar = .current) -> Bool {
    let components = calendar.dateComponents([.hour, .minute], from: now)
    guard (components.hour ?? 0) * 60 + (components.minute ?? 0) >= hour * 60 else { return false }
    return lastGeneratedStamp != "\(dailyReportDayKey(now, calendar: calendar))#\(version)"
}
// GitHub 贡献图同款五级强度（有效 tokens，按本机活跃日分布校准）：
// 无记录 / <100万 / <1000万 / <5000万 / ≥5000万。
func heatLevel(activeSeconds: Int) -> Int {
    heatLevel(tokens: activeSeconds)
}
func heatLevel(tokens: Int) -> Int {
    if tokens <= 0 { return 0 }
    if tokens < 1_000_000 { return 1 }
    if tokens < 10_000_000 { return 2 }
    if tokens < 50_000_000 { return 3 }
    return 4
}
