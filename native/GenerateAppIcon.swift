// Code-native vector artwork rendered into a standard macOS iconset.
// Usage: generate-app-icon <iconset-dir> [light|dark]
import AppKit
import Foundation

let output = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
let dark = CommandLine.arguments.count > 2 && CommandLine.arguments[2] == "dark"
try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)

func squircle(_ rect: NSRect, _ radius: CGFloat) -> NSBezierPath {
    NSBezierPath(roundedRect: rect, xRadius: radius, yRadius: radius)
}

func render(_ pixels: Int) -> Data {
    let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: pixels, pixelsHigh: pixels,
        bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
        colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
    let transform = AffineTransform(scale: CGFloat(pixels) / 1024)
    (transform as NSAffineTransform).concat()

    // Tile: light and dark keep the same geometry, only the palette switches.
    let tile = squircle(NSRect(x: 64, y: 64, width: 896, height: 896), 194)
    if dark {
        NSGradient(starting: NSColor(srgbRed: 0.25, green: 0.25, blue: 0.28, alpha: 1),
                   ending: NSColor(srgbRed: 0.11, green: 0.11, blue: 0.13, alpha: 1))!.draw(in: tile, angle: 270)
        NSColor.white.withAlphaComponent(0.10).setStroke()
    } else {
        NSGradient(starting: NSColor(srgbRed: 0.99, green: 0.99, blue: 1.00, alpha: 1),
                   ending: NSColor(srgbRed: 0.88, green: 0.89, blue: 0.92, alpha: 1))!.draw(in: tile, angle: 270)
        NSColor.black.withAlphaComponent(0.10).setStroke()
    }
    tile.lineWidth = 6
    tile.stroke()

    // Agent body: graphite on light, silver on dark.
    let bodyTop = dark ? NSColor(srgbRed: 0.91, green: 0.91, blue: 0.94, alpha: 1)
                       : NSColor(srgbRed: 0.39, green: 0.43, blue: 0.50, alpha: 1)
    let bodyBottom = dark ? NSColor(srgbRed: 0.69, green: 0.70, blue: 0.75, alpha: 1)
                          : NSColor(srgbRed: 0.22, green: 0.25, blue: 0.31, alpha: 1)

    // Antenna ball and stem above the head.
    bodyBottom.setFill()
    NSBezierPath(ovalIn: NSRect(x: 460, y: 804, width: 72, height: 72)).fill()
    squircle(NSRect(x: 480, y: 676, width: 32, height: 150), 16).fill()

    // Head.
    let head = squircle(NSRect(x: 272, y: 296, width: 440, height: 400), 120)
    NSGradient(starting: bodyTop, ending: bodyBottom)!.draw(in: head, angle: 270)

    // Eyes.
    (dark ? NSColor(srgbRed: 0.16, green: 0.16, blue: 0.18, alpha: 1) : NSColor.white).setFill()
    squircle(NSRect(x: 342, y: 430, width: 56, height: 150), 28).fill()
    squircle(NSRect(x: 586, y: 430, width: 56, height: 150), 28).fill()

    // Orange notification dot pinned to the agent's top-right, white ring for separation.
    NSColor.white.setFill()
    NSBezierPath(ovalIn: NSRect(x: 712 - 132, y: 696 - 132, width: 264, height: 264)).fill()
    NSGradient(starting: NSColor(srgbRed: 1.00, green: 0.66, blue: 0.33, alpha: 1),
               ending: NSColor(srgbRed: 0.96, green: 0.45, blue: 0.05, alpha: 1))!
        .draw(in: NSBezierPath(ovalIn: NSRect(x: 712 - 104, y: 696 - 104, width: 208, height: 208)), angle: 270)

    NSGraphicsContext.restoreGraphicsState()
    return bitmap.representation(using: .png, properties: [:])!
}
for size in [16, 32, 128, 256, 512] {
    try render(size).write(to: output.appendingPathComponent("icon_\(size)x\(size).png"))
    try render(size * 2).write(to: output.appendingPathComponent("icon_\(size)x\(size)@2x.png"))
}
