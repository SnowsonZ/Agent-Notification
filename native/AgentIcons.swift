import AppKit
import Foundation

private var agentIconCache: [String: NSImage] = [:]

// 统一来源标志：每个标志先按透明边界裁掉自带留白，再等比缩进目标方框居中，
// 各家标志在列表和入口里占的框一致；按目标点数光栅化（Retina 下 2x），不做二次放大。
@MainActor
func agentIcon(_ id: String, size points: CGFloat = 16) -> NSImage {
    // 缓存键带外观：pi 在暗色下走亮度翻转变体，系统外观切换后按新键重渲染。
    let dark = appearanceIsDark()
    let key = "\(id)@\(points)@\(dark ? "dark" : "light")"
    if let cached = agentIconCache[key] { return cached }
    let image = renderAgentIcon(id, size: NSSize(width: points, height: points), dark: dark)
    agentIconCache[key] = image
    return image
}

private func appearanceIsDark() -> Bool {
    NSApp?.effectiveAppearance.bestMatch(from: [
        NSAppearance.Name.aqua, NSAppearance.Name.darkAqua,
    ]) == NSAppearance.Name.darkAqua
}

private func agentIconSource(_ id: String) -> NSImage? {
    // 有桌面 App 的来源用应用图标；CLI 用官方标志。
    // claude 指 Claude Code，用其 Clawd 标志而非 Claude 桌面版图标，两者是不同产品。
    let bundleIds = ["zcode": "dev.zcode.app"]
    if let bundleId = bundleIds[id], let url = NSWorkspace.shared.urlForApplication(withBundleIdentifier: bundleId) {
        return NSWorkspace.shared.icon(forFile: url.path)
    }
    // 官方标志收进 agent-icons 随本 App 打包：claude.svg 取自 Claude Code VS Code 扩展的 clawd.svg；
    // codex.png、agy.png 分别由 ChatGPT.app 的 icon-codex-dark-color.png 和 Antigravity.app 图标抠掉瓦片得到，
    // 不直接用应用图标，避免出现白色方框；kimi.svg 按官方 favicon 几何重绘，避免 64px 位图放大发糊；
    // opencode.png 取自 opencode GitHub 组织头像的官方六边形标志，亮度键控成黑 glyph 透明底（原黑底白标）。
    let official: [String: (ext: String, fallbackPath: String)] = [
        "pi": ("svg", "native/agent-icons/pi.svg"),
        "claude": ("svg", "native/agent-icons/claude.svg"),
        "kimi": ("svg", "native/agent-icons/kimi.svg"),
        "codex": ("png", "native/agent-icons/codex.png"),
        "agy": ("png", "native/agent-icons/agy.png"),
        "opencode": ("png", "native/agent-icons/opencode.png"),
    ]
    guard let entry = official[id] else { return nil }
    var urls = [Bundle.main.url(forResource: id, withExtension: entry.ext)].compactMap { $0 }
    if let root = Bundle.main.object(forInfoDictionaryKey: "SessionManagerRoot") as? String {
        urls.append(URL(fileURLWithPath: root + "/" + entry.fallbackPath))
    }
    for url in urls where FileManager.default.fileExists(atPath: url.path) {
        if let icon = NSImage(contentsOf: url) { return icon }
    }
    return nil
}

@MainActor
private func renderAgentIcon(_ id: String, size: NSSize, dark: Bool) -> NSImage {
    let loaded = agentIconSource(id)
    if let source = loaded {
        return IconPipeline.fitted(source, id: id, size: size, dark: dark)
    }
    // 缺失时画品牌色字符兜底；providerStyles 与 providerReportColor 同源，同一来源同一色。
    let (glyph, color) = providerStyles[id].map { ($0.glyph, $0.color) } ?? ("?", NSColor.systemGray)
    return NSImage(size: size, flipped: false) { rect in
        color.setFill()
        NSBezierPath(roundedRect: rect, xRadius: size.width / 4, yRadius: size.height / 4).fill()
        let text = NSAttributedString(string: glyph, attributes: [
            .font: NSFont.systemFont(ofSize: size.height * 0.7, weight: .semibold),
            .foregroundColor: NSColor.white,
        ])
        let bounds = text.boundingRect(with: size, options: [.usesLineFragmentOrigin])
        text.draw(at: NSPoint(x: (size.width - bounds.width) / 2, y: (size.height - bounds.height) / 2))
        return true
    }
}
