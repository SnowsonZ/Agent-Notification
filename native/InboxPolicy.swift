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
