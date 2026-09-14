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

final class InboxAppDelegate: NSObject, NSApplicationDelegate, UNUserNotificationCenterDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        UNUserNotificationCenter.current().delegate = self
        applyAppearanceIcon()
        // Apply the compact default once; later user resizing remains persistent.
        if !UserDefaults.standard.bool(forKey: "compactWindowV1") {
            if let window = NSApp.windows.first(where: { $0.identifier?.rawValue == "inbox" }) {
                window.setContentSize(NSSize(width: 400, height: 620))
                UserDefaults.standard.set(true, forKey: "compactWindowV1")
            }
        }
        DistributedNotificationCenter.default().addObserver(forName: Notification.Name("AppleInterfaceThemeChangedNotification"),
                                                            object: nil, queue: .main) { [weak self] _ in
            self?.applyAppearanceIcon()
        }
    }
    // macOS 不为 icns 做外观切换：随系统明暗手动换 Dock 图标。
    func applyAppearanceIcon() {
        let dark = NSApp.effectiveAppearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
        if let url = Bundle.main.url(forResource: dark ? "AppIconDark" : "AppIcon", withExtension: "icns"),
           let image = NSImage(contentsOf: url) {
            NSApplication.shared.applicationIconImage = image
        }
    }
    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .sound])
    }
    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
                                withCompletionHandler completionHandler: @escaping () -> Void) {
        if response.actionIdentifier == UNNotificationDefaultActionIdentifier {
            Task { @MainActor in
                NSApplication.shared.activate(ignoringOtherApps: true)
                if let window = NSApplication.shared.windows.first(where: { $0.canBecomeMain && $0.isVisible }) {
                    window.makeKeyAndOrderFront(nil)
                } else {
                    // Window 已被关闭时 SwiftUI 已释放它，只能经 openWindow 重建；
                    // 常驻的菜单栏图标视图监听该事件并调用 openWindow(id: "inbox")。
                    NotificationCenter.default.post(name: .reopenInbox, object: nil)
                }
            }
        }
        completionHandler()
    }
}

extension Notification.Name {
    static let reopenInbox = Notification.Name("SessionInboxReopenInbox")
}

