# 第一版终端宿主改为 Terminal.app

当前状态：暂缓。用户随后决定优先尝试 iTerm2，并已反馈 API 枚举成功。本文保留为备选，不再是当前迁移计划。

2026-09-14 用户决定：Pi/Kimi 第一版使用 macOS 自带 Terminal，暂停 iTerm2 验证。三个桌面 App 的范围不变。

## 已确认与边界

本机 `/System/Applications/Utilities/Terminal.app/Contents/Resources/Terminal.sdef` 明确提供 window.id、window.frontmost、window.miniaturized、tab.tty 和可写的 tab.selected。

因此可以按 TTY 找到对应标签页并选择它，不依赖标题或当前标签页序号。窗口 ID 在应用重启后不能视为永久标识；TTY 也可能被复用。正式绑定必须同时记录 agent session ID、进程实例和宿主启动周期，原 agent 退出或 TTY 复用时失效。单独匹配 TTY 的探针不是完整进程绑定实现。

Computer Use 对 `com.apple.Terminal` 也返回安全拒绝。更换宿主并没有解除工具访问限制；本轮没有通过 AppleScript 绕过拒绝。

## 无额外依赖的手动探针

[terminal_probe.applescript](../../scripts/terminal_probe.applescript)已按本机脚本字典编写，尚未运行时验证。默认只返回窗口 ID、TTY、选中标记，不读取终端内容、不输入命令、不修改 agent 配置。

在 Terminal 中执行：

```sh
osascript scripts/terminal_probe.applescript
```

系统可能询问是否允许 Terminal 的自动化控制；由用户处理该提示。若报错，保留错误文本，不关闭系统保护。无需先安装 Python 或 iTerm2 API 包。

取得真实 TTY 后，显式传入它可以测试从另一标签页切回目标：

```sh
osascript scripts/terminal_probe.applescript /dev/ttysXXX
```

将占位设备替换为探针返回的值。只接受一个精确匹配；不存在或多个匹配则报错。不使用 `do script`，不会重启或恢复任何 agent。即使命令返回 selected，也应实际检查原 Pi/Kimi 会话是否在该页，才能完成运行时验收。

## 后续验收

1. 用户提供 Terminal 探针输出，确认 Apple Events 通道可用。
2. 两个标签页分别运行 Pi/Kimi，记录真实 TTY 和 agent session ID。
3. 事件结束后准确聚焦对应页；交换标签页顺序仍正确。
4. 关闭标签页、退出 agent、同 TTY 上启动另一会话时旧映射失效。

先前的 iTerm2 代码和独立环境保留为历史试验，不删除；不再要求用户执行 iTerm2 探针。
