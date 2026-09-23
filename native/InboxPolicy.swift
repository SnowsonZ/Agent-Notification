import Foundation

func inboxPageCount(total: Int, size: Int) -> Int { max(1, (total + size - 1) / size) }
// closed 且入口不可用的行没有可执行的后续，列表不展示，待查看计数与列表同口径。
func inboxRowListed(state: String, openAvailable: Bool) -> Bool {
    state != "closed" || openAvailable
}
// agent 会话只审计不提醒：审计开关打开后后端会返回 agent 行的真实 unread/attention，
// 通知、待查看与角标必须按有效来源过滤，不能依赖列表默认隐藏代替通知策略
// （2026-09-21 评审 R4）。origin 缺失（旧后端）视为可提醒，保持兼容。
func inboxNotifyEligible(origin: String?, unread: Bool) -> Bool {
    unread && origin != "agent"
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
// 自动刷新节拍：3 秒 tick 只读 store（纯渲染），全量采集按墙钟每 fullEvery 秒调度
// （距上次全量发起 ≥fullEvery 即扫）——丢拍（在途刷新被 guard 丢弃）下一个 tick 自动补，
// 系统唤醒后也立即补扫。即时性由各来源 hooks/事件承担，全量扫描只剩兜底职责；依据见
// docs/research/2026-09-20-push-channels-per-source.md「轮询的职责拆解」。
func inboxTickShouldScan(elapsed: TimeInterval, fullEvery: TimeInterval) -> Bool {
    guard fullEvery > 0 else { return true }
    return elapsed >= fullEvery
}
func inboxDurationText(from: Double, to: Double) -> String {
    let span = max(0, Int(to - from))
    if span < 60 { return "\(span)秒" }
    if span < 3600 { return "\(span / 60)分\(span % 60)秒" }
    if span < 86400 { return "\(span / 3600)小时\(span % 3600 / 60)分" }
    return "\(span / 86400)天\(span % 86400 / 3600)小时"
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

// MARK: - 组件快照刷新（desktop-widgets.md §3）

enum WidgetKind: String, CaseIterable {
    case inbox, recent, usage
}

struct WidgetRefreshDecision: Equatable {
    var writeSnapshot = false
    var reload: Set<WidgetKind> = []
}

enum WidgetRefreshPolicy {
    // 分区签名：条目 id:revision:state 用 | 连接（§3 签名内容）。
    static func signature(_ items: [(id: String, revision: Int, state: String)]) -> String {
        items.map { "\($0.id):\($0.revision):\($0.state)" }.joined(separator: "|")
    }

    struct Inputs {
        var inboxSignature: String
        var recentSignature: String
        var prefsSignature: String  // prefs + fx 的值本身
        var last: (inbox: String, recent: String, prefs: String)?
        var lastRecentReload: TimeInterval
        var lastUsageReload: TimeInterval
        var now: TimeInterval
        var usageEvery: TimeInterval = 15 * 60
        var recentMinReload: TimeInterval = 60
    }

    // 签名没变就不写文件、不 reload；recent 的 reload 最小间隔 60 秒，间隔内的
    // 变化合并到下一次（快照仍立即写，只是不触发组件 timeline 重载）；
    // prefs/fx 变化 reload 全部 kind；usage 按时间到期刷新。
    static func evaluate(_ input: Inputs) -> WidgetRefreshDecision {
        var decision = WidgetRefreshDecision()
        let last = input.last
        let inboxChanged = input.inboxSignature != last?.inbox
        let recentChanged = input.recentSignature != last?.recent
        let prefsChanged = input.prefsSignature != last?.prefs
        decision.writeSnapshot = inboxChanged || recentChanged || prefsChanged
        if prefsChanged {
            decision.reload = Set(WidgetKind.allCases)
            return decision
        }
        if inboxChanged { decision.reload.insert(.inbox) }
        if recentChanged, input.now - input.lastRecentReload >= input.recentMinReload {
            decision.reload.insert(.recent)
        }
        if input.now - input.lastUsageReload >= input.usageEvery {
            decision.reload.insert(.usage)
            decision.writeSnapshot = true
        }
        return decision
    }
}

// MARK: - 组件 URL 跳转（desktop-widgets.md §5）

// host 白名单 + 参数校验；任一条件不满足返回 nil，调用方只写不含参数原文的诊断日志。
enum WidgetURLRouter {
    enum Action: Equatable {
        case open(id: String, revision: Int?)
        case report(period: String)
        case inbox
    }

    static let scheme = "agentnotification"
    static let periods: Set<String> = ["day", "week", "month"]

    static func parse(_ url: URL?, knownIds: Set<String>) -> Action? {
        guard let url,
              url.scheme?.lowercased() == scheme,
              let host = url.host?.lowercased(),
              !host.isEmpty
        else { return nil }
        let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems ?? []
        func query(_ name: String) -> String? {
            items.first { $0.name == name }?.value
        }
        switch host {
        case "open":
            guard let id = query("id"), knownIds.contains(id) else { return nil }
            var revision: Int?
            if let raw = query("revision") {
                guard let value = Int(raw), value >= 0 else { return nil }
                revision = value
            }
            return .open(id: id, revision: revision)
        case "report":
            guard let period = query("period"), periods.contains(period) else { return nil }
            return .report(period: period)
        case "inbox":
            return .inbox
        default:
            return nil
        }
    }
}
