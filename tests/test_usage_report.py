"""U9：周期边界（ISO 周跨年、闰年二月、月末）与 `inbox usage` 聚合。"""

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from daily_report import build_report
from inbox_store import Store
from usage_cost import PricingTables
from usage_report import collect, period_bounds, previous_anchor


class PeriodBoundsTest(unittest.TestCase):
    def test_iso_week_crosses_year(self):
        # 2026-01-01 是周四：ISO 周从 2025-12-29（周一）到 2026-01-04（周日）。
        start, end = period_bounds("week", date(2026, 1, 1))
        self.assertEqual((start, end), (date(2025, 12, 29), date(2026, 1, 4)))

    def test_week_monday_to_sunday(self):
        start, end = period_bounds("week", date(2026, 9, 23))  # 周三
        self.assertEqual((start.weekday(), end.weekday()), (0, 6))
        self.assertEqual((start, end), (date(2026, 9, 21), date(2026, 9, 27)))

    def test_leap_year_february(self):
        start, end = period_bounds("month", date(2028, 2, 15))
        self.assertEqual((start, end), (date(2028, 2, 1), date(2028, 2, 29)))

    def test_non_leap_february(self):
        start, end = period_bounds("month", date(2026, 2, 15))
        self.assertEqual((start, end), (date(2026, 2, 1), date(2026, 2, 28)))

    def test_previous_month_from_march(self):
        anchor = previous_anchor("month", date(2026, 3, 15))
        self.assertEqual(
            period_bounds("month", anchor), (date(2026, 2, 1), date(2026, 2, 28))
        )

    def test_previous_month_year_rollover(self):
        anchor = previous_anchor("month", date(2027, 1, 15))
        self.assertEqual(
            period_bounds("month", anchor), (date(2026, 12, 1), date(2026, 12, 31))
        )

    def test_previous_week_and_day(self):
        self.assertEqual(
            period_bounds("week", previous_anchor("week", date(2026, 9, 23))),
            (date(2026, 9, 14), date(2026, 9, 20)),
        )
        self.assertEqual(previous_anchor("day", date(2026, 9, 23)), date(2026, 9, 22))

    def test_unknown_period_rejected(self):
        with self.assertRaises(ValueError):
            period_bounds("quarter", date(2026, 9, 23))


class CollectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root)

    def finalize(self, day, records):
        from daily_report import day_bounds, finalize_day_report

        report = build_report(day, records, 0.0)
        report["generated_at"] = day_bounds(day)[1] + 100
        return finalize_day_report(self.root, day, report)

    def test_week_aggregates_across_month_boundary(self):
        # 周窗口跨 8/31 与 9/1：两天报告按月不同，但按 ISO 周聚合。
        tables = PricingTables(
            official={"glm-5.3-flash": {"input": 0.8, "output": 2.8, "currency": "CNY"}}
        )
        day_a = date(2026, 8, 31)
        day_b = date(2026, 9, 1)
        records = [
            {
                "provider": "pi",
                "session_id": "s1",
                "title": "t",
                "project": "/work/x",
                "first_at": 0,
                "last_at": 1,
                "input_tokens": 1_000_000,
                "cache_tokens": 0,
                "output_tokens": 500_000,
                "total_tokens": 1_500_000,
                "turns": 1,
                "state": "idle",
                "fidelity": "exact",
                "segments": [],
                "models": {
                    "glm-5.3-flash": {
                        "fresh_input": 1_000_000,
                        "cache_write": 0,
                        "cache_read": 0,
                        "output": 500_000,
                        "native_cost_usd": None,
                    }
                },
            }
        ]
        self.finalize(day_a, records)
        self.finalize(day_b, [dict(records[0], session_id="s2")])
        payload = collect(
            self.store, self.root, "week", date(2026, 9, 2), tables=tables
        )
        self.assertEqual(
            (payload["start"], payload["end"]), ("2026-08-31", "2026-09-06")
        )
        # 周 totals = 两天合计。
        self.assertEqual(payload["totals"]["total_tokens"], 3_000_000)
        # series 覆盖窗口全部 7 天（零值天保留，供图表连续 x 轴）。
        self.assertEqual(len(payload["series"]), 7)
        self.assertEqual(
            sum(row["total_tokens"] for row in payload["series"]), 3_000_000
        )
        # by.model 聚合同一 model；金额 = (1.5M tokens) × 0.8 元/M × 2 天。
        top = payload["by"]["model"][0]
        self.assertEqual(top["key"], "glm-5.3-flash")
        # 两天：input 类 = 1M×0.8/M = 0.8/天；output 类 = 0.5M×2.8/M = 1.4/天。
        self.assertAlmostEqual(top["cost"]["input"]["CNY"], 1.6)
        self.assertAlmostEqual(top["cost"]["output"]["CNY"], 2.8)
        # by.harness 按 CNY 视图金额降序。
        self.assertEqual(payload["by"]["harness"][0]["key"], "pi")
        self.assertEqual(payload["pricing"]["unpriced_models"], [])

    def test_unpriced_model_listed(self):
        tables = PricingTables()
        day = date(2026, 9, 10)
        records = [
            {
                "provider": "kimi",
                "session_id": "s1",
                "title": "t",
                "project": "",
                "first_at": 0,
                "last_at": 1,
                "input_tokens": 10,
                "cache_tokens": 0,
                "output_tokens": 5,
                "total_tokens": 15,
                "turns": 1,
                "state": "idle",
                "fidelity": "exact",
                "segments": [],
                "models": {
                    "k3": {
                        "fresh_input": 10,
                        "cache_write": 0,
                        "cache_read": 0,
                        "output": 5,
                        "native_cost_usd": None,
                    }
                },
            }
        ]
        self.finalize(day, records)
        payload = collect(self.store, self.root, "day", day, tables=tables)
        self.assertEqual(payload["totals"]["cost"]["unpriced_tokens"], 15)
        self.assertEqual(payload["pricing"]["unpriced_models"], ["k3"])

    def test_period_all_returns_three_periods(self):
        payload = collect(self.store, self.root, "all", tables=PricingTables())
        self.assertEqual(sorted(payload), ["day", "month", "week"])

    def test_period_price_change_totals_match_daily_sum(self):
        # R11：期内调价时 totals 按每天适用价格逐日计价，等于 series 之和。
        tables = PricingTables(
            official={
                "glm-5.3-flash": {
                    "input": 0.8,
                    "output": 2.8,
                    "currency": "CNY",
                    "history": [
                        {"until": "2026-09-10", "input": 1.6, "output": 5.6}
                    ],
                }
            }
        )
        day_a = date(2026, 9, 9)  # 调价前（旧价 1.6/5.6）
        day_b = date(2026, 9, 11)  # 调价后（新价 0.8/2.8）
        records = [
            {
                "provider": "pi",
                "session_id": "s1",
                "title": "t",
                "project": "/work/x",
                "first_at": 0,
                "last_at": 1,
                "input_tokens": 1_000_000,
                "cache_tokens": 0,
                "output_tokens": 1_000_000,
                "total_tokens": 2_000_000,
                "turns": 1,
                "state": "idle",
                "fidelity": "exact",
                "segments": [],
                "models": {
                    "glm-5.3-flash": {
                        "fresh_input": 1_000_000,
                        "cache_write": 0,
                        "cache_read": 0,
                        "output": 1_000_000,
                        "native_cost_usd": None,
                    }
                },
            }
        ]
        self.finalize(day_a, records)
        self.finalize(day_b, [dict(records[0], session_id="s2")])
        payload = collect(
            self.store, self.root, "month", date(2026, 9, 15), tables=tables
        )
        # 逐日取价：9/9 按旧价（1.6+5.6=7.2 元），9/11 按新价（0.8+2.8=3.6 元）。
        self.assertAlmostEqual(payload["totals"]["cost"]["input"]["CNY"], 1.6 + 0.8)
        self.assertAlmostEqual(payload["totals"]["cost"]["output"]["CNY"], 5.6 + 2.8)
        series_sum = sum(
            row["cost"]["input"]["CNY"] for row in payload["series"]
        )
        self.assertAlmostEqual(
            series_sum, payload["totals"]["cost"]["input"]["CNY"]
        )
        # by.model 同样逐日累加。
        top = payload["by"]["model"][0]
        self.assertAlmostEqual(top["cost"]["input"]["CNY"], 1.6 + 0.8)
        self.assertAlmostEqual(top["cost"]["output"]["CNY"], 5.6 + 2.8)


