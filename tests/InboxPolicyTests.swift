import Foundation

@main struct PolicyTests {
    static func main() {
        precondition(inboxPageCount(total: 41, size: 20) == 3)
        precondition(inboxPageRange(total: 41, page: 2, size: 20) == 40..<41)
        precondition(inboxPageRange(total: 0, page: 0, size: 20).isEmpty)
        precondition(inboxPageRange(total: 5, page: 2, size: 20).isEmpty)
        precondition(!needsNotification(unread: true, token: "a", seen: nil, initialSnapshot: true, enabled: true))
        precondition(!needsNotification(unread: true, token: "a", seen: "a", initialSnapshot: false, enabled: true))
        precondition(!needsNotification(unread: false, token: "b", seen: "a", initialSnapshot: false, enabled: true))
        precondition(!needsNotification(unread: true, token: "b", seen: "a", initialSnapshot: false, enabled: false))
        precondition(needsNotification(unread: true, token: "b", seen: "a", initialSnapshot: false, enabled: true))
        precondition([pendingRank("waiting"), pendingRank("failed"), pendingRank("running"), pendingRank("idle")] == [0, 1, 2, 2])
        // closed 且入口不可用的行（如 /clear 后被替换的受管理会话）不展示也不计入待查看。
        precondition(!inboxRowListed(state: "closed", openAvailable: false))
        precondition(inboxRowListed(state: "closed", openAvailable: true))
        precondition(inboxRowListed(state: "idle", openAvailable: false))
        precondition(inboxRowListed(state: "waiting", openAvailable: false))
        // agent 会话只审计不提醒（评审 R4）：通知/待查看/角标按有效来源过滤；
        // origin 缺失（旧后端）视为可提醒。
        precondition(!inboxNotifyEligible(origin: "agent", unread: true))
        precondition(inboxNotifyEligible(origin: "user", unread: true))
        precondition(inboxNotifyEligible(origin: nil, unread: true))
        precondition(!inboxNotifyEligible(origin: "user", unread: false))
        // 进行中分段只收「等待模型回复」的回合：waiting（等用户确认权限）、开着空闲、
        // 出错/中断/已结束都不算（2026-09-20 用户确认口径）。
        precondition(inboxActiveListed(state: "running", openAvailable: false))
        precondition(inboxActiveListed(state: "running", openAvailable: true))
        precondition(!inboxActiveListed(state: "waiting", openAvailable: true))
        precondition(!inboxActiveListed(state: "idle", openAvailable: true))
        precondition(!inboxActiveListed(state: "failed", openAvailable: true))
        precondition(!inboxActiveListed(state: "interrupted", openAvailable: true))
        precondition(!inboxActiveListed(state: "closed", openAvailable: true))
        // 刷新节拍（墙钟）：距上次全量发起 ≥15 秒才再扫，丢拍由下一个 tick 按经过时间自动补。
        precondition(!inboxTickShouldScan(elapsed: 0, fullEvery: 15))
        precondition(!inboxTickShouldScan(elapsed: 14.9, fullEvery: 15))
        precondition(inboxTickShouldScan(elapsed: 15, fullEvery: 15))
        precondition(inboxTickShouldScan(elapsed: 3600, fullEvery: 15))  // 唤醒/久置后立即补扫
        precondition(inboxTickShouldScan(elapsed: 1, fullEvery: 0))  // 非法参数退化为每 tick 都扫
        precondition(inboxDurationText(from: 0, to: 59) == "59秒")
        precondition(inboxDurationText(from: 100, to: 200) == "1分40秒")
        precondition(inboxDurationText(from: 0, to: 3700) == "1小时1分")
        precondition(inboxDurationText(from: 0, to: 90000) == "1天1小时")
        precondition(inboxDurationText(from: 200, to: 100) == "0秒")
        precondition(tokenText(0) == "0")
        precondition(tokenText(895) == "895")
        precondition(tokenText(6_594) == "6.6k")
        precondition(tokenText(456_700) == "457k")
        precondition(tokenText(613_613) == "614k")
        precondition(tokenText(1_000_000) == "1M")
        precondition(tokenText(55_703_404) == "55.7M")
        precondition(tokenText(143_168_263) == "143M")
        precondition(tokenText(553_010_996) == "553M")
        precondition(tokenText(1_000_000_000) == "1B")
        precondition(tokenText(1_230_000_000) == "1.23B")
        precondition(dailyReportDayKey(Calendar.current.date(from: DateComponents(year: 2026, month: 9, day: 14))!) == "2026-09-14")
        // 节奏带：跨零点末次时间（次日 00:00）算 24 而不是 0；范围取偶数刻度；无段回退 8–24。
        let dayStart = 1_000_000.0
        precondition(rhythmHour(dayStart + 24 * 3600, dayStart: dayStart) == 24)
        precondition(rhythmHour(dayStart - 3600, dayStart: dayStart) == 0)
        precondition(rhythmHour(dayStart + 9.5 * 3600, dayStart: dayStart) == 9.5)
        precondition(rhythmRange([[[dayStart + 9.5 * 3600, dayStart + 10 * 3600]],
                                  [[dayStart + 16 * 3600, dayStart + 24 * 3600]]], dayStart: dayStart) == (8, 24))
        precondition(rhythmRange([[[dayStart + 1 * 3600, dayStart + 1 * 3600]]], dayStart: dayStart) == (0, 2))
        precondition(rhythmRange([[]], dayStart: dayStart) == (8, 24))
        // ---- 金额文字 moneyText：与 Python money_text 同一组边界值（usage-cost.md §5 / U8）。
        precondition(moneyText(nil, currency: "CNY") == "—")
        precondition(moneyText(nil, currency: "USD") == "—")
        precondition(moneyText(0, currency: "USD") == "$0.00")
        precondition(moneyText(0, currency: "CNY") == "¥0.00")
        precondition(moneyText(0.005, currency: "USD") == "<$0.01")
        precondition(moneyText(0.0099, currency: "CNY") == "<¥0.01")
        precondition(moneyText(0.01, currency: "USD") == "$0.01")
        precondition(moneyText(0.0149, currency: "CNY") == "¥0.01")
        precondition(moneyText(1.5, currency: "USD") == "$1.50")
        precondition(moneyText(1234.56, currency: "USD") == "$1,234.56")
        precondition(moneyText(1234567.891, currency: "CNY") == "¥1,234,567.89")
        // 展示换算：CNY 视图 = USD×rate + CNY；USD 视图 = USD + CNY/rate。
        precondition(convertAmount(usd: 1, cny: 7, to: "CNY", rate: 7) == 14)
        precondition(convertAmount(usd: 1, cny: 7, to: "USD", rate: 7) == 2)
        // ---- 组件快照刷新策略（desktop-widgets.md §3 / W3）。
        let ids = [(id: "a", revision: 1, state: "waiting"), (id: "b", revision: 2, state: "running")]
        precondition(WidgetRefreshPolicy.signature(ids) == "a:1:waiting|b:2:running")
        let base = WidgetRefreshPolicy.Inputs(
            inboxSignature: "i", recentSignature: "r", prefsSignature: "p",
            last: ("i", "r", "p"), lastRecentReload: 1000, lastUsageReload: 1000, now: 1000
        )
        // 签名全部不变：不写文件、不 reload。
        var decision = WidgetRefreshPolicy.evaluate(base)
        precondition(!decision.writeSnapshot && decision.reload.isEmpty)
        // inbox 条目 revision 变化：立即写并 reload inbox。
        decision = WidgetRefreshPolicy.evaluate(
            WidgetRefreshPolicy.Inputs(
                inboxSignature: "i2", recentSignature: "r", prefsSignature: "p",
                last: ("i", "r", "p"), lastRecentReload: 1000, lastUsageReload: 1000, now: 1000
            )
        )
        precondition(decision.writeSnapshot && decision.reload == [.inbox])
        // recent 变化但在 60 秒最小间隔内：写快照但不 reload（合并到下一次）。
        decision = WidgetRefreshPolicy.evaluate(
            WidgetRefreshPolicy.Inputs(
                inboxSignature: "i", recentSignature: "r2", prefsSignature: "p",
                last: ("i", "r", "p"), lastRecentReload: 980, lastUsageReload: 1000, now: 1030
            )
        )
        precondition(decision.writeSnapshot && decision.reload.isEmpty)
        // recent 变化且距上次 reload ≥60 秒：写并 reload recent。
        decision = WidgetRefreshPolicy.evaluate(
            WidgetRefreshPolicy.Inputs(
                inboxSignature: "i", recentSignature: "r2", prefsSignature: "p",
                last: ("i", "r", "p"), lastRecentReload: 900, lastUsageReload: 1000, now: 1030
            )
        )
        precondition(decision.writeSnapshot && decision.reload == [.recent])
        // usage 到期（15 分钟）：写并 reload usage；prefs/fx 变化 reload 全部。
        decision = WidgetRefreshPolicy.evaluate(
            WidgetRefreshPolicy.Inputs(
                inboxSignature: "i", recentSignature: "r", prefsSignature: "p",
                last: ("i", "r", "p"), lastRecentReload: 0, lastUsageReload: 0, now: 1900
            )
        )
        precondition(decision.writeSnapshot && decision.reload == [.usage])
        decision = WidgetRefreshPolicy.evaluate(
            WidgetRefreshPolicy.Inputs(
                inboxSignature: "i", recentSignature: "r", prefsSignature: "p2",
                last: ("i", "r", "p"), lastRecentReload: 0, lastUsageReload: 0, now: 1000
            )
        )
        precondition(decision.writeSnapshot && decision.reload == Set(WidgetKind.allCases))
        // ---- 组件 URL 解析（desktop-widgets.md §5 / W4）。
        func parse(_ text: String, known: Set<String> = ["row-1"]) -> WidgetURLRouter.Action? {
            WidgetURLRouter.parse(URL(string: text), knownIds: known)
        }
        precondition(parse("agentnotification://open?id=row-1&revision=12") == .open(id: "row-1", revision: 12))
        precondition(parse("agentnotification://open?id=row-1") == .open(id: "row-1", revision: nil))
        precondition(parse("agentnotification://open?id=missing&revision=1") == nil)  // id 不在行集合
        precondition(parse("agentnotification://open?id=row-1&revision=-1") == nil)  // revision 必须非负
        precondition(parse("agentnotification://open?id=row-1&revision=x") == nil)  // revision 必须是整数
        precondition(parse("agentnotification://open?revision=1") == nil)  // 缺 id
        precondition(parse("agentnotification://report?period=week") == .report(period: "week"))
        precondition(parse("agentnotification://report?period=year") == nil)  // period 枚举外
        precondition(parse("agentnotification://report") == nil)  // 缺 period
        precondition(parse("agentnotification://inbox") == .inbox)
        precondition(parse("agentnotification://unknown") == nil)  // host 白名单外
        precondition(parse("otherscheme://inbox") == nil)  // scheme 不符
        precondition(WidgetURLRouter.parse(nil, knownIds: ["row-1"]) == nil)
        // ---- R10 组件 URL 桥状态机：防抖、队列不重放、挂起补处理。
        var gate = WidgetURLGate()
        let urlA = URL(string: "agentnotification://open?id=a&revision=1")!
        precondition(gate.accept(urlA, now: 100))  // 首次点击接受
        precondition(!gate.accept(urlA, now: 101))  // 2 秒窗口内重复点击拒绝
        precondition(gate.accept(urlA, now: 103))  // 窗口外再次点击接受（同条目可再开）
        let urlB = URL(string: "agentnotification://inbox")!
        precondition(gate.accept(urlB, now: 103))  // 不同 URL 立即接受
        var queue = WidgetURLQueue()
        queue.enqueue(urlA)
        queue.enqueue(urlB)
        queue.enqueue(urlA)  // 队列内去重
        precondition(queue.pending.count == 2)
        queue.markHandled(urlA)  // 通知路径已处理：flush 不再重放
        var handled: [URL] = []
        for url in queue.flush() { handled.append(url) }
        precondition(handled == [urlB])
        precondition(queue.pending.isEmpty)
        // 挂起语义：enqueue 后不 markHandled，flush 时补处理。
        queue.enqueue(urlB)
        precondition(queue.flush() == [urlB])
        // ---- R8：组件进行中口径（只排除 agent，不要求未读）。
        precondition(widgetRunningListed(origin: "user", state: "running", openAvailable: false))
        precondition(widgetRunningListed(origin: nil, state: "running", openAvailable: true))
        precondition(!widgetRunningListed(origin: "agent", state: "running", openAvailable: true))
        precondition(!widgetRunningListed(origin: "user", state: "idle", openAvailable: true))
        precondition(!widgetRunningListed(origin: "user", state: "waiting", openAvailable: true))
        // ---- R15：hide_titles 只清标题，项目名保留。
        precondition(widgetEntryTitle(hideTitles: true, title: "标题") == "")
        precondition(widgetEntryTitle(hideTitles: true, title: "") == "")
        precondition(widgetEntryTitle(hideTitles: false, title: "标题") == "标题")
        // V080-R15（H0925-2）：写入器经 widgetEntryText 取条目文字，hide_titles 时项目名必须保留。
        precondition(widgetEntryText(hideTitles: true, title: "标题", project: "/work/x")
            == WidgetEntryText(title: "", project: "/work/x"))
        precondition(widgetEntryText(hideTitles: false, title: "标题", project: "/work/x")
            == WidgetEntryText(title: "标题", project: "/work/x"))
        // ---- R10 冷启动组合（第四轮）：flush 时行未就绪（处理方把 URL 放回
        // 队列），就绪后再 flush 最终处理且只处理一次。
        var coldQueue = WidgetURLQueue()
        coldQueue.enqueue(urlA)
        var rowsReady = false
        for url in coldQueue.flush() where !rowsReady {
            coldQueue.enqueue(url)  // 未就绪：处理方放回（flush 已清空，不重入）
        }
        precondition(coldQueue.pending == [urlA])
        rowsReady = true
        var coldHandled: [URL] = []
        for url in coldQueue.flush() {
            coldHandled.append(url)
        }
        precondition(coldHandled == [urlA] && coldQueue.pending.isEmpty)
        // 金额伴随指标（2026-09-24 USD-only）：USD + CNY/rate，含无值语义。
        do {
            let money = WidgetSnapshotMoney(
                input: ["USD": 1.0, "CNY": 7.10], cache: [:], output: [:],
                unpricedTokens: 0, nativeFallback: nil)
            precondition(usdTotal(money, rate: 7.10) == 2.0)
            precondition(usdTotal(nil, rate: 7.10) == nil)
            precondition(usdText(2.0) == "$2.00")
            precondition(usdText(nil) == "—")
            // 旧快照 fallback 带 metric 键：新结构解码忽略多余键（配置精简迁移）。
            let legacy = Data(#"{"currency":"CNY","hideTitles":false,"fallback":{"period":"day","dimension":"harness","metric":"cost"}}"#.utf8)
            let decoder = JSONDecoder()
            decoder.keyDecodingStrategy = .convertFromSnakeCase
            precondition((try? decoder.decode(WidgetSnapshot.Prefs.self, from: legacy)) != nil)
        }
        // ---- V080-R9：今日用量键与查找（最近任务条目今日 token/金额的唯一实现）。
        do {
            let money = WidgetSnapshotMoney(
                input: ["USD": 0.5], cache: [:], output: [:], unpricedTokens: 0, nativeFallback: nil)
            let mapping: [String: (tokens: Int?, cost: WidgetSnapshotMoney?)] = [
                usageMapKey(provider: "claude", sessionID: "s-1"): (tokens: 1234, cost: money)
            ]
            // 同一 provider 与会话 ID 生成的键能查到对应用量，tokens 与金额都带出来。
            let found = todayUsageFor(provider: "claude", sessionID: "s-1", mapping: mapping)
            precondition(found.tokens == 1234 && found.cost == money)
            // 会话 ID 为空、provider 不同、会话 ID 不同都查不到（两字段为空）。
            for miss in [
                todayUsageFor(provider: "claude", sessionID: nil, mapping: mapping),
                todayUsageFor(provider: "codex", sessionID: "s-1", mapping: mapping),
                todayUsageFor(provider: "claude", sessionID: "s-2", mapping: mapping),
            ] {
                precondition(miss.tokens == nil && miss.cost == nil)
            }
        }
        // ---- H0926-8：今日用量计入最近任务签名。tokens 与金额从无到有、数值变化、
        // 从有到无都改变签名；用量不变时签名不变。
        do {
            let money = WidgetSnapshotMoney(
                input: ["USD": 0.01], cache: [:], output: [:], unpricedTokens: 0, nativeFallback: nil)
            let doubled = WidgetSnapshotMoney(
                input: ["USD": 0.02], cache: [:], output: [:], unpricedTokens: 0, nativeFallback: nil)
            func recent(_ tokens: Int?, _ cost: WidgetSnapshotMoney?) -> WidgetSnapshot.RecentItem {
                WidgetSnapshot.RecentItem(
                    id: "r-1", revision: 1, provider: "claude", title: "t", project: "p",
                    state: "running", at: 0, todayTokens: tokens, todayCost: cost)
            }
            func signature(_ tokens: Int?, _ cost: WidgetSnapshotMoney?) -> String {
                WidgetRefreshPolicy.recentSignature([recent(tokens, cost)], usdCnyRate: 7.10)
            }
            let empty = signature(nil, nil)
            // 从无到有：tokens、金额各自改变签名。
            precondition(signature(1234, nil) != empty)
            precondition(signature(nil, money) != empty)
            // 数值变化：tokens 增长、金额变化都改变签名；从有到无回到空用量签名。
            let used = signature(1234, money)
            precondition(signature(1235, money) != used)
            precondition(signature(1234, doubled) != used)
            precondition(signature(nil, nil) == empty)
            // 用量不变：签名不变。
            precondition(signature(1234, money) == used)
        }
        print("widget URL bridge state machine checks passed")
        print("Pagination, notification, daily report policy checks passed")
    }
}
