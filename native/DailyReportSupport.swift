import SwiftUI

func heatColor(_ level: Int) -> Color {
    switch level {
    case 1: return Color.accentColor.opacity(0.22)
    case 2: return Color.accentColor.opacity(0.45)
    case 3: return Color.accentColor.opacity(0.70)
    case 4: return Color.accentColor
    default: return Color.primary.opacity(0.07)
    }
}

let reportDayFormatter: DateFormatter = {
    let formatter = DateFormatter()
    formatter.dateFormat = "yyyy-MM-dd"
    formatter.locale = Locale(identifier: "en_US_POSIX")
    return formatter
}()
func reportDate(_ text: String) -> Date? { reportDayFormatter.date(from: text) }
func reportDayDisplay(_ text: String) -> String {
    guard let date = reportDate(text) else { return text }
    let components = Calendar.current.dateComponents([.month, .day, .weekday], from: date)
    let weekdays = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"]
    return "\(components.month ?? 0)月\(components.day ?? 0)日 \(weekdays[(components.weekday ?? 1) - 1])"
}
func reportShortDay(_ text: String) -> String {
    guard let date = reportDate(text) else { return text }
    let components = Calendar.current.dateComponents([.month, .day], from: date)
    return "\(components.month ?? 0)/\(components.day ?? 0)"
}
func trendDays(_ overview: ReportOverview, endingAt current: Date) -> [OverviewDay] {
    let prior = overview.days.filter { day in
        guard let date = reportDate(day.date) else { return false }
        return date <= current
    }
    return prior.isEmpty ? [] : Array(prior.suffix(7))
}
func usageLine(input: Int, cache: Int, output: Int) -> String {
    "输入 \(tokenText(input)) · 缓存 \(tokenText(cache)) · 输出 \(tokenText(output))"
}
func usageLine(_ usage: SourceUsage) -> String {
    usageLine(input: usage.inputTokens, cache: usage.cacheTokens, output: usage.outputTokens)
}
func usageLine(_ task: ReportTask) -> String {
    if task.fidelity == "unavailable" { return "无 token 记录，不计入统计" }
    return usageLine(input: task.inputTokens, cache: task.cacheTokens, output: task.outputTokens)
}
func reportClock(_ stamp: Double) -> String {
    let components = Calendar.current.dateComponents([.hour, .minute], from: Date(timeIntervalSince1970: stamp))
    return String(format: "%02d:%02d", components.hour ?? 0, components.minute ?? 0)
}
func reportTimeRange(_ task: ReportTask) -> String {
    let first = reportClock(task.firstAt)
    let last = reportClock(task.lastAt)
    return first == last ? first : "\(first)–\(last)"
}
func heatWeekColumns(_ days: [OverviewDay], calendar: Calendar = .current) -> [[OverviewDay?]] {
    guard let first = days.first, let startDate = reportDate(first.date) else { return [] }
    // GitHub 布局：列=周、行=周一至周日；首列按周几留空补位。
    let leading = (calendar.component(.weekday, from: startDate) + 5) % 7
    var cells: [OverviewDay?] = Array(repeating: nil, count: leading) + days.map { Optional($0) }
    while cells.count % 7 != 0 { cells.append(nil) }
    return stride(from: 0, to: cells.count, by: 7).map { Array(cells[$0..<min($0 + 7, cells.count)]) }
}
func heatMonthMarks(_ columns: [[OverviewDay?]], calendar: Calendar = .current) -> [String?] {
    var result: [String?] = []
    var lastMonth = -1
    for column in columns {
        guard let day = column.compactMap({ $0 }).first, let date = reportDate(day.date) else {
            result.append(nil)
            continue
        }
        let month = calendar.component(.month, from: date)
        if month != lastMonth {
            result.append("\(month)月")
            lastMonth = month
        } else {
            result.append(nil)
        }
    }
    return result
}

