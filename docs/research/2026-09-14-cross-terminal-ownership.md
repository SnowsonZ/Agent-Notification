# 跨标签页前台归属误判修复

状态：本机真实跨会话 PTY 复现、修复和回归通过；用户重试原 focus 命令后反馈“可以了”，Pi 的受管理跨标签页跳转已获实际确认。随后按退出测试步骤重试，用户返回 refused / binding expired, superseded, or session changed，确认旧绑定被拒绝。日期：2026-09-14。Kimi 的同等路径尚待用户实测。

## 问题与原因

用户已成功登记运行中的 Pi，但从第二个 iTerm 标签页调用 focus 时，validate 抛出 `managed process group no longer owns the foreground terminal`，尚未执行导航。

旧实现从调用方打开目标 TTY 并执行 tcgetpgrp。macOS `man 3 tcgetpgrp` 明确规定：调用进程没有控制终端，或该 FD 不是调用进程的控制终端时，返回 ENOTTY。跨标签页、独立 GUI 或无控制终端的调用方都不能依赖这个查询。

用自己创建的 PTY 复现：目标有真实前台进程组，调用方位于另一会话；tcgetpgrp 返回 errno 25，旧 foreground_matches 返回 false。此前的烟测在目标会话内部调用 validate，因此漏掉了真实调用拓扑。这不是 iTerm API 断连，也不是凭此错误即可判定 Pi 已退出。

## 修复

[session_binding.py](../../scripts/session_binding.py)改为 `/bin/ps -t <tty> -o pgid=,tpgid=`，只读取进程组元数据，不读取终端内容。仅当查询成功、记录格式正确、所有记录的前台组一致且等于预期组、预期组内仍有进程时通过。未知、超时、退出或真正不匹配仍拒绝。

运行锁、run_id、agent session_id、pane 精确匹配及跳转前后校验全部保留；没有移除防误跳检查。新查询不改变原有绑定数据，仍运行的 Pi 无需重启。

[iterm_probe.py](../../scripts/iterm_probe.py)捕获预期的 ValueError 并返回简短 refused JSON 和非零退出码，避免把正常拒绝显示成完整 traceback。

## 证据与复现命令

```sh
python3 -m unittest discover -s tests -p test_foreground_terminal.py -v
python3 -m unittest discover -s tests -v
```

[新增回归](../../tests/test_foreground_terminal.py)实际创建独立控制终端、存活锁与绑定，调用方在目标会话之外。旧代码在“真实前台组识别”断言失败；修复后识别及完整 validate 通过，错误进程组仍被拒绝。全部 32 项检查通过。

测试子进程在 fork 后立即 exec 新解释器，避免继承父进程已使用的 macOS SQLite 库状态；这属于测试夹具修复，不涉及用户进程。

测试没有访问 iTerm2 App，也没有检查用户终端正文。最终 iTerm 导航由用户重试原命令确认；若原 Pi 已退出，拒绝旧绑定仍是正确行为。
