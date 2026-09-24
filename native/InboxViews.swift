import AppKit
import SwiftUI

// 「新建会话」行：已安装 agent 图标按钮平铺；放不下时行尾收敛为「+N」图标面板，点击列出剩余 agent。
// GeometryReader 独占整行拿可用宽度，按预算常数折算容量——不做子视图测量，避免布局提案耦合
// （裸 swiftc 构建没有 SwiftUIMacros，视图里用不了 @State）。
// 「+N」不用 SwiftUI Menu：macOS 菜单项不渲染自定义图片，只剩文字；改为 popover 图标面板与主行视觉统一。
final class OverflowPanelState: ObservableObject {
    @Published var isPresented = false
}

struct NewSessionLauncherRow: View {
    @ObservedObject var model: InboxModel
    @StateObject private var overflow = OverflowPanelState()
    // 标签「新建会话」自然宽度 ≤ 48pt，加一处 12pt 间距与 8pt 保险；宁可提前出「+N」也不裁切按钮。
    private let labelBudget: CGFloat = 68

    var body: some View {
        GeometryReader { proxy in
            let total = model.agents.count
            let visible = visibleCount(available: proxy.size.width, total: total)
            HStack(spacing: 12) {
                Text("新建会话").font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: true, vertical: false)
                ForEach(model.agents.prefix(visible)) { agent in
                    launcherButton(agent)
                }
                if visible < total {
                    overflowMenu(hidden: Array(model.agents.suffix(total - visible)))
                }
                Spacer()
            }
        }
        .frame(height: 36)
    }

    // 每个按钮占 36pt 图标 + 12pt 间距；全部放得下就不显示「+N」。
    private func visibleCount(available: CGFloat, total: Int) -> Int {
        let usable = max(0, available - labelBudget)
        if CGFloat(48 * total) - 12 <= usable + 0.5 { return total }
        return max(1, Int((usable - 36) / 48))
    }

    @ViewBuilder
    private func launcherButton(_ agent: AgentEntry) -> some View {
        Button {
            model.launch(agent)
        } label: {
            Image(nsImage: agentIcon(agent.id, size: 24))
                .frame(width: 24, height: 24)
                .frame(width: 36, height: 36)
                .background(Color.primary.opacity(0.05), in: RoundedRectangle(cornerRadius: 10))
        }
        .buttonStyle(.plain)
        .disabled(!agent.iterm)
        .accessibilityLabel("新建 \(agent.name) 会话")
        .help(agent.iterm ? "选择目录并在 iTerm2 新标签中启动 \(agent.name)" : "未检测到 iTerm2，无法在此启动")
    }

    private func overflowMenu(hidden: [AgentEntry]) -> some View {
        Button {
            overflow.isPresented = true
        } label: {
            Text("+\(hidden.count)")
                .font(.system(size: 12, weight: .semibold)).monospacedDigit()
                .foregroundStyle(.secondary)
                .frame(width: 36, height: 36)
                .background(Color.primary.opacity(0.05), in: RoundedRectangle(cornerRadius: 10))
        }
        .buttonStyle(.plain)
        .popover(isPresented: $overflow.isPresented, arrowEdge: .bottom) {
            HStack(spacing: 12) {
                ForEach(hidden) { agent in
                    Button {
                        overflow.isPresented = false
                        model.launch(agent)
                    } label: {
                        Image(nsImage: agentIcon(agent.id, size: 24))
                            .frame(width: 24, height: 24)
                            .frame(width: 36, height: 36)
                            .background(Color.primary.opacity(0.05), in: RoundedRectangle(cornerRadius: 10))
                    }
                    .buttonStyle(.plain)
                    .disabled(!agent.iterm)
                    .accessibilityLabel("新建 \(agent.name) 会话")
                    .help(agent.iterm ? "选择目录并在 iTerm2 新标签中启动 \(agent.name)" : "未检测到 iTerm2，无法在此启动")
                }
            }
            .padding(10)
        }
        .accessibilityLabel("其余 \(hidden.count) 个 agent")
        .help("其余 \(hidden.count) 个 agent：" + hidden.map { $0.name }.joined(separator: "、"))
    }
}
struct InboxView: View {
    @ObservedObject var model: InboxModel
    @Environment(\.openWindow) private var openWindow
    // 控制类图标统一描边族（2026-09-20 用户要求）：铃铛只保留开/关两态，
    // 授权与否看列表下方状态行与按钮 help，不再用实心/徽标变体混重量。
    private var notificationSymbol: String {
        model.notificationsEnabled ? "bell" : "bell.slash"
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("会话通知").font(.system(size: 22, weight: .bold))
            if !model.agents.isEmpty {
                NewSessionLauncherRow(model: model)
            }
            VStack(spacing: 8) {
                // 页签全宽分布（2026-09-20 终位：搜索图标移入工具栏，行内不再放
                // 其它元素）：左右对称，括号计数变化只改段内边界，控件整体不动。
                // 搜索点工具栏按钮展开，输入框在本行下方整行出现。
                HStack {
                    Picker("显示范围", selection: $model.scope) {
                        Text("待查看（\(model.unreadCount)）").tag(InboxScope.pending)
                        Text("进行中（\(model.activeCount)）").tag(InboxScope.active)
                        Text("全部（\(model.rows.count)）").tag(InboxScope.all)
                    }
                    .pickerStyle(.segmented)
                    .labelsHidden()
                    .frame(maxWidth: .infinity)
                }
                if model.searchExpanded {
                    InboxSearchField(model: model)
                }
            }
            .padding(.top, 8)
            if model.visible.isEmpty {
                Spacer()
                VStack(spacing: 10) {
                    Image(systemName: model.query.isEmpty ? "tray" : "magnifyingglass")
                        .font(.system(size: 30)).foregroundStyle(.tertiary)
                    Text(model.loading ? "正在加载会话…" : (!model.query.isEmpty ? "没有匹配的会话" : (model.scope == .active ? "当前没有正在运行的会话" : (model.scope == .all ? "还没有会话" : "暂无新通知"))))
                        .font(.subheadline).foregroundStyle(.secondary)
                }.frame(maxWidth: .infinity)
                Spacer()
            } else {
                ScrollView {
                    LazyVStack(spacing: 0) {
                        ForEach(model.visible) { row in
                            InboxRowView(model: model, row: row)
                            Divider().padding(.leading, 44)
                        }
                        if model.scope == .all && model.page + 1 < model.totalPages {
                            HStack(spacing: 8) {
                                ProgressView().controlSize(.small)
                                Text("继续加载").font(.caption2).foregroundStyle(.tertiary)
                            }
                            .frame(maxWidth: .infinity).padding(.vertical, 6)
                            .onAppear { model.page += 1 }
                        }
                    }.padding(.vertical, 2)
                }
            }
            if let error = model.error {
                HStack(alignment: .top, spacing: 8) {
                    Image(systemName: "exclamationmark.triangle.fill").font(.caption).foregroundStyle(.red)
                    Text(error).font(.caption).foregroundStyle(.red).textSelection(.enabled)
                    Spacer()
                    Button { model.error = nil } label: { Image(systemName: "xmark").font(.caption2) }
                        .buttonStyle(.plain).foregroundStyle(.secondary)
                }
                .padding(10)
                .background(Color.red.opacity(0.06), in: RoundedRectangle(cornerRadius: 8))
            }
            if !model.degraded.isEmpty {
                Text(model.degraded.joined(separator: " · ")).font(.caption).foregroundStyle(.orange)
            }
            if model.notificationsEnabled && !model.notificationsAllowed {
                Text(model.notificationStatus).font(.caption).foregroundStyle(.secondary)
            }
            // 底部固定高度行：待查看且有待办时承载批量已读栏，其余分段显示提示
            // 文案。两种内容同高互换——零新增空间、切换零位移，批量栏不再占用
            // 列表上方任何位置。默认无勾选，勾选任意子集后逐项确认；全选覆盖当前
            // 全部待查看。
            ZStack(alignment: .leading) {
                if model.scope == .pending && model.unreadCount > 0 {
                    HStack(spacing: 6) {
                        Button { model.toggleSelectAll() } label: {
                            HStack(spacing: 4) {
                                Image(systemName: model.allPendingSelected ? "checkmark.circle.fill" : "circle")
                                Text("全选")
                            }
                            .font(.caption)
                            .foregroundStyle(model.allPendingSelected ? Color.accentColor : Color.secondary)
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .help(model.allPendingSelected ? "取消全部勾选" : "勾选全部待查看事项")
                        .accessibilityLabel(model.allPendingSelected ? "全不选" : "全选")
                        Spacer()
                        if model.selectedCount > 0 {
                            Button { model.acknowledgeSelected() } label: {
                                Label("标记已读（\(model.selectedCount)）", systemImage: "checkmark.circle")
                                    .font(.caption)
                            }
                            .buttonStyle(.plain)
                            .foregroundStyle(Color.accentColor)
                            .help("把勾选的事项标记已读；确认瞬间有新活动的项保留未读")
                            .accessibilityLabel("批量标记已读")
                        }
                    }
                } else {
                    Text("打开会话后自动标记已读")
                        .font(.caption2).foregroundStyle(.tertiary)
                }
            }
            .frame(height: 22)
        }
        .padding(16)
        .frame(minWidth: 360, idealWidth: 400, minHeight: 480, idealHeight: 620)
        .background(Color(nsColor: .windowBackgroundColor))
        // 头部动作进窗口工具栏：macOS 26 起系统自动给工具栏项套 Liquid Glass，
        // 内容标题留在页内（工具栏用 unifiedCompact 且不显示标题，避免重复）。
        .toolbar {
            // 紧凑工具栏无标题时项目从左排起：先放弹性空白，把动作组推到右侧。
            ToolbarItem(placement: .automatic) { Spacer() }
            ToolbarItemGroup(placement: .primaryAction) {
                Button {
                    openWindow(id: "dailyReport")
                    NSApplication.shared.activate(ignoringOtherApps: true)
                } label: {
                    Label("工作日报", systemImage: "chart.bar.doc.horizontal")
                }
                .help("工作日报")
                // 铃铛开/关两态（描边族统一）；授权状态见列表下方状态行与按钮 help。
                // 工具栏项会缓存 label，按状态换 id 强制重建，否则切换后图标不刷新。
                Button { model.toggleNotifications() } label: {
                    Label("通知", systemImage: notificationSymbol)
                }
                .id(notificationSymbol)
                .help(model.notificationsEnabled ? model.notificationStatus + "（点击关闭）" : "点击开启消息通知")
                Button { model.toggleAgentSessions() } label: {
                    Label("显示 agent 会话", systemImage: model.showAgentSessions ? "eye" : "eye.slash")
                }
                .id(model.showAgentSessions ? "agents-shown" : "agents-hidden")
                .help(model.showAgentSessions ? "隐藏其它工具拉起的 agent 会话" : "显示其它工具拉起的 agent 会话（默认不通知、不进待查看）")
                // 搜索开关（2026-09-20 替换原刷新按钮——手动刷新托盘菜单仍有）：
                // 图标随展开态切换，与通知铃铛同款按 id 强制重建避免工具栏缓存 label。
                Button { model.setSearchExpanded(!model.searchExpanded) } label: {
                    // 展开态用 xmark（动作=收起）：circle 变体把放大镜本体缩小在圆内，
                    // 与相邻按钮视觉重量差太多；magnifyingglass↔xmark 同光学尺寸。
                    Label("搜索", systemImage: model.searchExpanded ? "xmark" : "magnifyingglass")
                }
                .id(model.searchExpanded ? "search-open" : "search-closed")
                .help(model.searchExpanded ? "收起搜索" : "搜索会话或项目")
            }
        }
        .onAppear {
            model.refresh()
            // 组件 URL 在窗口未开时由 bridge 暂存，窗口挂载即补送（§5）。
            WidgetURLBridge.shared.flush { handleWidgetURL($0) }
        }
        .onReceive(NotificationCenter.default.publisher(for: .widgetURLOpen)) { notification in
            if let url = notification.object as? URL { handleWidgetURL(url) }
        }
        // R10：行数据未就绪时收到的 URL 会留在队列，首批行加载完成后补处理。
        .onReceive(model.$rows.map(\.isEmpty).removeDuplicates()) { empty in
            if !empty { WidgetURLBridge.shared.flush { handleWidgetURL($0) } }
        }
    }

