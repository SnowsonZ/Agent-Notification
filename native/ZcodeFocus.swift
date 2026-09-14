// Native Zcode UI adapter. No app patching, private IPC, or message submission.
// Build-only verification is separate from user-run Accessibility verification.
import AppKit
import ApplicationServices
import Foundation

struct Target: Decodable { let task_id: String; let title: String; let workspace_path: String }
enum Failure: Error { case refused(String) }
var currentStage = "startup"
func stage(_ name: String) {
    currentStage = name
    let data = try! JSONSerialization.data(withJSONObject: ["trace_stage": name])
    FileHandle.standardError.write(data + Data([10]))
}

func uniqueSearchInputIndex(_ controls: [(role: String, enabled: Bool)], hasSuggestions: Bool) -> Int? {
    guard hasSuggestions else { return nil }
    let candidates = controls.indices.filter {
        controls[$0].enabled && [kAXComboBoxRole, kAXTextFieldRole].contains(controls[$0].role)
    }
    return candidates.count == 1 ? candidates[0] : nil
}

func openWithVerification<T>(_ name: String = "search input", existing: () -> T?, primary: () throws -> Void,
                             fallback: () throws -> Void, afterAction: () throws -> T?) throws -> T {
    if let input = existing() { return input }
    try primary()
    if let input = try afterAction() { return input }
    try fallback()
    guard let input = try afterAction() else { throw Failure.refused("timeout: " + name + " after verified click fallback") }
    return input
}

if CommandLine.arguments.contains("--self-test") {
    // Captured Zcode shape: unnamed AXComboBox alongside Suggestions.
    precondition(uniqueSearchInputIndex([(kAXComboBoxRole, true)], hasSuggestions: true) == 0)
    precondition(uniqueSearchInputIndex([(kAXComboBoxRole, true)], hasSuggestions: false) == nil)
    precondition(uniqueSearchInputIndex([(kAXTextAreaRole, true)], hasSuggestions: true) == nil)
    precondition(uniqueSearchInputIndex([(kAXComboBoxRole, true), (kAXTextFieldRole, true)], hasSuggestions: true) == nil)
    do {
        var visible = false
        var fallbackUsed = false
        let input: String = try openWithVerification(existing: { nil }, primary: {}, fallback: {
            visible = true; fallbackUsed = true
        }, afterAction: { visible ? "search-input" : nil })
        precondition(input == "search-input" && fallbackUsed)
        let _: String = try openWithVerification(existing: { "already-open" }, primary: {
            preconditionFailure("must not toggle an already-open search panel")
        }, fallback: {}, afterAction: { nil })
    } catch { fatalError("search opening regression failed") }
    var ready = false
    let timer = Timer(timeInterval: 0.02, repeats: false) { _ in ready = true }
    RunLoop.main.add(timer, forMode: .common)
    do {
        let _: Bool = try poll("run loop callback") { ready ? true : nil }
    } catch {
        FileHandle.standardError.write(Data("self-test failed: poll blocked the main run loop\n".utf8))
        exit(1)
    }
    print("native identity and run-loop checks passed (no UI access)")
    exit(0)
}

func attribute(_ node: AXUIElement, _ key: String) -> CFTypeRef? {
    var result: CFTypeRef?
    return AXUIElementCopyAttributeValue(node, key as CFString, &result) == .success ? result : nil
}
func string(_ node: AXUIElement, _ key: String) -> String { attribute(node, key) as? String ?? "" }
func label(_ node: AXUIElement) -> String {
    for key in [kAXTitleAttribute, kAXDescriptionAttribute, kAXValueAttribute, "AXPlaceholderValue"] {
        let value = string(node, key)
        if !value.isEmpty { return value }
    }
    return ""
}
func nodes(_ root: AXUIElement) -> [AXUIElement] {
    var queue = [root]; var position = 0
    while position < queue.count && queue.count < 12000 {
        let node = queue[position]; position += 1
        queue += attribute(node, kAXChildrenAttribute) as? [AXUIElement] ?? []
    }
    return queue
}
func bounds(_ node: AXUIElement) -> CGRect? {
    guard let p = attribute(node, kAXPositionAttribute), let s = attribute(node, kAXSizeAttribute),
          CFGetTypeID(p) == AXValueGetTypeID(), CFGetTypeID(s) == AXValueGetTypeID() else { return nil }
    var point = CGPoint.zero; var size = CGSize.zero
    guard AXValueGetValue(p as! AXValue, .cgPoint, &point),
          AXValueGetValue(s as! AXValue, .cgSize, &size), size.width > 0, size.height > 0 else { return nil }
    return CGRect(origin: point, size: size)
}
func poll<T>(_ description: String, _ action: () throws -> T?) throws -> T {
    // Electron 的 AX 树在任务列表流式刷新时更新慢，短轮询会把偶发抖动放大成失败。
    let deadline = Date().addingTimeInterval(8)
    while Date() < deadline {
        if let result = try action() { return result }
        // AppKit caches changing app properties until the main run loop runs.
        // Sleeping here can keep frontmostApplication stale for the whole wait.
        RunLoop.current.run(until: Date().addingTimeInterval(0.05))
    }
    throw Failure.refused("timeout: " + description)
}

