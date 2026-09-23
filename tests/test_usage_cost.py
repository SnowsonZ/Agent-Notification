"""U5/U7/U8：价格查找、历史价、未定价、money_text 与 fx（用量金额规范 §4–§5）。"""

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from usage_cost import (
    DEFAULT_FX,
    MILLION,
    PricingTables,
    candidate_names,
    cost_for_models,
    display_total,
    load_fx,
    merge_cost,
    model_cost,
    money_text,
    price_for_day,
    set_fx,
)

DAY = date(2026, 9, 23)


def table(**layers):
    return PricingTables(**layers)


class CandidateNamesTest(unittest.TestCase):
    def test_order_canonical_raw_then_prefix_stripped(self):
        names = candidate_names(
            "glm-5.3-flash", ["GLM-5.3-Flash", "zai-coding-plan/glm-5.3-flash"]
        )
        self.assertEqual(names, ["glm-5.3-flash", "zai-coding-plan/glm-5.3-flash"])

    def test_unknown_key_yields_only_canonical_and_filtered(self):
        self.assertEqual(candidate_names("unknown", []), [])


class LookupOrderTest(unittest.TestCase):
    def test_layer_priority_override_official_fetched_snapshot(self):
        base = {"input": 1.0, "output": 2.0, "currency": "CNY"}
        tables = table(
            override={"m-1": {"input": 9.0, "output": 9.0, "currency": "USD"}},
            official={"m-1": dict(base)},
            fetched={"m-2": dict(base)},
            snapshot={"m-2": {"input": 5.0, "output": 5.0, "currency": "USD"}},
        )
        entry, layer = tables.lookup("m-1")
        self.assertEqual((layer, entry["input"]), ("override", 9.0))
        entry, layer = tables.lookup("m-2")
        self.assertEqual((layer, entry["input"]), ("fetched", 1.0))

    def test_snapshot_used_when_fetch_missing(self):
        tables = table(snapshot={"m-3": {"input": 3.0, "currency": "USD"}})
        entry, layer = tables.lookup("m-3")
        self.assertEqual((layer, entry["input"]), ("snapshot", 3.0))

    def test_alias_exact_match(self):
        tables = table(
            official={"glm-5.3-flash": {"aliases": ["chat-glms"], "input": 0.8}}
        )
        self.assertEqual(tables.lookup("chat-glms")[0]["input"], 0.8)
        self.assertIsNone(tables.lookup("chat-glm")[0])

    def test_no_similarity_matching(self):
        tables = table(official={"glm-5.3": {"input": 8.0}})
        self.assertIsNone(tables.lookup("glm-5.3-flash")[0])


class HistoryPriceTest(unittest.TestCase):
    def test_day_before_change_uses_history(self):
        entry = {
            "input": 0.8,
            "output": 2.8,
            "currency": "CNY",
            "history": [
                {"until": "2026-08-01", "input": 1.6, "cache_read": 0.46, "output": 5.6}
            ],
        }
        prices, _ = price_for_day(entry, date(2026, 7, 15))
        self.assertEqual(prices["input"], 1.6)
        prices, _ = price_for_day(entry, date(2026, 8, 1))
        self.assertEqual(prices["input"], 0.8)

    def test_first_matching_segment_wins(self):
        entry = {
            "input": 0.8,
            "output": 2.8,
            "currency": "CNY",
            "history": [
                {"until": "2026-01-01", "input": 3.2},
                {"until": "2026-06-01", "input": 1.6},
            ],
        }
        prices, _ = price_for_day(entry, date(2026, 3, 1))
        self.assertEqual(prices["input"], 1.6)

    def test_cache_prices_fall_back_to_input_with_notes(self):
        entry = {"input": 2.0, "output": 8.0, "currency": "CNY"}
        prices, notes = price_for_day(entry, DAY)
        self.assertEqual(prices["cache_write"], 2.0)
        self.assertEqual(prices["cache_read"], 2.0)
        self.assertEqual(len(notes), 2)