@MainActor final class InboxModel: ObservableObject {
    @Published var rows: [InboxRow] = []
    @Published var showAll = false { didSet { page = 0 } }
    @Published var query = "" { didSet { page = 0 } }
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
    let root: String
    private var timer: Timer?
    init() {
        root = Bundle.main.object(forInfoDictionaryKey: "SessionManagerRoot") as? String ?? ""
        let tick = Timer(timeInterval: 3, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
        }
        RunLoop.main.add(tick, forMode: .common)
        timer = tick
        checkNotificationPermission()
        loadAgents()
    }
    var unreadCount: Int { rows.filter(\.unread).count }
    var filtered: [InboxRow] {
        rows.filter { row in
            // 已退出且原会话入口不可用的行没有可执行的后续，直接不展示。
            guard !(row.state == "closed" && !row.openAvailable) else { return false }
            return (showAll || row.unread) && (query.isEmpty ||
                (row.title + " " + row.project + " " + row.provider).localizedCaseInsensitiveContains(query))
        }.sorted {
            if !showAll {
                let rank: (InboxRow) -> Int = { $0.state == "waiting" ? 0 : ($0.state == "failed" ? 1 : 2) }
                if rank($0) != rank($1) { return rank($0) < rank($1) }
            }
            return max($0.activityAt ?? 0, $0.eventAt) > max($1.activityAt ?? 0, $1.eventAt)
        }
    }
    var totalPages: Int { inboxPageCount(total: filtered.count, size: pageSize) }
    var visible: [InboxRow] {
        let values = filtered
        guard showAll else { return values }
        // 懒加载：随滚动逐步放开已加载的行，到底自动追加下一页。
        return Array(values.prefix(min((page + 1) * pageSize, values.count)))
    }
    func loadMoreIfNeeded(for row: InboxRow) {
        guard showAll, page + 1 < totalPages, row.id == visible.last?.id else { return }
        page += 1
    }
    func toggleNotifications() {
        notificationsEnabled.toggle()
        UserDefaults.standard.set(notificationsEnabled, forKey: "notificationsEnabled")
        if notificationsEnabled { checkNotificationPermission() }
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
                    notificationStatus = granted ? "通知已开启" : "请在系统设置 → 通知中允许 会话通知"
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
    nonisolated static func call(root: String, arguments: [String]) -> (Int32, Data, String) {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: root + "/scratch/iterm-probe-venv/bin/python")
        process.arguments = [root + "/scripts/inbox.py"] + arguments
        let output = Pipe(); let errors = Pipe()
        process.standardOutput = output; process.standardError = errors
        do {
            try process.run()
            let data = output.fileHandleForReading.readDataToEndOfFile()
            let diagnostic = errors.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            return (process.terminationStatus, data, String(data: diagnostic, encoding: .utf8) ?? "")
        } catch { return (1, Data(), error.localizedDescription) }
    }
    func refresh() {
        guard !loading else { return }
        loading = true
        let directory = root
        Task {
            let result = await Task.detached { Self.call(root: directory, arguments: ["rows", "--all", "--refresh"]) }.value
            loading = false
            guard result.0 == 0 else { error = result.2; return }
            do {
                let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
                let payload = try decoder.decode(Envelope.self, from: result.1)
                rows = payload.sessions
                page = max(0, min(page, totalPages - 1))
                if agents.isEmpty { loadAgents() } // 启动时检测偶发失败会在后续刷新里自动补试。
                notifyNewItems(rows)
                degraded = (payload.health?.sources ?? [:]).filter { $0.value.status != "ok" }.map { name, info in
                    if let count = info.errors, count > 0 { return "\(providerName(name))：\(count) 个历史记录暂未接入" }
                    return "\(providerName(name))：来源暂不可用"
                }.sorted()
            } catch { self.error = "列表读取失败：" + error.localizedDescription }
        }
    }
    func acknowledge(_ row: InboxRow) { action(["ack", row.id, "--revision", String(row.revision)], isOpen: false) }
    func open(_ row: InboxRow) { action(["open", row.id, "--revision", String(row.revision)], isOpen: true) }
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
            }
            refresh()
        }
    }
}

func agentIcon(_ id: String) -> NSImage {
    let size = NSSize(width: 16, height: 16)
    // 有桌面 App 的来源用应用图标；CLI 用官方图标；缺失时画品牌色字符兜底。
    let bundleIds = ["claude": "com.anthropic.claudefordesktop", "zcode": "dev.zcode.app"]
    if let bundleId = bundleIds[id], let url = NSWorkspace.shared.urlForApplication(withBundleIdentifier: bundleId) {
        let icon = NSWorkspace.shared.icon(forFile: url.path)
        icon.size = size
        return icon
    }
    // Codex 桌面内嵌在 ChatGPT.app 的 Framework 里，icon 资源随包路径带版本号，
    // 因此把官方 logo 收进 agent-icons 随本 App 打包。
    let official: [String: (ext: String, fallbackPath: String)] = [
        "pi": ("svg", "native/agent-icons/pi.svg"),
        "kimi": ("ico", "/opt/homebrew/lib/node_modules/@moonshot-ai/kimi-code/dist-web/favicon.ico"),
        "codex": ("png", "/Applications/ChatGPT.app/Contents/Frameworks/Codex Framework.framework/Versions/Current/Resources/product_logo_32.png"),
    ]
    if let entry = official[id] {
        var urls = [Bundle.main.url(forResource: id, withExtension: entry.ext)].compactMap { $0 }
        if let root = Bundle.main.object(forInfoDictionaryKey: "SessionManagerRoot") as? String {
            urls.append(URL(fileURLWithPath: root + "/" + entry.fallbackPath))
        }
        urls.append(URL(fileURLWithPath: entry.fallbackPath))
        for url in urls where FileManager.default.fileExists(atPath: url.path) {
            if let icon = NSImage(contentsOf: url) {
                icon.size = size
                return icon
            }
        }
    }
    let glyphs = ["pi": ("π", NSColor(srgbRed: 0.42, green: 0.48, blue: 0.55, alpha: 1)),
                  "kimi": ("K", NSColor(srgbRed: 0.30, green: 0.43, blue: 0.96, alpha: 1)),
                  "codex": (">_", NSColor(srgbRed: 0.35, green: 0.45, blue: 0.95, alpha: 1)),
                  "zcode": ("Z", NSColor(srgbRed: 0.22, green: 0.25, blue: 0.30, alpha: 1))]
    let (glyph, color) = glyphs[id] ?? ("?", NSColor.systemGray)
    let image = NSImage(size: size)
    image.lockFocus()
    let rect = NSRect(origin: .zero, size: size)
    color.setFill()
    NSBezierPath(roundedRect: rect, xRadius: 4, yRadius: 4).fill()
    let text = NSAttributedString(string: glyph, attributes: [
        .font: NSFont.systemFont(ofSize: 11, weight: .semibold),
        .foregroundColor: NSColor.white,
    ])
    let bounds = text.boundingRect(with: size, options: [.usesLineFragmentOrigin])
    text.draw(at: NSPoint(x: (size.width - bounds.width) / 2, y: (size.height - bounds.height) / 2))
    image.unlockFocus()
    return image
}

