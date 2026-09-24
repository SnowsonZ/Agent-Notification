import SwiftUI

// 周 / 月视图（usage-cost.md §7，2026-09-24 重设计）：与日视图同一套设计语言——
// 同一卡片（dailyReportCard）、同一手绘柱图+悬浮卡、同一三类配色；token 为主线，
// 金额（USD）是伴随指标：hero 金额行、榜单双列（token 主列 + $ 次列）、柱图悬浮卡。
// 所有数字来自 `inbox usage`，不本地重算；无度量/币种切换器。

struct PeriodHeader: View {
    @ObservedObject var model: DailyReportModel

    var body: some View {
        Picker("周期", selection: $model.period) {
            Text("日").tag(ReportPeriod.day)
            Text("周").tag(ReportPeriod.week)
            Text("月").tag(ReportPeriod.month)
        }
        .pickerStyle(.segmented)
        .labelsHidden()
        .frame(maxWidth: 150)
    }
}

struct PeriodReportView: View {
    @ObservedObject var model: DailyReportModel
    let payload: WidgetUsagePayload

    private var rate: Double { payload.fx?.usdCny ?? model.fxRate }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            summaryCard
            PeriodBarsView(model: model, payload: payload)
            HStack(alignment: .top, spacing: 14) {
                DimensionRankCard(title: "来源", rows: payload.by?.harness ?? [],
                                  iconKind: .provider, rate: rate)
                DimensionRankCard(title: "模型", rows: payload.by?.model ?? [],
                                  iconKind: .model, rate: rate)
            }
            .fixedSize(horizontal: false, vertical: true)
            ProjectRankCard(title: "Top 项目 · \(periodName)", rows: payload.by?.project ?? [], rate: rate)
            footnote
        }
    }

    // §7.1 汇总卡：token 大字 + 金额伴随行 + 三类分段 + 任务/环比瓦片（与日视图同一组件）。
    private var summaryCard: some View {
        let series = payload.series ?? []
        let activeDays = series.filter { $0.totalTokens > 0 }.count
        let delta = changePercent
        return VStack(alignment: .leading, spacing: 8) {
            UsageHeroView(
                total: payload.totals.totalTokens,
                input: payload.totals.inputTokens,
                cache: payload.totals.cacheTokens,
                output: payload.totals.outputTokens,
                caption: "\(periodName) · \(dateRangeText)",
                accent: nil,
                money: usdTotal(payload.totals.cost, rate: rate),
                delta: delta.map { (text: String(format: "%.0f%%", abs($0) * 100), up: $0 >= 0) },
                tiles: [("\(payload.totals.tasks ?? 0)", "任务"),
                        ("\(activeDays)", "活跃天"),
                        (tokenText(payload.totals.inputTokens), "输入"),
                        (tokenText(payload.totals.outputTokens), "输出")]
            )
            if payload.totals.cost.unpricedTokens > 0 {
                Text("另有 \(tokenText(payload.totals.cost.unpricedTokens)) tokens 未定价")
                    .font(.caption).foregroundStyle(.orange)
            }
        }
    }

    private var changePercent: Double? {
        guard let previous = payload.previous, previous.totalTokens > 0,
              payload.totals.totalTokens > 0
        else { return nil }
        return Double(payload.totals.totalTokens - previous.totalTokens) / Double(previous.totalTokens)
    }

    private var periodName: String { ["day": "日", "week": "周", "month": "月"][payload.period] ?? "周期" }

    private var dateRangeText: String {
        let start = reportShortDay(payload.start)
        let end = payload.start == payload.end ? "" : " – \(reportShortDay(payload.end))"
        return start + end
    }

    // §7 注脚：估算免责（唯一保留处）、USD 与汇率、价格表时间、未定价模型。
    private var footnote: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("金额为按 API 标价的估算值（非账单）· USD 显示，CNY 官价按汇率 \(String(format: "%.2f", rate)) 折算。")
                .font(.caption2).foregroundStyle(.tertiary)
            Text("汇率基准日 \(payload.fx?.asOf.isEmpty != false ? "默认" : payload.fx?.asOf ?? "默认") · 价格表 \(updatedText)")
                .font(.caption2).foregroundStyle(.tertiary)
            if !unpriced.isEmpty {
                Text("未定价：\(unpriced.joined(separator: "、"))").font(.caption2).foregroundStyle(.orange)
            }
        }
    }

    private var unpriced: [String] { payload.pricing?.unpricedModels ?? [] }
    private var updatedText: String {
        guard let fetched = payload.pricing?.fetchedAt, fetched > 0 else { return "随包快照" }
        let formatter = DateFormatter()
        formatter.dateFormat = "MM-dd HH:mm"
        return formatter.string(from: Date(timeIntervalSince1970: fetched))
    }
}

