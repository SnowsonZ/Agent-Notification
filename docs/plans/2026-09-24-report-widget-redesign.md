# 日报与桌面组件整体重设计 — 设计决策与实施记录

日期：2026-09-24 · 状态：已实施（分支 `ui/report-widgets-redesign`），组件真机验收（W1/W4/W7/W9）待用户执行

## 背景与问题

金额 feature（PR #6）以「另起一套」的方式叠加：周/月视图是独立的 Swift Charts 平行体系（不同卡片、不同配色、不同类目顺序），金额散点式挂在部分位置（今日头卡无金额），热力图着色/周期图度量/币种三个局部切换器各自为政；桌面组件是纯文本堆叠，规范 §4 承诺的官方图标从未实现，recent 组件硬编码 CNY。

## 口径决定（与用户对齐，原型 `scratch/design-report-widgets-20260924/`）

1. **金额不做全局切换/独立视图**（推翻先提出的「全局 token⇄金额 切换」方案）：金额只是伴随指标——凡有 token 统计处旁边给出金额。理由：用户判断金额单拎出来会加重割裂。
2. **只展示 USD**：取消 CNY/USD 选择器（日报与组件都是）；计价层保持原币，展示层按汇率折算；「按标价估算」措辞不上界面，免责句只保留在各页注脚（spec §7 要求）与 App 内设置面板。
3. **官方来源图标**：`native/agent-icons` 六家 + zcode（App 取运行时图标 / 组件取构建期提取的 PNG）；模型行按名称前缀映射家族图标（claude-*→Claude、gpt-*→Codex、glm-*→Zcode…），未匹配退化灰底字牌。
4. **编排统一**：日总览与周/月同为「双栏（来源 | 模型）+ Top 项目全宽」；周期视图并入日视图设计语言（手绘堆叠柱 + 悬浮卡），删除 Swift Charts 平行体系。
5. **组件细节**：用量卡 token hero 中上定位、金额放大无标签；M 卡行宽不足以放横条，来源行只留 图标+名称+token+$；配置精简为 周期·视角（度量/币种删除）。

## 实施

- `c717dfe` 日报窗口：去度量/币种状态、hero 金额行、三类配色统一、总览新编排（WeekSourcesDonutCard/ModelRankCard/ProjectRankCard）、周期视图手绘柱图化、官方图标进日报。
- `46b27ff` 桌面组件：WidgetIcons.swift（appex 包内渲染）、构建脚本复制图标进 appex（zcode 构建期提取）、用量 S/M/L 新布局、recent CNY 硬编码修复、配置精简、共享层抽取（usdText/usdTotal、tokenClassColor、modelIconId、IconPipeline）、组件默认设置面板（托盘入口）。
- 快照：`Prefs.Fallback` 去 metric（旧快照多余键解码忽略，测试覆盖）；签名纳入 fallback 周期/视角，设置面板改动即触发快照重写与 reload。

## 已知边界

- 日总览来源环形/模型榜/Top 项目用 `usage` 周（近 7 天）口径（含 cost），不再用 overview 的 week_sources/topProjects（无逐类 cost）；详情页来源/项目悬浮卡仍为纯 token（逐任务 cost 已在任务行展示）。
- 组件 M 卡无占比横条（行宽不足）；L 卡保留。
- CI（无 Zcode.app）构建的 appex 无 zcode.png，组件退化品牌色字牌；本机构建有。
- 本机 CLT 构建为降级静态组件（W8 预期）；配置式组件的配置面板验收需 CI standalone 包。
- 桌面组件真机验收（W1/W4 端到端/W7/W9）待用户执行；重建后需重授辅助功能授权（已执行 tccutil reset）。

## 验收证据

- 日报三屏截图：`scratch/u10-daily-{day,week,month}.png`（真实数据）。
- Python 267 项 + ruff 0.16.8 + Swift 策略测试（含新增 USD 折算与旧快照兼容断言）全部通过。
