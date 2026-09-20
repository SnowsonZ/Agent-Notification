import SwiftUI

struct DayDetailView: View {
    @ObservedObject var model: DailyReportModel
    let date: String
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if let report = model.selectedDay, model.selectedDate == date {
                    if report.totals.tasks == 0 {
                        VStack(spacing: 10) {
                            Image(systemName: "calendar.badge.clock").font(.system(size: 30)).foregroundStyle(.tertiary)
                            Text("这一天没有会话记录").font(.subheadline).foregroundStyle(.secondary)
                        }.frame(maxWidth: .infinity).padding(.vertical, 60)
                    } else {
                        // 各区块统一卡片内边距，比例条/节奏带左右边界对齐。
                        // 来源与项目并排成一行，和总览页的双栏节奏一致。
                        SummaryCardView(report: report)
                        if let excluded = report.agentExcluded {
                            HStack(alignment: .top, spacing: 8) {
                                Image(systemName: "person.2.slash").font(.caption).foregroundStyle(.secondary)
                                Text("另有 \(excluded.tasks) 个 agent 会话（其它工具拉起）合计 "
                                     + tokenText(excluded.totalTokens) + " tokens 未计入。")
                                    .font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                                Spacer()
                            }
                            .padding(10)
                            .background(Color.secondary.opacity(0.06), in: RoundedRectangle(cornerRadius: 8))
                        }
                        RhythmBandView(report: report).dailyReportCard()
                        HStack(alignment: .top, spacing: 14) {
                            SourceShareView(title: "来源占比", sources: report.totals.sources,
                                            unavailable: Set(report.tasks.filter { $0.fidelity == "unavailable" }.map(\.provider)))
                            ProjectBarsView(report: report)
                                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                        }
                        .fixedSize(horizontal: false, vertical: true)
                        TaskListView(report: report)
                    }
                } else if let error = model.error {
                    HStack(alignment: .top, spacing: 8) {
                        Image(systemName: "exclamationmark.triangle.fill").font(.caption).foregroundStyle(.red)
                        Text(error).font(.caption).foregroundStyle(.red).textSelection(.enabled)
                        Spacer()
                        Button { model.loadDay(date) } label: { Text("重试").font(.caption) }
                            .buttonStyle(InboxActionButtonStyle())
                    }
                    .padding(10)
                    .background(Color.red.opacity(0.06), in: RoundedRectangle(cornerRadius: 8))
                } else {
                    HStack(spacing: 10) {
                        ProgressView().controlSize(.small)
                        Text("正在生成当日报告…").font(.subheadline).foregroundStyle(.secondary)
                    }.frame(maxWidth: .infinity).padding(.vertical, 60)
                }
            }
            .padding(16)
        }
        .navigationTitle(reportDayDisplay(date))
        .onAppear { model.loadDay(date) }
    }
}

struct SummaryCardView: View {
    let report: DayReport
    var body: some View {
        UsageHeroView(total: report.totals.totalTokens, input: report.totals.inputTokens,
                      cache: report.totals.cacheTokens, output: report.totals.outputTokens,
                      caption: "token 合计", accent: liveLabel,
                      tiles: [("\(report.totals.tasks)", "任务"), ("\(report.totals.turns)", "轮次"),
                              ("\(activeSourceCount)", "来源"), (activeSpanText, "活跃时长")])
    }
    // 今天不固化：每次打开即时重算，标明截止时刻，次日首次查看时补算定稿。
    private var liveLabel: String? {
        guard report.date == dailyReportDayKey(Date()) else { return nil }
        let until = reportClock(report.generatedAt ?? Date().timeIntervalSince1970)
        return "实时汇总 · 截至 \(until)"
    }
    private var activeSourceCount: Int {
        report.totals.sources.values.filter { $0.totalTokens > 0 }.count
    }
    // 活跃时长 = 全部任务真实活动段的并集长度；并行任务不重复计时。
    private var activeSpanText: String {
        let seconds = mergedActiveSeconds(report.tasks)
        let minutes = Int((seconds / 60).rounded())
        if minutes < 60 { return "\(minutes)m" }
        return minutes % 60 == 0 ? "\(minutes / 60)h" : "\(minutes / 60)h \(minutes % 60)m"
    }
}

