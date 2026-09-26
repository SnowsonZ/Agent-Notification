"""性质测试：对随机输入断言不变量，覆盖 v0.8.0 的 F2/F3 类缺陷（口径不一致、状态机边界）。

只用标准库：每条性质跑固定的一组种子（结果可复现，失败时报出种子）；
环境变量 PROPTEST_SEEDS=N 可放大到 N 个种子做更充分的搜索。
"""

import json
import os
import random
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from daily_report import (
    _merge_day_tasks,
    _task_key,
    _task_total,
    build_report,
    cost_thresholds,
    day_bounds,
    finalize_day_report,
)
from inbox_store import Store
from pricing_fetch import transform
from usage_cost import PricingTables, cost_for_models
from usage_report import collect

SEEDS = range(int(os.environ.get("PROPTEST_SEEDS", "25")))
DAY0 = date(2026, 8, 1)


class Property(unittest.TestCase):
    def for_each_seed(self, check):
        for seed in SEEDS:
            with self.subTest(seed=seed):
                check(random.Random(seed))


# ---- 价格拉取（V080-R4、V080-R13、V080-R19） ---------------------------------------------

NAMES = ["p1/m1", "p2/m1", "p3/m2", "p4/m2", "m3"]
UNIT_PRICES = [1e-6, 2e-6, 3e-6, 6e-6]


def random_raw(rng, previous):
    """上游快照：大多是正常单价，夹杂非法值、10 倍以上跳变与缺失条目。"""
    raw = {}
    for name in NAMES:
        roll = rng.random()
        if roll < 0.1:
            continue
        entry = {"mode": "chat"}
        old = previous.get(name)
        if roll < 0.2:
            entry["input_cost_per_token"] = rng.choice([-1e-6, float("inf"), float("nan")])
        elif roll < 0.3 and old:
            entry["input_cost_per_token"] = old["input"] * 1e-6 * rng.choice([20, 0.02])
        else:
            entry["input_cost_per_token"] = rng.choice(UNIT_PRICES)
        entry["output_cost_per_token"] = rng.choice(UNIT_PRICES)
        raw[name] = entry
    return raw


def is_guarded(value, old):
    """这条上游值是否应触发「保留上一版」：非法数值，或任一单价跳变超过 10 倍。"""
    number = value.get("input_cost_per_token")
    if number is None or not (number >= 0) or number == float("inf"):
        return True
    if old:
        for field, source in (("input", "input_cost_per_token"), ("output", "output_cost_per_token")):
            new = value.get(source)
            if old.get(field) and new:
                ratio = new * 1e6 / old[field]
                if ratio > 10 or ratio < 0.1:
                    return True
    return False


class PricingTransformProperties(Property):
    def test_history_only_grows_and_guards_keep_previous_entry(self):
        """V080-R4、V080-R19：history 只增不丢；非法值与跳变时条目与上一版完全一致。"""

        def check(rng):
            previous = {}
            for offset in range(12):
                day = DAY0 + timedelta(days=offset)
                raw = random_raw(rng, previous)
                entries, _ = transform(raw, previous, day)
                for name, value in raw.items():
                    old = previous.get(name)
                    if old is None:
                        continue
                    # 有上一版时条目不能消失：防护与非法值都应保留，正常值应更新（回放发现此前会被跳过）。
                    self.assertIn(name, entries, f"{day} {name}：有上一版的条目被删除")
                    new = entries[name]
                    if is_guarded(value, old):
                        # 价格与 history 原样保留；别名随后参与全局冲突清理（V080-R13），只可能减少。
                        strip = lambda entry: {k: v for k, v in entry.items() if k != "aliases"}
                        self.assertEqual(strip(new), strip(old), f"{day} {name}：防护触发时应原样保留上一版")
                        self.assertLessEqual(set(new.get("aliases") or []), set(old.get("aliases") or []))
                        continue
                    old_history = old.get("history") or []
                    new_history = new.get("history") or []
                    self.assertEqual(new_history[: len(old_history)], old_history, f"{day} {name}：history 丢失")
                    changed = old.get("input") != new.get("input") or old.get("output") != new.get("output")
                    self.assertEqual(len(new_history) - len(old_history), 1 if changed else 0, f"{day} {name}")
                previous = entries

        self.for_each_seed(check)

    def test_result_does_not_depend_on_upstream_order(self):
        """V080-R13：别名取舍不能取决于上游条目的顺序。"""

        def check(rng):
            previous = {}
            for offset in range(4):
                day = DAY0 + timedelta(days=offset)
                raw = random_raw(rng, previous)
                items = list(raw.items())
                rng.shuffle(items)
                shuffled = dict(items)
                forward, forward_guards = transform(raw, previous, day)
                backward, backward_guards = transform(shuffled, previous, day)
                self.assertEqual(json.dumps(forward, sort_keys=True), json.dumps(backward, sort_keys=True))
                self.assertEqual(len(forward_guards), len(backward_guards))
                previous = forward

        self.for_each_seed(check)