func stateName(_ state: String) -> String {    ["running": "运行中", "waiting": "等待输入", "idle": "本轮已结束", "failed": "发生错误",
     "interrupted": "已中断", "closed": "已退出", "unknown": "状态待确认"][state] ?? state
}
func providerName(_ provider: String) -> String {
    ["claude": "Claude", "codex": "Codex", "zcode": "Zcode", "pi": "Pi", "kimi": "Kimi"][provider] ?? provider
}
struct InboxView: View {
    @ObservedObject var model: InboxModel
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("会话通知").font(.system(size: 24, weight: .bold))
                    Text(model.unreadCount == 0 ? "暂无新通知" : "\(model.unreadCount) 条会话有新动态")
                        .font(.subheadline).foregroundStyle(.secondary)
                }
                Spacer()
                Button { model.toggleNotifications() } label: {
                    Image(systemName: model.notificationsEnabled && model.notificationsAllowed ? "bell.badge.fill" : "bell.slash")
                        .foregroundStyle(.secondary)
                }
                .buttonStyle(.plain)
                .help(model.notificationsEnabled ? model.notificationStatus + "（点击关闭）" : "点击开启消息通知")
                Button { model.refresh() } label: {
                    Image(systemName: "arrow.clockwise").foregroundStyle(.secondary)
                }
                .buttonStyle(.plain)
                .help("刷新").disabled(model.loading)
            }
            if !model.agents.isEmpty {
                HStack(spacing: 12) {
                    Text("新建会话").font(.caption).foregroundStyle(.secondary)
                    ForEach(model.agents) { agent in
                        Button {
                            model.launch(agent)
                        } label: {
                            Image(nsImage: agentIcon(agent.id))
                                .resizable()
                                .frame(width: 24, height: 24)
                                .frame(width: 36, height: 36)
                                .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 10))
                        }
                        .buttonStyle(.plain)
                        .disabled(!agent.iterm)
                        .accessibilityLabel("新建 \(agent.name) 会话")
                        .help(agent.iterm ? "选择目录并在 iTerm2 新标签中启动 \(agent.name)" : "未检测到 iTerm2，无法在此启动")
                    }
                    Spacer()
                }
            }
            VStack(spacing: 10) {
                Picker("显示范围", selection: $model.showAll) {
                    Text("待查看（\(model.unreadCount)）").tag(false)
                    Text("全部会话（\(model.rows.count)）").tag(true)
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                HStack(spacing: 6) {
                    Image(systemName: "magnifyingglass").font(.caption).foregroundStyle(.secondary)
                    TextField("搜索会话或项目", text: $model.query)
                        .textFieldStyle(.plain)
                }
                .padding(.horizontal, 10).padding(.vertical, 8)
                .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 7))
                .overlay(RoundedRectangle(cornerRadius: 7).strokeBorder(Color.primary.opacity(0.06)))
            }
            if model.visible.isEmpty {
                Spacer()
                VStack(spacing: 10) {
                    Image(systemName: model.query.isEmpty ? "tray" : "magnifyingglass")
                        .font(.system(size: 30)).foregroundStyle(.tertiary)
                    Text(model.loading ? "正在加载会话…" : (!model.query.isEmpty ? "没有匹配的会话" : (model.showAll ? "还没有会话" : "暂无新通知")))
                        .font(.subheadline).foregroundStyle(.secondary)
                }.frame(maxWidth: .infinity)
                Spacer()
            } else {
                ScrollView {
                    LazyVStack(spacing: 0) {
                        ForEach(model.visible) { row in
                            InboxRowView(model: model, row: row)
                            Divider().padding(.leading, 54)
                        }
                        if model.showAll && model.page + 1 < model.totalPages {
                            HStack(spacing: 8) {
                                ProgressView().controlSize(.small)
                                Text("继续加载").font(.caption2).foregroundStyle(.tertiary)
                            }
                            .frame(maxWidth: .infinity).padding(.vertical, 6)
                            .onAppear { model.page += 1 }
                        }
                    }.padding(.vertical, 2)
                }
            }
            if let error = model.error {
                HStack(alignment: .top, spacing: 8) {
                    Image(systemName: "exclamationmark.triangle.fill").font(.caption).foregroundStyle(.red)
                    Text(error).font(.caption).foregroundStyle(.red).textSelection(.enabled)
                    Spacer()
                    Button { model.error = nil } label: { Image(systemName: "xmark").font(.caption2) }
                        .buttonStyle(.plain).foregroundStyle(.secondary)
                }
                .padding(10)
                .background(Color.red.opacity(0.06), in: RoundedRectangle(cornerRadius: 8))
            }
            if !model.degraded.isEmpty {
                Text(model.degraded.joined(separator: " · ")).font(.caption).foregroundStyle(.orange)
            }
            if model.notificationsEnabled && !model.notificationsAllowed {
                Text(model.notificationStatus).font(.caption).foregroundStyle(.secondary)
            }
            Text("打开会话后自动标记已读")
                .font(.caption2).foregroundStyle(.tertiary)
        }
        .padding(16)
        .frame(minWidth: 360, idealWidth: 400, minHeight: 480, idealHeight: 620)
        .background(Color(nsColor: .windowBackgroundColor))
        .onAppear { model.refresh() }
    }
}

