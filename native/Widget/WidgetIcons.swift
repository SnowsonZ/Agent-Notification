import AppKit
import SwiftUI

// 组件侧官方来源图标（2026-09-24 落地 desktop-widgets.md §4 的图标承诺）：
// 从 appex Resources 读取（构建脚本从 native/agent-icons 复制；zcode.png 由构建
// 脚本从已安装 Zcode.app 图标现场提取，缺席时退化品牌色字牌）。组件沙盒读不到
// 其它 App 与仓库路径，只能用打进包里的资源；裁边/暗色变体走共享 IconPipeline。
@MainActor
enum WidgetAgentIconRenderer {
    static var cache: [String: NSImage] = [:]

    static func image(_ id: String, size: CGFloat, dark: Bool) -> NSImage? {
        let key = "\(id)@\(size)@\(dark ? "dark" : "light")"
        if let cached = cache[key] { return cached }
        guard let source = sourceImage(id, dark: dark) else { return nil }
        let image = IconPipeline.fitted(source, id: id, size: NSSize(width: size, height: size), dark: dark)
        cache[key] = image
        return image
    }

    private static func sourceImage(_ id: String, dark: Bool) -> NSImage? {
        for ext in ["png", "svg"] {
            if let url = Bundle.main.url(forResource: id, withExtension: ext),
               let image = NSImage(contentsOf: url) {
                return image
            }
        }
        return nil
    }
}

// 来源官方图标；未打进包的来源退化为品牌色圆角字牌。
struct AgentWidgetIcon: View {
    let id: String
    var size: CGFloat = 15
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        if let image = WidgetAgentIconRenderer.image(id, size: size, dark: colorScheme == .dark) {
            Image(nsImage: image)
                .resizable()
                .frame(width: size, height: size)
        } else {
            glyphChip
        }
    }

    private var glyphChip: some View {
        RoundedRectangle(cornerRadius: size * 0.28)
            .fill(providerReportColor(id))
            .frame(width: size, height: size)
            .overlay {
                Text(providerStyles[id]?.glyph ?? "·")
                    .font(.system(size: size * 0.5, weight: .bold))
                    .foregroundStyle(.white)
            }
    }
}

// 模型行图标：按名称前缀映射家族官方图标，未匹配退化为灰底字牌。
struct ModelWidgetIcon: View {
    let name: String
    var size: CGFloat = 15
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        if let family = modelIconId(name), let image = WidgetAgentIconRenderer.image(family, size: size, dark: colorScheme == .dark) {
            Image(nsImage: image)
                .resizable()
                .frame(width: size, height: size)
        } else {
            Text(String(name.prefix(2)))
                .font(.system(size: size * 0.5, weight: .bold))
                .foregroundStyle(.secondary)
                .frame(width: size, height: size)
                .background(Color.primary.opacity(0.1), in: RoundedRectangle(cornerRadius: size * 0.28))
        }
    }
}