enum TokenClass { case input, cache, output }
func tokenClassColor(_ cls: TokenClass) -> Color {
    switch cls {
    // 缓存是读取回放、通常占九成以上：用中性灰压住，让真正的输入/输出两端跳出来。
    // 输入=主题蓝，输出=青绿（systemTeal），与来源色系不冲突，明暗模式各自自适应。
    case .input: return Color.accentColor
    case .cache: return Color.primary.opacity(0.13)
    case .output: return Color(nsColor: .systemTeal)
    }
}
// 日报自定义悬浮：多行卡片，鼠标进入即显（不走系统 tooltip 的长延迟通道）。
// 黑色 80% 不透明底、白色文字（用户指定），尺寸随内容自适应、文本不折行。
struct HoverTipCard: View {
    let title: String
    let lines: [String]
    private var tipTitleStyle: AnyShapeStyle {
        if #available(macOS 26, *) { return AnyShapeStyle(.primary) }
        return AnyShapeStyle(Color.white)
    }
    private var tipLineStyle: AnyShapeStyle {
        if #available(macOS 26, *) { return AnyShapeStyle(.secondary) }
        return AnyShapeStyle(Color.white.opacity(0.85))
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(tipTitleStyle)
                .lineLimit(1)
            ForEach(lines.indices, id: \.self) { index in
                Text(lines[index])
                    .font(.system(size: 11))
                    .foregroundStyle(tipLineStyle)
                    .lineLimit(1)
            }
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .modifier(HoverTipSurface())
        .fixedSize()
    }
}

// 悬停态统一承载：本仓库用裸 swiftc 编译，@State 宏插件不可用，@StateObject 持此模型替代 @State。
final class HoverModel: ObservableObject { @Published var hovering = false }

// 悬浮卡是典型浮层：macOS 26 起用玻璃承托并沿用系统前景色；旧系统保留深色实底。
struct HoverTipSurface: ViewModifier {
    func body(content: Content) -> some View {
        if #available(macOS 26, *) {
            content
                .foregroundStyle(.primary)
                .glassEffect(.regular, in: RoundedRectangle(cornerRadius: 10))
                .shadow(color: .black.opacity(0.12), radius: 10, y: 4)
        } else {
            content
                .background(Color.black.opacity(0.8), in: RoundedRectangle(cornerRadius: 8))
                .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(Color.white.opacity(0.12)))
                .shadow(color: .black.opacity(0.25), radius: 8, y: 3)
        }
    }
}

