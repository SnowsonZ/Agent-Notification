"""U6：拉取转换、10 倍防护、历史追加与自适应节奏状态机（用量金额规范 §4.4–§4.5）。"""

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from pricing_fetch import DAY_SECONDS, load_state, transform
from pricing_fetch import fetch as run_fetch

DAY = date(2026, 9, 23)


def upstream(name="gpt-5.6-sol", input_cost=0.000003, output_cost=0.000012, **extra):
    entry = {
        "mode": "chat",
        "input_cost_per_token": input_cost,
        "output_cost_per_token": output_cost,
    }
    entry.update(extra)
    return {name: entry}


def downloader(payload, status=200):
    def fake(url, timeout):
        if status != 200:
            raise OSError(f"http {status}")
        if isinstance(payload, (bytes, str)):
            return payload
        return json.dumps(payload).encode()

    return fake


class TransformTest(unittest.TestCase):
    def test_mode_and_input_filter(self):
        entries, guards = transform(
            {
                "chat-model": {"mode": "chat", "input_cost_per_token": 1e-6},
                "responses-model": {
                    "mode": "responses",
                    "input_cost_per_token": 2e-6,
                },
                "embedding": {"mode": "embedding", "input_cost_per_token": 3e-6},
                "no-input": {"mode": "chat"},
            },
            today=DAY,
        )
        self.assertEqual(sorted(entries), ["chat-model", "responses-model"])
        self.assertEqual(guards, [])

    def test_field_mapping_times_one_million(self):
        entries, _ = transform(
            {
                "m": {
                    "mode": "chat",
                    "input_cost_per_token": 0.000003,
                    "output_cost_per_token": 0.000012,
                    "cache_read_input_token_cost": 0.0000003,
                    "cache_creation_input_token_cost": 0.00000375,
                }
            },
            today=DAY,
        )
        entry = entries["m"]
        self.assertEqual(entry["input"], 3.0)
        self.assertEqual(entry["output"], 12.0)
        self.assertEqual(entry["cache_read"], 0.3)
        self.assertEqual(entry["cache_write"], 3.75)
        self.assertEqual(entry["currency"], "USD")

    def test_lowercase_and_prefix_alias(self):
        entries, _ = transform(
            {"Provider/Model-X": {"mode": "chat", "input_cost_per_token": 1e-6}},
            today=DAY,
        )
        self.assertIn("provider/model-x", entries)
        self.assertEqual(entries["provider/model-x"]["aliases"], ["model-x"])

    def test_negative_and_nonfinite_rejected(self):
        entries, guards = transform(
            {
                "neg": {"mode": "chat", "input_cost_per_token": -1e-6},
                "inf": {
                    "mode": "chat",
                    "input_cost_per_token": float("inf"),
                },
            },
            today=DAY,
        )
        self.assertEqual(entries, {})
        self.assertEqual(len(guards), 2)

    def test_ten_fold_jump_keeps_old(self):
        previous = {"m": {"input": 3.0, "output": 12.0, "currency": "USD"}}
        entries, guards = transform(
            {"m": {"mode": "chat", "input_cost_per_token": 3.1e-5}}, previous, DAY
        )
        # 修订 §4.4：防护触发时原样保留上一版条目（含 history），不删除。
        self.assertEqual(entries["m"], previous["m"])
        self.assertTrue(any("kept old" in guard for guard in guards))

    def test_price_change_appends_history(self):
        previous = {"m": {"input": 3.0, "output": 12.0, "currency": "USD"}}
        entries, _ = transform(
            {"m": {"mode": "chat", "input_cost_per_token": 6e-6}}, previous, DAY
        )
        history = entries["m"]["history"]
        self.assertEqual(
            history, [{"until": "2026-09-23", "input": 3.0, "output": 12.0}]
        )
        self.assertEqual(entries["m"]["input"], 6.0)


class FetchStateMachineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_same_content_streaks_double_interval(self):
        # 严格序列：变、同、同、同、同、同、同 → 7,7,14,14,28,28,30。
        payload = upstream()
        now = 1_000_000.0
        intervals = []
        for index in range(7):
            result = run_fetch(
                self.root,
                now=now + index * DAY_SECONDS,
                download=downloader(payload),
                today=DAY,
            )
            self.assertTrue(result["ok"])
            intervals.append(result["interval_days"])
        self.assertEqual(intervals, [7, 7, 14, 14, 28, 28, 30])

    def test_failure_retries_next_day_without_state_change(self):
        now = 1_000_000.0
        ok = run_fetch(self.root, now=now, download=downloader(upstream()), today=DAY)
        self.assertTrue(ok["ok"])
        state = load_state(self.root)
        self.assertEqual(state["interval_days"], 7)
        failure = run_fetch(
            self.root,
            now=now + DAY_SECONDS,
            download=downloader("not json", status=500),
            today=DAY,
        )
        self.assertFalse(failure["ok"])
        state_after = load_state(self.root)
        self.assertEqual(state_after["interval_days"], 7)
        self.assertEqual(state_after["same_streak"], state["same_streak"])
        self.assertAlmostEqual(state_after["next_due"], now + DAY_SECONDS + DAY_SECONDS)
        self.assertTrue(state_after["last_error"])
        # 次日（到期后）重试成功恢复正常。
        retry = run_fetch(
            self.root,
            now=now + 2 * DAY_SECONDS,
            download=downloader(upstream()),
            today=DAY,
        )
        self.assertTrue(retry["ok"])

    def test_auto_skips_when_not_due(self):
        now = 1_000_000.0
        run_fetch(self.root, now=now, download=downloader(upstream()), today=DAY)
        result = run_fetch(
            self.root,
            now=now + DAY_SECONDS,
            auto=True,
            download=downloader(upstream()),
            today=DAY,
        )
        self.assertTrue(result["skipped"])

    def test_manual_ignores_due(self):
        now = 1_000_000.0
        run_fetch(self.root, now=now, download=downloader(upstream()), today=DAY)
        result = run_fetch(
            self.root,
            now=now + DAY_SECONDS,
            download=downloader(upstream()),
            today=DAY,
        )
        self.assertFalse(result.get("skipped", False))

    def test_fetched_file_written_only_on_change(self):
        now = 1_000_000.0
        run_fetch(self.root, now=now, download=downloader(upstream()), today=DAY)
        first = (self.root / "pricing" / "fetched.json").read_text()
        run_fetch(
            self.root,
            now=now + DAY_SECONDS,
            download=downloader(upstream()),
            today=DAY,
        )
        second = (self.root / "pricing" / "fetched.json").read_text()
        self.assertEqual(first, second)

    def test_history_inherited_when_price_unchanged(self):
        # R4 修订：单价不变时原样继承旧条目的 history，且不算内容变化。
        previous = {
            "m": {
                "input": 3.0,
                "output": 12.0,
                "currency": "USD",
                "history": [{"until": "2026-01-01", "input": 6.0, "output": 24.0}],
            }
        }
        entries, guards = transform(
            {
                "m": {
                    "mode": "chat",
                    "input_cost_per_token": 3e-6,
                    "output_cost_per_token": 12e-6,
                }
            },
            previous,
            DAY,
        )
        # 单价不变：history 原样继承，不追加、不判变化。
        self.assertEqual(
            entries["m"]["history"],
            [{"until": "2026-01-01", "input": 6.0, "output": 24.0}],
        )
        self.assertEqual(guards, [])

    def test_negative_keeps_old_entry_when_previous_exists(self):
        # R19 修订：非法数值时保留上一版条目（含 history）。
        previous = {"m": {"input": 3.0, "output": 12.0, "currency": "USD"}}
        entries, guards = transform(
            {"m": {"mode": "chat", "input_cost_per_token": -1e-6}}, previous, DAY
        )
        self.assertEqual(entries["m"], previous["m"])
        self.assertTrue(any("kept old" in guard for guard in guards))

    def test_alias_conflict_dropped_consistent_kept(self):
        # R13 修订：别名只有在所有指向条目四项单价完全一致时才保留。
        raw = {
            "provider-a/model-x": {
                "mode": "chat",
                "input_cost_per_token": 1e-6,
                "output_cost_per_token": 2e-6,
            },
            "provider-b/model-x": {
                "mode": "chat",
                "input_cost_per_token": 5e-6,
                "output_cost_per_token": 8e-6,
            },
            "provider-c/model-y": {
                "mode": "chat",
                "input_cost_per_token": 1e-6,
                "output_cost_per_token": 2e-6,
            },
            "provider-d/model-y": {
                "mode": "chat",
                "input_cost_per_token": 1e-6,
                "output_cost_per_token": 2e-6,
            },
        }
        entries, guards = transform(raw, today=DAY)
        self.assertNotIn("model-x", entries["provider-a/model-x"].get("aliases") or [])
        self.assertNotIn("model-x", entries["provider-b/model-x"].get("aliases") or [])
        self.assertTrue(any("model-x" in guard for guard in guards))
        # model-y 两处单价一致：别名保留。
        self.assertEqual(entries["provider-c/model-y"].get("aliases"), ["model-y"])
        self.assertEqual(entries["provider-d/model-y"].get("aliases"), ["model-y"])


class TransformBoundaryTest(unittest.TestCase):
    """变异测试（harness/mutate.py）暴露的边界：「超过 10 倍」不含 10 倍本身；0 是合法单价；
    输出单价非法同样触发保留；多级前缀的别名取最后一段。"""

    def test_exactly_ten_fold_is_not_a_jump(self):
        previous = {"m": {"input": 1.0, "output": 12.0, "currency": "USD"}}
        entries, guards = transform({"m": {"mode": "chat", "input_cost_per_token": 1e-5}}, previous, DAY)
        self.assertEqual(entries["m"]["input"], 10.0)
        self.assertEqual(guards, [])

    def test_exactly_one_tenth_is_not_a_jump(self):
        previous = {"m": {"input": 10.0, "output": 12.0, "currency": "USD"}}
        entries, guards = transform({"m": {"mode": "chat", "input_cost_per_token": 1e-6}}, previous, DAY)
        self.assertEqual(entries["m"]["input"], 1.0)
        self.assertEqual(guards, [])

    def test_zero_price_is_valid(self):
        entries, guards = transform({"free": {"mode": "chat", "input_cost_per_token": 0.0}}, today=DAY)
        self.assertEqual(entries["free"]["input"], 0.0)
        self.assertEqual(guards, [])

    def test_invalid_output_price_keeps_previous_entry(self):
        previous = {"m": {"input": 3.0, "output": 12.0, "currency": "USD"}}
        raw = {"m": {"mode": "chat", "input_cost_per_token": 3e-6, "output_cost_per_token": -1.0}}
        entries, guards = transform(raw, previous, DAY)
        self.assertEqual(entries["m"], previous["m"])
        self.assertTrue(any("kept old" in guard for guard in guards))

    def test_alias_is_last_path_segment(self):
        entries, _ = transform({"vendor/family/model-z": {"mode": "chat", "input_cost_per_token": 1e-6}}, today=DAY)
        self.assertEqual(entries["vendor/family/model-z"]["aliases"], ["model-z"])


if __name__ == "__main__":
    unittest.main()
