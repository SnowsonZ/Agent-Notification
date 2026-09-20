import AppKit
import ApplicationServices

// —— 辅助功能拖拽授权 ——
// Zcode「前往会话」依赖 AX 权限。缺权限时不让用户去 Finder 翻目录：
// 自动打开系统设置对应面板，并浮出一个可拖拽的本应用徽章，拖进列表即完成授权。
@MainActor
final class AccessibilitySetupController {
    static let shared = AccessibilitySetupController()
    private var panel: NSPanel?
    private var watchdog: Timer?

    var isGranted: Bool { AXIsProcessTrusted() }

    func present() {
        if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility") {
            NSWorkspace.shared.open(url)
        }
        if panel == nil { panel = makePanel() }
        panel?.orderFrontRegardless()
        startWatchdog()
    }

    func dismiss() {
        watchdog?.invalidate()
        watchdog = nil
        panel?.orderOut(nil)
        panel = nil
    }

    private func makePanel() -> NSPanel {
        let panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 268, height: 92),
                            styleMask: [.borderless, .nonactivatingPanel],
                            backing: .buffered, defer: false)
        panel.level = .floating
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.hidesOnDeactivate = false
        panel.isOpaque = false
        panel.backgroundColor = .clear
        let badge = DragBadgeView(frame: NSRect(x: 0, y: 0, width: 268, height: 92))
        badge.onClose = { [weak self] in self?.dismiss() }
        panel.contentView = badge
        if let screen = NSScreen.main {
            panel.setFrameOrigin(NSPoint(x: screen.frame.midX - 134, y: screen.frame.minY + 96))
        }
        return panel
    }

    // 拖进去之后无需用户回报：轮询到已授权就自动收起悬浮窗。
    private func startWatchdog() {
        watchdog?.invalidate()
        let timer = Timer(timeInterval: 1, repeats: true) { [weak self] _ in
            guard let self else { return }
            Task { @MainActor in
                if self.isGranted { self.dismiss() }
            }
        }
        RunLoop.main.add(timer, forMode: .common)
        watchdog = timer
    }
}

// 悬浮徽章：应用图标 + 提示文案；按住任意位置即发起携带本应用 URL 的拖拽。
final class DragBadgeView: NSView, NSDraggingSource {
    var onClose: (() -> Void)?
    private let icon: NSImage

    override init(frame frameRect: NSRect) {
        icon = NSApp.applicationIconImage
        super.init(frame: frameRect)
        wantsLayer = true
        layer?.cornerRadius = 14
        layer?.backgroundColor = NSColor.controlBackgroundColor.cgColor
        layer?.borderColor = NSColor.separatorColor.cgColor
        layer?.borderWidth = 1
        layer?.shadowOpacity = 0.3
        layer?.shadowColor = NSColor.black.cgColor
        layer?.shadowOffset = NSSize(width: 0, height: -4)
        layer?.shadowRadius = 8

        let iconView = NSImageView(frame: NSRect(x: 14, y: 20, width: 52, height: 52))
        iconView.image = icon
        iconView.imageScaling = .scaleProportionallyUpOrDown
        addSubview(iconView)

        let title = NSTextField(labelWithString: "会话通知")
        title.font = .systemFont(ofSize: 13, weight: .semibold)
        title.frame = NSRect(x: 76, y: 46, width: 176, height: 18)
        addSubview(title)

        let hint = NSTextField(labelWithString: "把我拖进「辅助功能」列表")
        hint.font = .systemFont(ofSize: 11)
        hint.textColor = .secondaryLabelColor
        hint.frame = NSRect(x: 76, y: 26, width: 176, height: 16)
        addSubview(hint)

        let close = ClosureButton(frame: NSRect(x: 242, y: 66, width: 18, height: 18))
        close.title = "✕"
        close.isBordered = false
        close.font = .systemFont(ofSize: 11)
        close.contentTintColor = .secondaryLabelColor
        close.handler = { [weak self] in self?.onClose?() }
        addSubview(close)
    }

    required init?(coder: NSCoder) { fatalError("unsupported") }

    override func mouseDown(with event: NSEvent) {
        let item = NSDraggingItem(pasteboardWriter: Bundle.main.bundleURL as NSURL)
        icon.size = NSSize(width: 52, height: 52)
        item.setDraggingFrame(NSRect(x: 14, y: frame.height - 72, width: 52, height: 52), contents: icon)
        beginDraggingSession(with: [item], event: event, source: self)
    }

    func draggingSession(_ session: NSDraggingSession, sourceOperationMaskForDraggingAt location: NSPoint) -> NSDragOperation {
        .copy
    }

    func draggingSession(_ session: NSDraggingSession, sourceOperationMaskFor context: NSDraggingContext) -> NSDragOperation {
        .copy
    }
}

final class ClosureButton: NSButton {
    var handler: (() -> Void)?
    override func mouseDown(with event: NSEvent) { handler?() }
}
