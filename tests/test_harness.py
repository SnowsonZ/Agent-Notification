"""harness 自身的测试：仓库卫生、风险等级、修复证据、verify 入口。

每个用例在临时 git 仓库里构造真实场景（v0.8.0 的 R1、X1、X4 等），不碰本仓库。
"""

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))

import evidence
import hygiene
import risk
import verify
from common import (
    clean_git_env,
    load_rules,
    parse_added_lines,
    path_matches,
)

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}


class TempRepo:
    """临时 git 仓库；git 调用清掉钩子注入的变量，避免落到本仓库上。"""

    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name)
        self.git("init", "-q", "-b", "main")

    def close(self):
        self._tmp.cleanup()

    def git(self, *args):
        result = subprocess.run(
            ["git", *args],
            cwd=self.path,
            capture_output=True,
            text=True,
            env=clean_git_env(GIT_ENV),
            check=False,
        )
        if result.returncode != 0:
            raise AssertionError(f"git {args} 失败：{result.stderr}")
        return result.stdout.strip()

    def write(self, rel, content):
        target = self.path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(textwrap.dedent(content))

    def commit(self, message, *paths):
        self.git("add", *(paths or ["-A"]))
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")


class GlobTest(unittest.TestCase):
    def test_double_star_matches_zero_or_more_dirs(self):
        self.assertTrue(path_matches("a.o", ["**/*.o"]))
        self.assertTrue(path_matches("native/x/a.o", ["**/*.o"]))
        self.assertTrue(path_matches("scratch/a/b.json", ["scratch/**"]))

    def test_single_star_does_not_cross_directories(self):
        self.assertIsNone(path_matches("docs/specs/a.md", ["docs/*.md"]))
        self.assertTrue(path_matches("docs/a.md", ["docs/*.md"]))

    def test_parse_added_lines_tracks_line_numbers(self):
        diff = "+++ b/x.py\n@@ -1,0 +3,2 @@\n+a\n+b\n+++ /dev/null\n@@ -1 +0,0 @@\n"
        self.assertEqual(parse_added_lines(diff), {"x.py": [(3, "a"), (4, "b")]})


class HygieneTest(unittest.TestCase):
    def setUp(self):
        self.rules = load_rules()

    def test_forbidden_paths(self):
        paths = ["widget/snapshot.json", "native/Widget.o", "scratch/r2.json", "scripts/inbox.py"]
        flagged = {v.path for v in hygiene.check_paths(paths, self.rules)}
        self.assertEqual(flagged, {"widget/snapshot.json", "native/Widget.o", "scratch/r2.json"})

    def test_secret_and_home_path_in_added_lines(self):
        added = {
            # 测试数据动态拼接：文件里写出真实形态的路径或凭据，本身就会被卫生检查拦下。
            "a.py": [(1, "token = 'ghp_" + "a" * 36 + "'"), (2, "p = '/Users/" + "alice/work'"), (3, "q = '/Users/x/p'")],
        }
        kinds = [(v.path, v.kind) for v in hygiene.check_added_lines(added, self.rules)]
        self.assertEqual(kinds, [("a.py:1", "疑似凭据"), ("a.py:2", "本机路径")])

    def test_size_limit(self):
        limit = self.rules["hygiene"]["max_file_kb"] * 1024
        flagged = hygiene.check_sizes({"big.bin": limit + 1, "ok.bin": limit}, self.rules)
        self.assertEqual([v.path for v in flagged], ["big.bin"])

    def test_staged_snapshot_is_rejected(self):
        """V080-R1 回放：真实快照被 git add -A 带进暂存区。"""
        repo = TempRepo()
        self.addCleanup(repo.close)
        repo.write("README.md", "x\n")
        repo.commit("init")
        repo.write("widget/snapshot.json", '{"pending": []}\n')
        repo.git("add", "-A")
        violations = hygiene.scan_staged(cwd=repo.path, rules=self.rules)
        self.assertEqual([v.kind for v in violations], ["禁止路径"])


