"""事故回放集与变异工具的自检（快速，默认 verify 即运行；完整回放在 verify --full）。"""

import ast
import importlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import mutate
import replay
from replay_cases import BASELINE, CASES, DEFERRED, GUARDED


class ReplayCatalogTest(unittest.TestCase):
    def test_every_baseline_item_is_covered_or_deferred_with_reason(self):
        self.assertEqual(replay.coverage_gaps(), [])
        for defect, reason in DEFERRED.items():
            self.assertIn(defect, BASELINE)
            self.assertGreater(len(reason), 10, f"{defect} 的暂缓原因要写清楚")

    def test_injection_points_are_current(self):
        """注入点必须在当前代码中恰好出现一次：代码改了，回放用例要一起改（否则回放形同虚设）。"""
        for case in CASES:
            with self.subTest(defect=case.defect, title=case.title):
                source = (ROOT / case.file).read_text(encoding="utf-8")
                self.assertEqual(source.count(case.find), 1)
                self.assertNotEqual(case.find, case.replace)

    def test_guarded_tests_exist(self):
        for defect, tests in GUARDED.items():
            for test_id in tests:
                with self.subTest(defect=defect, test=test_id):
                    module_name, class_name, method = test_id.split(".")
                    module = importlib.import_module(module_name)
                    self.assertTrue(hasattr(getattr(module, class_name), method))


SAMPLE = """
def f(values, limit):
    values.sort()
    if not values or values[0] < limit and True:
        return values[0] + 1
    return None


def g():
    return 1 == 2
"""


class MutateTest(unittest.TestCase):
    def test_enumerates_only_target_functions(self):
        mutants = mutate.enumerate_mutants(SAMPLE, ("f",))
        operators = [mutant.operator for mutant in mutants]
        self.assertIn("删除调用语句", operators)
        self.assertIn("Lt→LtE", operators)
        self.assertIn("Add→Sub", operators)
        self.assertIn("And→Or", operators)
        self.assertIn("去掉 not", operators)
        self.assertNotIn("Eq→NotEq", operators)  # g 不是目标函数

    def test_every_mutant_compiles_and_differs(self):
        tree = ast.parse(SAMPLE)
        ranges = mutate._function_ranges(tree, ("f",))
        original = ast.unparse(tree)
        for mutant in mutate.enumerate_mutants(SAMPLE, ("f",)):
            with self.subTest(mutant=mutant.operator):
                mutated = ast.unparse(mutate._mutate(tree, ranges, mutant.index))
                compile(mutated, "<mutant>", "exec")
                self.assertNotEqual(mutated, original)

    def test_targets_point_at_existing_functions(self):
        for target in mutate.TARGETS:
            with self.subTest(target=target.name):
                tree = ast.parse((ROOT / target.file).read_text(encoding="utf-8"))
                found = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
                self.assertLessEqual(set(target.functions), found)

    def test_equal_score_is_not_below_rounded_baseline(self):
        self.assertFalse(mutate.below_baseline(43 / 45, round(43 / 45, 4)))
        self.assertTrue(mutate.below_baseline(42 / 45, round(43 / 45, 4)))
        self.assertFalse(mutate.below_baseline(0.5, None))

    def test_baseline_covers_every_target(self):
        import json

        baseline = json.loads(mutate.BASELINE.read_text())
        self.assertEqual(set(baseline), {target.name for target in mutate.TARGETS})


if __name__ == "__main__":
    unittest.main()
