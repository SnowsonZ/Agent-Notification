import AppKit

// 官方图标处理管线（主 App 与组件共用，2026-09-24 组件落地官方图标时抽取）：
// 按透明边界裁掉自带留白、再缩进目标方框；NSImage 光栅化不执行 SVG 媒体查询
// （pi 的暗色白字变体不生效），亮度翻转得到官方本意的暗色形态；opencode 官方
// 标志是黑 glyph，深色底同样翻转。
@MainActor
enum IconPipeline {
    // 方框内按视觉分量微调：横宽形的 Kimi 保持满框，实心方块的 Pi 和
    // 撑满方框的 Antigravity 再多收一点，其余统一收一档。
    static func emphasis(_ id: String) -> CGFloat {
        ["kimi": 1.0, "pi": 0.68, "agy": 0.78][id] ?? 0.88
    }

    static func needsDarkVariant(_ id: String, dark: Bool) -> Bool {
        dark && (id == "pi" || id == "opencode")
    }

    static func luminanceFlipped(_ image: NSImage) -> NSImage {
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
                let offset = y * rowBytes + x * 4
                if data[offset + 3] > 24 {
                    data[offset] = 255 - data[offset]
                    data[offset + 1] = 255 - data[offset + 1]
                    data[offset + 2] = 255 - data[offset + 2]
                }
            }
        }
        rep.size = image.size
        let flipped = NSImage(size: image.size)
        flipped.addRepresentation(rep)
        return flipped
    }

    // 在 256px 画布上光栅化后扫描 alpha，得到标志实际占据的区域（源图坐标）。
    static func contentRect(_ image: NSImage) -> NSRect {
        let full = NSRect(origin: .zero, size: image.size)
        let px = 256
        guard image.size.width > 0, image.size.height > 0,
              let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: px, pixelsHigh: px, bitsPerSample: 8,
                                         samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
                                         bytesPerRow: 0, bitsPerPixel: 0),
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

    // 裁边 + 缩进方框的最终形态；调用方负责暗色变体与来源资源加载。
    static func fitted(_ source: NSImage, id: String, size: NSSize, dark: Bool) -> NSImage {
        var loaded = source
        if needsDarkVariant(id, dark: dark) { loaded = luminanceFlipped(loaded) }
        let content = contentRect(loaded)
        let scale = min(size.width / max(content.width, 1), size.height / max(content.height, 1)) * emphasis(id)
        let fittedSize = NSSize(width: content.width * scale, height: content.height * scale)
        let result = NSImage(size: size, flipped: false) { _ in
            NSGraphicsContext.current?.imageInterpolation = .high
            loaded.draw(in: NSRect(x: (size.width - fittedSize.width) / 2, y: (size.height - fittedSize.height) / 2,
                                   width: fittedSize.width, height: fittedSize.height),
                        from: content, operation: .sourceOver, fraction: 1)
            return true
        }
        return result
    }
}
