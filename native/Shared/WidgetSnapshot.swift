import Foundation

// 组件快照数据结构（desktop-widgets.md §2）：App 与组件编译同一份文件。
// 快照只包含收件箱已保存的元数据与数字，不新增任何正文内容；
// hide_titles 打开时标题根本不写入文件（写空字符串），而不只是显示时隐藏。
// Python 侧金额对象 / `inbox usage` 输出为 snake_case，统一用 CodingKeys 映射。

// token 计数 k/M/B：<1k 原值、k/M 段 mantissa<100 保留小数否则取整、B 段两位小数，
// 末尾零去除（与 Python token_text 同规则）。
func trimTrailingZeros(_ text: String) -> String {
    var result = text
    if result.contains(".") {
        while result.hasSuffix("0") { result.removeLast() }
        if result.hasSuffix(".") { result.removeLast() }
    }
    return result
}
func tokenText(_ value: Int) -> String {
    if value <= 0 { return "0" }
    if value < 1_000 { return String(value) }
    let units: [(factor: Double, symbol: String, decimals: Int)] =
        [(1_000_000_000, "B", 2), (1_000_000, "M", 1), (1_000, "k", 1)]
    for unit in units where Double(value) >= unit.factor {
        let mantissa = Double(value) / unit.factor
        let text = mantissa < 100
            ? trimTrailingZeros(String(format: "%." + String(unit.decimals) + "f", mantissa))
            : String(format: "%.0f", mantissa)
        return text + unit.symbol
    }
    return String(value)
}

// MARK: - 金额展示与换算（usage-cost.md §5；App 与组件共享）

// 与 Python money_text 同规则、测试用例相同：nil（无可定价 token）→ "—"；
// 0 → $0.00；0 < |v| < 0.01 → <$0.01 / <¥0.01；其余两位小数千分位。
func moneyText(_ value: Double?, currency: String) -> String {
    guard let value else { return "—" }
    let symbol = currency == "USD" ? "$" : "¥"
    if abs(value) < 1e-9 { return "\(symbol)0.00" }
    if abs(value) < 0.01 { return "<\(symbol)0.01" }
    let parts = String(format: "%.2f", abs(value)).split(separator: ".")
    var whole = String(parts[0])
    var grouped = ""
    while whole.count > 3 {
        let cut = whole.index(whole.endIndex, offsetBy: -3)
        grouped = "," + whole[cut...] + grouped
        whole = String(whole[..<cut])
    }
    let sign = value < 0 ? "-" : ""
    let fraction = parts.count > 1 ? String(parts[1]) : "00"
    return "\(sign)\(symbol)\(whole + grouped).\(fraction)"
}

// 展示换算：CNY 视图 = USD×rate + CNY；USD 视图 = USD + CNY/rate。
func convertAmount(usd: Double, cny: Double, to currency: String, rate: Double) -> Double {
    currency == "CNY" ? usd * rate + cny : usd + cny / rate
}

// 金额对象 → 单币种展示值（三类合计 + native 兜底；未定价 token 不折算）。
func widgetMoneyTotal(_ cost: WidgetSnapshotMoney, currency: String, rate: Double) -> Double {
    var total = 0.0
    for bucket in [cost.input, cost.cache, cost.output, cost.nativeFallback ?? [:]] {
        total += convertAmount(usd: bucket["USD"] ?? 0, cny: bucket["CNY"] ?? 0, to: currency, rate: rate)
    }
    return total
}

struct WidgetSnapshotMoney: Codable, Equatable {
    var input: [String: Double]
    var cache: [String: Double]
    var output: [String: Double]
    var unpricedTokens: Int
    var nativeFallback: [String: Double]?

    enum CodingKeys: String, CodingKey {
        case input, cache, output
        case unpricedTokens
        case nativeFallback
    }

    static let empty = WidgetSnapshotMoney(
        input: [:], cache: [:], output: [:], unpricedTokens: 0, nativeFallback: nil
    )
}

// `inbox usage` 单周期输出（usage-cost.md §6）中组件消费的字段。
struct WidgetUsagePayload: Codable, Equatable {
    var period: String
    var start: String
    var end: String
    var isCurrent: Bool
    var totals: Totals
    var previous: Previous?
    var series: [SeriesRow]?
    var by: By?
    var pricing: Pricing?
    var fx: WidgetSnapshot.Fx?

    enum CodingKeys: String, CodingKey {
        case period, start, end
        case isCurrent
        case totals, previous, series, by, pricing, fx
    }

    struct Pricing: Codable, Equatable {
        var fetchedAt: Double
        var unpricedModels: [String]?

        enum CodingKeys: String, CodingKey {
            case fetchedAt
            case unpricedModels
        }
    }

    struct Totals: Codable, Equatable {
        var inputTokens: Int
        var cacheTokens: Int
        var outputTokens: Int
        var totalTokens: Int
        var tasks: Int?
        var cost: WidgetSnapshotMoney

