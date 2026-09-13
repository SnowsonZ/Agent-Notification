import AppKit
import SwiftUI

struct InboxRow: Decodable, Identifiable {
    let id: String
    let provider: String
    let title: String
    let project: String
    let state: String
    let unread: Bool
    let revision: Int
    let eventAt: Double
    let openAvailable: Bool
}
struct SourceHealth: Decodable { let status: String; let errors: Int? }
struct Health: Decodable { let sources: [String: SourceHealth]? }
struct Envelope: Decodable { let sessions: [InboxRow]; let health: Health? }

@MainActor final class InboxModel: ObservableObject {
    @Published var rows: [InboxRow] = []
    @Published var showAll = false
    @Published var query = ""
    @Published var loading = false
    @Published var opening = false
    @Published var error: String?
    @Published var degraded: [String] = []
    let root: String
    private var timer: Timer?
    init() {
        root = Bundle.main.object(forInfoDictionaryKey: "SessionManagerRoot") as? String ?? ""
        let tick = Timer(timeInterval: 3, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
        }
        RunLoop.main.add(tick, forMode: .common)
        timer = tick
    }
    var unreadCount: Int { rows.filter(\.unread).count }
    var visible: [InboxRow] {
        rows.filter { (showAll || $0.unread) && (query.isEmpty ||
            ($0.title + " " + $0.project + " " + $0.provider).localizedCaseInsensitiveContains(query)) }
    }
    nonisolated static func call(root: String, arguments: [String]) -> (Int32, Data, String) {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: root + "/scratch/iterm-probe-venv/bin/python")
        process.arguments = [root + "/scripts/inbox.py"] + arguments
        let output = Pipe(); let errors = Pipe()
        process.standardOutput = output; process.standardError = errors
        do {
            try process.run()
            let data = output.fileHandleForReading.readDataToEndOfFile()
            let diagnostic = errors.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            return (process.terminationStatus, data, String(data: diagnostic, encoding: .utf8) ?? "")
        } catch { return (1, Data(), error.localizedDescription) }
    }
    func refresh() {
        guard !loading else { return }
        loading = true
        let directory = root
        Task {
            let result = await Task.detached { Self.call(root: directory, arguments: ["rows", "--all", "--refresh"]) }.value
            loading = false
            guard result.0 == 0 else { error = result.2; return }
            do {
                let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
                let payload = try decoder.decode(Envelope.self, from: result.1)
                rows = payload.sessions
                degraded = (payload.health?.sources ?? [:]).filter { $0.value.status != "ok" }.map { name, info in
                    if let count = info.errors, count > 0 { return "\(providerName(name))：\(count) 个历史记录暂未接入" }
                    return "\(providerName(name))：来源暂不可用"
                }.sorted()
            } catch { self.error = "列表读取失败：" + error.localizedDescription }
        }
    }
    func acknowledge(_ row: InboxRow) { action(["ack", row.id, "--revision", String(row.revision)], isOpen: false) }
    func open(_ row: InboxRow) { action(["open", row.id], isOpen: true) }
    private func action(_ arguments: [String], isOpen: Bool) {
        if isOpen { opening = true }
        error = nil
        let directory = root
        Task {
            let result = await Task.detached { Self.call(root: directory, arguments: arguments) }.value
            if isOpen { opening = false }
            if result.0 != 0 {
                let raw = result.2.isEmpty ? String(data: result.1, encoding: .utf8) ?? "操作未完成" : result.2
                if let data = raw.data(using: .utf8), let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any], let reason = object["reason"] as? String {
                    error = reason
                } else { error = String(raw.prefix(500)) }
            }
            refresh()
        }
    }
}

func stateName(_ state: String) -> String {
    ["running": "运行中", "waiting": "等待输入", "idle": "本轮已结束", "failed": "发生错误",
     "interrupted": "已中断", "closed": "已退出", "unknown": "状态待确认"][state] ?? state
}
func providerName(_ provider: String) -> String {
    ["claude": "Claude", "codex": "Codex", "zcode": "Zcode", "pi": "Pi", "kimi": "Kimi"][provider] ?? provider
}
func stateColor(_ state: String) -> Color {
    switch state {
    case "waiting": return .orange
    case "failed": return .red
    case "running": return .blue
    case "idle": return .green
    default: return .secondary
    }
}

