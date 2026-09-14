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
        print("Pagination and notification policy checks passed")
    }
}
