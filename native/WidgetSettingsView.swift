import SwiftUI

// 组件默认设置（desktop-widgets.md §4，2026-09-24 落地）：写入 widgetFallback*
// UserDefaults；快照签名包含这些键（WidgetSnapshotWriter），改动即触发快照重写
// 与组件 reload。金额口径：统一 USD、无度量/币种配置（重设计决定）。
struct WidgetSettingsView: View {
    @AppStorage("widgetFallbackPeriod") private var period = "day"
    @AppStorage("widgetFallbackDimension") private var dimension = "harness"
    @AppStorage("widgetHideTitles") private var hideTitles = false

    var body: some View {
        Form {
            Section("用量组件默认") {
                Picker("周期", selection: $period) {
                    Text("日").tag("day")
                    Text("周").tag("week")
                    Text("月").tag("month")
                }
                .pickerStyle(.segmented)
                Picker("视角", selection: $dimension) {
                    Text("来源").tag("harness")
                    Text("模型").tag("model")
                    Text("项目").tag("project")
                }
                .pickerStyle(.segmented)
            }
            Section {
                Toggle("隐藏任务标题", isOn: $hideTitles)
            } footer: {
                Text("开启后组件只显示项目名，快照文件不含标题（不只是显示时隐藏）。")
            }
            Section("金额口径") {
                Text("金额为按 API 标价的估算值（非账单），统一以 USD 显示；CNY 官价模型按汇率折算，汇率用 `inbox pricing fx` 维护。组件无度量与币种配置。")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Section("刷新") {
                Text("收件箱事件即时刷新 · 最近任务 60 秒合并 · 用量 15 分钟（打开日报立即）。组件不运行 Python，只读快照文件。")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
        .padding(20)
        .frame(width: 400)
    }
}