struct InboxActionButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .foregroundStyle(isEnabled ? Color.primary.opacity(0.75) : Color.secondary.opacity(0.4))
            .background {
                RoundedRectangle(cornerRadius: 7)
                    .fill(Color.primary.opacity(isEnabled && configuration.isPressed ? 0.18 : 0.035))
            }
            .contentShape(RoundedRectangle(cornerRadius: 7))
            .scaleEffect(isEnabled && configuration.isPressed ? 0.90 : 1)
            .animation(.easeOut(duration: 0.12), value: configuration.isPressed)
    }
}

struct InboxRowView: View {
    @ObservedObject var model: InboxModel
    let row: InboxRow
    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            // 主内容为 agent 图标；待处理时右上角橙色圆点，描边取窗口背景色，
            // 亮色/暗色模式自动适配。
            // 已退出与已结束对用户含义一致（都可重开查看），外观保持一致，不做置灰。
            Image(nsImage: agentIcon(row.provider))
                .resizable()
                .frame(width: 30, height: 30)
                .overlay(alignment: .topTrailing) {
                    if row.unread {
                        Circle()
                            .fill(Color.orange)
                            .frame(width: 9, height: 9)
                            .overlay(Circle().strokeBorder(Color(nsColor: .windowBackgroundColor), lineWidth: 1.5))
                            .offset(x: 3, y: -3)
                    }
                }
                .help(providerName(row.provider))
                .padding(.top, 1)
            VStack(alignment: .leading, spacing: 5) {
                Text(row.title)
                    .font(.system(size: 13, weight: row.unread ? .semibold : .medium))
                    .lineLimit(2)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .help(row.title)
                HStack(spacing: 5) {
                    Text(stateName(row.state))
                        .foregroundStyle(row.unread ? AnyShapeStyle(.orange) : AnyShapeStyle(.secondary))
                        .fixedSize()
                    if !row.project.isEmpty {
                        Text("·").foregroundStyle(.tertiary)
                        Text((row.project as NSString).lastPathComponent)
                            .foregroundStyle(.secondary)
                            .lineLimit(1).help(row.project)
                    }
                }.font(.system(size: 11))
                // 会话时长只在未处理期间展示：从最近一次通知起实时累计（随刷新周期）；
                // 已处理即不再展示，来源侧清未读（如 Interrupt）同样不展示。
                if row.unread, let start = row.attentionAt, start > 0 {
                    Text(inboxDurationText(from: start, to: Date().timeIntervalSince1970))
                        .font(.system(size: 10)).foregroundStyle(.secondary)
                        .help("会话时长：从最近一次通知起累计")
                }
            }
            HStack(spacing: 4) {
                if row.unread {
                    Button { model.acknowledge(row) } label: {
                        Image(systemName: "checkmark.circle").frame(width: 28, height: 30)
                    }
                    .help("标记已读").accessibilityLabel("标记已读")
                }
                Button { model.open(row) } label: {
                    Image(systemName: "arrow.up.forward.app").frame(width: 28, height: 30)
                }
                .help("前往会话").accessibilityLabel("前往会话")
                .disabled(!row.openAvailable || model.opening)
            }
            .font(.system(size: 15)).foregroundStyle(.secondary)
            .buttonStyle(InboxActionButtonStyle())
        }
        .padding(.horizontal, 4).padding(.vertical, 12)
        .background(row.unread ? Color.orange.opacity(0.045) : Color.clear, in: RoundedRectangle(cornerRadius: 8))
        .onAppear { model.loadMoreIfNeeded(for: row) }
    }
}