# ---- 热力金额分级（V080-R17） ------------------------------------------------------------


class CostThresholdProperties(Property):
    def test_thresholds_do_not_depend_on_input_order(self):
        """V080-R17（H0925-1：原回归测试自行排序，守不住缺陷）：阈值基于排序后的样本；输入按日期（乱序）给出时结果不变且单调。"""

        def check(rng):
            amounts = [round(rng.uniform(0.01, 500), 2) for _ in range(rng.randint(0, 40))]
            shuffled = amounts[:]
            rng.shuffle(shuffled)
            expected = cost_thresholds(sorted(amounts))
            self.assertEqual(cost_thresholds(shuffled), expected)
            self.assertEqual(expected, sorted(expected))
            if len(amounts) >= 8:
                self.assertEqual(expected, [sorted(amounts)[int(len(amounts) * q)] for q in (0.5, 0.75, 0.9)])

        self.for_each_seed(check)


# ---- 过去日报告合并不降级（V080-R2、V080-R3） ---------------------------------------------


def random_task(rng, provider, session, v7=False):
    """任务记录；约一成三类合计为 0。v7=True 时模拟旧版报告（没有 models）。"""
    parts = [0, 0, 0] if rng.random() < 0.1 else [rng.randint(0, 5000) for _ in range(3)]
    fresh = rng.randint(0, parts[0]) if parts[0] else 0
    record = {
        "provider": provider,
        "session_id": session,
        "title": "t",
        "project": "/work/x",
        "input_tokens": parts[0],
        "cache_tokens": parts[1],
        "output_tokens": parts[2],
        "total_tokens": sum(parts),
        "fidelity": "exact",
        "models": {
            "m": {
                "fresh_input": fresh,
                "cache_write": parts[0] - fresh,
                "cache_read": parts[1],
                "output": parts[2],
            }
        },
    }
    if v7:
        del record["models"]
    return record


def model_sums(task):
    """规范口径：输入 = fresh_input + cache_write，缓存 = cache_read，输出 = output。"""
    models = task.get("models") or {}
    return (
        sum(int(entry.get("fresh_input") or 0) + int(entry.get("cache_write") or 0) for entry in models.values()),
        sum(int(entry.get("cache_read") or 0) for entry in models.values()),
        sum(int(entry.get("output") or 0) for entry in models.values()),
    )


class KnownDefectTest(unittest.TestCase):
    def test_h0925_4_category_totals_match_models_after_downgrade_protection(self):
        """H0925-4 回归：重扫总量更小但某一类比现有报告大时，三类合计按类别取
        max(现有, 重扫)，正差额记 unknown，model 明细之和与三类合计一致。"""
        old = {"provider": "zcode", "session_id": "s0", "input_tokens": 3582, "cache_tokens": 3632,
               "output_tokens": 4467, "total_tokens": 11681, "fidelity": "exact",
               "models": {"m": {"fresh_input": 3582, "cache_write": 0, "cache_read": 3632, "output": 4467}}}
        new = {"provider": "zcode", "session_id": "s0", "input_tokens": 1786, "cache_tokens": 4487,
               "output_tokens": 846, "total_tokens": 7119, "fidelity": "exact",
               "models": {"m": {"fresh_input": 1786, "cache_write": 0, "cache_read": 4487, "output": 846}}}
        merged, _ = _merge_day_tasks({"tasks": [new]}, {"tasks": [old]}, set())
        task = merged[0]
        self.assertEqual(model_sums(task), (task["input_tokens"], task["cache_tokens"], task["output_tokens"]))


