# Zcode 原生自动导航适配器

状态：首次真实 AX 自动导航及任务 ID 后置核验已在真实会话中通过（本地日志 exit_code=0）。该结论针对已测任务；同名多候选、冷启动、多显示器和长期稳定性尚未完成验收。

## 成功验收

2026-09-14 01:57:49（Asia/Shanghai）开始的运行完整经过激活、搜索、输入、结果确认、打开任务菜单、复制任务路径和 ID 比对。运行结果为 status=focused、selection_identity_matches=true，目标 task_id（sess_afcd78bb…）与索引一致，method 为 accessibility-copy-task-path。本地 `scratch/zcode-focus-latest.json` 同时记录 exit_code=0 和完整阶段轨迹。

当前 Zcode 路径已有真实成功证据，不再列为“只有工作区级打开”。后文保留实现与排错过程，历史“待重试”状态以本节为准。

## 激活超时修正

用户首次运行返回 `timeout: activate Zcode`，失败发生在搜索前。本机 SDK 的 `NSRunningApplication.h` 明确说明：时变属性直到主 run loop 下一轮才刷新。旧 poll 用 Thread.sleep 阻塞主线程，可能一直读取缓存的前台状态。

已添加只依赖主线程 Timer 的回归：旧实现等待 4 秒仍失败，新实现运行 RunLoop 后通过。等待器改为服务主 run loop，动作前也刷新事件；激活请求若被系统拒绝会直接返回 activation_request_rejected，超时则附 request_sent 和 frontmost bundle ID，便于区分后续问题。

已重新编译，身份与 run-loop 自检均通过，没有运行原生 UI 自动化。尚需用户用原命令重试；Timer 回归不等同于实际 Zcode 激活已通过。暂保留原激活 API，避免同时更换多个变量。

## 搜索输入框超时修正

下一次用户反馈变为 `timeout: search input`，说明该次已通过激活检查，但不能据此确定是面板没打开还是控件识别失败。随后 CUA 状态没有显示命令面板输入框；这个快照不作为超时发生瞬间的完整证明。

新逻辑先查找已打开的搜索框，避免重试时把面板关闭；再执行原生点击并等待输入框出现；仍未出现时，对搜索按钮进行一次坐标点击并重新验证。输入框兼容 AXComboBox 和 AXTextField，匹配 title/description/placeholder/help 中的专用搜索描述，仍不回退到通用聊天输入框。

增加无 UI 的点击状态机自检，覆盖“原生点击无效果但返回成功”与“搜索本已打开不能重复切换”。编译及自检通过；真实 UI 路径仍待用户运行。

用户再次反馈 `timeout: search input after verified click fallback`。停止继续猜测点击方式，新增 `--diagnose-search`：记录打开前、AXPress 后、必要的坐标点击后三个阶段的控件角色、属性名称、位置及是否匹配输入框，不读取 AXValue，不输入查询或选择任务。该模式已编译，待用户运行取得原生 AX 实际证据；不能将模拟点击自检当作真实 UI 通过。

用户已运行诊断。本地结果 `scratch/zcode-search-diagnostic.json` 显示 before 阶段已有 Suggestions 列表和唯一 AXComboBox，但 title/description/placeholder 均为空；search_buttons 为空。搜索面板实际上已打开，文字选择器未识别输入框。

据此新增结构匹配：从 Suggestions 向上最多四层，在最近的局部面板内寻找唯一启用的 AXComboBox/AXTextField；到 WebArea/Window/Application 就停止，不扩展到任意聊天输入区。没有 Suggestions、只有 AXTextArea 或多个输入框均拒绝。基于该诊断形态的原生自检通过，已重新编译，实际搜索输入仍待重试。

下一次用户运行推进到 `timeout: query text applied`，证明输入框发现和初始聚焦检查已通过。随后截图中输入框仍为空；尚未证明是事件投递、Electron 输入处理还是控件引用失效导致。输入方式改为原生 Command-V，键盘事件使用 privateState 避免继承其他按键状态；输入前再次核对专用搜索框焦点，输入后重新查找控件。保存/恢复剪贴板，若用户已更改则不覆盖。失败信息追加只含长度、字符数、值类型和事件权限的诊断，不输出输入文本。编译/无 UI 自检与实际粘贴效果仍分开验收。

用户下一次反馈 `timeout: search result selected`，该次已通过输入和候选发现。CUA 树确认：搜索输入为完整目标标题，Suggestions 内目标结果处于 selected，面板仍打开；同一结果下还有三个重复标题文本节点。修正候选筛选，仅保留有 AXSelected 属性的非静态文本结果行，避免把文本叶子作为独立候选。若点击仅高亮，重新核对目标行选中和专用搜索框焦点后发送一次 Return，再等待面板关闭。最终复制任务路径的 ID 比对保持不变。编译及结果行筛选自检通过，实际打开和最终 ID 验收仍待重试。

