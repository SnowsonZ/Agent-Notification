import SwiftUI

struct DailyReportView: View {
    @ObservedObject var model: DailyReportModel
    @ObservedObject private var hoverCenter = HoverTipCenter.shared
    var body: some View {
        NavigationStack(path: $model.path) {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("工作日报").font(.system(size: 24, weight: .bold))
                        Text("数据来自本地会话元数据 · 今日随时实时汇总 · 次日首次查看定稿").font(.subheadline).foregroundStyle(.secondary)
                    }
                    PeriodHeader(model: model)
                    if model.period != .day {
                        // 周 / 月视图（usage-cost.md §7）：数据来自 inbox usage
                        if let payload = model.usageByPeriod[model.period.rawValue] {
                            PeriodNavigator(model: model)
                            PeriodReportView(model: model, payload: payload)
                        } else if let usageError = model.usageError {
                            Text("周期用量读取失败：\(usageError)").font(.caption).foregroundStyle(.red)
                            Button { model.loadUsage(force: true) } label: { Text("重试").font(.caption) }
                                .buttonStyle(InboxActionButtonStyle())
                        } else {
                            Text("正在读取周期用量…").font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    if let error = model.error {
                        HStack(alignment: .top, spacing: 8) {
                            Image(systemName: "exclamationmark.triangle.fill").font(.caption).foregroundStyle(.red)
                            Text(error).font(.caption).foregroundStyle(.red).textSelection(.enabled)
                            Spacer()
                            Button { model.loadOverview() } label: { Text("重试").font(.caption) }
                                .buttonStyle(InboxActionButtonStyle())
                        }
                        .padding(10)
                        .background(Color.red.opacity(0.06), in: RoundedRectangle(cornerRadius: 8))
                    }
                    if model.period == .day, let overview = model.overview {
                        if let today = overview.today {
                            TodayChipsView(model: model, today: today, overview: overview)
                        }
                        HeatmapView(model: model, overview: overview)
                        if let today = overview.today, let current = reportDate(today.date) {
                            WeekTrendView(days: trendDays(overview, endingAt: current),
                                          model: model)
                        }
                        // 总览编排与周/月一致：双栏（来源环形 | 模型）+ Top 项目全宽（2026-09-24）。
                        // 来源/模型/项目数据来自 usage 周（近 7 天窗口），金额伴随在悬浮卡与行内。
                        HStack(alignment: .top, spacing: 14) {
                            WeekSourcesDonutCard(model: model)
                            ModelRankCard(model: model, title: "模型")
                        }
                        .fixedSize(horizontal: false, vertical: true)
                        ProjectRankCard(title: "Top 项目 · 本周",
                                        rows: model.usageByPeriod["week"]?.by?.project ?? [],
                                        rate: model.fxRate)
                    } else if model.loading {
                        HStack(spacing: 10) {
                            ProgressView().controlSize(.small)
                            Text("正在汇总近半年会话…").font(.subheadline).foregroundStyle(.secondary)
                        }.frame(maxWidth: .infinity).padding(.vertical, 40)
                    }
                    Text("口径：token 三类之和参与全部统计——输入=新鲜输入+缓存写入；缓存=读取回放；输出=含 reasoning。取消/出错轮次照计。")
                        .font(.caption2).foregroundStyle(.tertiary)
                    Text("金额为按 API 标价的估算值（非账单）· USD 显示，CNY 官价按汇率 \(String(format: "%.2f", model.fxRate)) 折算。")
                        .font(.caption2).foregroundStyle(.tertiary)
                }
                .padding(16)
            }
            .navigationDestination(for: String.self) { date in
                DayDetailView(model: model, date: date)
            }
        }
        // 悬浮卡片渲染在根层覆盖：不受相邻单元格/分区卡片遮挡影响。
        .overlay(alignment: .topLeading) {
            GeometryReader { proxy in
                if let tip = hoverCenter.active {
                    HoverTipCard(title: tip.title, lines: tip.lines)
                        .fixedSize()
                        .allowsHitTesting(false)
                        .offset(
                            x: min(max(hoverCenter.activeFrame.minX + 14, 8), max(proxy.size.width - 230, 8)),
                            y: min(hoverCenter.activeFrame.maxY + 6, max(proxy.size.height - 120, 8)))
                        .zIndex(9999)
                }
            }
            .allowsHitTesting(false)
        }
        .coordinateSpace(name: HoverTipCenter.space)
        .frame(minWidth: 560, idealWidth: 600, minHeight: 520, idealHeight: 780)
        .background(Color(nsColor: .windowBackgroundColor))
        .onAppear {
            model.loadOverview()
            model.loadUsage(force: false)
            WidgetSnapshotWriter.shared.refreshUsageNow()  // §3：打开日报窗口立即刷新 usage
        }
        .onReceive(NotificationCenter.default.publisher(for: .widgetReportPeriod)) { notification in
            if let period = notification.object as? String {
                // 组件 report URL：切到对应周期的本期（§5）
                if let target = ReportPeriod(rawValue: period), target != model.period {
                    model.period = target
                } else {
                    model.shiftPeriod(0)
                }
            }
        }
    }
}

