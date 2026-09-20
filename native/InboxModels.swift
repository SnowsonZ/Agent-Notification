import AppKit
import SwiftUI
import UserNotifications

struct InboxRow: Decodable, Identifiable, Sendable {
    let id: String
    let provider: String
    let title: String
    let project: String
    let state: String
    let unread: Bool
    let revision: Int
    let eventAt: Double
    let activityAt: Double?
    let attentionToken: String?
    // 可选：App 新于脚本时旧 JSON 缺这两键，非可选会让整行解码失败。
    let attentionAt: Double?
    let openAvailable: Bool
}
struct SourceHealth: Decodable { let status: String; let errors: Int? }
struct Health: Decodable { let sources: [String: SourceHealth]? }
struct Envelope: Decodable { let sessions: [InboxRow]; let health: Health? }

struct AgentEntry: Decodable, Identifiable {
    let id: String
    let name: String
    let installed: Bool
    let iterm: Bool
}
struct AgentList: Decodable { let agents: [AgentEntry] }

// 列表显示范围：待查看（未读）/ 进行中（等待模型回复的回合）/ 全部会话。
enum InboxScope {
    case pending, active, all
}

@MainActor final class InboxModel: ObservableObject {
    @Published var rows: [InboxRow] = []
    @Published var scope = InboxScope.pending { didSet { page = 0 } }
    // 其它工具拉起的 agent 会话默认隐藏（不通知、不进待查看）；开关只为审计，
    // 打开后列表可见但仍不产生通知。
    @Published var showAgentSessions = UserDefaults.standard.bool(forKey: "showAgentSessions") {
        didSet { UserDefaults.standard.set(showAgentSessions, forKey: "showAgentSessions") }
    }
    @Published var query = "" { didSet { page = 0 } }
    // 悬停中的行 ID（批量选择圈浮现用）：视图层不能用 @State（裸 swiftc 无宏），
    // 按行存模型；同一时刻只有一行悬停。
    @Published var hoveringRow: String?
    @Published var searchExpanded = false
    func setSearchExpanded(_ expanded: Bool) {
        searchExpanded = expanded
        if !expanded { query = "" }
    }
    @Published var page = 0
    let pageSize = 20
    @Published var loading = false
    @Published var opening = false
    @Published var error: String?
    @Published var degraded: [String] = []
    @Published var notificationsEnabled = UserDefaults.standard.object(forKey: "notificationsEnabled") as? Bool ?? true
    @Published var notificationsAllowed = false
    @Published var notificationStatus = "正在检查通知权限"
    @Published var agents: [AgentEntry] = []
    private var notificationSeen = UserDefaults.standard.dictionary(forKey: "notificationSeen") as? [String: String] ?? [:]
    private var lastLaunchDirectory = UserDefaults.standard.string(forKey: "lastLaunchDirectory")
    private var loadingAgents = false
    private var initialNotificationSnapshot = true
    private var notificationInFlight = Set<String>()
    private var notificationRetry: [String: Date] = [:]
    private var lastDockBadge = -1
    let root: String
    private var timer: Timer?
    private var lastFullScanAt: Date?
    private var pendingFullRefresh = false
    init() {
        root = Bundle.main.object(forInfoDictionaryKey: "SessionManagerRoot") as? String ?? ""
        let tick = Timer(timeInterval: 3, repeats: true) { [weak self] _ in
            // Timer 闭包非 MainActor 隔离，状态读取与决策全部转入 MainActor Task。
            Task { @MainActor in
                guard let self else { return }
                // 3 秒 tick 只读 store（纯渲染）；全量采集按墙钟每 15 秒调度（距上次
                // 全量发起 ≥15 秒即扫）——丢拍（在途刷新被 guard 挂起）下一个 tick 自动
                // 补，系统唤醒后也立即补扫。即时性由各来源 hooks/事件承担。
                let elapsed = Date().timeIntervalSince(self.lastFullScanAt ?? .distantPast)
                self.refresh(full: inboxTickShouldScan(elapsed: elapsed, fullEvery: 15))
            }
        }
        RunLoop.main.add(tick, forMode: .common)
        timer = tick
        checkNotificationPermission()
        loadAgents()
    }
    var unreadCount: Int { rows.filter { $0.unread && inboxRowListed(state: $0.state, openAvailable: $0.openAvailable) }.count }
    var activeCount: Int { rows.filter { inboxActiveListed(state: $0.state, openAvailable: $0.openAvailable) }.count }
    var filtered: [InboxRow] {
        rows.filter { row in
            guard inboxRowListed(state: row.state, openAvailable: row.openAvailable) else { return false }
            let scopeListed: Bool
            switch scope {
            case .pending: scopeListed = row.unread
            case .active: scopeListed = inboxActiveListed(state: row.state, openAvailable: row.openAvailable)
            case .all: scopeListed = true
            }
            return scopeListed && (query.isEmpty ||
                (row.title + " " + row.project + " " + row.provider).localizedCaseInsensitiveContains(query))
        }.sorted {
            if scope == .pending && pendingRank($0.state) != pendingRank($1.state) {
                return pendingRank($0.state) < pendingRank($1.state)
            }
            return max($0.activityAt ?? 0, $0.eventAt) > max($1.activityAt ?? 0, $1.eventAt)
        }
    }
    var totalPages: Int { inboxPageCount(total: filtered.count, size: pageSize) }
    var visible: [InboxRow] {
        let values = filtered
        guard scope == .all else { return values }
        // 懒加载：随滚动逐步放开已加载的行，到底自动追加下一页。
        return Array(values.prefix(min((page + 1) * pageSize, values.count)))
    }
    func loadMoreIfNeeded(for row: InboxRow) {
        guard scope == .all, page + 1 < totalPages, row.id == visible.last?.id else { return }
        page += 1
    }
    func toggleNotifications() {
        notificationsEnabled.toggle()
        UserDefaults.standard.set(notificationsEnabled, forKey: "notificationsEnabled")
        if notificationsEnabled { checkNotificationPermission() }
    }
    // agent 会话查看开关只影响列表可见性；通知层在 rows 默认过滤里已关闭，无需额外处理。
    func toggleAgentSessions() {
        showAgentSessions.toggle()
        refresh(full: false)  // 只影响列表可见性，读 store 即可
    }
    // 手动改判 origin：自动分类的最终兜底；ruleProject 非空时沉淀为目录覆盖规则。
    func setOrigin(_ row: InboxRow, origin: String, ruleProject: String?) {
        var arguments = ["origin", "--id", row.id, "--set", origin]
        if let dir = ruleProject { arguments += ["--rule-project", dir] }
        action(arguments, isOpen: false)
    }
    func loadAgents() {
        guard !loadingAgents else { return }
        loadingAgents = true
        let directory = root
        Task {
            let result = await Task.detached { Self.call(root: directory, arguments: ["agents"]) }.value
            loadingAgents = false
            guard result.0 == 0 else { return }
            let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
            if let list = try? decoder.decode(AgentList.self, from: result.1) {
                agents = list.agents.filter(\.installed)
            }
        }
    }
    func launch(_ agent: AgentEntry) {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.canCreateDirectories = true
        panel.allowsMultipleSelection = false
        panel.directoryURL = URL(fileURLWithPath: lastLaunchDirectory ?? NSHomeDirectory())
        panel.message = "选择启动 \(agent.name) 的工作目录"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        lastLaunchDirectory = url.path
        UserDefaults.standard.set(url.path, forKey: "lastLaunchDirectory")
        action(["launch", "--agent", agent.id, "--dir", url.path], isOpen: false)
    }
    func checkNotificationPermission() {
        Task {
            let center = UNUserNotificationCenter.current()
            let settings = await center.notificationSettings()
            notificationsAllowed = settings.authorizationStatus == .authorized || settings.authorizationStatus == .provisional
            notificationStatus = notificationsAllowed ? "通知已开启" : "系统尚未允许通知"
            if settings.authorizationStatus == .notDetermined && notificationsEnabled {
                do {
                    let granted = try await center.requestAuthorization(options: [.alert, .sound])
                    notificationsAllowed = granted
                    notificationStatus = granted ? "通知已开启" : "请在系统设置 → 通知中允许「会话通知」"
                } catch {
                    let detail = error as NSError
                    notificationStatus = "通知授权请求失败（\(detail.domain) \(detail.code)）"
                }
            }
        }
    }
    private func notifyNewItems(_ values: [InboxRow]) {
        let center = UNUserNotificationCenter.current()
        let previousSeen = notificationSeen
        let readIDs = values.filter { !$0.unread && notificationSeen[$0.id] != nil }.map(\.id)
        if !readIDs.isEmpty { center.removeDeliveredNotifications(withIdentifiers: readIDs) }
        for row in values {
            let token = row.attentionToken ?? ""
            let enabled = notificationsEnabled && notificationsAllowed
            if initialNotificationSnapshot || !enabled || !row.unread {
                notificationSeen[row.id] = token
                continue
            }
            guard needsNotification(unread: row.unread, token: token, seen: notificationSeen[row.id],
                                    initialSnapshot: false, enabled: enabled), !notificationInFlight.contains(row.id),
                  (notificationRetry[row.id] ?? .distantPast) <= Date() else { continue }
            notificationInFlight.insert(row.id)
            let content = UNMutableNotificationContent()
            content.title = "\(providerName(row.provider)) · \(stateName(row.state))"
            content.body = row.title
            content.sound = .default
            content.threadIdentifier = row.id
            content.userInfo = ["item_id": row.id, "revision": row.revision]
            center.add(UNNotificationRequest(identifier: row.id, content: content, trigger: nil)) { [weak self] failure in
                Task { @MainActor in
                    guard let self else { return }
                    self.notificationInFlight.remove(row.id)
                    if failure == nil {
                        self.notificationSeen[row.id] = token
                        UserDefaults.standard.set(self.notificationSeen, forKey: "notificationSeen")
                    } else { self.notificationRetry[row.id] = Date().addingTimeInterval(60) }
                }
            }
        }
        initialNotificationSnapshot = false
        if previousSeen != notificationSeen { UserDefaults.standard.set(notificationSeen, forKey: "notificationSeen") }
    }
    // Dock 角标与菜单栏托盘同口径（未读会话数）；随 3 秒 tick 更新，仅数值变化时改写。
    // 不用 dockTile.badgeLabel：本应用启动时替换 applicationIconImage 后系统角标不再渲染
    // （实测 badgeLabel 有值而 Dock 不画、邻居应用角标正常），改为把角标画进应用图标。
    private func updateDockBadge() {
        let count = unreadCount
        guard count != lastDockBadge else { return }
        lastDockBadge = count
        DockBadge.unread = count
        let dark = NSApp.effectiveAppearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
        applyDockIcon(dark: dark, unread: count)
    }
    nonisolated static func call(root: String, arguments: [String]) -> (Int32, Data, String) {
        ProcessRunner.run(root: root, arguments: arguments)
    }
    func refresh(full: Bool = true) {
        if loading {
            // 显式全量请求（用户刷新/窗口重开/定时全量）不静默丢弃：挂起，当前请求
            // 结束后补执行——否则双节拍下用户动作要再等最长 15 秒（codex 评审 P2）。
            if full { pendingFullRefresh = true }
            return
        }
        loading = true
        if full { lastFullScanAt = Date() }  // 发起即计时：失败也按节拍重试，不密集轰炸
        let directory = root
        var arguments = ["rows", "--all"]
        if full { arguments.append("--refresh") }
        if showAgentSessions { arguments.append("--include-agents") }
        Task {
            let result = await Task.detached { Self.call(root: directory, arguments: arguments) }.value
            loading = false
            if pendingFullRefresh {
                pendingFullRefresh = false
                refresh(full: true)
            }
            guard result.0 == 0 else { error = result.2; return }
            do {
                let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
                let payload = try decoder.decode(Envelope.self, from: result.1)
                rows = payload.sessions
                selected = selected.intersection(pendingIDs) // 已转已读/不再列出的项自动移出勾选。
                page = max(0, min(page, totalPages - 1))
                if agents.isEmpty { loadAgents() } // 启动时检测偶发失败会在后续刷新里自动补试。
                notifyNewItems(rows)
                updateDockBadge()
                degraded = (payload.health?.sources ?? [:]).filter { $0.value.status != "ok" }.map { name, info in
                    if let count = info.errors, count > 0 { return "\(providerName(name))：\(count) 个历史记录暂未接入" }
                    return "\(providerName(name))：来源暂不可用"
                }.sorted()
            } catch { self.error = "列表读取失败：" + error.localizedDescription }
        }
    }
    func acknowledge(_ row: InboxRow) { action(["ack", row.id, "--revision", String(row.revision)], isOpen: false) }
    // 批量已读：手动勾选任意子集后按点击时的整份 (id, revision) 快照逐项 CAS 确认。
    // 已有新活动的项 revision 失配被跳过并保留未读，与单条已读同合同；默认不选，勾多少清多少。
    @Published var selected: Set<String> = []
    var pendingIDs: Set<String> {
        Set(rows.filter { $0.unread && inboxRowListed(state: $0.state, openAvailable: $0.openAvailable) }.map(\.id))
    }
    var selectedCount: Int { selected.intersection(pendingIDs).count }
    var allPendingSelected: Bool { !pendingIDs.isEmpty && selected.isSuperset(of: pendingIDs) }
    func toggleSelected(_ id: String) {
        if selected.contains(id) { selected.remove(id) } else { selected.insert(id) }
    }
    func toggleSelectAll() {
        if allPendingSelected { selected.removeAll() } else { selected = pendingIDs }
    }
    func acknowledgeSelected() {
        let pairs = rows.filter { $0.unread && selected.contains($0.id) }.map { [$0.id, $0.revision] }
        guard !pairs.isEmpty, let data = try? JSONSerialization.data(withJSONObject: pairs),
              let payload = String(data: data, encoding: .utf8) else { return }
        selected.removeAll()
        action(["ack-batch", "--items", payload], isOpen: false)
    }
    func open(_ row: InboxRow) {
        // Zcode 跳转依赖辅助功能授权；缺失时走拖拽授权悬浮窗，不发起会失败的开销、不动未读状态。
        if row.provider == "zcode" && !AccessibilitySetupController.shared.isGranted {
            AccessibilitySetupController.shared.present()
            return
        }
        action(["open", row.id, "--revision", String(row.revision)], isOpen: true)
    }
    private func action(_ arguments: [String], isOpen: Bool) {
        if isOpen { opening = true }
        error = nil
        let directory = root
        Task {
            let result = await Task.detached { Self.call(root: directory, arguments: arguments) }.value
            if isOpen { opening = false }
            if result.0 != 0 {
                let raw = result.2.isEmpty ? String(data: result.1, encoding: .utf8) ?? "操作未完成" : result.2
                if let data = raw.data(using: .utf8), let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any], let reason = object["reason"] as? String {
                    error = reason
                } else { error = String(raw.prefix(500)) }
                if raw.contains("accessibility_permission_required") {
                    AccessibilitySetupController.shared.present()
                }
            }
            refresh(full: false)  // 操作后的状态回读走 store；采集由兜底扫描节奏覆盖
        }
    }
}