// §7.2 逐天堆叠柱图：手绘 + 悬浮卡（与最近 7 天柱同一语言）；月视图叠加累计虚线。
struct PeriodBarsView: View {
    @ObservedObject var model: DailyReportModel
    let payload: WidgetUsagePayload

    private var rows: [WidgetUsagePayload.SeriesRow] { payload.series ?? [] }
    private var isMonth: Bool { payload.period == "month" }

    var body: some View {
        let series = rows
        let longest = max(series.map(\.totalTokens).max() ?? 1, 1)
        let average = series.isEmpty ? 0 : series.reduce(0) { $0 + $1.totalTokens } / max(series.count, 1)
        return VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 10) {
                Text("逐日 · token" + (isMonth ? "（虚线=累计）" : "")).font(.headline)
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
                if !isMonth, average > 0 {
                    Line().stroke(style: StrokeStyle(lineWidth: 1, dash: [3, 3]))
                        .foregroundStyle(Color.primary.opacity(0.28))
                        .frame(height: 1)
                        .offset(y: -(12 + 3 + CGFloat(average) / CGFloat(longest) * 96))
                        .allowsHitTesting(false)
                }
                HStack(alignment: .bottom, spacing: isMonth ? 4 : 10) {
                    ForEach(series) { row in
                        VStack(spacing: 3) {
                            if !isMonth {
                                Text(tokenText(row.totalTokens))
                                    .font(.system(size: 9)).monospacedDigit().foregroundStyle(.secondary)
                                    .lineLimit(1)
                            }
                            stackedBar(row, longest: longest)
                                .frame(maxWidth: .infinity)
                                .clipShape(RoundedRectangle(cornerRadius: 3))
                                .overlay {
                                    if row.date == todayKey {
                                        RoundedRectangle(cornerRadius: 3)
                                            .strokeBorder(Color.attention, lineWidth: 1.5)
                                    }
                                }
                            Text(xLabel(row.date))
                                .font(.system(size: 9)).monospacedDigit().foregroundStyle(.tertiary)
                        }
                        .chartHover(scale: 1.04)
                        .dailyHoverTip(title: reportDayDisplay(row.date),
                                       lines: seriesTipLines(row, model: model))
                    }
                }
                if isMonth {
                    CumulativeLineShape(points: cumulativePoints(longest: longest))
                        .stroke(style: StrokeStyle(lineWidth: 1.2, dash: [4, 3]))
                        .foregroundStyle(Color.primary.opacity(0.6))
                        .allowsHitTesting(false)
                }
            }
            .frame(height: 132, alignment: .bottom)
        }
        .dailyReportCard()
    }

    private var todayKey: String { dailyReportDayKey(Date()) }

    // 三类层叠：自上而下 输出/缓存/输入（底=输入），与最近 7 天柱一致。
    private func stackedBar(_ row: WidgetUsagePayload.SeriesRow, longest: Int) -> some View {
        let total = max(row.totalTokens, 1)
        let barHeight = max(3, CGFloat(row.totalTokens) / CGFloat(longest) * 96)
        let segments: [(value: Int, color: Color)] = [
            (row.outputTokens ?? 0, tokenClassColor(.output)),
            (row.cacheTokens ?? 0, tokenClassColor(.cache)),
            (row.inputTokens ?? 0, tokenClassColor(.input)),
        ]
        return VStack(spacing: 0) {
            ForEach(Array(segments.enumerated()), id: \.offset) { _, segment in
                Rectangle()
                    .fill(segment.color)
                    .frame(height: barHeight * CGFloat(segment.value) / CGFloat(total))
            }
        }
    }

    private func cumulativePoints(longest: Int) -> [CGPoint] {
        let series = rows
        guard !series.isEmpty, longest > 0 else { return [] }
        var running = 0.0
        var points: [CGPoint] = []
        for (index, row) in series.enumerated() {
            running += Double(row.totalTokens)
            let x = (CGFloat(index) + 0.5) / CGFloat(series.count)
            let y = 1 - running / Double(longest) * 0.8
            points.append(CGPoint(x: x, y: max(y, 0.02)))
        }
        return points
    }

    // 周视图标 MM/dd；月视图每 7 天标一次天号。
    private func xLabel(_ iso: String) -> String {
        if isMonth {
            guard let day = Int(iso.suffix(2)) else { return "" }
            return (day - 1) % 7 == 0 ? String(day) : ""
        }
        return reportShortDay(iso)
    }
}

