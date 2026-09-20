import Foundation

@main struct ProcessRunnerTests {
    static func main() {
        // stderr（100KB，超 64KB 管道缓冲）先写满、stdout 后写：串行先读 stdout EOF
        // 的实现会与子进程互等死锁；并发排空必须拿全两侧输出（2026-09-21 评审 P2）。
        // 若实现回归为串行，下方 timeout 会终止子进程并以输出缺失使断言失败。
        let stderrPayload = String(repeating: "e", count: 100_000)
        let started = Date()
        let result = ProcessRunner.run(
            executable: "/bin/sh",
            arguments: ["-c", "printf '\(stderrPayload)' >&2; printf out"],
            timeout: 20
        )
        precondition(result.0 == 0, "exit=\(result.0) stderr=\(result.2.prefix(200))")
        precondition(
            result.1 == Data("out".utf8),
            "stdout=\(String(data: result.1, encoding: .utf8) ?? "?")"
        )
        precondition(result.2.count == 100_000, "stderr bytes=\(result.2.count)")
        precondition(Date().timeIntervalSince(started) < 15, "大输出不应走到超时兜底")

        // 挂死子进程：超时梯度 SIGTERM 兜底，调用方不被永久阻塞。
        let hungStart = Date()
        let hung = ProcessRunner.run(
            executable: "/bin/sh",
            arguments: ["-c", "sleep 60"],
            timeout: 2
        )
        let hungElapsed = Date().timeIntervalSince(hungStart)
        precondition(hung.0 != 0, "挂死进程应被终止: exit=\(hung.0)")
        precondition(hungElapsed < 15, "超时兜底耗时 \(hungElapsed)s")

        // 启动失败：诊断进 stderr，不抛异常。
        let missing = ProcessRunner.run(
            executable: "/nonexistent/binary",
            arguments: [],
            timeout: 5
        )
        precondition(missing.0 == 1 && !missing.2.isEmpty, "启动失败诊断缺失")

        print("ProcessRunner concurrency, timeout and spawn-failure checks passed")
    }
}
