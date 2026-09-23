import Foundation

// 组件快照数据结构（desktop-widgets.md §2）：App 与组件编译同一份文件。
// 快照只包含收件箱已保存的元数据与数字，不新增任何正文内容；
// hide_titles 打开时标题根本不写入文件（写空字符串），而不只是显示时隐藏。
// Python 侧金额对象 / `inbox usage` 输出为 snake_case，统一用 CodingKeys 映射。

struct WidgetSnapshotMoney: Codable, Equatable {
    var input: [String: Double]
    var cache: [String: Double]
    var output: [String: Double]
    var unpricedTokens: Int
    var nativeFallback: [String: Double]?

    enum CodingKeys: String, CodingKey {
        case input, cache, output
        case unpricedTokens = "unpriced_tokens"
        case nativeFallback = "native_fallback"
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

    enum CodingKeys: String, CodingKey {
        case period, start, end
        case isCurrent = "is_current"
        case totals, previous, series, by
    }

    struct Totals: Codable, Equatable {
        var inputTokens: Int
        var cacheTokens: Int
        var outputTokens: Int
        var totalTokens: Int
        var cost: WidgetSnapshotMoney

        enum CodingKeys: String, CodingKey {
            case cost
            case inputTokens = "input_tokens"
            case cacheTokens = "cache_tokens"
            case outputTokens = "output_tokens"
            case totalTokens = "total_tokens"
        }
    }

    struct Previous: Codable, Equatable {
        var totalTokens: Int
        var cost: WidgetSnapshotMoney

        enum CodingKeys: String, CodingKey {
            case cost
            case totalTokens = "total_tokens"
        }
    }

    struct SeriesRow: Codable, Equatable {
        var date: String
        var totalTokens: Int
        var cost: WidgetSnapshotMoney

        enum CodingKeys: String, CodingKey {
            case date, cost
            case totalTokens = "total_tokens"
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
                case totalTokens = "total_tokens"
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
        case generatedAt = "generated_at"
        case prefs, fx, inbox, usage
        case usageAt = "usage_at"
        case recent
    }

    struct Prefs: Codable, Equatable {
        var currency: String
        var hideTitles: Bool
        var fallback: Fallback

        enum CodingKeys: String, CodingKey {
            case currency
            case hideTitles = "hide_titles"
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
            case usdCny = "USD_CNY"
            case asOf = "as_of"
        }
    }

    struct Inbox: Codable, Equatable {
        var pending: Int
        var running: Int
        var pendingItems: [Item]
        var runningItems: [Item]

        enum CodingKeys: String, CodingKey {
            case pending, running
            case pendingItems = "pending_items"
            case runningItems = "running_items"
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
            case todayTokens = "today_tokens"
            case todayCost = "today_cost"
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
