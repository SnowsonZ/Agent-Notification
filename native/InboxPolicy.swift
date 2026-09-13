import Foundation

func inboxPageCount(total: Int, size: Int) -> Int { max(1, (total + size - 1) / size) }
func inboxPageRange(total: Int, page: Int, size: Int) -> Range<Int> {
    let start = min(max(0, page) * size, total)
    return start..<min(start + size, total)
}
func needsNotification(unread: Bool, token: String, seen: String?, initialSnapshot: Bool, enabled: Bool) -> Bool {
    enabled && !initialSnapshot && unread && !token.isEmpty && token != seen
}