struct TrayMenu: View {
    @ObservedObject var model: InboxModel
    @Environment(\.openWindow) var openWindow
    var body: some View {
        Button("查看会话通知（\(model.unreadCount) 条待查看）") {
            openWindow(id: "inbox")
            NSApplication.shared.activate()
        }
        Button("刷新") { model.refresh() }
        Divider()
        Button("退出") { NSApplication.shared.terminate(nil) }
    }
}

struct TrayIcon: View {
    @ObservedObject var model: InboxModel
    @Environment(\.openWindow) private var openWindow
    var body: some View {
        Label(model.unreadCount > 0 ? String(model.unreadCount) : "", systemImage: "tray.full")
            .onReceive(NotificationCenter.default.publisher(for: .reopenInbox)) { _ in
                openWindow(id: "inbox")
                NSApplication.shared.activate()
            }
    }
}

@main struct SessionInboxApp: App {
    @NSApplicationDelegateAdaptor(InboxAppDelegate.self) var appDelegate
    @StateObject private var model = InboxModel()
    var body: some Scene {
        // Window（而非 WindowGroup）：收件箱只允许一个实例，openWindow 聚焦已有窗口；
        // WindowGroup 的 openWindow 每次调用都会新建窗口。
        Window("会话通知", id: "inbox") { InboxView(model: model) }
            .defaultSize(width: 400, height: 620)
            .windowResizability(.contentMinSize)
        MenuBarExtra {
            TrayMenu(model: model)
        } label: {
            TrayIcon(model: model)
        }
    }
}
