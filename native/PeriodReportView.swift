import Charts
import SwiftUI

// 周 / 月视图（usage-cost.md §7）：汇总卡、逐天堆叠柱图（金额/token 切换，
// 月视图叠加累计折线）、占比区（harness/model Top 6，其余合并「其他」）、
// Top 5 项目、周期导航（下期不超本期）。所有数字来自 `inbox usage`，不本地重算。

struct PeriodHeader: View {
    @ObservedObject var model: DailyReportModel

    var body: some View {
        HStack(spacing: 8) {
            Picker("周期", selection: $model.period) {
                Text("日").tag(ReportPeriod.day)
                Text("周").tag(ReportPeriod.week)
                Text("月").tag(ReportPeriod.month)
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .frame(maxWidth: 150)
            Spacer()
            Picker("币种", selection: $model.currency) {
                Text("CNY").tag("CNY")
                Text("USD").tag("USD")
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .frame(maxWidth: 110)
        }
    }
}

struct PeriodReportView: View {
    @ObservedObject var model: DailyReportModel
    let payload: WidgetUsagePayload
    // 金额 / token 切换：状态在模型层（裸 swiftc 无 @State 宏）
    private var metric: String { model.usageMetric }

    private var currency: String { model.currency }
    private var rate: Double { payload.fx?.usdCny ?? 7.10 }

    private var totalText: String {
        let cost = payload.totals.cost
        let total = widgetMoneyTotal(cost, currency: currency, rate: rate)
        return moneyText(total > 0 ? total : nil, currency: currency)
    }

    private var previousTotal: Double {
        widgetMoneyTotal(payload.previous?.cost ?? .empty, currency: currency, rate: rate)
    }

    private var changePercent: Double? {
        guard let previous = payload.previous, previous.totalTokens > 0,
              payload.totals.totalTokens > 0
        else { return nil }
        return Double(payload.totals.totalTokens - previous.totalTokens) / Double(previous.totalTokens)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            summaryCard
            barChart
            breakdown
            topProjects
            footnote
        }
    }

    // §7.1 汇总卡：金额大字、三类 token、任务数、环比、未定价提示
    private var summaryCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline) {
                Text(totalText).font(.system(size: 30, weight: .bold))
                Spacer()
                if let change = changePercent {
                    Label(
                        String(format: "%.0f%%", abs(change) * 100),
                        systemImage: change >= 0 ? "arrow.up.right" : "arrow.down.right"
                    )
                    .font(.caption).foregroundStyle(.secondary)
                    .help("环比上一\(periodName)")
                }
            }
            HStack(spacing: 12) {
                Text("输入 \(tokenText(payload.totals.inputTokens))").font(.caption)
                Text("缓存 \(tokenText(payload.totals.cacheTokens))").font(.caption)
                Text("输出 \(tokenText(payload.totals.outputTokens))").font(.caption)
                Spacer()
                Text("\(payload.totals.tasks ?? 0) 个任务").font(.caption).foregroundStyle(.secondary)
            }
            if cost.unpricedTokens > 0 {
                Text("另有 \(tokenText(cost.unpricedTokens)) tokens 未定价")
                    .font(.caption).foregroundStyle(.orange)
            }
        }
        .padding(14)
        .background(Color(nsColor: .controlBackgroundColor))
        .cornerRadius(8)
    }

    private var cost: WidgetSnapshotMoney { payload.totals.cost }

    // §7.2 逐天堆叠柱图（金额 / token），月视图叠加累计金额折线
    private var barChart: some View {
        VStack(alignment: .leading, spacing: 6) {
            Picker("度量", selection: $model.usageMetric) {
                Text("金额").tag("cost")
                Text("token").tag("tokens")
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .frame(maxWidth: 140)
            Chart(seriesRows) { row in
                BarMark(
                    x: .value("日期", row.date),
                    y: .value(metric == "cost" ? "金额" : "token", row.value),
                    width: .automatic
                )
                .foregroundStyle(by: .value("类别", row.kind))
                .cornerRadius(2)
            }
            .chartForegroundStyleScale([
                "输入": Color.blue, "缓存": Color.teal, "输出": Color.orange,
            ])
            .chartLegend(.visible)
            .frame(height: 180)
            if payload.period == "month" {
                cumulativeLine
            }
        }
        .padding(14)
        .background(Color(nsColor: .controlBackgroundColor))
        .cornerRadius(8)
    }

    private var cumulativeLine: some View {
        Chart(cumulativeRows) { row in
            LineMark(x: .value("日期", row.date), y: .value("累计", row.value))
                .foregroundStyle(Color.accentColor)
                .interpolationMethod(.monotone)
        }
        .chartYAxis(.hidden)
        .frame(height: 60)
        .help("本月累计金额（\(currency) 视图）")
    }

    // §7.3 占比区：harness / model 两个横向条形榜，各取 Top 6，其余合并「其他」
    private var breakdown: some View {
        VStack(alignment: .leading, spacing: 10) {
            dimensionRows("来源", payload.by?.harness)
            dimensionRows("模型", payload.by?.model)
        }
        .padding(14)
        .background(Color(nsColor: .controlBackgroundColor))
        .cornerRadius(8)
    }

    private func dimensionRows(_ title: String, _ rows: [WidgetUsagePayload.By.Row]?) -> some View {
        let list = rows ?? []
        let total = list.reduce(0) { $0 + $1.totalTokens }
        return VStack(alignment: .leading, spacing: 6) {
            Text(title).font(.caption).foregroundStyle(.secondary)
            ForEach(Array(list.prefix(6).enumerated()), id: \.element.key) { _, row in
                HStack(spacing: 6) {
                    Text(displayName(row.key)).font(.caption).frame(width: 90, alignment: .leading).lineLimit(1)
                    ProgressBar(
                        fraction: total > 0 ? Double(row.totalTokens) / Double(total) : 0
                    )
                    Text(tokenText(row.totalTokens)).font(.caption2).foregroundStyle(.secondary)
                    Text(moneyText(
                        moneyOrNil(row.cost), currency: currency
                    ))
                    .font(.caption2).foregroundStyle(.secondary).frame(width: 76, alignment: .trailing)
                }
            }
        }
    }

    // §7.4 Top 5 项目
    private var topProjects: some View {
        let rows = (payload.by?.project ?? []).prefix(5)
        return VStack(alignment: .leading, spacing: 6) {
            Text("Top 项目").font(.caption).foregroundStyle(.secondary)
            if rows.isEmpty {
                Text("无项目记录").font(.caption).foregroundStyle(.tertiary)
            }
            ForEach(Array(rows), id: \.key) { row in
                HStack {
                    Text(row.name ?? (row.key as NSString).lastPathComponent)
                        .font(.caption).lineLimit(1)
                    Spacer()
                    Text(tokenText(row.totalTokens)).font(.caption2).foregroundStyle(.secondary)
                    Text(moneyText(moneyOrNil(row.cost), currency: currency))
                        .font(.caption2).foregroundStyle(.secondary)
                }
            }
        }
        .padding(14)
        .background(Color(nsColor: .controlBackgroundColor))
        .cornerRadius(8)
    }

    // §7 注脚：按标价估算、汇率及其日期、价格表时间、首版不含阶梯价
    private var footnote: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("按 API 标价估算，不代表实际账单；首版不含阶梯价。").font(.caption2).foregroundStyle(.tertiary)
            Text("汇率 1 USD = \(String(format: "%.2f", rate)) CNY（\(payload.fx?.asOf.isEmpty != false ? "默认" : payload.fx?.asOf ?? "默认")）· 价格表 \(updatedText)")
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

    private var periodName: String { ["day": "日", "week": "周", "month": "月"][payload.period] ?? "周期" }

    private func moneyOrNil(_ cost: WidgetSnapshotMoney) -> Double? {
        let total = widgetMoneyTotal(cost, currency: currency, rate: rate)
        return total > 0 ? total : nil
    }

    // 三段堆叠：金额取各桶单币种折算值；token 取三类 tokens。
    private var seriesRows: [SeriesDatum] {
        let rows = payload.series ?? []
        return rows.flatMap { row -> [SeriesDatum] in
            let amount = { (bucket: [String: Double]) in
                convertAmount(
                    usd: bucket["USD"] ?? 0, cny: bucket["CNY"] ?? 0,
                    to: currency, rate: rate
                )
            }
            return [
                SeriesDatum(
                    date: shortDate(row.date), kind: "输入",
                    value: metric == "cost"
                        ? amount(row.cost.input)
                        : Double(row.inputTokens ?? 0)
                ),
                SeriesDatum(
                    date: shortDate(row.date), kind: "缓存",
                    value: metric == "cost"
                        ? amount(row.cost.cache)
                        : Double(row.cacheTokens ?? 0)
                ),
                SeriesDatum(
                    date: shortDate(row.date), kind: "输出",
                    value: metric == "cost"
                        ? amount(row.cost.output)
                        : Double(row.outputTokens ?? 0)
                ),
            ]
        }
    }

    private var cumulativeRows: [SeriesDatum] {
        var running = 0.0
        return (payload.series ?? []).map { row in
            running += row.cost.input[currency] ?? 0
            return SeriesDatum(date: shortDate(row.date), kind: "累计", value: running)
        }
    }

    // x 轴标签用「天号」短标签（完整日期在悬浮卡里），避免 30 天类别轴全部截断成 "0…"。
    private func shortDate(_ iso: String) -> String {
        let day = iso.suffix(2)
        return String(day.prefix(1) == "0" ? day.dropFirst() : day)
    }

    private func displayName(_ key: String) -> String {
        providerName(key)
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

struct SeriesDatum: Identifiable {
    let date: String
    let kind: String
    let value: Double
    var id: String { date + kind }
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
