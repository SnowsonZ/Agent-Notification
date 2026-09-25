"""架构适应度：同一口径只实现一次，调用方不得另行内联（v0.8.0 F2 类缺陷）。

v0.8.0 修复 R8、R15 时补了纯函数与测试，但 WidgetSnapshotWriter 没有调用它们，而是各自内联了
一份判断：测试测的是没人用的函数，写入器改回旧逻辑时没有任何测试会失败。这里用源码级检查
（Linux 上也能跑）确认调用方经过被测的那一份实现。
"""

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def swift_source(name):
    return (ROOT / "native" / name).read_text(encoding="utf-8")


def python_function(module, name):
    source = (ROOT / "scripts" / module).read_text(encoding="utf-8")
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{module} 中没有 {name}")


class WidgetWriterUsesPolicyTest(unittest.TestCase):
    def setUp(self):
        self.writer = swift_source("WidgetSnapshotWriter.swift")

    def test_running_rows_go_through_widget_running_listed(self):
        """V080-R8（H0925-2）：进行中条目由 widgetRunningListed 判定，不另写过滤条件。"""
        block = re.search(r"let runningRows = (.+?)\n        \}", self.writer, flags=re.DOTALL)
        self.assertIsNotNone(block, "找不到 runningRows")
        self.assertIn("widgetRunningListed(", block.group(1))
        self.assertNotIn("inboxNotifyEligible", block.group(1))

    def test_entry_text_goes_through_widget_entry_text(self):
        """V080-R15（H0925-2）：条目标题与项目名经 widgetEntryText，写入器里不再内联 hideTitles 判断。"""
        for pattern in (r"hideTitles\s*\?", r"^\s*title:\s*row\.title,?\s*$", r"^\s*project:\s*row\.project,?\s*$"):
            match = re.search(pattern, self.writer, flags=re.MULTILINE)
            self.assertIsNone(match, f"写入器内联了条目文字：{match and match.group(0).strip()}")
        self.assertGreaterEqual(self.writer.count("project: text.project"), 2)


class DailyPricingTest(unittest.TestCase):
    def test_overview_thresholds_use_cost_thresholds(self):
        """V080-R17（H0925-1）：热力阈值只由 cost_thresholds 计算。"""
        source = ast.unparse(python_function("daily_report.py", "generate_overview"))
        self.assertIn("cost_thresholds(amounts)", source)
        self.assertNotIn("0.75", source)

    def test_period_costs_are_priced_on_each_day(self):
        """V080-R11：collect 中每次计价都用当天的日期，不用锚点或期初。"""
        node = python_function("usage_report.py", "collect")
        calls = [
            call
            for call in ast.walk(node)
            if isinstance(call, ast.Call) and getattr(call.func, "id", None) == "cost_for_models"
        ]
        self.assertGreaterEqual(len(calls), 3)
        for call in calls:
            self.assertIsInstance(call.args[1], ast.Name)
            self.assertEqual(call.args[1].id, "day", ast.unparse(call))


if __name__ == "__main__":
    unittest.main()
