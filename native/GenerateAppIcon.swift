// Code-native vector artwork rendered into a standard macOS iconset.
import AppKit
import Foundation

let output = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)

func render(_ pixels: Int) -> Data {
    let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: pixels, pixelsHigh: pixels,
        bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
        colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
    let transform = AffineTransform(scale: CGFloat(pixels) / 1024)
    (transform as NSAffineTransform).concat()
    let tile = NSBezierPath(roundedRect: NSRect(x: 64, y: 64, width: 896, height: 896), xRadius: 194, yRadius: 194)
    let gradient = NSGradient(starting: NSColor(srgbRed: 0.07, green: 0.18, blue: 0.34, alpha: 1),
                              ending: NSColor(srgbRed: 0.08, green: 0.54, blue: 0.52, alpha: 1))!
    gradient.draw(in: tile, angle: 55)
    NSColor.white.withAlphaComponent(0.36).setFill()
    NSBezierPath(roundedRect: NSRect(x: 298, y: 610, width: 428, height: 108), xRadius: 28, yRadius: 28).fill()
    NSColor.white.withAlphaComponent(0.7).setFill()
    NSBezierPath(roundedRect: NSRect(x: 260, y: 474, width: 504, height: 108), xRadius: 28, yRadius: 28).fill()
    NSColor.white.setFill()
    NSBezierPath(roundedRect: NSRect(x: 222, y: 274, width: 580, height: 174), xRadius: 42, yRadius: 42).fill()
    NSColor(srgbRed: 0.10, green: 0.40, blue: 0.46, alpha: 1).setFill()
    NSBezierPath(roundedRect: NSRect(x: 382, y: 378, width: 260, height: 110), xRadius: 34, yRadius: 34).fill()
    NSColor(srgbRed: 1.0, green: 0.76, blue: 0.28, alpha: 1).setFill()
    NSBezierPath(ovalIn: NSRect(x: 698, y: 656, width: 156, height: 156)).fill()
    NSColor.white.setStroke()
    let check = NSBezierPath()
    check.move(to: NSPoint(x: 737, y: 735)); check.line(to: NSPoint(x: 765, y: 707)); check.line(to: NSPoint(x: 813, y: 766))
    check.lineWidth = 15; check.lineCapStyle = .round; check.lineJoinStyle = .round; check.stroke()
    NSGraphicsContext.restoreGraphicsState()
    return bitmap.representation(using: .png, properties: [:])!
}
for size in [16, 32, 128, 256, 512] {
    try render(size).write(to: output.appendingPathComponent("icon_\(size)x\(size).png"))
    try render(size * 2).write(to: output.appendingPathComponent("icon_\(size)x\(size)@2x.png"))
}
