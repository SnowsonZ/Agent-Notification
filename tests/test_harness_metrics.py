"""熵治理棘轮与交付度量的测试。"""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import metrics
import quality
from test_harness import FIXED, TEST_MODULE, TempRepo


class QualityRatchetTest(unittest.TestCase):
    def test_only_ratcheted_metrics_fail(self):
        baseline = {"complex_functions": 20, "files_over_800": 1, "largest_file_lines": 1693}
        self.assertEqual(quality.compare(dict(baseline, largest_file_lines=1700), baseline), [])
        self.assertEqual(len(quality.compare(dict(baseline, complex_functions=21), baseline)), 1)
        self.assertEqual(len(quality.compare(dict(baseline, files_over_800=2), baseline)), 1)
        self.assertEqual(quality.compare(dict(baseline, complex_functions=19), baseline), [])

    def test_repository_is_within_baseline(self):
        import json

        current = quality.measure()
        baseline = json.loads(quality.BASELINE.read_text())
        self.assertEqual(quality.compare(current, baseline), [])
        self.assertEqual(set(current), set(baseline))


class DeliveryMetricsTest(unittest.TestCase):
    def test_counts_fixes_tests_and_missing_replay(self):
        repo = TempRepo()
        self.addCleanup(repo.close)
        repo.write("scripts/mod.py", "def percentile_levels(amounts):\n    return amounts[len(amounts) // 2]\n")
        repo.write("tests/test_mod.py", "")
        base = repo.commit("base")
        repo.write("scripts/mod.py", FIXED)
        repo.write("tests/test_mod.py", TEST_MODULE)
        repo.commit("fix\n\nDefect: T-R1")
        data = metrics.collect(base, "HEAD", cwd=repo.path)
        self.assertEqual((data["Defect 修复"], data["声称已修但代码未变"]), (1, 0))
        self.assertEqual(data["新增测试函数"], 2)
        self.assertEqual((data["修复带回放用例"], data["缺回放的修复"]), ("0/1", ["T-R1"]))
        self.assertIn("v0.8.0 基线", metrics.render(data))

    def test_github_runs_unavailable_without_credentials(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(metrics.github_runs("feature"))


if __name__ == "__main__":
    unittest.main()