func taskActivitySegments(_ task: ReportTask) -> [[Double]] {
    if let segments = task.segments, !segments.isEmpty { return segments }
    return task.firstAt > 0 ? [[task.firstAt, max(task.lastAt, task.firstAt)]] : []
}
func mergedActiveSeconds(_ tasks: [ReportTask]) -> Double {
    let segments = tasks.flatMap(taskActivitySegments)
        .filter { $0.count == 2 && $0[0] > 0 }
        .sorted { $0[0] < $1[0] }
    var total = 0.0
    var current: (Double, Double)?
    for segment in segments {
        let end = max(segment[1], segment[0])
        if let open = current, segment[0] <= open.1 {
            current = (open.0, max(open.1, end))
        } else {
            if let open = current { total += open.1 - open.0 }
            current = (segment[0], end)
        }
    }
    if let open = current { total += open.1 - open.0 }
    return total
}

struct RhythmBandView: View {
    let report: DayReport
    var tasks: [ReportTask] { report.tasks }
    private var dayStart: Double { reportDate(report.date)?.timeIntervalSince1970 ?? 0 }
    private let laneHeight: CGFloat = 9
    private let laneGap: CGFloat = 3
    var body: some View {
        let range = rhythmRange(tasks.map(taskActivitySegments), dayStart: dayStart)
        let hours = range.1 - range.0
        let lanes = providerLanes
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text("一天节奏 · \(Int(range.0))–\(Int(range.1)) 时").font(.headline)
                Spacer()
                legend
            }
            // 每个来源一条泳道：并行任务不再互相覆盖，一眼能看出几个 agent 同时在跑。
            GeometryReader { proxy in
                let width = proxy.size.width
                ZStack(alignment: .topLeading) {
                    ForEach(Array(stride(from: range.0, through: range.1, by: 2)), id: \.self) { hour in
                        Rectangle().fill(Color.primary.opacity(0.06)).frame(width: 1)
                            .offset(x: min(max((hour - range.0) / hours * width, 0), width - 1))
                    }
                    ForEach(Array(lanes.enumerated()), id: \.offset) { index, provider in
                        let y = CGFloat(index) * (laneHeight + laneGap)
                        RoundedRectangle(cornerRadius: 3)
                            .fill(providerReportColor(provider).opacity(0.10))
                            .frame(height: laneHeight).offset(y: y)
                        ForEach(tasks.filter { $0.provider == provider }) { task in
                            ForEach(Array(taskActivitySegments(task).enumerated()), id: \.offset) { _, segment in
                                let left = (rhythmHour(segment[0], dayStart: dayStart) - range.0) / hours
                                let span = (rhythmHour(segment[1], dayStart: dayStart)
                                            - rhythmHour(segment[0], dayStart: dayStart)) / hours
                                RoundedRectangle(cornerRadius: 3)
                                    .fill(providerReportColor(task.provider))
                                    .frame(width: max(4, span * width), height: laneHeight)
                                    .offset(x: min(max(0, left * width), width - 4), y: y)
                                    .chartHover(scale: 1.15)
                                    .dailyHoverTip(title: task.title,
                                                   lines: ["\(providerName(task.provider)) · \(reportClock(segment[0]))–\(reportClock(segment[1])) · 全天 \(reportTimeRange(task)) · \(task.turns) 轮"]
                                                       + classTipLines(input: task.inputTokens, cache: task.cacheTokens,
                                                                       output: task.outputTokens)
                                                       + ["合计：\(tokenText(task.totalTokens))"])
                            }
                        }
                    }
                    if let now = nowFraction(range: range) {
                        Rectangle().fill(Color.attention).frame(width: 1.5)
                            .offset(x: now * width)
                    }
                }
            }
            .frame(height: CGFloat(max(lanes.count, 1)) * (laneHeight + laneGap) - laneGap)
            .help("按来源分泳道的节奏带；色块为任务真实活动时段，空闲留白")
            GeometryReader { proxy in
                let width = proxy.size.width
                ZStack(alignment: .topLeading) {
                    ForEach(Array(stride(from: range.0, through: range.1, by: 2)), id: \.self) { hour in
                        Text(String(format: "%02d", Int(hour)))
                            .font(.system(size: 9)).monospacedDigit().foregroundStyle(.tertiary)
                            .position(x: min(max((hour - range.0) / hours * width, 8), width - 8), y: 6)
                    }
                }
            }
            .frame(height: 12)
        }
    }
    // 泳道按来源 token 量降序，最活跃的来源在最上面。
    private var providerLanes: [String] {
        var totals: [String: Int] = [:]
        for task in tasks { totals[task.provider, default: 0] += task.totalTokens }
        return totals.keys.sorted { (totals[$0]!, $1) > (totals[$1]!, $0) }
    }
    // 今天画一条「现在」竖线，让实时汇总的截止点落在时间轴上。
    private func nowFraction(range: (Double, Double)) -> Double? {
        guard report.date == dailyReportDayKey(Date()) else { return nil }
        let hour = rhythmHour(Date().timeIntervalSince1970, dayStart: dayStart)
        guard hour >= range.0, hour <= range.1 else { return nil }
        return (hour - range.0) / (range.1 - range.0)
    }
    private var legend: some View {
        HStack(spacing: 8) {
            ForEach(providerLanes, id: \.self) { provider in
                HStack(spacing: 3) {
                    Circle().fill(providerReportColor(provider)).frame(width: 6, height: 6)
                    Text(providerName(provider)).font(.system(size: 9)).foregroundStyle(.secondary)
                }
            }
        }
    }
}