struct InboxView: View {
    @ObservedObject var model: InboxModel
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Agent 会话").font(.title2.bold())
                    Text(model.unreadCount == 0 ? "暂时没有待处理事项" : "\(model.unreadCount) 条待处理事项").foregroundStyle(.secondary)
                }
                Spacer()
                Button { model.refresh() } label: { Image(systemName: "arrow.clockwise") }
                    .help("刷新").disabled(model.loading)
            }
            Picker("显示范围", selection: $model.showAll) {
                Text("待处理").tag(false)
                Text("全部会话").tag(true)
            }.pickerStyle(.segmented)
            TextField("搜索任务、项目或 agent", text: $model.query).textFieldStyle(.roundedBorder)
            if model.visible.isEmpty {
                Spacer()
                VStack(spacing: 10) {
                    Image(systemName: "tray").font(.system(size: 32)).foregroundStyle(.secondary)
                    Text(model.query.isEmpty ? "新回复和需要输入的会话会出现在这里" : "没有匹配的会话").foregroundStyle(.secondary)
                }.frame(maxWidth: .infinity)
                Spacer()
            } else {
                ScrollView {
                    LazyVStack(spacing: 10) {
                        ForEach(model.visible) { row in
                            VStack(alignment: .leading, spacing: 8) {
                                HStack(alignment: .top) {
                                    Circle().fill(stateColor(row.state)).frame(width: 8, height: 8).padding(.top, 5)
                                    Text(row.title).font(.headline).lineLimit(2)
                                    Spacer(minLength: 0)
                                    if row.unread { Text("待处理").font(.caption).foregroundStyle(.orange) }
                                }
                                HStack(spacing: 8) {
                                    Text(providerName(row.provider)).fontWeight(.medium)
                                    Text(stateName(row.state))
                                    Spacer()
                                    if row.eventAt > 0 { Text(Date(timeIntervalSince1970: row.eventAt), style: .relative) }
                                }.font(.caption).foregroundStyle(.secondary)
                                if !row.project.isEmpty {
                                    Text((row.project as NSString).lastPathComponent).font(.caption).foregroundStyle(.secondary).lineLimit(1).help(row.project)
                                }
                                HStack {
                                    if !row.openAvailable { Text("原会话入口暂不可用").font(.caption).foregroundStyle(.secondary) }
                                    Spacer()
                                    if row.unread { Button("已处理") { model.acknowledge(row) } }
                                    Button("打开会话") { model.open(row) }.disabled(!row.openAvailable || model.opening)
                                }
                            }.padding(12).background(.background, in: RoundedRectangle(cornerRadius: 10))
                        }
                    }.padding(1)
                }
            }
            if let error = model.error {
                HStack(alignment: .top) {
                    Text(error).font(.caption).foregroundStyle(.red).textSelection(.enabled)
                    Spacer()
                    Button { model.error = nil } label: { Image(systemName: "xmark") }.buttonStyle(.plain)
                }
            }
            if !model.degraded.isEmpty {
                Text(model.degraded.joined(separator: " · ")).font(.caption).foregroundStyle(.orange)
            }
            Text("打开不会自动标为已处理 · Pi/Kimi 从受管理入口启动后显示")
                .font(.caption2).foregroundStyle(.secondary)
        }.padding(18).frame(minWidth: 480, idealWidth: 540, minHeight: 500, idealHeight: 680)
            .background(Color(nsColor: .windowBackgroundColor))
            .onAppear { model.refresh() }
    }
}

struct TrayMenu: View {
    @ObservedObject var model: InboxModel
    @Environment(\.openWindow) var openWindow
    var body: some View {
        Button("打开会话列表（\(model.unreadCount) 条待处理）") {
            openWindow(id: "inbox")
            NSApplication.shared.activate()
        }
        Button("刷新") { model.refresh() }
        Divider()
        Button("退出") { NSApplication.shared.terminate(nil) }
    }
}

@main struct SessionInboxApp: App {
    @StateObject private var model = InboxModel()
    var body: some Scene {
        WindowGroup("Agent 会话", id: "inbox") { InboxView(model: model) }
            .defaultSize(width: 540, height: 680)
        MenuBarExtra {
            TrayMenu(model: model)
        } label: {
            Label(model.unreadCount > 0 ? String(model.unreadCount) : "", systemImage: "tray.full")
        }
    }
}
