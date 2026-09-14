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

    // A broad, friendly agent silhouette remains legible at Dock and Finder sizes.
    bodyBottom.setFill()
    squircle(NSRect(x: 470, y: 690, width: 44, height: 94), 22).fill()
    NSBezierPath(ovalIn: NSRect(x: 454, y: 762, width: 76, height: 76)).fill()
    squircle(NSRect(x: 190, y: 408, width: 72, height: 152), 36).fill()
    squircle(NSRect(x: 734, y: 408, width: 72, height: 152), 36).fill()
    let head = squircle(NSRect(x: 236, y: 250, width: 524, height: 460), 144)
    NSGradient(starting: bodyTop, ending: bodyBottom)!.draw(in: head, angle: 270)
    let face = squircle(NSRect(x: 294, y: 340, width: 408, height: 272), 90)
    (dark ? NSColor(srgbRed: 0.17, green: 0.19, blue: 0.23, alpha: 1)
          : NSColor(srgbRed: 0.94, green: 0.97, blue: 1, alpha: 1)).setFill()
    face.fill()
    (dark ? NSColor.white : bodyBottom).setFill()
    squircle(NSRect(x: 372, y: 432, width: 48, height: 100), 24).fill()
    squircle(NSRect(x: 576, y: 432, width: 48, height: 100), 24).fill()

    // Notification badge overlaps the upper-right corner of the agent, not the tile.
    (dark ? NSColor(srgbRed: 0.20, green: 0.20, blue: 0.23, alpha: 1)
          : NSColor(srgbRed: 0.96, green: 0.96, blue: 0.98, alpha: 1)).setFill()
    NSBezierPath(ovalIn: NSRect(x: 641, y: 601, width: 218, height: 218)).fill()
    NSGradient(starting: NSColor(srgbRed: 1, green: 0.66, blue: 0.20, alpha: 1),
               ending: NSColor(srgbRed: 1, green: 0.43, blue: 0.06, alpha: 1))!
        .draw(in: NSBezierPath(ovalIn: NSRect(x: 660, y: 620, width: 180, height: 180)), angle: 270)

    NSGraphicsContext.restoreGraphicsState()
    return bitmap.representation(using: .png, properties: [:])!
}
for size in [16, 32, 128, 256, 512] {
    try render(size).write(to: output.appendingPathComponent("icon_\(size)x\(size).png"))
    try render(size * 2).write(to: output.appendingPathComponent("icon_\(size)x\(size)@2x.png"))
}
