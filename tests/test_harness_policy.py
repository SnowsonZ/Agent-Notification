"""合并路由（harness/policy.py）与 R1 加强判定（harness/r1_checks.py）。

设计 16.1 P2 的机器验收：单测覆盖各条判定分支（风险、类别、声明不一致、误差预算、预算标签、规模、
R1 加强判定），job summary 写明每条理由。
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".harness"))
sys.path.insert(0, str(ROOT / "tests"))

from engine.checks import r1_checks
from engine.core.common import load_rules
from engine.routing import policy, risk
from test_harness import TempRepo

AUTONOMY = r1_checks.load_autonomy()


def facts(level=0, klass="K1", **overrides):
    report = risk.RiskReport(level=level)
    return policy.Facts(report, klass, **overrides)


def verdict(item):
    rules = policy.decide(item, AUTONOMY)
    return all(rule.ok for rule in rules), {rule.name: rule for rule in rules}


class DecideTest(unittest.TestCase):
    def test_r0_docs_within_budget_auto_merge(self):
        auto, rules = verdict(facts(changed_lines=40, escapes=1, window=20))
        self.assertTrue(auto)
        self.assertIn("逃逸 1，预算 ≤ 1", rules["预算"].reason)

    def test_risk_r2_goes_to_user(self):
        auto, rules = verdict(facts(level=2, klass="K5"))
        self.assertFalse(auto)
        self.assertFalse(rules["风险"].ok)
        self.assertFalse(rules["类别"].ok)  # K5 为 L3

    def test_class_not_l4_goes_to_user(self):
        autonomy = {**AUTONOMY, "classes": {**AUTONOMY["classes"], "K1": {"level": "L3"}}}
        rules = {rule.name: rule for rule in policy.decide(facts(), autonomy)}
        self.assertFalse(rules["类别"].ok)
        self.assertIn("L3", rules["类别"].reason)

    def test_declared_class_mismatch_is_unclassified(self):
        auto, rules = verdict(facts(klass="K2", declared_class="K1"))
        self.assertFalse(auto)
        self.assertIn("任务书声明 K1，机器判定 K2", rules["类别"].reason)
        auto, rules = verdict(facts(klass="K2", declared_problem="`Task: T009` 在 main 上找不到唯一的任务书"))
        self.assertFalse(auto)
        self.assertTrue(verdict(facts(klass="K2", declared_class="K2"))[0])
        self.assertTrue(verdict(facts(klass="K0", declared_class="K4"))[0])  # 任务书 PR 声明的是实现的类别

    def test_error_budget_exhausted_stops_the_class(self):
        auto, rules = verdict(facts(klass="K2", escapes=2, window=20))
        self.assertFalse(auto)
        self.assertIn("该类自动合并暂停", rules["预算"].reason)
        self.assertFalse(verdict(facts(level=1, klass="K3", escapes=1, window=10))[0])  # R1：逃逸 = 0
        self.assertTrue(verdict(facts(level=1, klass="K3", escapes=0, window=10))[0])

    def test_budget_label_and_unreadable_data_go_to_user(self):
        auto, rules = verdict(facts(labels={"budget-exceeded"}))
        self.assertFalse(auto)
        self.assertIn("budget-exceeded", rules["预算"].reason)
        auto, rules = verdict(facts(labels=None, escapes=None))
        self.assertFalse(auto)
        self.assertIn("读不到", rules["预算"].reason)

    def test_size_over_threshold_goes_to_user(self):
        auto, rules = verdict(facts(changed_lines=401))
        self.assertFalse(auto)
        self.assertIn("请拆分", rules["规模"].reason)
        self.assertTrue(verdict(facts(changed_lines=400))[0])

    def test_summary_explains_every_rule(self):
        item = facts(klass="K2", changed_lines=500, labels={"budget-exceeded"})
        text = policy.render(policy.decide(item, AUTONOMY), item, audit=False)
        self.assertIn("转用户评审", text)
        for name in ("风险", "类别", "预算", "规模"):
            self.assertIn(f"| {name} |", text)
        self.assertIn("Disable workflow", text)


class MachineClassTest(unittest.TestCase):
    def report(self, level, *paths):
        return risk.RiskReport(files=[risk.FileRisk(path, "M", level, "") for path in paths], level=level)

    def test_classes(self):
        cases = [
            (self.report(0, "docs/research/x.md"), False, "K1"),
            (self.report(0, "tests/test_new.py", "docs/research/x.md"), False, "K2"),
            (self.report(0, "docs/plans/task-005-x.md"), False, "K0"),
            (self.report(1, "scripts/a.py"), False, "K3"),
            (self.report(2, "scripts/a.py"), True, "K4"),
            (self.report(2, "scripts/a.py"), False, "K5"),
            (self.report(2, "native/A.swift"), False, "K6"),
            (self.report(2, "docs/templates/task.md", "docs/plans/backlog.md"), False, "K0"),
            (self.report(3, ".harness/x.py"), False, "K7"),
        ]
        for report, defects, expected in cases:
            with self.subTest(paths=[item.path for item in report.files]):
                self.assertEqual(policy.machine_class(report, defects), expected)

    def test_audit_sampling_is_stable_and_about_one_in_three(self):
        picked = [number for number in range(1, 301) if policy.audit_sampled(number, 3)]
        self.assertEqual(picked, [number for number in range(1, 301) if policy.audit_sampled(number, 3)])
        self.assertTrue(70 <= len(picked) <= 130, len(picked))
        self.assertFalse(any(policy.audit_sampled(number, 0) for number in range(1, 50)))


class EscapeWindowTest(unittest.TestCase):
    def fake_gh(self, merged, issues):
        def gh(*args):
            return json.dumps([{"number": n} for n in merged] if args[:2] == ("pr", "list") else issues)
        return gh

    def test_escapes_count_only_prs_in_window_and_closed_issues_still_count(self):
        gh = self.fake_gh([50, 51, 52], [
            {"number": 1, "title": "逃逸：日报合并", "body": "引入：#51"},
            {"number": 2, "title": "逃逸", "body": "引入：#12（窗口之外）"},
        ])
        self.assertEqual(policy.escapes_in_window("K2", 20, gh), (1, 3))


class GatherTest(unittest.TestCase):
    def setUp(self):
        self.repo = TempRepo()
        self.addCleanup(self.repo.close)
        self.repo.write("docs/research/a.md", "a\n")
        self.repo.write("docs/plans/task-005-x.md", "---\ntask: T005\nclass: K1\n---\n")
        self.base = self.repo.commit("base")
        self.repo.git("checkout", "-q", "-b", "work")

    def gh(self, labels=()):
        def gh(*args):
            if args[:2] == ("pr", "view"):
                return json.dumps({"labels": [{"name": name} for name in labels]})
            return "[]"
        return gh

    def test_declared_class_comes_from_the_task_trailer(self):
        self.repo.write("docs/research/a.md", "a2\n")
        self.repo.git("add", "-A")
        self.repo.git("commit", "-q", "-m", "docs\n\nTask: T005")
        item = policy.gather(self.base, "HEAD", 7, self.repo.path, AUTONOMY, self.gh())
        self.assertEqual((item.machine_class, item.declared_class, item.declared_problem), ("K1", "K1", None))
        # P5 起：带 Task: 即在实现任务书，没有运行记录就不自动合并；其余判定照常通过。
        failed = [rule.name for rule in policy.decide(item, AUTONOMY) if not rule.ok]
        self.assertEqual(failed, ["运行记录"])

    def test_unknown_task_and_missing_pr_fail_closed(self):
        self.repo.write("docs/research/a.md", "a2\n")
        self.repo.git("add", "-A")
        self.repo.git("commit", "-q", "-m", "docs\n\nTask: T009")
        item = policy.gather(self.base, "HEAD", None, self.repo.path, AUTONOMY, self.gh())
        self.assertIn("T009", item.declared_problem)
        self.assertIsNone(item.labels)
        self.assertFalse(all(rule.ok for rule in policy.decide(item, AUTONOMY)))


PY_BASE = '''import json


def total(items: list[int]) -> int:
    return sum(items)


class Report:
    def render(self, width=80):
        return "x" * width
'''

SWIFT_BASE = '''import Foundation

func usageKey(provider: String, sessionID: String?) -> String {
    "\\(provider):\\(sessionID ?? "")"
}
'''


class StrengthenedR1Test(unittest.TestCase):
    """2026-09-28 决定 2：声明 R1 的重构，任一加强判定不满足即降为 R2。"""

    def setUp(self):
        self.repo = TempRepo()
        self.addCleanup(self.repo.close)
        self.repo.write("scripts/mod.py", PY_BASE)
        self.repo.write("scripts/helper.py", "X = 1\n")
        self.repo.write("native/Policy.swift", SWIFT_BASE)
        self.base = self.repo.commit("base")
        self.repo.git("checkout", "-q", "-b", "work")

    def refactor(self, path, content):
        self.repo.git("checkout", "-q", "-B", "work", self.base)
        self.repo.write(path, content)
        self.repo.git("add", "-A")
        self.repo.git("commit", "-q", "-m", "refactor\n\nRisk: R1")
        return risk.classify(self.base, "HEAD", cwd=self.repo.path, rules=load_rules())

    def test_extracting_a_new_function_stays_r1(self):
        report = self.refactor("scripts/mod.py", PY_BASE + "\n\ndef _clamp(value):\n    return max(value, 0)\n")
        self.assertEqual((report.label, report.r1_violations), ("R1", []))
        swift = SWIFT_BASE + "\nfunc lookup(key: String) -> Int? { nil }\n"
        self.assertEqual(self.refactor("native/Policy.swift", swift).label, "R1")

    def test_changed_or_removed_signature_is_r2(self):
        cases = {
            "scripts/mod.py": PY_BASE.replace("def total(items: list[int]) -> int", "def total(items, start=0)"),
            "native/Policy.swift": SWIFT_BASE.replace("sessionID: String?", "session: String?"),
        }
        for path, content in cases.items():
            with self.subTest(path=path):
                report = self.refactor(path, content)
                self.assertEqual(report.label, "R2")
                self.assertTrue(any("签名" in reason for reason in report.r1_violations), report.r1_violations)
        report = self.refactor("scripts/mod.py", PY_BASE.replace("    def render(self, width=80):\n        return \"x\" * width\n", "    pass\n"))
        self.assertTrue(any("删除了函数 `Report.render`" in reason for reason in report.r1_violations))

    def test_new_dependency_is_r2_but_stdlib_and_local_modules_are_not(self):
        report = self.refactor("scripts/mod.py", "import requests\n" + PY_BASE)
        self.assertEqual(report.label, "R2")
        self.assertIn("`requests`", " ".join(report.r1_violations))
        report = self.refactor("scripts/mod.py", "import re\nfrom helper import X\n" + PY_BASE)
        self.assertEqual(report.label, "R1")
        report = self.refactor("native/Policy.swift", "import SwiftUI\n" + SWIFT_BASE)
        self.assertEqual(report.label, "R2")

    def test_schema_change_is_r2(self):
        report = self.refactor("scripts/mod.py", PY_BASE + '\nSQL = "ALTER TABLE rows ADD COLUMN x"\n')
        self.assertEqual(report.label, "R2")
        self.assertIn("迁移", " ".join(report.r1_violations))

    def test_over_size_threshold_is_r2(self):
        big = PY_BASE + "".join(f"\nV{index} = {index}" for index in range(401))
        report = self.refactor("scripts/mod.py", big)
        self.assertEqual(report.label, "R2")
        self.assertIn("超过规模阈值 400", " ".join(report.r1_violations))


if __name__ == "__main__":
    unittest.main()