// 悬浮行提取成函数：行内表达式过重会让 swiftc 类型检查超时。
@MainActor
func seriesTipLines(_ row: WidgetUsagePayload.SeriesRow, model: DailyReportModel) -> [String] {
    classTipLines(input: row.inputTokens ?? 0, cache: row.cacheTokens ?? 0, output: row.outputTokens ?? 0)
        + ["合计：\(tokenText(row.totalTokens))"]
        + costTipLine(row.cost, rate: model.fxRate)
}
// 月视图累计虚线：归一化坐标（0–1），在柱图 ZStack 里铺满。
struct CumulativeLineShape: Shape {
    let points: [CGPoint]
    func path(in rect: CGRect) -> Path {
        guard points.count > 1 else { return Path() }
        var path = Path()
        for (index, point) in points.enumerated() {
            let location = CGPoint(x: point.x * rect.width, y: point.y * rect.height)
            if index == 0 { path.move(to: location) } else { path.addLine(to: location) }
        }
        return path
    }
}

// §7.3 榜单卡：图标 + 名称 + 占比条 + token + 金额（token 主列、金额次列）。
enum RankIconKind { case provider, model }

struct DimensionRankCard: View {
    let title: String
    let rows: [WidgetUsagePayload.By.Row]
    let iconKind: RankIconKind
    let rate: Double

    var body: some View {
        let list = rows.filter { $0.totalTokens > 0 && $0.key != "__other__" }
            .sorted { $0.totalTokens > $1.totalTokens }
        let longest = max(list.first?.totalTokens ?? 1, 1)
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(title).font(.headline)
                Spacer()
                Text("按 token 排序").font(.caption2).foregroundStyle(.tertiary)
            }
            if list.isEmpty {
                Text("没有可统计的数据").font(.subheadline).foregroundStyle(.secondary)
            }
            VStack(alignment: .leading, spacing: 8) {
                ForEach(list.prefix(6), id: \.key) { row in
                    HStack(spacing: 6) {
                        rankIcon(row.key)
                        Text(displayName(row)).font(.footnote).lineLimit(1)
                        GeometryReader { proxy in
                            ZStack(alignment: .leading) {
                                RoundedRectangle(cornerRadius: 2).fill(Color.primary.opacity(0.05))
                                RoundedRectangle(cornerRadius: 2)
                                    .fill(barColor(row.key))
                                    .frame(width: max(3, proxy.size.width * Double(row.totalTokens) / Double(longest)))
                            }
                        }
                        .frame(height: 6)
                        Text(tokenText(row.totalTokens))
                            .font(.footnote.weight(.medium)).monospacedDigit()
                        Text(usdText(usdTotal(row.cost, rate: rate)))
                            .font(.footnote).monospacedDigit().foregroundStyle(.secondary)
                            .frame(minWidth: 60, alignment: .trailing)
                    }
                    .rowHover()
                    .dailyHoverTip(title: displayName(row),
                                   lines: ["合计：\(tokenText(row.totalTokens))"]
                                       + costTipLines(rate))
                }
            }
        }
        .dailyReportCard()
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }

    private func costTipLines(_ rate: Double) -> [String] {
        rows.first(where: { $0.key == "" }).map { _ in [] } ?? []
    }

    private func rankIcon(_ key: String) -> some View {
        switch iconKind {
        case .provider: return AnyView(AgentIconView(id: key, size: 15))
        case .model: return AnyView(ModelIconView(name: key))
        }
    }
    private func barColor(_ key: String) -> Color {
        iconKind == .provider ? providerReportColor(key).opacity(0.85) : Color.accentColor.opacity(0.75)
    }
    private func displayName(_ row: WidgetUsagePayload.By.Row) -> String {
        iconKind == .provider ? providerName(row.key) : row.key
    }
}