class CostTest(unittest.TestCase):
    def test_model_cost_amounts_and_currency(self):
        entry = {"input": 2.0, "cache_read": 0.4, "output": 8.0, "currency": "CNY"}
        cost = model_cost(
            {
                "fresh_input": MILLION,
                "cache_write": 0,
                "cache_read": MILLION // 2,
                "output": MILLION // 4,
            },
            entry,
            DAY,
        )
        self.assertAlmostEqual(cost["input"]["CNY"], 2.0)
        self.assertAlmostEqual(cost["cache"]["CNY"], 0.2)
        self.assertAlmostEqual(cost["output"]["CNY"], 2.0)
        self.assertEqual(cost["unpriced_tokens"], 0)

    def test_unpriced_model_counts_tokens_only(self):
        cost = model_cost(
            {"fresh_input": 10, "cache_write": 1, "cache_read": 2, "output": 3},
            None,
            DAY,
        )
        self.assertEqual(cost["unpriced_tokens"], 16)
        self.assertEqual(display_total(cost, "CNY", DEFAULT_FX), 0.0)

    def test_cost_for_models_native_fallback_and_unknown(self):
        models = {
            "unknown": {
                "fresh_input": 100,
                "cache_write": 0,
                "cache_read": 0,
                "output": 0,
                "native_cost_usd": 0.5,
            },
            "glm-5.3-flash": {
                "fresh_input": 10,
                "cache_write": 0,
                "cache_read": 0,
                "output": 0,
                "native_cost_usd": 0.25,
            },
        }
        tables = table()  # 空表：glm 未命中 → native 兜底；unknown 一律未定价
        cost, unpriced, notes = cost_for_models(models, DAY, tables)
        self.assertAlmostEqual(cost["native_fallback"]["USD"], 0.25)
        self.assertEqual(cost["unpriced_tokens"], 100)
        self.assertEqual(unpriced, [])
        self.assertTrue(any("native cost fallback" in note for note in notes))

    def test_cost_for_models_prefixed_raw_name_hits_official(self):
        models = {
            "glm-5.3-flash": {
                "fresh_input": MILLION,
                "cache_write": 0,
                "cache_read": 0,
                "output": 0,
                "raw_names": ["zai-coding-plan/glm-5.3-flash"],
            }
        }
        tables = table(official={})  # 空官方层 → 未定价
        cost, unpriced, _ = cost_for_models(models, DAY, tables)
        self.assertEqual(cost["unpriced_tokens"], MILLION)
        self.assertEqual(unpriced, ["glm-5.3-flash"])
        tables = table(official={"glm-5.3-flash": {"input": 0.8, "currency": "CNY"}})
        cost, _, _ = cost_for_models(models, DAY, tables)
        self.assertAlmostEqual(cost["input"]["CNY"], 0.8)

    def test_merge_cost_accumulates(self):
        target = merge_cost(
            merge_cost(
                {
                    "input": {"USD": 0.0, "CNY": 0.0},
                    "cache": {"USD": 0.0, "CNY": 0.0},
                    "output": {"USD": 0.0, "CNY": 0.0},
                    "unpriced_tokens": 0,
                },
                {
                    "input": {"USD": 1.0, "CNY": 2.0},
                    "cache": {},
                    "output": {},
                    "unpriced_tokens": 5,
                },
            ),
            {"input": {"CNY": 1.0}, "cache": {}, "output": {}, "unpriced_tokens": 7},
        )
        self.assertEqual(target["input"]["CNY"], 3.0)
        self.assertEqual(target["unpriced_tokens"], 12)


class ReconciliationTest(unittest.TestCase):
    """U7：同一 model、同一单价下，我方计价与 native_cost_usd 误差 <1%。"""

    def test_computed_matches_native_within_one_percent(self):
        entry = {"input": 0.3, "output": 1.2, "currency": "USD"}
        models = {
            "glm-5.3-flash": {
                "fresh_input": 2_000_000,
                "cache_write": 0,
                "cache_read": 1_000_000,
                "output": 500_000,
                "native_cost_usd": 2_000_000 / MILLION * 0.3
                + 1_000_000 / MILLION * 0.3
                + 500_000 / MILLION * 1.2,
            }
        }
        cost, _, _ = cost_for_models(
            models, DAY, table(fetched={"glm-5.3-flash": entry})
        )
        total = display_total(cost, "USD", 1.0)
        native = models["glm-5.3-flash"]["native_cost_usd"]
        self.assertLess(abs(total - native) / native, 0.01)


class MoneyTextTest(unittest.TestCase):
    """U8：与 Swift moneyText 跑同一组边界值。"""

    CASES = (
        (None, "CNY", "—"),
        (None, "USD", "—"),
        (0, "USD", "$0.00"),
        (0, "CNY", "¥0.00"),
        (0.005, "USD", "<$0.01"),
        (0.0099, "CNY", "<¥0.01"),
        (0.01, "USD", "$0.01"),
        (0.0149, "CNY", "¥0.01"),
        (1.5, "USD", "$1.50"),
        (1234.56, "USD", "$1,234.56"),
        (1234567.891, "CNY", "¥1,234,567.89"),
    )

    def test_boundaries(self):
        for value, currency, expected in self.CASES:
            with self.subTest(value=value, currency=currency):
                self.assertEqual(money_text(value, currency), expected)


class FxTest(unittest.TestCase):
    def test_default_and_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load_fx(tmp), {"USD_CNY": DEFAULT_FX, "as_of": ""})
            payload = set_fx(tmp, 7.25)
            self.assertEqual(payload["USD_CNY"], 7.25)
            self.assertEqual(load_fx(tmp)["USD_CNY"], 7.25)
            self.assertTrue(payload["as_of"])

    def test_invalid_rate_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                set_fx(tmp, 0)
            with self.assertRaises(ValueError):
                set_fx(tmp, -1)


class DisplayConvertTest(unittest.TestCase):
    def test_convert_display_both_views(self):
        cost = {
            "input": {"USD": 1.0, "CNY": 7.0},
            "cache": {"USD": 0.0, "CNY": 0.0},
            "output": {"USD": 2.0, "CNY": 0.0},
            "unpriced_tokens": 0,
        }
        import usage_cost

        input_v, _cache_v, output_v, fallback_v = usage_cost.convert_display(
            cost, "CNY", 7.0
        )
        self.assertAlmostEqual(input_v, 14.0)
        self.assertAlmostEqual(output_v, 14.0)
        self.assertAlmostEqual(fallback_v, 0.0)
        input_v, _, output_v, _ = __import__("usage_cost").convert_display(
            cost, "USD", 7.0
        )
        self.assertAlmostEqual(input_v, 2.0)
        self.assertAlmostEqual(output_v, 2.0)


if __name__ == "__main__":
    unittest.main()