    // 组件 URL 跳转（desktop-widgets.md §5）：行未就绪时挂起不丢；非法 URL
    // （就绪但 id 不存在/参数非法）标记处理后忽略、不输出参数原文。
    private func handleWidgetURL(_ url: URL) {
        // R10 冷启动：行未就绪时放回队列（flush 已清空，直接 return 会丢 URL），
        // rows 首批加载后 flush 补处理。
        guard !model.rows.isEmpty else {
            WidgetURLBridge.shared.enqueue(url)
            return
        }
        defer { WidgetURLBridge.shared.markHandled(url) }
        guard let action = WidgetURLRouter.parse(url, knownIds: Set(model.rows.map(\.id))) else { return }
        NSApplication.shared.activate()
        switch action {
        case .open(let id, let revision):
            model.openByID(id, revision: revision)
        case .report(let period):
            NotificationCenter.default.post(name: .widgetReportPeriod, object: period)
            openWindow(id: "dailyReport")
        case .inbox:
            openWindow(id: "inbox")
        }
    }
}

// 展开态搜索框：出现即聚焦；关闭清空关键词，让列表回到未过滤状态。
struct InboxSearchField: View {
    @ObservedObject var model: InboxModel
    @FocusState private var focused: Bool
    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: "magnifyingglass").font(.caption).foregroundStyle(.secondary)
            TextField("搜索会话或项目", text: $model.query)
                .textFieldStyle(.plain)
                .focused($focused)
                .onExitCommand { model.setSearchExpanded(false) }
            Button { model.setSearchExpanded(false) } label: {
                Image(systemName: "xmark.circle.fill").font(.caption).foregroundStyle(.tertiary)
            }
            .buttonStyle(.plain)
            .help("关闭搜索")
            .accessibilityLabel("关闭搜索")
        }
        .padding(.horizontal, 10).padding(.vertical, 8)
        .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(Color.primary.opacity(0.06)))
        .onAppear { focused = true }
    }
}