class RiskTest(unittest.TestCase):
    def setUp(self):
        self.repo = TempRepo()
        self.addCleanup(self.repo.close)
        self.repo.write("scripts/mod.py", "X = 1\n")
        self.repo.write("tests/test_mod.py", "A = 1\nB = 2\n")
        self.repo.write("docs/plans/p.md", "plan\n")
        self.base = self.repo.commit("base")
        self.repo.git("checkout", "-q", "-b", "work")

    def classify(self):
        return risk.classify(self.base, "HEAD", cwd=self.repo.path, rules=load_rules())

    def test_docs_only_is_r0(self):
        self.repo.write("docs/plans/p.md", "plan v2\n")
        self.repo.commit("docs")
        self.assertEqual(self.classify().label, "R0")

    def test_appending_to_existing_test_is_r0(self):
        self.repo.write("tests/test_mod.py", "A = 1\nB = 2\nC = 3\n")
        self.repo.write("tests/test_new.py", "N = 1\n")
        self.repo.commit("tests")
        report = self.classify()
        self.assertEqual((report.label, report.flags), ("R0", []))

    def test_modifying_existing_test_is_flagged_r2(self):
        self.repo.write("tests/test_mod.py", "A = 1\nB = 3\n")
        self.repo.commit("weaken")
        report = self.classify()
        self.assertEqual(report.label, "R2")
        self.assertTrue(any("改动已有测试" in flag for flag in report.flags))

    def test_deleting_test_is_flagged_r2(self):
        self.repo.git("rm", "-q", "tests/test_mod.py")
        self.repo.commit("drop test")
        report = self.classify()
        self.assertEqual(report.label, "R2")
        self.assertTrue(any("删除已有测试" in flag for flag in report.flags))

    def test_ci_change_is_r3(self):
        self.repo.write(".github/workflows/build.yml", "on: push\n")
        self.repo.commit("ci")
        self.assertEqual(self.classify().label, "R3")

    def test_code_change_is_r2_unless_every_commit_claims_r1(self):
        self.repo.write("scripts/mod.py", "X = 2\n")
        self.repo.commit("refactor\n\nRisk: R1")
        self.assertEqual(self.classify().label, "R1")
        self.repo.write("scripts/mod.py", "X = 3\n")
        self.repo.commit("unclaimed")
        self.assertEqual(self.classify().label, "R2")

    def test_r1_claim_before_co_author_paragraph_is_recognized(self):
        """H0925-5：`Risk: R1` 不在最后一段也要识别。"""
        self.repo.write("scripts/mod.py", "X = 2\n")
        self.repo.commit("refactor\n\nRisk: R1\n\nCo-Authored-By: someone <someone@example.com>")
        self.assertEqual(self.classify().label, "R1")

    def test_r1_claim_rejected_when_tests_modified(self):
        self.repo.write("scripts/mod.py", "X = 2\n")
        self.repo.write("tests/test_mod.py", "A = 9\nB = 2\n")
        self.repo.commit("refactor\n\nRisk: R1")
        report = self.classify()
        self.assertEqual(report.label, "R2")
        self.assertIn("机器核对不满足", report.notes[0])

    def test_shrinking_gap_list_is_r0_but_growing_is_r3(self):
        self.repo.write("harness/acceptance-gaps.txt", "DR14  # a\nIN99  # b\n")
        self.repo.commit("gaps")
        self.base = self.repo.git("rev-parse", "HEAD")
        self.repo.write("harness/acceptance-gaps.txt", "DR14  # a\n")
        self.repo.commit("close IN99")
        self.assertEqual(self.classify().label, "R0")
        self.repo.write("harness/acceptance-gaps.txt", "DR14  # a\nXX1  # new\n")
        self.repo.commit("grow")
        self.assertEqual(self.classify().label, "R3")

    def test_new_golden_file_is_a_new_test(self):
        self.repo.write("tests/golden/new.json", "{}\n")
        self.repo.commit("golden")
        self.assertEqual((self.classify().label, self.classify().flags), ("R0", []))

    def test_r3_paths_are_reported_as_one_flag(self):
        for index in range(10):
            self.repo.write(f"harness/tool{index}.py", "X = 1\n")
        self.repo.commit("harness")
        flags = self.classify().flags
        self.assertEqual(len(flags), 1)
        self.assertIn("等 10 个", flags[0])

    def test_golden_change_is_flagged(self):
        self.repo.write("tests/golden/usage.json", "{}\n")
        self.repo.commit("golden base")
        self.base = self.repo.git("rev-parse", "HEAD")
        self.repo.write("tests/golden/usage.json", '{"changed": true}\n')
        self.repo.commit("golden")
        report = self.classify()
        self.assertEqual(report.label, "R2")
        self.assertTrue(any("黄金快照" in flag for flag in report.flags))