class MergeNoDowngradeProperties(Property):
    def test_merged_tasks_are_internally_consistent(self):
        """每个合并后的任务：各 model 的 token 之和等于三类合计（差额必须如数记入 unknown）；
        发生 v7 恢复时打上标记；结果按合计降序、来源、会话排序。变异测试发现这些此前都没被检查。"""

        def check(rng):
            keys = [(rng.choice(["pi", "codex", "zcode"]), f"s{i}") for i in range(rng.randint(1, 8))]
            base_tasks = [random_task(rng, *key, v7=rng.random() < 0.3) for key in keys if rng.random() < 0.8]
            rescan_tasks = [random_task(rng, *key) for key in keys if rng.random() < 0.7]
            excluded = {key for key in keys if rng.random() < 0.15}
            merged, restored = _merge_day_tasks({"tasks": rescan_tasks}, {"tasks": base_tasks}, excluded)
            rescan_map = {_task_key(task): task for task in rescan_tasks}
            for task in merged:
                sums = model_sums(task)
                self.assertEqual(sums, (task["input_tokens"], task["cache_tokens"], task["output_tokens"]), task)
            rescan_keys = set(rescan_map)
            v7_restorable = [
                task
                for task in base_tasks
                if "models" not in task and _task_key(task) not in rescan_keys and _task_key(task) not in excluded
            ]
            self.assertEqual(restored, bool(v7_restorable))
            restored_keys = {_task_key(task) for task in merged if task.get("restored_from") == 7}
            self.assertEqual(restored_keys, {_task_key(task) for task in v7_restorable if _task_total(task) > 0})
            order = [(-task.get("total_tokens", 0), task["provider"], task["session_id"]) for task in merged]
            self.assertEqual(order, sorted(order))

        self.for_each_seed(check)

    def test_merged_tasks_never_lose_tokens(self):
        """V080-R2、V080-R3：按任务合并，任何任务的三类合计都不低于磁盘上的现有报告；
        只在现有报告中的任务被恢复，判定为 agent 的除外。"""

        def check(rng):
            keys = [(rng.choice(["pi", "codex", "zcode"]), f"s{i}") for i in range(rng.randint(1, 8))]
            base_tasks = [random_task(rng, *key) for key in keys if rng.random() < 0.8]
            rescan_tasks = [random_task(rng, *key) for key in keys if rng.random() < 0.7]
            excluded = {key for key in keys if rng.random() < 0.15}
            merged, _ = _merge_day_tasks({"tasks": rescan_tasks}, {"tasks": base_tasks}, excluded)
            merged_map = {_task_key(task): task for task in merged}
            rescan_keys = {_task_key(task) for task in rescan_tasks}
            for old in base_tasks:
                key = _task_key(old)
                if key not in rescan_keys and key in excluded:
                    self.assertNotIn(key, merged_map, "判定为 agent 的会话不应恢复")
                    continue
                if _task_total(old) == 0 and key not in rescan_keys:
                    continue
                self.assertIn(key, merged_map, f"{key} 在合并后丢失")
                self.assertGreaterEqual(_task_total(merged_map[key]), _task_total(old), f"{key} 被降级")
            for new in rescan_tasks:
                self.assertGreaterEqual(_task_total(merged_map[_task_key(new)]), _task_total(new))

        self.for_each_seed(check)


# ---- 周期金额逐日计价（V080-R11） ---------------------------------------------------------


class PeriodCostProperties(Property):
    def test_period_totals_equal_daily_priced_sum(self):
        """V080-R11：期内有调价时，totals、series、by.model 都按每天适用的价格计价。"""

        def check(rng):
            change = DAY0 + timedelta(days=rng.randint(1, 27))
            old_price, new_price = rng.choice([(1.6, 0.8), (2.0, 4.0), (0.5, 3.0)])
            tables = PricingTables(
                official={
                    "glm-x": {
                        "input": new_price,
                        "output": new_price * 2,
                        "currency": "CNY",
                        "history": [{"until": change.isoformat(), "input": old_price, "output": old_price * 2}],
                    }
                }
            )
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                store = Store(root)
                expected_input = 0.0
                days = sorted(rng.sample(range(28), rng.randint(2, 6)))
                for index, offset in enumerate(days):
                    day = DAY0 + timedelta(days=offset)
                    tokens = rng.randint(1, 5) * 100_000
                    record = random_task(rng, "pi", f"s{index}")
                    record.update(
                        input_tokens=tokens,
                        cache_tokens=0,
                        output_tokens=0,
                        total_tokens=tokens,
                        first_at=0,
                        last_at=1,
                        turns=1,
                        state="idle",
                        segments=[],
                        models={"glm-x": {"fresh_input": tokens, "cache_write": 0, "cache_read": 0, "output": 0, "native_cost_usd": None}},
                    )
                    report = build_report(day, [record], 0.0)
                    report["generated_at"] = day_bounds(day)[1] + 100
                    finalize_day_report(root, day, report)
                    price = old_price if day < change else new_price
                    expected_input += tokens * price / 1e6
                payload = collect(store, root, "month", DAY0 + timedelta(days=15), tables=tables)
                totals = payload["totals"]["cost"]["input"]["CNY"]
                self.assertAlmostEqual(totals, expected_input, places=6, msg=f"调价日 {change}")
                series = sum(row["cost"]["input"]["CNY"] for row in payload["series"])
                self.assertAlmostEqual(series, totals, places=6)
                self.assertAlmostEqual(payload["by"]["model"][0]["cost"]["input"]["CNY"], totals, places=6)
                for row in payload["series"]:
                    day = date.fromisoformat(row["date"])
                    if row["input_tokens"]:
                        want, _, _ = cost_for_models(
                            {"glm-x": {"fresh_input": row["input_tokens"], "cache_write": 0, "cache_read": 0, "output": 0}},
                            day,
                            tables,
                        )
                        self.assertAlmostEqual(row["cost"]["input"]["CNY"], want["input"]["CNY"], places=6)

        for seed in list(SEEDS)[:10]:  # 每个种子要落盘一个月的报告，控制耗时
            with self.subTest(seed=seed):
                check(random.Random(seed))


if __name__ == "__main__":
    unittest.main()