final class Navigator {
    let app: NSRunningApplication
    let root: AXUIElement
    init() throws {
        stage("accessibility_check")
        guard AXIsProcessTrustedWithOptions([kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary)
        else { throw Failure.refused("accessibility_permission_required: authorize this helper or its launching terminal in System Settings, then retry") }
        guard let process = NSRunningApplication.runningApplications(withBundleIdentifier: "dev.zcode.app").first
        else { throw Failure.refused("Zcode must already be running") }
        app = process; root = AXUIElementCreateApplication(process.processIdentifier)
        AXUIElementSetMessagingTimeout(root, 1)
        stage("activate")
        guard app.activate(options: []) else { throw Failure.refused("activation_request_rejected") }
        do {
            let _: Bool = try poll("activate Zcode") { NSWorkspace.shared.frontmostApplication?.processIdentifier == process.processIdentifier ? true : nil }
        } catch {
            let front = NSWorkspace.shared.frontmostApplication?.bundleIdentifier ?? "unknown"
            throw Failure.refused("timeout: activate Zcode; request_sent=true; frontmost=" + front)
        }
    }
    func checkFront() throws {
        RunLoop.current.run(until: Date().addingTimeInterval(0.005))
        if NSWorkspace.shared.frontmostApplication?.processIdentifier != app.processIdentifier {
            // 真实点击启动方窗口后前台可能被短暂回收：先重拉一次 Zcode，
            // 等不回来才拒绝；输入只发给确认在前台的 Zcode。
            app.activate(options: [])
            let deadline = Date().addingTimeInterval(1.5)
            while Date() < deadline,
                  NSWorkspace.shared.frontmostApplication?.processIdentifier != app.processIdentifier {
                RunLoop.current.run(until: Date().addingTimeInterval(0.05))
            }
        }
        let front = NSWorkspace.shared.frontmostApplication
        guard front?.processIdentifier == app.processIdentifier
        else {
            throw Failure.refused("focus_changed; expected=dev.zcode.app; observed=" +
                (front?.bundleIdentifier ?? "unknown") + "; no further input sent")
        }
    }
    func click(_ node: AXUIElement) throws {
        try checkFront()
        if AXUIElementPerformAction(node, kAXPressAction as CFString) == .success { return }
        try coordinateClick(node)
    }
    func coordinateClick(_ node: AXUIElement) throws {
        guard let rect = bounds(node), rect.minX.isFinite, rect.minY.isFinite else { throw Failure.refused("element has no actionable bounds") }
        let point = CGPoint(x: rect.midX, y: rect.midY)
        for type in [CGEventType.leftMouseDown, .leftMouseUp] {
            try checkFront()
            CGEvent(mouseEventSource: nil, mouseType: type, mouseCursorPosition: point, mouseButton: .left)?.post(tap: .cghidEventTap)
        }
    }
    func searchInput() -> AXUIElement? {
        let all = nodes(root)
        if let named = all.first(where: { node in
            let names = [kAXTitleAttribute, kAXDescriptionAttribute, "AXPlaceholderValue", kAXHelpAttribute]
                .map { string(node, $0) }.joined(separator: " ")
            return [kAXComboBoxRole, kAXTextFieldRole].contains(string(node, kAXRoleAttribute)) &&
                (names.contains("搜索操作") || names.contains("Search actions"))
        }) { return named }
        // Electron can advertise placeholder attributes while returning no text.
        // Scope to the nearest shared panel of Suggestions and its input, never
        // choose an arbitrary unnamed text control from the application.
        guard let suggestions = all.first(where: {
            string($0, kAXRoleAttribute) == kAXListRole && label($0) == "Suggestions"
        }) else { return nil }
        var ancestor = suggestions
        for _ in 0..<4 {
            guard let parent = attribute(ancestor, kAXParentAttribute),
                  CFGetTypeID(parent) == AXUIElementGetTypeID() else { return nil }
            ancestor = parent as! AXUIElement
            if [kAXApplicationRole, kAXWindowRole, "AXWebArea"].contains(string(ancestor, kAXRoleAttribute)) { return nil }
            let local = nodes(ancestor)
            let controls = local.map { (role: string($0, kAXRoleAttribute), enabled: (attribute($0, kAXEnabledAttribute) as? Bool) != false) }
            if let index = uniqueSearchInputIndex(controls, hasSuggestions: true) { return local[index] }
            if controls.filter({ [kAXComboBoxRole, kAXTextFieldRole].contains($0.role) && $0.enabled }).count > 1 { return nil }
        }
        return nil
    }
    func searchButtons() -> [AXUIElement] {
        nodes(root).filter { node in
            let name = label(node)
            return string(node, kAXRoleAttribute) == kAXButtonRole &&
                (name == "搜索" || name.hasPrefix("搜索 ⌘") || name == "Search")
        }
    }
    func searchDiagnostics(_ stage: String) -> [String: Any] {
        let all = nodes(root)
        var counts: [String: Int] = [:]
        for node in all { counts[string(node, kAXRoleAttribute), default: 0] += 1 }
        func details(_ node: AXUIElement) -> [String: Any] {
            // Never include AXValue: it can contain user input or message text.
            var result: [String: Any] = ["role": string(node, kAXRoleAttribute),
                "subrole": string(node, kAXSubroleAttribute),
                "role_description": string(node, kAXRoleDescriptionAttribute),
                "title": String(string(node, kAXTitleAttribute).prefix(120)),
                "description": String(string(node, kAXDescriptionAttribute).prefix(120)),
                "placeholder": String(string(node, "AXPlaceholderValue").prefix(120)),
                "enabled": attribute(node, kAXEnabledAttribute) as? Bool ?? true]
            if let rect = bounds(node) {
                result["bounds"] = ["x": rect.minX, "y": rect.minY, "width": rect.width, "height": rect.height]
            }
            var names: CFArray?
            if AXUIElementCopyAttributeNames(node, &names) == .success { result["attribute_names"] = names as? [String] ?? [] }
            return result
        }
        let inputs = all.filter { node in
            [kAXComboBoxRole, kAXTextFieldRole, kAXTextAreaRole].contains(string(node, kAXRoleAttribute)) ||
                !string(node, "AXPlaceholderValue").isEmpty || string(node, kAXRoleDescriptionAttribute).localizedCaseInsensitiveContains("combo")
        }
        return ["stage": stage, "node_count": all.count, "roles": counts,
                "frontmost": NSWorkspace.shared.frontmostApplication?.bundleIdentifier ?? "unknown",
                "search_input_matched": searchInput() != nil,
                "has_suggestions_list": all.contains { label($0) == "Suggestions" },
                "inputs": Array(inputs.prefix(20)).map(details),
                "search_buttons": searchButtons().prefix(10).map(details)]
    }
    func diagnoseSearch() throws -> [String: Any] {
        var snapshots = [searchDiagnostics("before")]
        if searchInput() != nil { return ["status": "diagnostic", "snapshots": snapshots] }
        guard let button = searchButtons().first else {
            return ["status": "diagnostic", "snapshots": snapshots, "reason": "no_search_button"]
        }
        try checkFront()
        let code = AXUIElementPerformAction(button, kAXPressAction as CFString)
        RunLoop.current.run(until: Date().addingTimeInterval(0.5))
        snapshots.append(searchDiagnostics("after_ax_press"))
        if searchInput() == nil, let freshButton = searchButtons().first {
            try coordinateClick(freshButton)
            RunLoop.current.run(until: Date().addingTimeInterval(0.5))
            snapshots.append(searchDiagnostics("after_coordinate_click"))
        }
        return ["status": "diagnostic", "ax_press_result": code.rawValue, "snapshots": snapshots]
    }
    func key(_ code: CGKeyCode, flags: CGEventFlags = []) throws {
        for down in [true, false] {
            try checkFront()
            let event = CGEvent(keyboardEventSource: CGEventSource(stateID: .privateState), virtualKey: code, keyDown: down)
            event?.flags = flags; event?.post(tap: .cghidEventTap)
        }
    }
    func search(_ title: String) throws {
        stage("search_prepare")
        if searchInput() != nil {
            try key(53) // Close the search overlay a previous failed attempt left open.
            RunLoop.current.run(until: Date().addingTimeInterval(0.2))
        }
        if searchInput() == nil && nodes(root).contains(where: {
            string($0, kAXRoleAttribute) == kAXMenuRole && ["更多", "More"].contains(label($0))
        }) {
            try key(53) // Close a task menu left open by an earlier attempt.
            RunLoop.current.run(until: Date().addingTimeInterval(0.1))
        }
        func searchButton() throws -> AXUIElement { try poll("search button") {
            nodes(root).first { node in
                let name = label(node)
                return string(node, kAXRoleAttribute) == kAXButtonRole && (name == "搜索" || name.hasPrefix("搜索 ⌘") || name == "Search")
            }
        } }
        stage("search_open")
        var input: AXUIElement = try openWithVerification(existing: { searchInput() }, primary: {
            try click(searchButton())
        }, fallback: {
            try coordinateClick(searchButton())
        }, afterAction: {
            let deadline = Date().addingTimeInterval(2)
            while Date() < deadline {
                if let input = searchInput() { return input }
                try checkFront()
                RunLoop.current.run(until: Date().addingTimeInterval(0.05))
            }
            return nil
        })
        stage("search_focus")
        func windowFrame() -> CGRect? {
            let window = attribute(root, kAXFocusedWindowAttribute) ?? attribute(root, kAXMainWindowAttribute)
            guard let window, CFGetTypeID(window) == AXUIElementGetTypeID() else { return nil }
            return bounds(window as! AXUIElement)
        }
        let focusDeadline = Date().addingTimeInterval(8)
        while true {
            if let focused = attribute(root, kAXFocusedUIElementAttribute), CFEqual(focused, input) { break }
            guard Date() < focusDeadline else { throw Failure.refused("timeout: search input focus") }
            // Electron 有时不把键盘焦点交给搜索框：AX 设置与窗口内的真实点击都试。
            AXUIElementSetAttributeValue(input, kAXFocusedAttribute as CFString, kCFBooleanTrue)
            RunLoop.current.run(until: Date().addingTimeInterval(0.05))
            if let focused = attribute(root, kAXFocusedUIElementAttribute), CFEqual(focused, input) { break }
            if let rect = bounds(input), let frame = windowFrame(), frame.contains(CGPoint(x: rect.midX, y: rect.midY)) {
                try checkFront()
                let point = CGPoint(x: rect.midX, y: rect.midY)
                for type in [CGEventType.leftMouseDown, .leftMouseUp] {
                    CGEvent(mouseEventSource: nil, mouseType: type, mouseCursorPosition: point, mouseButton: .left)?.post(tap: .cghidEventTap)
                }
            }
            RunLoop.current.run(until: Date().addingTimeInterval(0.25))
        }
        stage("search_scope")
        // 全部范围下消息命中会排在前面，其行标题以别的任务名开头；切到任务范围，
        // 结果行标题才会以目标任务名开头，hasPrefix 匹配才成立。
        if let scope = nodes(root).first(where: {
            string($0, kAXRoleAttribute) == kAXRadioButtonRole && ["任务", "Tasks"].contains(label($0))
        }) {
            AXUIElementPerformAction(scope, kAXPressAction as CFString)
            // 范围切换会重建浮层，旧输入框节点随时失效：重新定位并恢复焦点后再粘贴。
            input = try poll("search input after scope switch") {
                guard let fresh = searchInput() else { return nil }
                AXUIElementSetAttributeValue(fresh, kAXFocusedAttribute as CFString, kCFBooleanTrue)
                RunLoop.current.run(until: Date().addingTimeInterval(0.05))
                if let focused = attribute(root, kAXFocusedUIElementAttribute), CFEqual(focused, fresh) { return fresh }
                return nil
            }
        }
        stage("search_paste")
        try pasteSearchQuery(title)
    }
    func pasteSearchQuery(_ title: String) throws {
        // Use the app's normal paste handling instead of synthesized Unicode
        // key events, which did not populate the observed Electron input.
        let board = NSPasteboard.general
        let originalCount = board.changeCount
        let saved = (board.pasteboardItems ?? []).map { item -> NSPasteboardItem in
            let copy = NSPasteboardItem()
            for type in item.types { if let data = item.data(forType: type) { copy.setData(data, forType: type) } }
            return copy
        }
        var ownedCount: Int?
        defer {
            if let count = ownedCount, board.changeCount == count {
                board.clearContents(); board.writeObjects(saved)
            }
        }
        try checkFront()
        guard let current = searchInput(), let focused = attribute(root, kAXFocusedUIElementAttribute),
              CFEqual(current, focused) else { throw Failure.refused("search input lost focus before paste") }
        guard board.changeCount == originalCount else { throw Failure.refused("clipboard changed by user") }
        board.clearContents()
        ownedCount = board.changeCount
        guard board.setString(title, forType: .string) else { throw Failure.refused("cannot prepare search pasteboard") }
        ownedCount = board.changeCount
        try key(0, flags: .maskCommand)
        RunLoop.current.run(until: Date().addingTimeInterval(0.05))
        try checkFront()
        guard let fresh = searchInput(), let active = attribute(root, kAXFocusedUIElementAttribute), CFEqual(fresh, active)
        else { throw Failure.refused("search input lost focus before paste") }
        try key(9, flags: .maskCommand) // Command-V; no Return key is sent.
        do {
            let _: Bool = try poll("query text applied") {
                guard let fresh = searchInput() else { return nil }
                return string(fresh, kAXValueAttribute) == title ? true : nil
            }
        } catch {
            let fresh = searchInput()
            let value = fresh.map { string($0, kAXValueAttribute) } ?? ""
            let characters = fresh.flatMap { attribute($0, kAXNumberOfCharactersAttribute) as? Int } ?? -1
            let valueType = fresh.flatMap { attribute($0, kAXValueAttribute) }.map {
                CFCopyTypeIDDescription(CFGetTypeID($0)) as String
            } ?? "absent"
            // Report lengths only, not any text currently in the user's input.
            throw Failure.refused("timeout: query text applied; method=paste; input_present=\(fresh != nil); actual_length=\(value.utf16.count); reported_characters=\(characters); value_type=\(valueType); expected_length=\(title.utf16.count); post_event_access=\(CGPreflightPostEventAccess())")
        }
    }
    func focus(_ target: Target) throws -> [String: Any] {
        // 用户决定：搜索可能命中多个同名/相似任务，助手停在任务搜索结果页，
        // 由用户自行选择目标；不执行点击行或回车打开的精确跳转。
        try search(target.title)
        return ["status": "search_opened", "task_id": target.task_id, "title": target.title]
    }
}

do {
    let target = try JSONDecoder().decode(Target.self, from: FileHandle.standardInput.readDataToEndOfFile())
    guard target.task_id.range(of: "^sess_[A-Za-z0-9-]+$", options: .regularExpression) != nil,
          !target.title.isEmpty, target.title.count <= 1000,
          !target.title.contains(where: { $0.isNewline }), target.workspace_path.hasPrefix("/") else {
        throw Failure.refused("invalid task descriptor")
    }
    let navigator = try Navigator()
    let result = try CommandLine.arguments.contains("--diagnose-search") ? navigator.diagnoseSearch() : navigator.focus(target)
    print(String(data: try JSONSerialization.data(withJSONObject: result), encoding: .utf8)!)
} catch {
    let reason: String
    if case Failure.refused(let message) = error { reason = message } else { reason = "native_adapter_error: \(type(of: error))" }
    let data = try! JSONSerialization.data(withJSONObject: ["status": "refused", "reason": reason, "stage": currentStage])
    FileHandle.standardError.write(data + Data([10])); exit(1)
}
