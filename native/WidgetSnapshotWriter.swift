import Foundation
import WidgetKit

// 组件快照写入器（desktop-widgets.md §2–§3）：主 App 唯一写方。
// 快照路径固定 ~/.local/state/session-manager/widget/snapshot.json（目录 0700、
// 文件 0600、临时文件 + rename 原子写）；签名没变就不写文件、不 reload
//（判定在 InboxPolicy.WidgetRefreshPolicy，单测同守）；hide_titles 打开时标题
// 根本不写入文件，而不只是显示时隐藏（W6）。
@MainActor
final class WidgetSnapshotWriter {
    static let shared = WidgetSnapshotWriter()

    private let root: String
    private var last: (inbox: String, recent: String, prefs: String)?
    private var lastRecentReload: TimeInterval = 0
    private var lastUsageReload: TimeInterval = 0
    private var usagePayload: [String: WidgetUsagePayload] = [:]
    private var usageAt: TimeInterval = 0
    private var usageLoading = false
    private var hasUsage = false
    // 最近一次收件箱输入：usage 异步拉取完成后用它重建快照落盘。
    private var lastRows: [InboxRow] = []
    private var lastTodayUsage: [String: (tokens: Int?, cost: WidgetSnapshotMoney?)] = [:]

    init(root: String = Bundle.main.object(forInfoDictionaryKey: "SessionManagerRoot") as? String ?? "") {
        self.root = root
    }

    // 金额口径（2026-09-24）：组件只展示 USD，不再有币种配置；快照里的 currency
    // 字段保留（schema 稳定），恒写 "USD"。
    private let currency = "USD"
    private var hideTitles: Bool { UserDefaults.standard.bool(forKey: "widgetHideTitles") }
    private var fallbackPeriod: String { UserDefaults.standard.string(forKey: "widgetFallbackPeriod") ?? "day" }
    private var fallbackDimension: String { UserDefaults.standard.string(forKey: "widgetFallbackDimension") ?? "harness" }

    // MARK: 数据入口（InboxModel 每 3 秒 tick 调用）