/// 悬浮内容统一渲染在日报根视图的覆盖层上，避免被相邻单元格或后续卡片遮挡。
/// （SwiftUI zIndex 只在同层兄弟间生效，单元格内悬浮无法盖过其他分区的卡片。）
@MainActor
final class HoverTipCenter: ObservableObject {
    static let shared = HoverTipCenter()
    static let space = "dailyReportRoot"
    struct Tip: Equatable {
        let title: String
        let lines: [String]
    }
    @Published var active: Tip?
    @Published var activeFrame: CGRect = .zero
    private var activeId: UUID?
    private var frames: [UUID: CGRect] = [:]
    func move(_ id: UUID, frame: CGRect) {
        frames[id] = frame
        if activeId == id { activeFrame = frame }  // 悬浮期间内容滚动时卡片跟随。
    }
    func hover(_ id: UUID, inside: Bool, tip: Tip) {
        guard inside else {
            if activeId == id { active = nil }
            return
        }
        activeId = id
        active = tip
        activeFrame = frames[id] ?? activeFrame
    }
}
// 悬浮身份必须按视图身份稳定：帧登记只发生在 onAppear/帧变化，若键随父视图重建换新
// （悬浮本身就会触发 HoverTipCenter 发布→日报根视图重算），新键永远查不到帧，
// hover() 回退 activeFrame 就继承上一次悬浮位置。@StateObject 每个视图身份只建一次。
final class HoverTipIdentity: ObservableObject { let id = UUID() }
struct DailyHoverTip: ViewModifier {
    let title: String
    let lines: [String]
    @StateObject private var identity = HoverTipIdentity()
    @ObservedObject private var center = HoverTipCenter.shared
    func body(content: Content) -> some View {
        content
            .background(
                GeometryReader { geo in
                    Color.clear
                        .onAppear { center.move(identity.id, frame: geo.frame(in: .named(HoverTipCenter.space))) }
                        .onChange(of: geo.frame(in: .named(HoverTipCenter.space))) { _, newFrame in
                            center.move(identity.id, frame: newFrame)
                        }
                }
            )
            .onHover { inside in
                center.hover(identity.id, inside: inside, tip: HoverTipCenter.Tip(title: title, lines: lines))
            }
    }
}
extension View {
    func dailyHoverTip(title: String, lines: [String]) -> some View {
        modifier(DailyHoverTip(title: title, lines: lines))
    }
}
struct HoverTipModifier<Tip: View>: ViewModifier {
    @ViewBuilder let tip: () -> Tip
    @StateObject private var model = HoverModel()
    func body(content: Content) -> some View {
        content
            .overlay(alignment: .topLeading) {
                if model.hovering {
                    tip().allowsHitTesting(false).transition(.opacity).zIndex(10)
                }
            }
            .zIndex(model.hovering ? 999 : 0)
            .onHover { entering in
                withAnimation(.easeIn(duration: 0.06)) { model.hovering = entering }
            }
    }
}
extension View {
    func hoverTip<Tip: View>(@ViewBuilder tip: @escaping () -> Tip) -> some View {
        modifier(HoverTipModifier(tip: tip))
    }
}
// 图表 hover 动效：色块轻微放大/提亮，行式条目底色浮现；与悬浮提示互不影响。
struct ChartHoverEffect: ViewModifier {
    let scale: CGFloat
    let highlight: Bool
    @StateObject private var model = HoverModel()
    func body(content: Content) -> some View {
        content
            .scaleEffect(model.hovering ? scale : 1)
            .brightness(model.hovering && !highlight ? 0.08 : 0)
            .background {
                if highlight {
                    RoundedRectangle(cornerRadius: 6)
                        .fill(Color.primary.opacity(model.hovering ? 0.05 : 0))
                        .padding(-4)
                }
            }
            .animation(.easeOut(duration: 0.15), value: model.hovering)
            .onHover { model.hovering = $0 }
    }
}
extension View {
    /// 色块类：放大 + 提亮。
    func chartHover(scale: CGFloat = 1.06) -> some View {
        modifier(ChartHoverEffect(scale: scale, highlight: false))
    }
    /// 行式条目：底色浮现，不缩放。
    func rowHover() -> some View {
        modifier(ChartHoverEffect(scale: 1, highlight: true))
    }
}
// 金额悬浮行（usage-cost.md §7）：`金额：¥12.34（输入 ¥a · 缓存 ¥b · 输出 ¥c）` +
// 未定价行；金额无值（全未定价）时不加行。
@MainActor
func costTipLine(_ cost: WidgetSnapshotMoney?, model: DailyReportModel) -> [String] {
    guard let cost else { return [] }
    let rate = model.fxRate
    let currency = model.currency
    let amount = { (bucket: [String: Double]) in
        convertAmount(usd: bucket["USD"] ?? 0, cny: bucket["CNY"] ?? 0, to: currency, rate: rate)
    }
    let input = amount(cost.input), cache = amount(cost.cache), output = amount(cost.output)
    let total = input + cache + output + convertAmount(
        usd: cost.nativeFallback?["USD"] ?? 0, cny: cost.nativeFallback?["CNY"] ?? 0,
        to: currency, rate: rate
    )
    guard total > 0 else { return [] }
    var lines = [
        "金额：\(moneyText(total, currency: currency))（输入 \(moneyText(input, currency: currency)) · "
            + "缓存 \(moneyText(cache, currency: currency)) · 输出 \(moneyText(output, currency: currency))）"
    ]
    if cost.unpricedTokens > 0 {
        lines.append("未定价：\(tokenText(cost.unpricedTokens)) tokens")
    }
    return lines
}

func classTipLines(input: Int, cache: Int, output: Int) -> [String] {
    // 展示顺序遵循用户模板：缓存 / 输入 / 输出。
    ["缓存：\(tokenText(cache))", "输入：\(tokenText(input))", "输出：\(tokenText(output))"]
}
func classTipLines(_ usage: SourceUsage) -> [String] {
    classTipLines(input: usage.inputTokens, cache: usage.cacheTokens, output: usage.outputTokens)
}
struct DailyReportCardBackground: ViewModifier {
    func body(content: Content) -> some View {
        content
            .padding(14)
            .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Color.primary.opacity(0.06)))
    }
}
extension View {
    func dailyReportCard() -> some View { modifier(DailyReportCardBackground()) }
}
