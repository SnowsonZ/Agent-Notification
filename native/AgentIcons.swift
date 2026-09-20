import AppKit
import Foundation

private var agentIconCache: [String: NSImage] = [:]

// 统一来源标志：每个标志先按透明边界裁掉自带留白，再等比缩进目标方框居中，
// 各家标志在列表和入口里占的框一致；按目标点数光栅化（Retina 下 2x），不做二次放大。
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

// pi 官方 svg 自带 prefers-color-scheme 暗色白字变体，但 NSImage 光栅化不执行媒体
// 查询，暗色下仍是黑方块、黑底不可见。这里像素级翻转亮度得到官方本意的白字
// （与 opencode 亮度键控同思路：对纯色 glyph 保 alpha、翻 RGB）。
private func luminanceFlipped(_ image: NSImage) -> NSImage {
    let px = 256
    guard let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: px, pixelsHigh: px,
                                     bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true,
                                     isPlanar: false, colorSpaceName: .deviceRGB,
                                     bytesPerRow: 0, bitsPerPixel: 0),
        let data = rep.bitmapData else { return image }
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    image.draw(in: NSRect(x: 0, y: 0, width: px, height: px), from: .zero, operation: .sourceOver, fraction: 1)
    NSGraphicsContext.restoreGraphicsState()
    let rowBytes = rep.bytesPerRow
    for y in 0..<px {
        for x in 0..<px {
            let o = y * rowBytes + x * 4
            if data[o + 3] > 24 {
                data[o] = 255 - data[o]
                data[o + 1] = 255 - data[o + 1]
                data[o + 2] = 255 - data[o + 2]
            }
        }
    }
    rep.size = image.size
    let flipped = NSImage(size: image.size)
    flipped.addRepresentation(rep)
    return flipped
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

// 在 256px 画布上光栅化后扫描 alpha，得到标志实际占据的区域（源图坐标）。
private func agentIconContentRect(_ image: NSImage) -> NSRect {
    let full = NSRect(origin: .zero, size: image.size)
    let px = 256
    guard image.size.width > 0, image.size.height > 0,
          let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: px, pixelsHigh: px, bitsPerSample: 8,
                                     samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                                     colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0),
          let data = rep.bitmapData else { return full }
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    image.draw(in: NSRect(x: 0, y: 0, width: px, height: px), from: full, operation: .sourceOver, fraction: 1)
    NSGraphicsContext.restoreGraphicsState()
    var minX = px, minY = px, maxX = -1, maxY = -1
    let rowBytes = rep.bytesPerRow
    for y in 0..<px {
        for x in 0..<px where data[y * rowBytes + x * 4 + 3] > 24 {
            minX = min(minX, x); maxX = max(maxX, x); minY = min(minY, y); maxY = max(maxY, y)
        }
    }
    guard maxX >= minX, maxY >= minY else { return full }
    // 位图 y 向下，NSImage 坐标 y 向上。
    let sx = image.size.width / CGFloat(px), sy = image.size.height / CGFloat(px)
    return NSRect(x: CGFloat(minX) * sx, y: CGFloat(px - 1 - maxY) * sy,
                  width: CGFloat(maxX - minX + 1) * sx, height: CGFloat(maxY - minY + 1) * sy)
}

private func renderAgentIcon(_ id: String, size: NSSize, dark: Bool) -> NSImage {
    var loaded = agentIconSource(id)
    if dark, id == "pi", let original = loaded {
        loaded = luminanceFlipped(original)
    }
    if let source = loaded {
        let content = agentIconContentRect(source)
        // 方框内再按视觉分量微调：横宽形的 Kimi 本就矮一截保持满框，其余标志统一收一档，
        // 实心方块的 Pi 和撑满方框的 Antigravity 再多收一点。
        let emphasis: CGFloat = ["kimi": 1, "pi": 0.68, "agy": 0.78][id] ?? 0.88
        let scale = min(size.width / max(content.width, 1), size.height / max(content.height, 1)) * emphasis
        let fitted = NSSize(width: content.width * scale, height: content.height * scale)
        // 用绘制闭包而非 lockFocus：按实际输出的缩放因子按需绘制，Retina 下矢量与高清位图保持清晰。
        return NSImage(size: size, flipped: false) { _ in
            NSGraphicsContext.current?.imageInterpolation = .high
            source.draw(in: NSRect(x: (size.width - fitted.width) / 2, y: (size.height - fitted.height) / 2,
                                   width: fitted.width, height: fitted.height),
                        from: content, operation: .sourceOver, fraction: 1)
            return true
        }
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
