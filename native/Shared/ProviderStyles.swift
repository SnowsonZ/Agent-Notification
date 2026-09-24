import AppKit
import SwiftUI

// 来源注册表：展示名、品牌色与图标兜底字形一处维护，与 Python 侧 scripts/providers.py 对齐；
// 改动任一侧时同步检查另一侧。统计口径（热力分级阈值等）只在 Python 实现，
// 界面直接使用载荷数值，不在 Swift 留第二份实现（旧副本曾与 Python 漂移）。
typealias ProviderStyle = (name: String, color: NSColor, glyph: String)

let providerStyles: [String: ProviderStyle] = [
    "zcode": ("Zcode", NSColor(srgbRed: 0.0, green: 0.478, blue: 1.0, alpha: 1), "Z"),  // #007AFF
    "codex": ("Codex", NSColor(srgbRed: 0.063, green: 0.639, blue: 0.498, alpha: 1), ">_"),  // #10A37F
    "claude": ("Claude", NSColor(srgbRed: 0.851, green: 0.467, blue: 0.341, alpha: 1), "C"),  // #D97757
    "pi": ("Pi", NSColor(srgbRed: 0.392, green: 0.824, blue: 1.0, alpha: 1), "π"),  // #64D2FF
    "kimi": ("Kimi", NSColor(srgbRed: 0.749, green: 0.353, blue: 0.949, alpha: 1), "K"),  // #BF5AF2
    "agy": ("Antigravity CLI", NSColor(srgbRed: 0.259, green: 0.522, blue: 0.957, alpha: 1), "A"),  // #4285F4 Google 蓝
    "opencode": ("OpenCode", NSColor(srgbRed: 0.961, green: 0.620, blue: 0.043, alpha: 1), "OC"),  // #F59E0B 暂定
]

func providerName(_ provider: String) -> String {
    providerStyles[provider]?.name ?? provider
}

func providerReportColor(_ provider: String) -> Color {
    providerStyles[provider].map { Color(nsColor: $0.color) } ?? Color.secondary
}

func stateName(_ state: String) -> String {
    ["running": "运行中", "waiting": "等待输入", "idle": "本轮已结束", "failed": "发生错误",
     "interrupted": "已中断", "closed": "已退出", "unknown": "状态待确认"][state] ?? state
}

// 需要用户关注的统一橙：未读点、未读文字、未读行底色、托盘与 Dock 角标同一色。
// 状态点（stateDotColor）保留语义化系统色，不走这里。
extension Color {
    static let attention = Color(nsColor: .systemOrange)
}

func stateDotColor(_ state: String) -> Color {
    switch state {
    case "running": return .green
    case "waiting": return .orange
    case "failed": return .red
    default: return Color.secondary
    }
}

// 模型名 → 家族图标 id（日报模型榜与组件共用）：前缀映射，未匹配返回 nil（调用方退化字牌）。
func modelIconId(_ modelName: String) -> String? {
    let name = modelName.lowercased()
    if name.contains("claude") { return "claude" }
    if name.contains("glm") { return "zcode" }
    if name.contains("gpt") || name.contains("codex")
        || name.range(of: "^o[134]", options: .regularExpression) != nil { return "codex" }
    if name.contains("kimi") { return "kimi" }
    if name.contains("gemini") { return "agy" }
    return nil
}

// 三类 token 色（日报与组件同一份，2026-09-24 重设计）：
// 输入=#0A84FF、缓存=#64D2FF、输出=#FF9F0A；不再随主题强调色漂移。
enum TokenClass { case input, cache, output }
func tokenClassColor(_ cls: TokenClass) -> Color {
    switch cls {
    case .input: return Color(red: 0.039, green: 0.518, blue: 1.0)
    case .cache: return Color(red: 0.392, green: 0.824, blue: 1.0)
    case .output: return Color(red: 1.0, green: 0.624, blue: 0.039)
    }
}