struct ProjectBarsView: View {
    let report: DayReport
    var body: some View {
        let entries: [(key: String, usage: SourceUsage)] =
            report.totals.projects.map { (key: $0.key, usage: $0.value) }
                .sorted { $0.usage.totalTokens > $1.usage.totalTokens }
        let longest = max(entries.first?.usage.totalTokens ?? 1, 1)
        let split = providerSplit
        VStack(alignment: .leading, spacing: 10) {
            Text("项目 · \(entries.count)").font(.headline)
            if entries.isEmpty {
                Text("没有可统计的项目").font(.subheadline).foregroundStyle(.secondary)
            }
            VStack(alignment: .leading, spacing: 10) {
            ForEach(Array(entries.prefix(6).enumerated()), id: \.element.key) { index, entry in
                VStack(alignment: .leading, spacing: 4) {
                    HStack(spacing: 6) {
                        Text("\(index + 1)")
                            .font(.system(size: 10, weight: .semibold)).monospacedDigit()
                            .foregroundStyle(.tertiary).frame(width: 12, alignment: .trailing)
                        Text((entry.key as NSString).lastPathComponent)
                            .font(.footnote).lineLimit(1)
                            .help(entry.key)
                        Spacer(minLength: 4)
                        Text(tokenText(entry.usage.totalTokens))
                            .font(.footnote.weight(.medium)).monospacedDigit()
                    }
                    // 条按来源分段着色：同一项目里哪个 agent 用得多，和上方环形图同一套色。
                    GeometryReader { proxy in
                        let width = proxy.size.width * Double(entry.usage.totalTokens) / Double(longest)
                        let parts = split[entry.key] ?? []
                        let partTotal = max(parts.reduce(0) { $0 + $1.tokens }, 1)
                        ZStack(alignment: .leading) {
                            RoundedRectangle(cornerRadius: 3).fill(Color.primary.opacity(0.05))
                            HStack(spacing: 1) {
                                if parts.isEmpty {
                                    Rectangle().fill(Color.accentColor.opacity(0.65))
                                } else {
                                    ForEach(parts, id: \.provider) { part in
                                        Rectangle().fill(providerReportColor(part.provider).opacity(0.85))
                                            .frame(width: max(2, width * Double(part.tokens) / Double(partTotal)))
                                    }
                                }
                            }
                            .frame(width: max(4, width))
                            .clipShape(RoundedRectangle(cornerRadius: 3))
                        }
                    }.frame(height: 8).padding(.leading, 18)
                }
                .rowHover()
                .dailyHoverTip(title: (entry.key as NSString).lastPathComponent,
                               lines: classTipLines(entry.usage)
                                   + ["合计：\(tokenText(entry.usage.totalTokens))"]
                                   + (split[entry.key] ?? []).map { "\(providerName($0.provider))：\(tokenText($0.tokens))" })
            }
            }
            .frame(maxHeight: .infinity, alignment: .center)
        }
        .dailyReportCard()
        .frame(maxWidth: .infinity, alignment: .leading)
    }
    // 项目 × 来源的 token 拆分来自任务列表；无 token 记录的任务不参与。
    private var providerSplit: [String: [(provider: String, tokens: Int)]] {
        var totals: [String: [String: Int]] = [:]
        for task in report.tasks where task.fidelity != "unavailable" && task.totalTokens > 0 {
            let key = task.project.isEmpty ? "(无项目)" : task.project
            totals[key, default: [:]][task.provider, default: 0] += task.totalTokens
        }
        return totals.mapValues { byProvider in
            byProvider.map { (provider: $0.key, tokens: $0.value) }
                .sorted { ($0.tokens, $1.provider) > ($1.tokens, $0.provider) }
        }
    }
}