struct InboxActionButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .foregroundStyle(isEnabled ? Color.primary.opacity(0.75) : Color.secondary.opacity(0.4))
            .background {
                RoundedRectangle(cornerRadius: 8)
                    .fill(Color.primary.opacity(isEnabled && configuration.isPressed ? 0.12 : 0.035))
            }
            .contentShape(RoundedRectangle(cornerRadius: 8))
            .scaleEffect(isEnabled && configuration.isPressed ? 0.90 : 1)
            .animation(.easeOut(duration: 0.12), value: configuration.isPressed)
    }
}

struct InboxRowView: View {
    @ObservedObject var model: InboxModel
    let row: InboxRow
    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            // 主内容为 agent 图标；待处理时右上角橙色圆点，描边取窗口背景色，
            // 亮色/暗色模式自动适配。
            // 已退出与已结束对用户含义一致（都可重开查看），外观保持一致，不做置灰。
            Image(nsImage: agentIcon(row.provider, size: 30))
                .frame(width: 30, height: 30)
                .overlay(alignment: .topTrailing) {
                    if row.unread {
                        Circle()
                            .fill(Color.attention)
                            .frame(width: 9, height: 9)
                            .overlay(Circle().strokeBorder(Color(nsColor: .windowBackgroundColor), lineWidth: 1.5))
                            .offset(x: 3, y: -3)
                    }
                }
                .help(providerName(row.provider))
                // 批量选择圈（Mail 式）：悬停浮现、选中常显，盖在图标位置上；
                // 图标列三个分段恒为 30pt，不新增列宽、不产生水平位移。
                .overlay {
                    if model.scope == .pending {
                        let selected = model.selected.contains(row.id)
                        Button { model.toggleSelected(row.id) } label: {
                            Image(systemName: selected ? "checkmark.circle.fill" : "circle")
                                .font(.system(size: 16))
                                .foregroundStyle(selected ? Color.accentColor : Color.secondary)
                                .frame(width: 30, height: 30)
                                .background(Color(nsColor: .windowBackgroundColor).opacity(0.85))
                                .clipShape(Circle())
                        }
                        .buttonStyle(.plain)
                        .opacity(selected || model.hoveringRow == row.id ? 1 : 0)
                        .allowsHitTesting(selected || model.hoveringRow == row.id)
                        .help(selected ? "取消选择" : "选择该会话")
                        .accessibilityLabel((selected ? "取消选择 " : "选择 ") + row.title)
                    }
                }
                .padding(.top, 1)
            VStack(alignment: .leading, spacing: 5) {
                Text(row.title)
                    .font(.body.weight(row.unread ? .semibold : .medium))
                    .lineLimit(2)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .help(row.title)
                HStack(spacing: 5) {
                    Text(stateName(row.state))
                        .foregroundStyle(row.unread ? AnyShapeStyle(Color.attention) : AnyShapeStyle(.secondary))
                        .fixedSize()
                    if !row.project.isEmpty {
                        Text("·").foregroundStyle(.tertiary)
                        Text((row.project as NSString).lastPathComponent)
                            .foregroundStyle(.secondary)
                            .lineLimit(1).help(row.project)
                    }
                }.font(.subheadline)
                // 会话时长只在未处理期间展示：从最近一次通知起实时累计（随刷新周期）；
                // 已处理即不再展示，来源侧清未读（如 Interrupt）同样不展示。
                if row.unread, let start = row.attentionAt, start > 0 {
                    Text(inboxDurationText(from: start, to: Date().timeIntervalSince1970))
                        .font(.footnote).foregroundStyle(.secondary)
                        .help("会话时长：从最近一次通知起累计")
                }
            }
            HStack(spacing: 4) {
                if row.unread {
                    Button { model.acknowledge(row) } label: {
                        Image(systemName: "checkmark.circle").frame(width: 28, height: 30)
                    }
                    .help("标记已读").accessibilityLabel("标记已读")
                }
                Button { model.open(row) } label: {
                    Image(systemName: "arrow.up.right.square").frame(width: 28, height: 30)
                }
                .help("前往会话").accessibilityLabel("前往会话")
                .disabled(!row.openAvailable || model.opening)
            }
            .font(.system(size: 15)).foregroundStyle(.secondary)
            .buttonStyle(InboxActionButtonStyle())
        }
        .padding(.horizontal, 4).padding(.vertical, 12)
        .background(row.unread ? Color.attention.opacity(0.045) : Color.clear, in: RoundedRectangle(cornerRadius: 8))
        // 右键改判 origin：自动分类的最终兜底，用户说了算。目录规则读时覆盖、即时生效。
        .contextMenu {
            Button("标记为 agent 会话（隐藏，仅此条）") {
                model.setOrigin(row, origin: "agent", ruleProject: nil)
            }
            Button("恢复人工（仅此条）") {
                model.setOrigin(row, origin: "user", ruleProject: nil)
            }
            if !row.project.isEmpty {
                let dir = (row.project as NSString).lastPathComponent
                Divider()
                Button("「\(dir)」目录的会话都按 agent 过滤") {
                    model.setOrigin(row, origin: "agent", ruleProject: row.project)
                }
                Button("「\(dir)」目录的会话恢复人工") {
                    model.setOrigin(row, origin: "user", ruleProject: row.project)
                }
            }
        }
        .onHover { inside in model.hoveringRow = inside ? row.id : nil }
        .onAppear { model.loadMoreIfNeeded(for: row) }
    }
}