struct HeatmapView: View {
    @ObservedObject var model: DailyReportModel
    let overview: ReportOverview

    var body: some View {
        let columns = heatWeekColumns(overview.days)
        let marks = heatMonthMarks(columns)
        let todayKey = dailyReportDayKey(Date())
        let activeDays = overview.days.filter { $0.totalTokens > 0 }.count
        let streak = activeStreak(overview.days, todayKey: todayKey)
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                Text("活跃热力 · 近半年").font(.headline)
                Spacer()
                Text("活跃 \(activeDays) 天").font(.caption).foregroundStyle(.secondary).monospacedDigit()
                if streak > 1 {
                    Text("·").font(.caption).foregroundStyle(.tertiary)
                    Text("连续 \(streak) 天").font(.caption).foregroundStyle(Color.accentColor).monospacedDigit()
                }
            }
            if columns.isEmpty {
                Text("还没有可统计的数据").font(.subheadline).foregroundStyle(.secondary)
            } else {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(alignment: .top, spacing: 4) {
                        VStack(alignment: .leading, spacing: 3) {
                            Text("").frame(height: 15)
                            ForEach(["一", "二", "三", "四", "五", "六", "日"], id: \.self) { label in
                                Text(label).font(.system(size: 9)).foregroundStyle(.tertiary)
                                    .frame(width: 13, height: 13, alignment: .trailing)
                            }
                        }
                        VStack(alignment: .leading, spacing: 3) {
                            HStack(spacing: 3) {
                                ForEach(marks.indices, id: \.self) { index in
                                    Text(marks[index] ?? "")
                                        .font(.system(size: 9)).foregroundStyle(.tertiary)
                                        .frame(width: 13, alignment: .leading)
                                }
                            }
                            HStack(alignment: .top, spacing: 3) {
                                ForEach(columns.indices, id: \.self) { column in
                                    VStack(spacing: 3) {
                                        ForEach(columns[column].indices, id: \.self) { row in
                                            if let day = columns[column][row], day.totalTokens > 0 || day.date == todayKey {
                                                // 有记录（或今天）才可点进详情；空白日只是占位。
                                                NavigationLink(value: day.date) {
                                                    RoundedRectangle(cornerRadius: 3)
                                                        .fill(heatColor(day.level))
                                                        .frame(width: 13, height: 13)
                                                        .overlay {
                                                            if day.date == todayKey {
                                                                RoundedRectangle(cornerRadius: 3)
                                                                    .strokeBorder(Color.attention, lineWidth: 1.5)
                                                            }
                                                        }
                                                }
                                                .buttonStyle(.plain)
                                                .chartHover(scale: 1.3)
                                                .dailyHoverTip(title: reportDayDisplay(day.date),
                                                               lines: day.totalTokens > 0
                                                                   ? ["合计：\(tokenText(day.totalTokens))", "\(day.tasks) 个任务"]
                                                                       + costTipLine(day.cost, rate: model.fxRate)
                                                                   : ["暂无记录"])
                                            } else if let day = columns[column][row] {
                                                RoundedRectangle(cornerRadius: 3)
                                                    .fill(heatColor(day.level))
                                                    .frame(width: 13, height: 13)
                                                    .dailyHoverTip(title: reportDayDisplay(day.date), lines: ["无记录"])
                                            } else {
                                                Rectangle().fill(.clear).frame(width: 13, height: 13)
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                HStack(spacing: 4) {
                    Text("少").font(.caption2).foregroundStyle(.tertiary)
                    ForEach(0..<5, id: \.self) { level in
                        RoundedRectangle(cornerRadius: 2).fill(heatColor(level)).frame(width: 10, height: 10)
                    }
                    Text("多").font(.caption2).foregroundStyle(.tertiary)
                    Spacer()
                    Text("点击任意日期查看当日详情").font(.caption2).foregroundStyle(.tertiary)
                }
            }
        }
        .dailyReportCard()
    }
}
// 连续活跃天数：从今天（或今天无记录时从昨天）往前数连续有 token 的日子。
func activeStreak(_ days: [OverviewDay], todayKey: String) -> Int {
    let ordered = days.sorted { $0.date < $1.date }
    guard var index = ordered.lastIndex(where: { $0.date <= todayKey }) else { return 0 }
    if ordered[index].date == todayKey, ordered[index].totalTokens <= 0 { index -= 1 }
    var streak = 0
    var expected = index >= 0 ? reportDate(ordered[index].date) : nil
    while index >= 0, let date = expected, ordered[index].totalTokens > 0,
          reportDate(ordered[index].date) == date {
        streak += 1
        index -= 1
        expected = Calendar.current.date(byAdding: .day, value: -1, to: date)
    }
    return streak
}

// 总览/详情共用的头卡：大数字 + 金额伴随行 + 三类 token 分段条 + 右侧 2×2 数据瓦片。
// 金额是伴随指标：放在 token 正下方、字号小一档（2026-09-24 重设计）。
struct UsageHeroView: View {
    let total: Int
    let input: Int
    let cache: Int
    let output: Int
    let caption: String
    let accent: String?
    var money: Double? = nil  // USD 伴随金额；nil 显示 —
    var delta: (text: String, up: Bool)? = nil  // 较上一期（token 口径），涨红降绿
    let tiles: [(value: String, label: String)]
    var body: some View {
        HStack(alignment: .top, spacing: 18) {
            VStack(alignment: .leading, spacing: 6) {
                Text(tokenText(total))
                    .font(.system(size: 30, weight: .bold)).monospacedDigit()
                Text(usdText(money))
                    .font(.system(size: 15, weight: .semibold)).monospacedDigit()
                    .padding(.top, -2)
                HStack(spacing: 6) {
                    Text(caption).font(.caption).foregroundStyle(.secondary)
                    if let accent {
                        Text("·").foregroundStyle(.tertiary).font(.caption)
                        Text(accent).font(.caption).foregroundStyle(Color.accentColor)
                    }
                    if let delta {
                        Text("·").foregroundStyle(.tertiary).font(.caption)
                        Text((delta.up ? "▲ " : "▼ ") + delta.text)
                            .font(.caption).monospacedDigit()
                            .foregroundStyle(delta.up ? Color(red: 1.0, green: 0.271, blue: 0.227) : Color(red: 0.188, green: 0.820, blue: 0.345))
                    }
                }
                classBar.frame(height: 8).padding(.top, 4)
                HStack(spacing: 10) {
                    classLegend(.input, "输入", input)
                    classLegend(.cache, "缓存", cache)
                    classLegend(.output, "输出", output)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Grid(alignment: .leading, horizontalSpacing: 8, verticalSpacing: 8) {
                ForEach(Array(stride(from: 0, to: tiles.count, by: 2)), id: \.self) { start in
                    GridRow {
                        ForEach(start..<min(start + 2, tiles.count), id: \.self) { index in
                            statTile(tiles[index].value, tiles[index].label)
                        }
                    }
                }
            }
        }
        .dailyReportCard()
    }
    private var classBar: some View {
        let sum = max(total, 1)
        // 三类统一顺序（图例与分段条同序）：输入 / 缓存 / 输出。
        let parts: [(Int, Color)] = [(input, tokenClassColor(.input)),
                                     (cache, tokenClassColor(.cache)),
                                     (output, tokenClassColor(.output))]
        return GeometryReader { proxy in
            HStack(spacing: 1) {
                ForEach(Array(parts.enumerated()), id: \.offset) { _, part in
                    if part.0 > 0 {
                        Rectangle().fill(part.1)
                            .frame(width: max(2, proxy.size.width * Double(part.0) / Double(sum)))
                    }
                }
            }
            .clipShape(RoundedRectangle(cornerRadius: 3))
        }
    }
    private func classLegend(_ cls: TokenClass, _ label: String, _ value: Int) -> some View {
        HStack(spacing: 4) {
            RoundedRectangle(cornerRadius: 1.5).fill(tokenClassColor(cls)).frame(width: 6, height: 6)
            Text(label).font(.caption2).foregroundStyle(.secondary)
            Text(tokenText(value)).font(.caption2.weight(.medium)).monospacedDigit()
        }
    }
    private func statTile(_ value: String, _ label: String) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(value).font(.system(size: 16, weight: .semibold)).monospacedDigit().lineLimit(1)
            Text(label).font(.caption2).foregroundStyle(.secondary)
        }
        .frame(width: 66, alignment: .leading)
        .padding(.horizontal, 9).padding(.vertical, 6)
        .background(Color.primary.opacity(0.04), in: RoundedRectangle(cornerRadius: 8))
    }
}

struct TodayChipsView: View {
    @ObservedObject var model: DailyReportModel
    let today: TodaySummary
    let overview: ReportOverview
    var body: some View {
        UsageHeroView(total: today.totalTokens, input: today.inputTokens, cache: today.cacheTokens,
                      output: today.outputTokens, caption: "今日 token 合计", accent: "实时汇总",
                      money: usdTotal(today.cost, rate: model.fxRate),
                      tiles: [("\(today.tasks)", "任务"), ("\(today.turns)", "轮次"),
                              ("\(today.sources)", "活跃来源"), (deltaText, "较昨日")])
    }
    // 较昨日：昨日无记录显示「—」，避免除零后的夸张百分比。
    private var deltaText: String {
        guard let date = reportDate(today.date),
              let yesterday = Calendar.current.date(byAdding: .day, value: -1, to: date),
              let previous = overview.days.first(where: { $0.date == dailyReportDayKey(yesterday) }),
              previous.totalTokens > 0 else { return "—" }
        let ratio = Double(today.totalTokens - previous.totalTokens) / Double(previous.totalTokens)
        let percent = Int((abs(ratio) * 100).rounded())
        return (ratio >= 0 ? "▲ " : "▼ ") + "\(percent)%"
    }
}

struct WeekTrendView: View {
    let days: [OverviewDay]
    var usesCostLines = false  // 金额悬浮行需要模型（fx/币种）；总览无 model 时省略
    var model: DailyReportModel? = nil
    var body: some View {
        let longest = max(days.map(\.totalTokens).max() ?? 1, 1)
        let average = days.isEmpty ? 0 : days.reduce(0) { $0 + $1.totalTokens } / days.count
        let todayKey = dailyReportDayKey(Date())
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                Text("最近 7 天 · token 合计").font(.headline)
                if average > 0 {
                    Text("日均 \(tokenText(average))").font(.caption).monospacedDigit().foregroundStyle(.secondary)
                }
                Spacer()
                ForEach([(tokenClassColor(.input), "输入"), (tokenClassColor(.cache), "缓存"),
                         (tokenClassColor(.output), "输出")], id: \.1) { color, label in
                    HStack(spacing: 3) {
                        RoundedRectangle(cornerRadius: 1.5).fill(color).frame(width: 6, height: 6)
                        Text(label).font(.system(size: 9)).foregroundStyle(.secondary)
                    }
                }
            }
            ZStack(alignment: .bottomLeading) {
                // 7 日均值虚线画在柱子后面，数值放标题行，避免与柱顶数字打架。
                if average > 0 {
                    Line().stroke(style: StrokeStyle(lineWidth: 1, dash: [3, 3]))
                        .foregroundStyle(Color.primary.opacity(0.28))
                        .frame(height: 1)
                        .offset(y: -(12 + 3 + CGFloat(average) / CGFloat(longest) * 52))
                        .allowsHitTesting(false)
                }
                HStack(alignment: .bottom, spacing: 10) {
                    ForEach(days) { day in
                        VStack(spacing: 3) {
                            Text(tokenText(day.totalTokens))
                                .font(.system(size: 9)).monospacedDigit()
                                .foregroundStyle(day.date == todayKey ? AnyShapeStyle(Color.accentColor) : AnyShapeStyle(.secondary))
                                .lineLimit(1)
                            stackedBar(day, longest: longest)
                                .frame(maxWidth: .infinity)
                                .clipShape(RoundedRectangle(cornerRadius: 3))
                                .overlay {
                                    if day.date == todayKey {
                                        RoundedRectangle(cornerRadius: 3)
                                            .strokeBorder(Color.attention, lineWidth: 1.5)
                                    }
                                }
                            Text(reportShortDay(day.date))
                                .font(.system(size: 9)).monospacedDigit()
                                .foregroundStyle(day.date == todayKey ? AnyShapeStyle(.primary) : AnyShapeStyle(.tertiary))
                        }
                        .chartHover(scale: 1.04)
                        .dailyHoverTip(title: reportDayDisplay(day.date),
                                       lines: trendTipLines(day, model: model))
                    }
                }
            }
            .frame(height: 84, alignment: .bottom)
        }
        .dailyReportCard()
    }
    // 三类层叠：自上而下 输出/缓存/输入（底=输入），高度按合计相对最长日。
    private func stackedBar(_ day: OverviewDay, longest: Int) -> some View {
        let total = max(day.totalTokens, 1)
        let barHeight = max(3, CGFloat(day.totalTokens) / CGFloat(longest) * 52)
        let segments: [(value: Int, color: Color)] = [
            (day.outputTokens, tokenClassColor(.output)),
            (day.cacheTokens, tokenClassColor(.cache)),
            (day.inputTokens, tokenClassColor(.input)),
        ]
        return VStack(spacing: 0) {
            ForEach(Array(segments.enumerated()), id: \.offset) { _, segment in
                Rectangle()
                    .fill(segment.color)
                    .frame(height: barHeight * CGFloat(segment.value) / CGFloat(total))
            }
        }
    }
}
// 悬浮行提取成函数：行内表达式过重会让 swiftc 类型检查超时。
@MainActor
func trendTipLines(_ day: OverviewDay, model: DailyReportModel?) -> [String] {
    classTipLines(input: day.inputTokens, cache: day.cacheTokens, output: day.outputTokens)
        + ["合计：\(tokenText(day.totalTokens))", "\(day.tasks) 个任务"]
        + (model.map { costTipLine(day.cost, rate: $0.fxRate) } ?? [])
}
struct Line: Shape {
    func path(in rect: CGRect) -> Path {
        var path = Path()
        path.move(to: CGPoint(x: rect.minX, y: rect.midY))
        path.addLine(to: CGPoint(x: rect.maxX, y: rect.midY))
        return path
    }
}

// 来源占比：环形图在上、图例在下；总览（近 7 天）与详情（当日）共用。
struct SourceShareView: View {
    let title: String
    let sources: [String: SourceUsage]
    var unavailable: Set<String> = []
    var body: some View {
        let entries: [(key: String, usage: SourceUsage)] =
            sources.map { (key: $0.key, usage: $0.value) }
                .sorted { $0.usage.totalTokens > $1.usage.totalTokens }
                .filter { $0.usage.totalTokens > 0 }
        let total = max(entries.reduce(0) { $0 + $1.usage.totalTokens }, 1)
        let missing = unavailable.subtracting(entries.map(\.key))
        VStack(alignment: .leading, spacing: 10) {
            Text(title).font(.headline)
            if entries.isEmpty {
                Text("没有可统计的来源").font(.subheadline).foregroundStyle(.secondary)
            } else {
                VStack(alignment: .leading, spacing: 12) {
                    donut(entries, total: total).frame(maxWidth: .infinity)
                    VStack(alignment: .leading, spacing: 7) {
                        ForEach(entries, id: \.key) { entry in
                            let share = Double(entry.usage.totalTokens) / Double(total)
                            VStack(alignment: .leading, spacing: 3) {
                                HStack(spacing: 5) {
                                    Circle().fill(providerReportColor(entry.key)).frame(width: 6, height: 6)
                                    Text(providerName(entry.key)).font(.footnote).lineLimit(1)
                                    Spacer(minLength: 4)
                                    Text(tokenText(entry.usage.totalTokens))
                                        .font(.footnote.weight(.medium)).monospacedDigit()
                                    Text("\(Int((share * 100).rounded()))%")
                                        .font(.system(size: 10)).monospacedDigit().foregroundStyle(.tertiary)
                                        .frame(width: 30, alignment: .trailing)
                                }
                                GeometryReader { proxy in
                                    ZStack(alignment: .leading) {
                                        Capsule().fill(Color.primary.opacity(0.05))
                                        Capsule().fill(providerReportColor(entry.key).opacity(0.75))
                                            .frame(width: max(3, proxy.size.width * share))
                                    }
                                }.frame(height: 3)
                            }
                            .rowHover()
                            .dailyHoverTip(title: providerName(entry.key),
                                           lines: classTipLines(entry.usage)
                                               + ["合计：\(tokenText(entry.usage.totalTokens))"])
                        }
                    }
                }
            }
            if !missing.isEmpty {
                Text(missing.map { providerName($0) }.joined(separator: "、") + " 无 token 统计，不计入")
                    .font(.system(size: 10)).foregroundStyle(.tertiary)
            }
        }
        .dailyReportCard()
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
    // 环形图：从 12 点方向顺时针；中心标最大来源的占比，段间留小缝。
    private func donut(_ entries: [(key: String, usage: SourceUsage)], total: Int) -> some View {
        var cursor = 0.0
        var segments: [(color: Color, start: Double, end: Double)] = []
        for entry in entries {
            let start = cursor
            cursor = min(cursor + Double(entry.usage.totalTokens) / Double(total), 1)
            segments.append((providerReportColor(entry.key), start, cursor))
        }
        let gap = segments.count > 1 ? 0.004 : 0
        let lead = entries[0]
        let leadShare = Int((Double(lead.usage.totalTokens) / Double(total) * 100).rounded())
        return ZStack {
            Circle().stroke(Color.primary.opacity(0.06), lineWidth: 14)
            ForEach(Array(segments.enumerated()), id: \.offset) { _, segment in
                Circle()
                    .trim(from: segment.start + gap, to: max(segment.start + gap, segment.end - gap))
                    .stroke(segment.color, style: StrokeStyle(lineWidth: 14, lineCap: .butt))
                    .rotationEffect(.degrees(-90))
            }
            VStack(spacing: 0) {
                Text("\(leadShare)%").font(.system(size: 20, weight: .bold)).monospacedDigit()
                Text(providerName(lead.key)).font(.system(size: 10)).foregroundStyle(.secondary).lineLimit(1)
            }
        }
        .frame(width: 112, height: 112)
    }
}

// 官方来源图标（AgentIcons 渲染管线：裁边+缩进+暗色变体）。
struct AgentIconView: View {
    let id: String
    var size: CGFloat = 16
    var body: some View {
        Image(nsImage: agentIcon(id, size: size))
            .resizable()
            .frame(width: size, height: size)
    }
}

// 模型行图标：按名称前缀映射家族官方图标，未匹配退化为灰底字牌。
struct ModelIconView: View {
    let name: String
    var size: CGFloat = 15
    var body: some View {
        if let id = modelIconId(name) {
            AgentIconView(id: id, size: size)
        } else {
            Text(String(name.prefix(2)))
                .font(.system(size: size * 0.5, weight: .bold))
                .foregroundStyle(.secondary)
                .frame(width: size, height: size)
                .background(Color.primary.opacity(0.1), in: RoundedRectangle(cornerRadius: size * 0.28))
        }
    }
}

// 周口径（usage.week.by）来源环形卡：金额伴随在悬浮卡；图例行 = 图标 + 名称 + token。
struct WeekSourcesDonutCard: View {
    @ObservedObject var model: DailyReportModel
    var body: some View {
        let rows = (model.usageByPeriod["week"]?.by?.harness ?? [])
            .filter { $0.totalTokens > 0 }
            .sorted { $0.totalTokens > $1.totalTokens }
        let total = max(rows.reduce(0) { $0 + $1.totalTokens }, 1)
        VStack(alignment: .leading, spacing: 10) {
            Text("来源占比 · 本周").font(.headline)
            if rows.isEmpty {
                Text("没有可统计的来源").font(.subheadline).foregroundStyle(.secondary)
            } else {
                VStack(alignment: .leading, spacing: 12) {
                    donut(rows, total: total).frame(maxWidth: .infinity)
                    VStack(alignment: .leading, spacing: 7) {
                        ForEach(rows, id: \.key) { row in
                            HStack(spacing: 6) {
                                AgentIconView(id: row.key, size: 15)
                                Text(providerName(row.key)).font(.footnote).lineLimit(1)
                                Spacer(minLength: 4)
                                Text(tokenText(row.totalTokens))
                                    .font(.footnote.weight(.medium)).monospacedDigit()
                            }
                            .rowHover()
                            .dailyHoverTip(title: providerName(row.key),
                                           lines: ["合计：\(tokenText(row.totalTokens))"]
                                               + costTipLine(row.cost, rate: model.fxRate))
                        }
                    }
                }
            }
        }
        .dailyReportCard()
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
    // 环形图：从 12 点方向顺时针；中心标本周合计 token。
    private func donut(_ rows: [WidgetUsagePayload.By.Row], total: Int) -> some View {
        var cursor = 0.0
        var segments: [(color: Color, start: Double, end: Double)] = []
        for row in rows {
            let start = cursor
            cursor = min(cursor + Double(row.totalTokens) / Double(total), 1)
            segments.append((providerReportColor(row.key), start, cursor))
        }
        let gap = segments.count > 1 ? 0.004 : 0
        return ZStack {
            Circle().stroke(Color.primary.opacity(0.06), lineWidth: 14)
            ForEach(Array(segments.enumerated()), id: \.offset) { _, segment in
                Circle()
                    .trim(from: segment.start + gap, to: max(segment.start + gap, segment.end - gap))
                    .stroke(segment.color, style: StrokeStyle(lineWidth: 14, lineCap: .butt))
                    .rotationEffect(.degrees(-90))
            }
            VStack(spacing: 0) {
                Text(tokenText(total)).font(.system(size: 16, weight: .bold)).monospacedDigit()
                Text("本周").font(.system(size: 10)).foregroundStyle(.secondary)
            }
        }
        .frame(width: 112, height: 112)
    }
}

// 周口径模型榜：家族图标 + token，金额伴随在悬浮卡（与来源环形卡同编排）。
struct ModelRankCard: View {
    @ObservedObject var model: DailyReportModel
    let title: String
    var body: some View {
        let rows = (model.usageByPeriod["week"]?.by?.model ?? [])
            .filter { $0.totalTokens > 0 }
            .sorted { $0.totalTokens > $1.totalTokens }
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(title).font(.headline)
                Spacer()
                Text("按 token 排序").font(.caption2).foregroundStyle(.tertiary)
            }
            if rows.isEmpty {
                Text("没有可统计的模型").font(.subheadline).foregroundStyle(.secondary)
            } else {
                VStack(alignment: .leading, spacing: 7) {
                    ForEach(rows, id: \.key) { row in
                        HStack(spacing: 6) {
                            ModelIconView(name: row.key)
                            Text(row.key).font(.footnote).lineLimit(1)
                            Spacer(minLength: 4)
                            Text(tokenText(row.totalTokens))
                                .font(.footnote.weight(.medium)).monospacedDigit()
                        }
                        .rowHover()
                        .dailyHoverTip(title: row.key,
                                       lines: ["合计：\(tokenText(row.totalTokens))"]
                                           + costTipLine(row.cost, rate: model.fxRate))
                    }
                }
            }
        }
        .dailyReportCard()
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
}