class BackfillAgentStatsTest(unittest.TestCase):
    """V080-R12：`inbox usage` 补录过去日时必须带上 agent 统计，定稿报告才有 agent_excluded 注脚。

    事故回放发现 v0.8.0 标为已修的 R12 没有任何回归测试：把 agent_stats 参数去掉，全部测试仍通过。"""

    def test_backfilled_report_keeps_agent_footnote(self):
        from unittest import mock

        import usage_report
        from daily_report import load_report

        day = date(2026, 8, 3)

        def fake_scan(store, home, first_day, last_day, agent_stats=None):
            if agent_stats is not None:
                agent_stats[day] = {"tasks": {("codex", "agent-1")}, "total_tokens": 1234}
            return {}

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(usage_report, "scan_buckets", fake_scan):
                usage_report.collect(Store(root), root, "day", day, tables=PricingTables())
            report = load_report(root, day.isoformat())
        self.assertEqual(report.get("agent_excluded"), {"tasks": 1, "total_tokens": 1234})


# ---- 变异测试缺口（2026-09-26 任务 002，只新增） ------------------------------------------
# previous_anchor 的锚点本身（经 period_bounds 断言会被掩蔽）与 _merge_models 的精确合同。


class PreviousAnchorExactTest(unittest.TestCase):
    def test_previous_week_anchor_is_exactly_seven_days_back(self):
        self.assertEqual(previous_anchor("week", date(2026, 9, 23)), date(2026, 9, 16))
        # 周一锚点同样回退 7 天；9/3 回退后跨月。
        self.assertEqual(previous_anchor("week", date(2026, 9, 21)), date(2026, 9, 14))
        self.assertEqual(previous_anchor("week", date(2026, 9, 3)), date(2026, 8, 27))

    def test_previous_month_anchor_is_first_of_previous_month(self):
        # 月末、跨年、月初三种锚点都落到上月 1 日。
        self.assertEqual(previous_anchor("month", date(2026, 3, 31)), date(2026, 2, 1))
        self.assertEqual(previous_anchor("month", date(2026, 1, 15)), date(2025, 12, 1))
        self.assertEqual(previous_anchor("month", date(2026, 9, 1)), date(2026, 8, 1))


class MergeModelsContractTest(unittest.TestCase):
    def _entry(self, **overrides):
        entry = {
            "fresh_input": 0,
            "cache_write": 0,
            "cache_read": 0,
            "output": 0,
            "native_cost_usd": None,
        }
        entry.update(overrides)
        return entry

    def test_native_cost_accumulates(self):
        from usage_report import _merge_models

        target = {}
        _merge_models(target, {"glm-x": self._entry(native_cost_usd=1.5)})
        _merge_models(target, {"glm-x": self._entry(native_cost_usd=0.5)})
        self.assertAlmostEqual(target["glm-x"]["native_cost_usd"], 2.0)

    def test_raw_names_dedup_and_cap_at_five(self):
        from usage_report import _merge_models

        target = {}
        _merge_models(target, {"glm-x": self._entry(raw_names=["a", "a", "b"])})
        _merge_models(target, {"glm-x": self._entry(raw_names=["b", "c", "d", "e", "f"])})
        # 去重保序；第 6 个不再收入。
        self.assertEqual(target["glm-x"]["_raw"], ["a", "b", "c", "d", "e"])


if __name__ == "__main__":
    unittest.main()
