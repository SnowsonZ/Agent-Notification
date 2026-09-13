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

func matchesTaskPath(_ target: Target, _ actual: String) -> Bool {
    actual == (target.workspace_path as NSString).appendingPathComponent(target.task_id + ".zcode-session")
}

func uniqueSearchInputIndex(_ controls: [(role: String, enabled: Bool)], hasSuggestions: Bool) -> Int? {
    guard hasSuggestions else { return nil }
    let candidates = controls.indices.filter {
        controls[$0].enabled && [kAXComboBoxRole, kAXTextFieldRole].contains(controls[$0].role)
    }
    return candidates.count == 1 ? candidates[0] : nil
}

func isResultRow(role: String, hasSelectedAttribute: Bool) -> Bool {
    role != kAXStaticTextRole && hasSelectedAttribute
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
    let sample = Target(task_id: "sess_one", title: "same title", workspace_path: "/fixture")
    precondition(matchesTaskPath(sample, "/fixture/sess_one.zcode-session"))
    precondition(!matchesTaskPath(sample, "/fixture/sess_other.zcode-session"))
    precondition(!matchesTaskPath(sample, "/other/sess_one.zcode-session"))
    // Captured Zcode shape: unnamed AXComboBox alongside Suggestions.
    precondition(uniqueSearchInputIndex([(kAXComboBoxRole, true)], hasSuggestions: true) == 0)
    precondition(uniqueSearchInputIndex([(kAXComboBoxRole, true)], hasSuggestions: false) == nil)
    precondition(uniqueSearchInputIndex([(kAXTextAreaRole, true)], hasSuggestions: true) == nil)
    precondition(uniqueSearchInputIndex([(kAXComboBoxRole, true), (kAXTextFieldRole, true)], hasSuggestions: true) == nil)
    precondition(isResultRow(role: "AXUnknown", hasSelectedAttribute: true))
    precondition(!isResultRow(role: kAXStaticTextRole, hasSelectedAttribute: true))
    precondition(!isResultRow(role: kAXGroupRole, hasSelectedAttribute: false))
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
    let deadline = Date().addingTimeInterval(4)
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
    func resultRows(_ title: String) -> [AXUIElement] {
        guard let list = nodes(root).first(where: { string($0, kAXRoleAttribute) == kAXListRole && label($0) == "Suggestions" }) else { return [] }
        return nodes(list).filter { node in
            label(node).hasPrefix(title) && bounds(node) != nil &&
                isResultRow(role: string(node, kAXRoleAttribute), hasSelectedAttribute: attribute(node, kAXSelectedAttribute) != nil)
        }.sorted { (bounds($0)?.minY ?? .infinity) < (bounds($1)?.minY ?? .infinity) }
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
    func search(_ title: String) throws -> [AXUIElement] {
        stage("search_prepare")
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
        let input: AXUIElement = try openWithVerification(existing: { searchInput() }, primary: {
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
        if let rect = bounds(input) {
            try checkFront()
            let point = CGPoint(x: rect.midX, y: rect.midY)
            for type in [CGEventType.leftMouseDown, .leftMouseUp] {
                CGEvent(mouseEventSource: nil, mouseType: type, mouseCursorPosition: point, mouseButton: .left)?.post(tap: .cghidEventTap)
            }
        }
        stage("search_focus")
        let _: Bool = try poll("search input focus") {
            guard let focused = attribute(root, kAXFocusedUIElementAttribute) else { return nil }
            return CFEqual(focused, input) ? true : nil
        }
        stage("search_paste")
        try pasteSearchQuery(title)
        stage("search_results")
        return try poll("matching task result") {
            let candidates = resultRows(title)
            return candidates.isEmpty ? nil : candidates
        }
    }
    func openResult(_ candidate: AXUIElement, title: String, ordinal: Int) throws {
        stage("candidate_click_\(ordinal)")
        try click(candidate)
        RunLoop.current.run(until: Date().addingTimeInterval(0.2))
        guard let input = searchInput() else { return } // Click already opened it.
        let _: Bool = try poll("target result highlighted") {
            let rows = resultRows(title)
            guard ordinal < rows.count else { return nil }
            return (attribute(rows[ordinal], kAXSelectedAttribute) as? Bool) == true ? true : nil
        }
        // AXPress may highlight an option without confirming it. Put keyboard
        // focus back in the identified search box before Enter, never a chat box.
        try coordinateClick(input)
        let _: Bool = try poll("search focus before result confirmation") {
            guard let fresh = searchInput(), let focused = attribute(root, kAXFocusedUIElementAttribute) else { return nil }
            return CFEqual(fresh, focused) ? true : nil
        }
        let rows = resultRows(title)
        guard ordinal < rows.count, (attribute(rows[ordinal], kAXSelectedAttribute) as? Bool) == true
        else { throw Failure.refused("selected search result changed before confirmation") }
        stage("candidate_confirm_\(ordinal)")
        try key(36) // Return only while the verified search input has focus.
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
    func copiedTaskPath() throws -> String {
        stage("task_menu_prepare")
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
        func taskMenu() throws -> AXUIElement { try poll("task menu") {
            nodes(root).filter { node in
                let role = string(node, kAXRoleAttribute)
                return [kAXPopUpButtonRole, kAXMenuButtonRole, kAXButtonRole].contains(role) && ["更多", "More"].contains(label(node))
            }.sorted {
                let a = bounds($0) ?? .infinite; let b = bounds($1) ?? .infinite
                return a.minY == b.minY ? a.minX < b.minX : a.minY < b.minY
            }.first
        } }
        func copyItem() -> AXUIElement? {
            nodes(root).first { ["复制任务路径", "Copy task path"].contains(label($0)) && string($0, kAXRoleAttribute) == kAXMenuItemRole && (attribute($0, kAXEnabledAttribute) as? Bool != false) }
        }
        stage("task_menu_open")
        let copy: AXUIElement = try openWithVerification("Copy task path", existing: { copyItem() }, primary: {
            try click(taskMenu())
        }, fallback: {
            try coordinateClick(taskMenu())
        }, afterAction: {
            let deadline = Date().addingTimeInterval(2)
            while Date() < deadline {
                if let item = copyItem() { return item }
                try checkFront()
                RunLoop.current.run(until: Date().addingTimeInterval(0.05))
            }
            return nil
        })
        guard board.changeCount == originalCount else { throw Failure.refused("clipboard changed by user") }
        stage("task_path_copy")
        let value: String = try openWithVerification("fresh copied task path", existing: { nil }, primary: {
            try click(copy)
        }, fallback: {
            guard let item = copyItem() else { throw Failure.refused("copy menu closed without a fresh task path") }
            try coordinateClick(item)
        }, afterAction: {
            let deadline = Date().addingTimeInterval(2)
            while Date() < deadline {
                if board.changeCount != originalCount {
                    guard let value = board.string(forType: .string), value.hasPrefix("/"), value.trimmingCharacters(in: .whitespacesAndNewlines).hasSuffix(".zcode-session")
                    else { throw Failure.refused("clipboard changed without a task path; clipboard left untouched") }
                    ownedCount = board.changeCount
                    return value
                }
                try checkFront()
                RunLoop.current.run(until: Date().addingTimeInterval(0.05))
            }
            return nil
        })
        return value.trimmingCharacters(in: .whitespacesAndNewlines)
    }
    func focus(_ target: Target) throws -> [String: Any] {
        // Rebuild the search UI after each candidate: old AX nodes are not reused.
        for ordinal in 0..<20 {
            let candidates = try search(target.title)
            guard ordinal < candidates.count else { break }
            try openResult(candidates[ordinal], title: target.title, ordinal: ordinal)
            stage("candidate_opened_\(ordinal)")
            let _: Bool = try poll("search result selected") {
                searchInput() == nil ? true : nil
            }
            let actual = try copiedTaskPath()
            stage("task_identity_check_\(ordinal)")
            if matchesTaskPath(target, actual) {
                return ["status": "focused", "task_id": target.task_id, "selection_identity_matches": true, "method": "accessibility-copy-task-path"]
            }
        }
        throw Failure.refused("no candidate matched task ID; a candidate may remain visible")
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