struct TaskListView: View {
    let report: DayReport
    var body: some View {
        let dayTotal = max(report.totals.totalTokens, 1)
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text("任务 · \(report.tasks.count)").font(.headline)
                Spacer()
                Text("底色长度 = token 占今日合计比例").font(.system(size: 9)).foregroundStyle(.tertiary)
            }
            .padding(.bottom, 8)
            ForEach(Array(report.tasks.enumerated()), id: \.element.id) { index, task in
                taskRow(task, dayTotal: dayTotal)
                if index < report.tasks.count - 1 {
                    Divider().opacity(0.5)
                }
            }
        }
        .dailyReportCard()
    }
    // 一行一任务：左侧来源色条 + 行内底色按 token 占今日合计的比例变长，取代逐张卡片和胶囊条。
    // 行内已列全所有字段，不再挂悬浮提示。
    private func taskRow(_ task: ReportTask, dayTotal: Int) -> some View {
        let color = providerReportColor(task.provider)
        let share = task.fidelity == "unavailable" ? 0 : Double(task.totalTokens) / Double(dayTotal)
        return HStack(alignment: .top, spacing: 10) {
            RoundedRectangle(cornerRadius: 1.5).fill(color).frame(width: 3)
                .padding(.vertical, 2)
            VStack(alignment: .leading, spacing: 4) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(task.title)
                        .font(.body.weight(.medium)).lineLimit(1)
                        .help(task.title)
                    Spacer(minLength: 8)
                    Text(task.fidelity == "unavailable" ? "无 token" : tokenText(task.totalTokens))
                        .font(.system(size: 12, weight: .semibold)).monospacedDigit()
                        .foregroundStyle(task.fidelity == "unavailable" ? AnyShapeStyle(.secondary) : AnyShapeStyle(.primary))
                }
                HStack(spacing: 6) {
                    Text(providerName(task.provider))
                        .font(.system(size: 10, weight: .medium))
                        .foregroundStyle(color)
                        .padding(.horizontal, 5).padding(.vertical, 1)
                        .background(color.opacity(0.13), in: Capsule())
                    if !task.project.isEmpty {
                        Text((task.project as NSString).lastPathComponent)
                            .foregroundStyle(.secondary).lineLimit(1).help(task.project)
                    }
                    if task.turns > 0 {
                        Text("\(task.turns) 轮").foregroundStyle(.secondary)
                    }
                    Text(reportTimeRange(task)).monospacedDigit().foregroundStyle(.secondary)
                    Spacer(minLength: 4)
                    Circle().fill(stateDotColor(task.state)).frame(width: 6, height: 6)
                    Text(stateName(task.state)).foregroundStyle(.secondary)
                }
                .font(.footnote)
                Text(usageLine(task)).font(.caption2).foregroundStyle(.tertiary).lineLimit(1)
            }
        }
        .padding(.vertical, 8)
        .padding(.horizontal, 6)
        .background(alignment: .leading) {
            GeometryReader { proxy in
                RoundedRectangle(cornerRadius: 6)
                    .fill(color.opacity(0.07))
                    .frame(width: max(0, proxy.size.width * share))
            }
        }
        .rowHover()
    }
}
