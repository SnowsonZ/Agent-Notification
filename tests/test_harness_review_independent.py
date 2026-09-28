"""独立评审（harness/review.py）：结论解析、分离规则、只读的评审方参数、评论格式、校准样本与打分。

设计 8.3 的机器验收中「TPR、TNR 入库」「3 个 R2 PR 有非设计方的评审结论」需真实运行，另行记录。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT / "tests"))

import review
from test_harness import TempRepo


class ParseTest(unittest.TestCase):
    def test_last_json_line_wins(self):
        text = '评审意见……\n{"verdict": "通过", "findings": [], "summary": "旧"}\n更多\n' \
               '{"verdict": "不通过", "findings": [{"severity": "阻断", "location": "a.py:3", "problem": "p", "fix": "f"}], "summary": "s"}'
        verdict = review.parse_output(text)
        self.assertEqual((verdict.verdict, verdict.summary, len(verdict.findings)), ("不通过", "s", 1))
        self.assertTrue(verdict.flagged)

    def test_code_fence_and_unknown_verdict(self):
        self.assertEqual(review.parse_output('```json {"verdict": "通过", "findings": []}').verdict, "通过")
        missing = review.parse_output('{"verdict": "好"}\n没有结论')
        self.assertEqual((missing.verdict, missing.parsed), ("需用户验收", False))

    def test_flagged_by_severity_even_if_verdict_passes(self):
        self.assertTrue(review.Verdict("通过", [{"severity": "严重"}]).flagged)
        self.assertFalse(review.Verdict("需用户验收", [{"severity": "一般"}]).flagged)


class SeparationTest(unittest.TestCase):
    def setUp(self):
        self.repo = TempRepo()
        self.addCleanup(self.repo.close)
        self.repo.write("docs/plans/task-008-x.md", "---\ntask: T008\nclass: K4\ndesigner: codex\n---\n")
        self.base = self.repo.commit("base")

    def commit(self, message):
        self.repo.write("scripts/a.py", message)
        self.repo.git("add", "-A")
        self.repo.git("commit", "-q", "-m", message)

    def test_taskbook_designer_decides(self):
        self.commit("fix\n\nTask: T008\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>")
        self.assertEqual(review.designer_of(self.base, "HEAD", "task/008-x", self.repo.path), "codex")
        self.assertEqual(review.OTHER["codex"], "claude-code")

    def test_co_author_trailer_without_taskbook(self):
        self.commit("harness\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>")
        self.assertEqual(review.designer_of(self.base, "HEAD", "task/harness-x", self.repo.path), "claude-code")

    def test_unknown_designer(self):
        self.commit("plain")
        self.assertIsNone(review.designer_of(self.base, "HEAD", "feature", self.repo.path))


class ReviewerTest(unittest.TestCase):
    def test_reviewers_are_read_only(self):
        codex = review.CodexReviewer("m").argv("p", Path("/w"), Path("/o"))
        self.assertEqual(codex[:4], ["codex", "exec", "-s", "read-only"])
        tuned = review.CodexReviewer("gpt-6-sol", "max").argv("p", Path("/w"), Path("/o"))
        self.assertIn('model_reasoning_effort="max"', tuned)
        self.assertEqual(review.make_reviewer("codex").argv("p", Path("/w"), Path("/o"))[5], "gpt-6-sol")
        claude = review.ClaudeReviewer().argv("p", Path("/w"), Path("/o"))
        self.assertEqual(claude[claude.index("--allowedTools") + 1], "Read,Grep,Glob")

    def test_reviewer_runs_without_github_credentials(self):
        class Fake(review.Reviewer):
            name = "fake"

            def argv(self, prompt, workspace, output):
                code = ("import os; print('意见');"
                        "print('{\"verdict\": \"' + ('不通过' if os.environ.get('GH_TOKEN') else '通过') + '\", \"findings\": []}')")
                return [sys.executable, "-c", code]

            def read(self, stdout, output):
                return stdout, "fake-model"

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"GH_TOKEN": "secret"}):
            verdict, model, _ = review.run_reviewer(Fake(), Path(tmp), 60)
        self.assertEqual((verdict.verdict, model), ("通过", "fake-model"))

    def test_pi_reviewer_is_read_only_and_parses_events(self):
        argv = review.PiReviewer("zai-coding-cn/glm-5.3").argv("p", Path("/w"), Path("/o"))
        self.assertEqual(argv[:4], ["pi", "-na", "--tools", "read,grep,find,ls"])
        self.assertEqual(argv[argv.index("--model") + 1], "zai-coding-cn/glm-5.3")
        events = [
            {"type": "message_end", "message": {"role": "assistant", "model": "glm-5.3",
                                                "content": [{"type": "text", "text": "先看看"}]}},
            {"type": "message_end", "message": {"role": "toolResult", "content": [{"type": "text", "text": "x"}]}},
            {"type": "message_end", "message": {"role": "assistant", "model": "glm-5.3", "content": [
                {"type": "thinking", "thinking": "…"}, {"type": "text", "text": '意见\n{"verdict": "通过", "findings": []}'}]}},
        ]
        text, model = review.PiReviewer().read("\n".join(json.dumps(e, ensure_ascii=False) for e in events), Path("/o"))
        self.assertEqual((review.parse_output(text).verdict, model), ("通过", "glm-5.3"))
        self.assertEqual(review.make_reviewer("pi").argv("p", Path("/w"), Path("/o"))[-2], "zai-coding-cn/glm-5.3")

    def test_claude_output_parsing(self):
        stdout = json.dumps({"result": '好\n{"verdict": "通过", "findings": []}', "modelUsage": {"claude-x": {}}})
        text, model = review.ClaudeReviewer().read(stdout, Path("/none"))
        self.assertEqual(model, "claude-x")
        self.assertEqual(review.parse_output(text).verdict, "通过")


class CommentAndScoreTest(unittest.TestCase):
    def test_comment_has_fixed_fields_and_hidden_data(self):
        verdict = review.Verdict("不通过", [{"severity": "阻断", "location": "a.py:1", "problem": "漏传参数", "fix": "补上"}], "有阻断")
        text = review.render_comment(verdict, "codex", "gpt-x", "claude-code", "abcdef1234567890", 90)
        self.assertNotIn("同为", text)
        self.assertIn("评审方与本 PR 的执行方同为 pi",
                      review.render_comment(verdict, "pi", "glm-5.3", "claude-code", "abc", 1, same_host=True))
        self.assertIn("### 独立评审（试行）：不通过", text)
        self.assertIn("评审方：codex（gpt-x）；设计方：claude-code", text)
        self.assertIn("| 阻断 | a.py:1 | 漏传参数 | 补上 |", text)
        data = json.loads(text.split("<!-- independent-review ")[1].split(" -->")[0])
        self.assertEqual((data["flagged"], data["reviewer"]), (True, "codex"))

    def test_score_excludes_errors_and_failed_reviews(self):
        """2026-09-28 实测：Codex 额度用尽后的失败曾被当成「放过」，会虚高 TNR。"""
        results = [
            {"kind": "bad", "flagged": True}, {"kind": "bad", "flagged": False},
            {"kind": "good", "flagged": False}, {"kind": "good", "flagged": False, "parsed": False},
            {"kind": "bad", "error": "注入点过期"},
        ]
        self.assertEqual(review.score(results),
                         {"bad": 2, "good": 1, "errors": 1, "tpr": 0.5, "tnr": 1.0, "unparsed": 1, "total": 5})


class FailureTest(unittest.TestCase):
    """评审方报错（如额度用尽）不是结论：记为评审失败，不评论为结论，不计入校准。"""

    class Scripted(review.Reviewer):
        name = "scripted"

        def __init__(self, outputs):
            self.outputs = list(outputs)

        def argv(self, prompt, workspace, output):
            code, text = self.outputs.pop(0)
            return [sys.executable, "-c", f"import sys; print({text!r}); sys.exit({code})"]

        def read(self, stdout, output):
            return stdout, "m"

    def test_error_exit_is_a_failure_not_a_verdict(self):
        quota = "ERROR: You've hit your usage limit. Try again at 12:51 AM."
        with tempfile.TemporaryDirectory() as tmp:
            verdict, _, _ = review.run_reviewer(self.Scripted([(1, quota)]), Path(tmp), 60)
        self.assertEqual(verdict.verdict, "评审失败")
        self.assertIn("usage limit", verdict.failure)
        self.assertFalse(verdict.flagged)

    def test_calibration_stops_after_three_failures_and_resumes(self):
        ok = (0, '{"verdict": "不通过", "findings": []}')
        fail = (1, "ERROR: usage limit")
        samples = [{"kind": "bad", "id": f"S{n}", "title": "t", "file": "a", "find": "x", "replace": "y"} for n in range(6)]
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "result.json"
            with mock.patch.object(review, "calibration_samples", return_value=samples), \
                    mock.patch.object(review, "review_workspace", return_value=Path(tmp)), \
                    mock.patch.object(review, "git", return_value="abc"), \
                    mock.patch.object(review, "prepare_sample", return_value=("abc", "pr")), \
                    mock.patch.object(review, "write_materials"), \
                    mock.patch.object(review, "EVALS", Path(tmp)):
                out.write_text(json.dumps({"main": "abc", "date": "d", "samples": []}))
                first = review.calibrate("codex", root=Path(tmp), resume=out,
                                         reviewer=self.Scripted([ok, fail, fail, fail, ok, ok]))
                self.assertEqual([item["id"] for item in first["samples"]], ["S0", "S1", "S2", "S3"])
                self.assertEqual((first["bad"], first["tpr"], first["unparsed"]), (1, 1.0, 3))
                saved = json.loads(out.read_text())
                self.assertEqual(len(saved["samples"]), 4)  # 每个样本都已落盘
                second = review.calibrate("codex", root=Path(tmp), resume=out,
                                          reviewer=self.Scripted([ok, ok, ok, ok, ok]))
        self.assertEqual(sorted(item["id"] for item in second["samples"]), [f"S{n}" for n in range(6)])
        self.assertEqual((second["bad"], second["unparsed"]), (6, 0))


class CalibrationTest(unittest.TestCase):
    def test_samples_cover_replay_cases_and_manifest(self):
        from replay_cases import CASES

        samples = review.calibration_samples()
        manifest = json.loads((ROOT / "evals/review/samples.json").read_text())
        self.assertEqual(sum(s["kind"] == "bad" for s in samples), len(CASES))
        self.assertEqual([s["pr"] for s in samples if s["kind"] == "good"], manifest["good_prs"])
        self.assertGreaterEqual(len(samples), 40)

    def test_bad_sample_is_injected_without_touching_branches(self):
        repo = TempRepo()
        self.addCleanup(repo.close)
        repo.write("scripts/a.py", "def f(x):\n    return x > 0\n")
        base = repo.commit("base")
        sample = {"kind": "bad", "id": "T-1:0", "title": "重构", "file": "scripts/a.py",
                  "find": "return x > 0", "replace": "return x >= 0"}
        got_base, text = review.prepare_sample(repo.path, sample, base, repo.path)
        self.assertEqual(got_base, base)
        self.assertIn("行为不变", text)
        diff = subprocess.run(["git", "diff", f"{base}...HEAD"], cwd=repo.path, capture_output=True, text=True, check=True).stdout
        self.assertIn("+    return x >= 0", diff)
        self.assertEqual(repo.git("rev-parse", "main"), base)  # 分支未动
        stale = dict(sample, find="不存在的代码")
        with self.assertRaisesRegex(ValueError, "过期"):
            review.prepare_sample(repo.path, stale, base, repo.path)


class WorkflowTest(unittest.TestCase):
    def test_r2_and_above_are_marked_for_independent_review(self):
        workflow = (ROOT / ".github/workflows/auto-merge.yml").read_text()
        job = workflow.split("\n  request-review:\n", 1)[1].split("\n  merge:\n", 1)[0]
        self.assertIn('if [ "$RISK" = "R2" ] || [ "$RISK" = "R3" ]; then', job)
        self.assertIn("--add-label needs-independent-review", job)
        self.assertNotIn("--force", job.split("needs-independent-review")[0].split("R3")[-1])


if __name__ == "__main__":
    unittest.main()
