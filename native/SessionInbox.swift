import AppKit
import SwiftUI
import UserNotifications

final class InboxAppDelegate: NSObject, NSApplicationDelegate, UNUserNotificationCenterDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        UNUserNotificationCenter.current().delegate = self
        applyAppearanceIcon()
        // 系统 tooltip 默认延迟约 1.5s：日报悬浮要求即触即显，压到 120ms。
        UserDefaults.standard.register(defaults: ["NSInitialToolTipDelay": 120])
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
    // macOS 不为 icns 做外观切换：随系统明暗手动换 Dock 图标（并叠加当前未读角标）。
    func applyAppearanceIcon() {
        let dark = NSApp.effectiveAppearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
        applyDockIcon(dark: dark, unread: DockBadge.unread)
    }
    // 组件点击经 agentnotification:// scheme 唤起；scheme 在 Info.plist CFBundleURLTypes。
    func application(_ application: NSApplication, open urls: [URL]) {
        Task { @MainActor in
            NSApplication.shared.activate(ignoringOtherApps: true)
            for url in urls { WidgetURLBridge.shared.deliver(url) }
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
    static let widgetURLOpen = Notification.Name("SessionInboxWidgetURLOpen")
    static let widgetReportPeriod = Notification.Name("SessionInboxWidgetReportPeriod")
}

// 组件 URL 投递桥（desktop-widgets.md §5）：URL 事件由 AppDelegate 全 App 级接收，
// 窗口未开时 SwiftUI 的 onOpenURL 收不到，这里先暂存、InboxView 挂载时补送。
@MainActor
final class WidgetURLBridge {
    static let shared = WidgetURLBridge()
    private var gate = WidgetURLGate()
    private var queue = WidgetURLQueue()

    /// 组件点击入口：防抖后投递通知（窗口已开时立即处理）并入队；
    /// 处理方完成后调 markHandled 把 URL 移出队列（R10：只处理一次）。
    func deliver(_ url: URL, now: TimeInterval = Date().timeIntervalSince1970) {
        guard gate.accept(url, now: now) else { return }
        queue.enqueue(url)
        NotificationCenter.default.post(name: .widgetURLOpen, object: url)
    }

    /// 通知路径处理完成：移出队列，flush 不再重放。
    func markHandled(_ url: URL) {
        queue.markHandled(url)
    }

    /// R10 冷启动：处理方发现行数据未就绪时把 URL 放回队列，等 flush 补处理。
    func enqueue(_ url: URL) {
        queue.enqueue(url)
    }

    /// 窗口挂载或首批行加载完成：补处理仍挂起的 URL（R10 冷启动挂起语义）。
    func flush(to handler: (URL) -> Void) {
        for url in queue.flush() { handler(url) }
    }
}

// Dock 未读角标：dockTile.badgeLabel 在本应用不渲染（见 InboxModel.updateDockBadge 注），
// 把角标直接画进应用图标。主题切换重绘底图时要带上同一数值，故未读数存全局。
enum DockBadge {
    static var unread = 0
}

func applyDockIcon(dark: Bool, unread: Int) {
    guard let url = Bundle.main.url(forResource: dark ? "AppIconDark" : "AppIcon", withExtension: "icns"),
          let base = NSImage(contentsOf: url) else { return }
    guard unread > 0 else {
        NSApplication.shared.applicationIconImage = base
        return
    }
    let canvas = NSSize(width: 1024, height: 1024)
    let image = NSImage(size: canvas)
    image.lockFocus()
    base.draw(in: NSRect(origin: .zero, size: canvas))
    let text = unread > 99 ? "99+" : String(unread)
    let fontSize: CGFloat = text.count >= 3 ? 140 : (text.count == 2 ? 180 : 230)
    let string = NSAttributedString(string: text, attributes: [
        .font: NSFont.systemFont(ofSize: fontSize, weight: .bold),
        .foregroundColor: NSColor.white,
    ])
    let bounds = string.boundingRect(with: canvas, options: [.usesLineFragmentOrigin])
    let center = NSPoint(x: 852, y: 852)
    let radius: CGFloat = text.count >= 3 ? 176 : 158
    let circle = NSRect(x: center.x - radius, y: center.y - radius, width: radius * 2, height: radius * 2)
    // 橙底白字与托盘徽标、行内未读点同一视觉语言（统一 systemOrange，明暗自适应）；白描边与浅色瓦片分隔。
    NSColor.systemOrange.setFill()
    NSBezierPath(ovalIn: circle).fill()
    NSColor.white.setStroke()
    let border = NSBezierPath(ovalIn: circle)
    border.lineWidth = 16
    border.stroke()
    string.draw(at: NSPoint(x: center.x - bounds.width / 2, y: center.y - bounds.height / 2))
    image.unlockFocus()
    NSApplication.shared.applicationIconImage = image
}

struct TrayMenu: View {
    @ObservedObject var model: InboxModel
    @Environment(\.openWindow) var openWindow
    var body: some View {
        Button("查看会话通知（\(model.unreadCount) 条待查看）") {
            openWindow(id: "inbox")
            NSApplication.shared.activate()
        }
        Button("查看工作日报") {
            openWindow(id: "dailyReport")
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
    @Environment(\.colorScheme) private var colorScheme
    var body: some View {
        // 徽标必须显式配色：模板渲染会把橙底白字拍平成单色，数字不可读；
        // 图标本体跟随菜单栏明暗手动着色。padding 与 offset 配合保证徽标在状态项边界内。
        Image(systemName: model.unreadCount > 0 ? "tray.fill" : "tray")
            .foregroundStyle(colorScheme == .dark ? Color.white : Color.black)
            .overlay(alignment: .topTrailing) {
                if model.unreadCount > 0 {
                    Text(model.unreadCount > 99 ? "99+" : String(model.unreadCount))
                        .font(.system(size: 8, weight: .bold))
                        .foregroundStyle(.white)
                        .padding(.horizontal, 3)
                        .frame(minWidth: 12, minHeight: 12)
                        .background(Circle().fill(Color.attention))
                        .offset(x: 5, y: -2)
                }
            }
            .padding(.top, 2)
            .padding(.trailing, 5)
            .onReceive(NotificationCenter.default.publisher(for: .reopenInbox)) { _ in
                openWindow(id: "inbox")
                NSApplication.shared.activate()
            }
    }
}

@main struct SessionInboxApp: App {
    @NSApplicationDelegateAdaptor(InboxAppDelegate.self) var appDelegate
    @StateObject private var model = InboxModel()
    @StateObject private var reportModel = DailyReportModel()
    var body: some Scene {
        // Window（而非 WindowGroup）：收件箱只允许一个实例，openWindow 聚焦已有窗口；
        // WindowGroup 的 openWindow 每次调用都会新建窗口。
        Window("会话通知", id: "inbox") { InboxView(model: model) }
            .defaultSize(width: 400, height: 620)
            .windowResizability(.contentMinSize)
            .windowToolbarStyle(.unifiedCompact(showsTitle: false))
        Window("日报", id: "dailyReport") { DailyReportView(model: reportModel) }
            .defaultSize(width: 600, height: 780)
            .windowResizability(.contentMinSize)
        MenuBarExtra {
            TrayMenu(model: model)
        } label: {
            TrayIcon(model: model)
        }
    }
}