    func update(
        rows: [InboxRow],
        todayUsage: [String: (tokens: Int?, cost: WidgetSnapshotMoney?)]
    ) {
        lastRows = rows
        lastTodayUsage = todayUsage
        let now = Date().timeIntervalSince1970
        // 快照条目一律排除 agent 拉起的会话（§2：即使审计开关打开也不写入）。
        // pending 计数与条目和 Dock 角标完全同口径（inboxNotifyEligible &&
        // inboxRowListed）；running 取等待模型回复的回合（inboxActiveListed）。
        let human = rows.filter { $0.origin != "agent" }
        let pendingRows = human.filter {
            inboxNotifyEligible(origin: $0.origin, unread: $0.unread)
                && inboxRowListed(state: $0.state, openAvailable: $0.openAvailable)
        }
        // V080-R8：进行中口径只在 widgetRunningListed 实现（只排除 agent，不要求未读，与 activeCount 同口径）。
        let runningRows = rows.filter {
            widgetRunningListed(origin: $0.origin, state: $0.state, openAvailable: $0.openAvailable)
        }
        let recentRows = human.sorted {
            max($0.activityAt ?? 0, $0.eventAt) > max($1.activityAt ?? 0, $1.eventAt)
        }

        func item(_ row: InboxRow) -> WidgetSnapshot.Inbox.Item {
            let text = widgetEntryText(hideTitles: hideTitles, title: row.title, project: row.project)
            return WidgetSnapshot.Inbox.Item(
                id: row.id, revision: row.revision, provider: row.provider,
                title: text.title,
                project: text.project,
                state: row.state, at: max(row.activityAt ?? 0, row.eventAt)
            )
        }
        let pendingItems = pendingRows.prefix(WidgetSnapshot.maxPendingItems).map(item)
        let runningItems = runningRows.prefix(WidgetSnapshot.maxRunningItems).map(item)
        let recentItems = recentRows.prefix(WidgetSnapshot.maxRecentItems).map { row in
            // V080-R9：今日用量键与查找只经 InboxPolicy 的纯函数，两字段找不到即为 nil。
            let usage = todayUsageFor(provider: row.provider, sessionID: row.sessionId, mapping: todayUsage)
            let text = widgetEntryText(hideTitles: hideTitles, title: row.title, project: row.project)
            return WidgetSnapshot.RecentItem(
                id: row.id, revision: row.revision, provider: row.provider,
                title: text.title,
                project: text.project,
                state: row.state, at: max(row.activityAt ?? 0, row.eventAt),
                todayTokens: usage.tokens, todayCost: usage.cost
            )
        }

        // 签名含计数：pending 第 7 条之后的变化不改前 6 条，但计数本身要更新。
        let inboxSignature = "p\(pendingRows.count)/r\(runningRows.count)|"
            + WidgetRefreshPolicy.signature(
                (pendingItems + runningItems).map { (id: $0.id, revision: $0.revision, state: $0.state) }
            )
        let recentSignature = WidgetRefreshPolicy.recentSignature(recentItems, usdCnyRate: fx.usdCny)
        // 签名含组件默认设置：设置面板改周期/视角/隐藏标题后，快照与 reload 跟随。
        let prefsSignature = "\(currency)|\(hideTitles)|\(fx.usdCny)|\(fx.asOf)|\(usageAt)|\(fallbackPeriod)|\(fallbackDimension)"

        let decision = WidgetRefreshPolicy.evaluate(
            WidgetRefreshPolicy.Inputs(
                inboxSignature: inboxSignature,
                recentSignature: recentSignature,
                prefsSignature: prefsSignature,
                last: last,
                lastRecentReload: lastRecentReload,
                lastUsageReload: lastUsageReload,
                now: now
            )
        )
        last = (inboxSignature, recentSignature, prefsSignature)
        if decision.reload.contains(.recent) { lastRecentReload = now }
        if decision.reload.contains(.usage) { lastUsageReload = now }
        guard decision.writeSnapshot else { return }

        write(snapshot(pendingItems: pendingItems, runningItems: runningItems, recentItems: recentItems, pendingCount: pendingRows.count, runningCount: runningRows.count))
        for kind in decision.reload { reload(kind: kind.rawValue) }
    }

    // MARK: usage 刷新（§3：每 15 分钟；打开日报窗口 / 切换币种时立即）

    func refreshUsageIfNeeded(force: Bool = false) {
        let now = Date().timeIntervalSince1970
        if usageLoading { return }
        if !force && hasUsage && now - usageAt < 15 * 60 { return }
        usageLoading = true
        let directory = root
        Task {
            let result = await Task.detached {
                ProcessRunner.run(root: directory, arguments: ["usage", "--period", "all", "--json"])
            }.value
            usageLoading = false
            guard result.0 == 0,
                  let object = try? JSONSerialization.jsonObject(with: result.1) as? [String: Any]
            else {
                // 诊断：失败原因落盘（不含正文，只含错误摘要）
                let reason = result.2.isEmpty ? String(data: result.1, encoding: .utf8) ?? "" : result.2
                try? String(reason.prefix(300)).write(
                    to: dataDirectory.appendingPathComponent("widget/usage-error.txt"),
                    atomically: true, encoding: .utf8
                )
                return
            }
            let decoder = JSONDecoder()
            decoder.keyDecodingStrategy = .convertFromSnakeCase
            var payload: [String: WidgetUsagePayload] = [:]
            for name in ["day", "week", "month"] {
                guard let section = object[name],
                      let data = try? JSONSerialization.data(withJSONObject: section)
                else { continue }
                if let value = try? decoder.decode(WidgetUsagePayload.self, from: data) {
                    var trimmed = value
                    if var by = trimmed.by {
                        by.harness = WidgetSnapshotLimiter.truncate(by.harness)
                        by.model = WidgetSnapshotLimiter.truncate(by.model)
                        by.project = WidgetSnapshotLimiter.truncate(by.project)
                        trimmed.by = by
                    }
                    payload[name] = trimmed
                }
            }
            usagePayload = payload
            usageAt = Date().timeIntervalSince1970
            hasUsage = true
            // usage 更新即写快照并 reload usage kind（§3）；收件箱数据用最近一次输入。
            lastUsageReload = usageAt
            update(rows: lastRows, todayUsage: lastTodayUsage)
        }
    }

