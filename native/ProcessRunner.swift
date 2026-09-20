import Foundation

/// 子进程统一执行边界：stdout/stderr 并发排空 + 超时梯度终止。
/// 串行「先读 stdout 到 EOF 再读 stderr」的实现，会被先写满 stderr 缓冲的子进程
/// 互等死锁（2026-09-21 评审 P2）；收件箱刷新、日报读取与操作动作共用此入口。
enum ProcessRunner {
    /// 收件箱 CLI：发布包走 bundle 内脚本与依赖，开发包走仓库 venv。
    static func run(root: String, arguments: [String], timeout: TimeInterval = 600)
        -> (Int32, Data, String) {
        let process = Process()
        if let resources = Bundle.main.resourceURL,
           FileManager.default.isExecutableFile(
               atPath: resources.appendingPathComponent("bin/session-manager").path),
           FileManager.default.fileExists(
               atPath: resources.appendingPathComponent("pylib").path) {
            // 发布包：脚本与依赖随包，解释器选择和 PYTHONPATH 由随包的 CLI 统一处理。
            process.executableURL = resources.appendingPathComponent("bin/session-manager")
            process.arguments = ["inbox"] + arguments
        } else {
            // 开发包：直接跑仓库里的脚本和 venv。
            process.executableURL =
                URL(fileURLWithPath: root + "/scratch/iterm-probe-venv/bin/python")
            process.arguments = [root + "/scripts/inbox.py"] + arguments
        }
        return run(process: process, timeout: timeout)
    }

    /// 通用进程执行。整体超过 timeout 先 SIGTERM 再 SIGKILL；启动失败退出码 1、
    /// 诊断进 stderr，不抛异常。
    static func run(executable: String, arguments: [String], timeout: TimeInterval = 600)
        -> (Int32, Data, String) {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: executable)
        process.arguments = arguments
        return run(process: process, timeout: timeout)
    }

    private static func run(process: Process, timeout: TimeInterval) -> (Int32, Data, String) {
        let output = Pipe()
        let errors = Pipe()
        process.standardOutput = output
        process.standardError = errors
        // 须在 run 前挂好：秒退的子进程不会给 run 之后才设的 handler 补发回调。
        let exited = DispatchSemaphore(value: 0)
        process.terminationHandler = { _ in exited.signal() }
        do {
            try process.run()
        } catch {
            return (1, Data(), error.localizedDescription)
        }
        let group = DispatchGroup()
        let queue = DispatchQueue.global(qos: .userInitiated)
        let lock = NSLock()
        var stdoutData = Data()
        var stderrData = Data()
        group.enter()
        queue.async {
            let data = output.fileHandleForReading.readDataToEndOfFile()
            lock.lock(); stdoutData = data; lock.unlock()
            group.leave()
        }
        group.enter()
        queue.async {
            let data = errors.fileHandleForReading.readDataToEndOfFile()
            lock.lock(); stderrData = data; lock.unlock()
            group.leave()
        }
        var timedOut = false
        if group.wait(timeout: .now() + timeout) == .timedOut {
            timedOut = true
            if process.isRunning { process.terminate() }
            if group.wait(timeout: .now() + 5) == .timedOut {
                if process.isRunning { _ = kill(process.processIdentifier, SIGKILL) }
                // 直接子进程终止不保证管道 EOF：后代进程可能仍持有写端
                // （如 sh -c 'sleep 30 & exit 0'）。读取改为有界等待，超时后带
                // 部分输出显式失败，绝不以子进程 exit=0 掩盖超时（评审 R7）。
                if group.wait(timeout: .now() + 5) == .timedOut {
                    lock.lock()
                    let partialOut = stdoutData
                    let partialErr = stderrData
                    lock.unlock()
                    let note = "ProcessRunner: timeout after \(Int(timeout))s; "
                        + "descendant processes may still hold output pipes"
                    let text = String(data: partialErr, encoding: .utf8) ?? ""
                    return (124, partialOut, text.isEmpty ? note : text + "\n" + note)
                }
            }
        }
        group.wait()  // 直接子进程已终止，读取必然返回
        if exited.wait(timeout: .now() + 10) == .timedOut {
            if process.isRunning { _ = kill(process.processIdentifier, SIGKILL) }
            _ = exited.wait(timeout: .now() + 10)
        }
        lock.lock(); defer { lock.unlock() }
        var stderrText = String(data: stderrData, encoding: .utf8) ?? ""
        if timedOut {
            stderrText += (stderrText.isEmpty ? "" : "\n")
                + "ProcessRunner: terminated after \(Int(timeout))s timeout"
        }
        return (process.terminationStatus, stdoutData, stderrText)
    }
}
