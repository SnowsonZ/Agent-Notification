"""独立评审资料的一致性：评审清单覆盖基线失败分类；证据包可在临时仓库生成。"""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import review_pack
from test_harness import FIXED, TEST_MODULE, TempRepo

BUGGY = "def percentile_levels(amounts):\n    return amounts[len(amounts) // 2]\n"


class ReviewChecklistTest(unittest.TestCase):
    def test_checklist_covers_every_failure_type_in_baseline(self):
        baseline = (ROOT / "docs/review/2026-09-25-harness-baseline.md").read_text(encoding="utf-8")
        checklist = (ROOT / "docs/templates/review-checklist.md").read_text(encoding="utf-8")
        types = set(re.findall(r"^\| ([FX]\d) ", baseline, flags=re.MULTILINE))
        self.assertGreaterEqual(len(types), 13)  # F1–F7、X1–X6
        rows = set(re.findall(r"^\| ([FX]\d) ", checklist, flags=re.MULTILINE))
        self.assertEqual(types - rows, set(), "评审清单缺少这些失败类型")

    def test_review_prompt_points_at_existing_tools(self):
        prompt = (ROOT / "docs/templates/review-prompt.md").read_text(encoding="utf-8")
        for path in re.findall(r"`((?:harness|docs)/[\w./-]+\.(?:py|md))", prompt):
            self.assertTrue((ROOT / path).exists(), path)


class ReviewPackTest(unittest.TestCase):
    def test_pack_contains_risk_evidence_and_verification(self):
        repo = TempRepo()
        self.addCleanup(repo.close)
        repo.write("scripts/mod.py", BUGGY)
        repo.write("tests/test_mod.py", "")
        base = repo.commit("base")
        repo.write("scripts/mod.py", FIXED)
        repo.write("tests/test_mod.py", TEST_MODULE)
        repo.commit("fix\n\nDefect: T-R1")
        text = review_pack.build(base, "HEAD", run_tests=True, cwd=repo.path)
        self.assertIn("### 风险等级：**R2**", text)
        self.assertIn("| T-R1 |", text)
        self.assertIn("✗ 失败 | ✓ 通过 | ✅", text)
        self.assertIn("### 验证", text)
        self.assertIn("本次改动的新增行没有涉及验收编号", text)


if __name__ == "__main__":
    unittest.main()