        enum CodingKeys: String, CodingKey {
            case cost, tasks
            case inputTokens
            case cacheTokens
            case outputTokens
            case totalTokens
        }
    }

    struct Previous: Codable, Equatable {
        var totalTokens: Int
        var cost: WidgetSnapshotMoney

        enum CodingKeys: String, CodingKey {
            case cost
            case totalTokens
        }
    }

    struct SeriesRow: Codable, Equatable {
        var date: String
        var totalTokens: Int
        var inputTokens: Int?
        var cacheTokens: Int?
        var outputTokens: Int?
        var cost: WidgetSnapshotMoney

        enum CodingKeys: String, CodingKey {
            case date, cost
            case totalTokens
            case inputTokens
            case cacheTokens
            case outputTokens
        }
    }

    struct By: Codable, Equatable {
        var harness: [Row]
        var model: [Row]
        var project: [Row]
        struct Row: Codable, Equatable {
            var key: String
            var name: String?
            var totalTokens: Int
            var cost: WidgetSnapshotMoney

            enum CodingKeys: String, CodingKey {
                case key, name, cost
                case totalTokens
            }
        }
    }
}

struct WidgetSnapshot: Codable, Equatable {
    static let schemaVersion = 1
    static let maxPendingItems = 6
    static let maxRunningItems = 3
    static let maxRecentItems = 8
    static let maxByRows = 5  // usage.*.by 每维度前 5 名，其余合并为 __other__

    var schema: Int = WidgetSnapshot.schemaVersion
    var generatedAt: Double
    var prefs: Prefs
    var fx: Fx
    var inbox: Inbox
    // key = day / week / month；值 = `inbox usage` 单周期输出（by 已截断）。
    var usage: [String: WidgetUsagePayload]
    var usageAt: Double
    var recent: [RecentItem]

    enum CodingKeys: String, CodingKey {
        case schema
        case generatedAt
        case prefs, fx, inbox, usage
        case usageAt
        case recent
    }

    struct Prefs: Codable, Equatable {
        var currency: String
        var hideTitles: Bool
        var fallback: Fallback

        enum CodingKeys: String, CodingKey {
            case currency
            case hideTitles
            case fallback
        }

        struct Fallback: Codable, Equatable {
            var period: String
            var dimension: String
            var metric: String

            enum CodingKeys: String, CodingKey {
                case period, dimension, metric
            }
        }
    }

    struct Fx: Codable, Equatable {
        var usdCny: Double
        var asOf: String

        enum CodingKeys: String, CodingKey {
            case usdCny
            case asOf
        }
    }

    struct Inbox: Codable, Equatable {
        var pending: Int
        var running: Int
        var pendingItems: [Item]
        var runningItems: [Item]

        enum CodingKeys: String, CodingKey {
            case pending, running
            case pendingItems
            case runningItems
        }

        struct Item: Codable, Equatable {
            var id: String
            var revision: Int
            var provider: String
            var title: String
            var project: String
            var state: String
            var at: Double
        }
    }

    struct RecentItem: Codable, Equatable {
        var id: String
        var revision: Int
        var provider: String
        var title: String
        var project: String
        var state: String
        var at: Double
        // 今日用量：取自今日报告中 (provider, session_id) 匹配的任务；
        // 今天没有用量的任务两字段均为 nil，组件显示 —。
        var todayTokens: Int?
        var todayCost: WidgetSnapshotMoney?

        enum CodingKeys: String, CodingKey {
            case id, revision, provider, title, project, state, at
            case todayTokens
            case todayCost
        }
    }
}

// by 维度截断（§2）：每个维度保留前 5 名，其余合并为 {"key": "__other__"}；
// 调用方传入前 Python 已按金额降序排序，这里只负责截断与合并。
enum WidgetSnapshotLimiter {
    static func truncate(_ rows: [WidgetUsagePayload.By.Row]) -> [WidgetUsagePayload.By.Row] {
        guard rows.count > WidgetSnapshot.maxByRows else { return rows }
        let kept = Array(rows.prefix(WidgetSnapshot.maxByRows))
        var other = WidgetUsagePayload.By.Row(
            key: "__other__", name: nil, totalTokens: 0, cost: .empty
        )
        for row in rows.dropFirst(WidgetSnapshot.maxByRows) {
            other.totalTokens += row.totalTokens
            other.cost.unpricedTokens += row.cost.unpricedTokens
            for (currency, amount) in row.cost.input {
                other.cost.input[currency, default: 0] += amount
            }
            for (currency, amount) in row.cost.cache {
                other.cost.cache[currency, default: 0] += amount
            }
            for (currency, amount) in row.cost.output {
                other.cost.output[currency, default: 0] += amount
            }
            if let fallback = row.cost.nativeFallback {
                var merged = other.cost.nativeFallback ?? [:]
                for (currency, amount) in fallback {
                    merged[currency, default: 0] += amount
                }
                other.cost.nativeFallback = merged
            }
        }
        return kept + [other]
    }
}