用户随后确认页面切换成功，但返回 `timeout: Copy task path`：导航动作已获目视确认，最终身份核验尚未通过。CUA 坐标打开任务菜单后确认“复制任务路径”存在且可用，“复制会话 ID”是另一个被禁用的选项。为菜单打开及复制动作分别增加后置条件与一次坐标回退，复制必须使剪贴板产生新的任务路径才接受；重试前可关闭此前遗留的任务菜单。仍只在实际路径和目标 ID 一致时报告 focused。

随后一次运行未切换，用户提供 `focus_changed_by_user; no further input sent`。该消息只能证明观察到前台不再是目标 PID，不能推断用户手动切换。现改为中性 focus_changed，并记录 observed bundle ID 和当前执行阶段，保留停止输入保护。

入口自动将每次运行的阶段轨迹、helper SHA-256、开始时间及最终输出写到 `scratch/zcode-focus-latest.json`（0600）。终端仅显示最终结果，阶段日志不夹杂在用户输出里。编译、自检和日志保存/输出过滤测试通过；等待带日志的原生运行定位具体焦点来源。在拿到新证据前不继续堆叠重试或关闭焦点保护。

## 定位方式

输入 task ID，读取本地任务索引中的标题和工作区；激活 Zcode，打开搜索并输入标题；选择候选后，通过原生菜单“复制任务路径”获取实际任务身份。仅当路径与索引中的 workspace + task ID 一致时返回 focused。同名候选逐个核对，最多尝试 20 个；找不到则 refused，候选页可能仍可见。

使用 [Apple Accessibility API](https://developer.apple.com/documentation/applicationservices/1462091-axuielementperformaction)和公开的系统事件接口，不修改 Zcode 安装包、不注入私有 IPC、不改任务标题或数据库，也不提交聊天消息。程序发送搜索文本前核对前台 App 和搜索框焦点，焦点被用户切走则停止。

剪贴板只用于原生“复制任务路径”，保存原内容于进程内存，操作结束后在没有用户再次修改的情况下恢复；不打印或落盘原剪贴板。复制必须产生新的 changeCount，不能把陈旧剪贴板当成成功证据。任务路径允许是逻辑路径，不要求对应文件存在。

## 运行

先在 Zcode 切到另一个任务，再在终端执行：

```sh
bin/session-manager zcode-focus TASK_ID
```

TASK_ID 为本地任务索引中的真实任务 ID。已验证的运行打开了目标任务并返回 status=focused、selection_identity_matches=true。如果报告 accessibility_permission_required，运行 `bin/session-manager permissions`（或在 App 内点击 Zcode 事项的「前往会话」会自动触发同一流程），在打开的「隐私与安全性 → 辅助功能」面板中把可拖拽悬浮窗里的 SessionInbox.app（Agent Notification）拖入列表（从终端直接运行时则检查启动它的终端 App），授权后悬浮窗自动收起，再重新运行。[Apple 的权限检查说明](https://developer.apple.com/documentation/applicationservices/1459186-axisprocesstrustedwithoptions)说明权限提示是异步的，当次返回并不自动变成已授权。

仅解析目标而不操作界面：

```sh
bin/session-manager zcode-focus TASK_ID --describe
```

编译及不访问 UI 的自检：

```sh
xcrun swiftc native/ZcodeFocus.swift -o build/zcode-focus
build/zcode-focus --self-test
python3 -m unittest discover -s tests -v
```

首次编译前创建 build 目录。产物目录不纳入版本管理。

## 边界和证据

- [Swift 实现](../../native/ZcodeFocus.swift)、[Python 入口](../../scripts/zcode_focus.py)、[目标数据测试](../../tests/test_zcode_focus.py)。
- 已核实本地真实 task ID 可解析成正确标题/工作区；归档、缺失、歧义及含控制字符的标题在界面操作之前拒绝。
- Swift 自检只验证身份比对，未执行 Navigator，也不申请权限。
- 本轮通过 CUA 再次打开搜索，输入仍未稳定生效；没有把 CUA 的失败当作原生 AX 程序成功的证据。（历史节点，当时原生程序尚未实际运行；最终验收以顶部状态为准。）
- 依赖 Zcode 3.11.2 的搜索和菜单结构；中文/英文标签有兼容分支，但英文 UI 未测试。应用更新或控件变化可能需要改适配器。
- 同名候选遍历、多显示器、冷启动没有完成 UI 验收。当前要求 Zcode 已运行。
- 这条路线替代了“只能打开工作区”的实现方案；只有实际返回 focused 并经页面核验，才可更新全链路验收状态。