// §7.4 Top 项目全宽卡：名称 + token + 金额 + 占比条（总览/周/月共用）。
struct ProjectRankCard: View {
    let title: String
    let rows: [WidgetUsagePayload.By.Row]
    let rate: Double

    var body: some View {
        let list = rows.filter { $0.totalTokens > 0 && $0.key != "__other__" }
            .sorted { $0.totalTokens > $1.totalTokens }
        let longest = max(list.first?.totalTokens ?? 1, 1)
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(title).font(.headline)
                Spacer()
                Text("按 token 排序").font(.caption2).foregroundStyle(.tertiary)
            }
            if list.isEmpty {
                Text("没有可统计的项目").font(.subheadline).foregroundStyle(.secondary)
            }
            ForEach(list.prefix(6), id: \.key) { row in
                let base = (row.key as NSString).lastPathComponent
                VStack(alignment: .leading, spacing: 4) {
                    HStack(spacing: 6) {
                        Text(base).font(.footnote).lineLimit(1).help(row.key)
                        Spacer(minLength: 4)
                        Text(tokenText(row.totalTokens))
                            .font(.footnote.weight(.medium)).monospacedDigit()
                        Text(usdText(usdTotal(row.cost, rate: rate)))
                            .font(.footnote).monospacedDigit().foregroundStyle(.secondary)
                            .frame(minWidth: 60, alignment: .trailing)
                    }
                    GeometryReader { proxy in
                        ZStack(alignment: .leading) {
                            RoundedRectangle(cornerRadius: 3).fill(Color.primary.opacity(0.05))
                            RoundedRectangle(cornerRadius: 3)
                                .fill(Color.accentColor.opacity(0.75))
                                .frame(width: max(3, proxy.size.width * Double(row.totalTokens) / Double(longest)))
                        }
                    }
                    .frame(height: 6)
                }
                .rowHover()
                .dailyHoverTip(title: base,
                               lines: ["合计：\(tokenText(row.totalTokens))",
                                       "金额：\(usdText(usdTotal(row.cost, rate: rate)))"])
            }
        }
        .dailyReportCard()
    }
}

// 横向比例条（与日报既有比例条同一视觉语言：圆角矩形 + accent 填充）
struct ProgressBar: View {
    let fraction: Double

    var body: some View {
        GeometryReader { proxy in
            ZStack(alignment: .leading) {
                RoundedRectangle(cornerRadius: 3).fill(Color.secondary.opacity(0.15))
                RoundedRectangle(cornerRadius: 3)
                    .fill(Color.accentColor.opacity(0.75))
                    .frame(width: max(2, proxy.size.width * min(max(fraction, 0), 1)))
            }
        }
        .frame(height: 8)
    }
}

// 周期导航（§7.5）：‹ 上一期 · 本期 · 下一期 ›，下一期不能超过本期。
struct PeriodNavigator: View {
    @ObservedObject var model: DailyReportModel

    var body: some View {
        HStack(spacing: 12) {
            Button { model.shiftPeriod(-1) } label: { Text("‹ 上一期") }
            Spacer()
            Button { model.shiftPeriod(0) } label: { Text("本期") }
            Spacer()
            Button { model.shiftPeriod(1) } label: { Text("下一期 ›") }
                .disabled(model.atCurrentPeriod)
        }
        .buttonStyle(.plain)
        .font(.caption)
        .foregroundStyle(Color.accentColor)
    }
}