BUGGY = """\
def percentile_levels(amounts):
    return amounts[len(amounts) // 2]
"""
FIXED = """\
def percentile_levels(amounts):
    amounts = sorted(amounts)
    return amounts[len(amounts) // 2]
"""
TEST_MODULE = """\
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from mod import percentile_levels


class LevelTest(unittest.TestCase):
    def test_median_of_unsorted(self):
        # T-R1：取分位数前必须排序。
        self.assertEqual(percentile_levels([9, 1, 5]), 5)

    def test_weak(self):
        # T-R3：这个测试不检查排序。
        self.assertEqual(percentile_levels([1]), 1)
"""


class EvidenceTest(unittest.TestCase):
    def setUp(self):
        self.repo = TempRepo()
        self.addCleanup(self.repo.close)
        self.repo.write("scripts/mod.py", BUGGY)
        self.repo.write("tests/test_mod.py", "")
        self.base = self.repo.commit("base")

    def analyse(self, **kwargs):
        return evidence.analyse(self.base, "HEAD", cwd=self.repo.path, **kwargs)

    def test_real_fix_fails_before_and_passes_after(self):
        self.repo.write("scripts/mod.py", FIXED)
        self.repo.write("tests/test_mod.py", TEST_MODULE)
        self.repo.commit("fix sort\n\nDefect: T-R1")
        result = self.analyse()["T-R1"]
        self.assertEqual((result.before, result.after, result.problems), ("fail", "pass", []))
        self.assertEqual(result.python_tests, ["test_mod.LevelTest.test_median_of_unsorted"])
        self.assertIn("scripts/mod.py", result.code_files)

    def test_claimed_fix_without_code_change(self):
        """V080-X1 回放：提交说明声称已修，实际只改了文档。"""
        self.repo.write("docs/notes.md", "R2 已修复\n")
        self.repo.commit("fix R2\n\nDefect: T-R2")
        result = self.analyse()["T-R2"]
        self.assertIn("没有代码改动", result.problems[0])

    def test_test_that_does_not_detect_the_bug(self):
        """V080-X4 回放：测试在修复前也通过，说明它没有检查这个缺陷。"""
        self.repo.write("scripts/mod.py", FIXED)
        self.repo.write("tests/test_mod.py", TEST_MODULE)
        self.repo.commit("fix\n\nDefect: T-R3")
        result = self.analyse()["T-R3"]
        self.assertEqual(result.before, "pass")
        self.assertIn("测试没有检查到这个缺陷", result.problems[0])

    def test_defect_before_co_author_paragraph_is_recognized(self):
        """H0925-5：`Defect:` 与 `Co-Authored-By:` 之间隔空行时，git 不把它当 trailer，
        证据检查曾因此静默跳过全部修复。"""
        self.repo.write("scripts/mod.py", FIXED)
        self.repo.write("tests/test_mod.py", TEST_MODULE)
        self.repo.commit("fix sort\n\nDefect: T-R1\n\nCo-Authored-By: someone <someone@example.com>")
        result = self.analyse()
        self.assertIn("T-R1", result)
        self.assertEqual(result["T-R1"].after, "pass")

    def test_doc_defect_that_changes_code_is_rejected(self):
        """PR7-R1：`Defect: X doc` 免于测试核对，改了代码就不能再算 doc 类修复。"""
        self.repo.write("scripts/mod.py", FIXED)
        self.repo.write("docs/specs/a.md", "改规格\n")
        self.repo.commit("spec + code\n\nDefect: T-R6 doc")
        result = self.analyse(run_tests=False)["T-R6"]
        self.assertIn("标为 doc 类修复却改了代码", result.problems[0])

    def test_error_before_fix_is_not_proof(self):
        """PR7-R4：修复新增模块时，退回后测试只会 import 出错，这证明不了测试检查了缺陷。"""
        self.repo.write("scripts/helper.py", "def fixed():\n    return 1\n")
        self.repo.write(
            "tests/test_helper.py",
            "import sys\nimport unittest\nfrom pathlib import Path\n\n"
            "sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))\n\n"
            "from helper import fixed\n\n\nclass HelperTest(unittest.TestCase):\n"
            "    def test_fixed(self):\n        # T-R7\n        self.assertEqual(fixed(), 1)\n",
        )
        self.repo.commit("add helper\n\nDefect: T-R7")
        result = self.analyse()["T-R7"]
        self.assertEqual(result.before, "error")
        self.assertIn("出错而非断言失败", result.problems[0])

    def test_revert_only_the_fix_not_later_commits(self):
        """PR7-R5：整文件退回会连带撤掉后续无关提交，让一个没检查缺陷的测试「修复前失败」。"""
        self.repo.write("scripts/mod.py", BUGGY + "VERSION = 1\n")
        self.base = self.repo.commit("versioned base")
        self.repo.write("scripts/mod.py", FIXED + "VERSION = 1\n")
        self.repo.commit("fix\n\nDefect: T-R8")
        self.repo.write("scripts/mod.py", FIXED + "VERSION = 2\n")
        self.repo.write(
            "tests/test_mod.py",
            "import sys\nimport unittest\nfrom pathlib import Path\n\n"
            "sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))\n\n"
            "import mod\n\n\nclass VersionTest(unittest.TestCase):\n"
            "    def test_version(self):\n        # T-R8：只检查了版本号，没检查排序缺陷\n"
            "        self.assertEqual(mod.VERSION, 2)\n",
        )
        self.repo.commit("bump version")
        result = self.analyse()["T-R8"]
        self.assertEqual(result.before, "pass")
        self.assertIn("测试没有检查到这个缺陷", result.problems[0])

    def test_fix_without_referencing_test(self):
        self.repo.write("scripts/mod.py", FIXED)
        self.repo.commit("fix\n\nDefect: T-R4")
        self.assertIn("没有引用 T-R4 的测试", self.analyse()["T-R4"].problems[0])

    def test_doc_only_defect_needs_no_test(self):
        self.repo.write("docs/specs/a.md", "按任务合并\n")
        self.repo.commit("spec\n\nDefect: T-R5 doc")
        result = self.analyse()["T-R5"]
        self.assertTrue(result.ok)
        self.assertTrue(result.doc_only)

    def test_requested_id_without_commit(self):
        result = self.analyse(ids=["T-R9"], run_tests=False)["T-R9"]
        self.assertIn("没有带 `Defect: T-R9` 的提交", result.problems[0])

    def test_malformed_trailer(self):
        self.repo.write("scripts/mod.py", FIXED)
        self.repo.commit("fix\n\nDefect: r17")
        self.assertIn("格式不对", self.analyse(run_tests=False)["r17"].problems[0])

    def test_markdown_lists_verdicts(self):
        self.repo.write("scripts/mod.py", FIXED)
        self.repo.write("tests/test_mod.py", TEST_MODULE)
        self.repo.commit("fix\n\nDefect: T-R1")
        text = evidence.render_markdown(self.analyse(), self.base, "HEAD", cwd=self.repo.path)
        self.assertIn("| T-R1 |", text)
        self.assertIn("✗ 失败 | ✓ 通过 | ✅", text)