    /// 打开日报窗口 / 切换币种时由日报模型调用（§3 立即刷新）。
    func refreshUsageNow() { refreshUsageIfNeeded(force: true) }

    // MARK: 快照构建与落盘

    private func snapshot(
        pendingItems: [WidgetSnapshot.Inbox.Item],
        runningItems: [WidgetSnapshot.Inbox.Item],
        recentItems: [WidgetSnapshot.RecentItem],
        pendingCount: Int,
        runningCount: Int
    ) -> WidgetSnapshot {
        var recent = recentItems
        if hideTitles {
            for index in recent.indices {
                recent[index].title = widgetEntryTitle(hideTitles: true, title: recent[index].title)
            }
        }
        return WidgetSnapshot(
            generatedAt: Date().timeIntervalSince1970,
            prefs: WidgetSnapshot.Prefs(
                currency: currency,
                hideTitles: hideTitles,
                fallback: WidgetSnapshot.Prefs.Fallback(
                    period: fallbackPeriod,
                    dimension: fallbackDimension
                )
            ),
            fx: fx,
            inbox: WidgetSnapshot.Inbox(
                pending: pendingCount,
                running: runningCount,
                pendingItems: pendingItems,
                runningItems: runningItems
            ),
            usage: usagePayload,
            usageAt: usageAt,
            recent: recent
        )
    }

    // 手动汇率文件由 CLI `inbox pricing fx` 写入；缺失用默认值（usage-cost.md §5）。
    private var fx: WidgetSnapshot.Fx {
        let url = dataDirectory.appendingPathComponent("pricing/fx.json")
        if let data = try? Data(contentsOf: url),
           let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            let rate = (object["USD_CNY"] as? NSNumber)?.doubleValue ?? 7.10
            return WidgetSnapshot.Fx(usdCny: rate, asOf: object["as_of"] as? String ?? "")
        }
        return WidgetSnapshot.Fx(usdCny: 7.10, asOf: "")
    }

    /// 快照数据目录固定在 ~/.local/state/session-manager/widget/（§2）：
    /// SessionManagerRoot 是代码根（开发包=repo），不是数据根，不能混用。
    private var dataDirectory: URL {
        // 沙盒外取真实 home 直接用 NSHomeDirectory（getpwuid 是组件沙盒内才需要）
        URL(fileURLWithPath: NSHomeDirectory())
            .appendingPathComponent(".local/state/session-manager", isDirectory: true)
    }

    private func write(_ snapshot: WidgetSnapshot) {
        let directoryURL = dataDirectory.appendingPathComponent("widget", isDirectory: true)
        try? FileManager.default.createDirectory(
            at: directoryURL, withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700]
        )
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        guard let data = try? encoder.encode(snapshot) else { return }
        let target = directoryURL.appendingPathComponent("snapshot.json")
        let temporary = directoryURL.appendingPathComponent(".snapshot.json.tmp")
        do {
            try data.write(to: temporary, options: [.atomic])
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: temporary.path)
            if FileManager.default.fileExists(atPath: target.path) {
                _ = try FileManager.default.replaceItemAt(target, withItemAt: temporary)
            } else {
                try FileManager.default.moveItem(at: temporary, to: target)
            }
        } catch {
            try? FileManager.default.removeItem(at: temporary)
        }
    }

    private func reload(kind: String) {
        WidgetCenter.shared.reloadTimelines(ofKind: kind)
    }
}