class BaseTestsTest(unittest.TestCase):
    """PR7-R2：往已有测试文件末尾追加 monkeypatch 即可禁用已有测试；已有测试必须按 base 版本运行。"""

    def setUp(self):
        self.repo = TempRepo()
        self.addCleanup(self.repo.close)
        self.repo.write("scripts/mod.py", FIXED)
        self.repo.write("tests/test_mod.py", TEST_MODULE)
        self.base = self.repo.commit("base with guarding test")

    def test_appended_monkeypatch_cannot_disable_existing_test(self):
        import base_tests

        self.repo.write("scripts/mod.py", BUGGY)
        self.repo.write(
            "tests/test_mod.py",
            TEST_MODULE + "\nLevelTest.test_median_of_unsorted = lambda self: None\n",
        )
        self.repo.commit("reintroduce bug and silence the test\n\nRisk: R1")
        ok, enforced, _ = base_tests.run(self.base, "HEAD", cwd=self.repo.path)
        self.assertEqual((ok, enforced), (False, True))

    def test_new_test_file_cannot_patch_existing_tests(self):
        import base_tests

        self.repo.write("scripts/mod.py", BUGGY)
        self.repo.write(
            "tests/test_zzz.py",
            "import test_mod\n\ntest_mod.LevelTest.test_median_of_unsorted = lambda self: None\n",
        )
        self.repo.commit("sneaky new file")
        ok, enforced, _ = base_tests.run(self.base, "HEAD", cwd=self.repo.path)
        self.assertEqual((ok, enforced), (False, True))

    def test_intended_test_change_is_reported_not_enforced(self):
        import base_tests

        self.repo.write("scripts/mod.py", BUGGY)
        self.repo.write("tests/test_mod.py", TEST_MODULE.replace("[9, 1, 5]), 5)", "[9, 1, 5]), 1)"))
        self.repo.commit("change behaviour and update the test")
        ok, enforced, _ = base_tests.run(self.base, "HEAD", cwd=self.repo.path)
        self.assertEqual((ok, enforced), (False, False))

    def test_clean_change_passes(self):
        import base_tests

        self.repo.write("scripts/mod.py", FIXED + "\nEXTRA = 1\n")
        self.repo.commit("harmless")
        ok, enforced, _ = base_tests.run(self.base, "HEAD", cwd=self.repo.path)
        self.assertEqual((ok, enforced), (True, True))


class VerifyTest(unittest.TestCase):
    def test_pinned_ruff_version_is_declared(self):
        self.assertRegex(verify.pinned_version("ruff") or "", r"^\d+\.\d+\.\d+$")

    def test_ci_ruff_pin_matches_dev_requirements(self):
        """工具版本三处同口径：requirements-dev.txt、CI、ruff.toml 注释。"""
        pinned = verify.pinned_version("ruff")
        self.assertIn(f"当前 {pinned}", (ROOT / "ruff.toml").read_text())
        self.assertNotIn("ruff==", (ROOT / ".github/workflows/build.yml").read_text())

    @unittest.skipIf(sys.platform == "darwin", "macOS 上 Swift 检查会真实运行")
    def test_strict_turns_skipped_swift_into_failure(self):
        with mock.patch.dict(os.environ), contextlib.redirect_stdout(io.StringIO()):
            os.environ.pop("GITHUB_STEP_SUMMARY", None)  # 不往 CI 摘要里写
            code = verify.main(["--only", "swift-policy", "--strict"])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
