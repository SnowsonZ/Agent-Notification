"""回放强制（harness/evidence.py）与守卫小修（harness/command_guard.py、各宿主钩子）。

设计 16.1 P3 的机器验收：无回放的新 Defect 使 harness job 失败；守卫测试覆盖放行与拒绝。
"""

import contextlib
import io
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT / "tests"))

import command_guard
import evidence
from test_harness_guard import (
    OpenCodePluginTest,
    PiExtensionTest,
    _node_runs_typescript,
)

REGISTRY = SimpleNamespace(
    CASES=[SimpleNamespace(defect="T-INJ")],
    GUARDED={"T-GRD": ("test_x.Guard",)},
    DEFERRED={"T-DEF": "需要真实数据", "T-EMPTY": "  "},
)


def fixed(defect, **fields):
    return evidence.DefectEvidence(defect, commits=[("a" * 40, "fix")], **fields)


class ReplayCoverageTest(unittest.TestCase):
    def test_each_code_defect_needs_a_case_guard_or_reasoned_deferral(self):
        evidences = {name: fixed(name) for name in ("T-INJ", "T-GRD", "T-DEF", "T-EMPTY", "T-NEW")}
        coverage = evidence.replay_coverage(evidences, REGISTRY)
        self.assertEqual({name: ok for name, (ok, _) in coverage.items()},
                         {"T-INJ": True, "T-GRD": True, "T-DEF": True, "T-EMPTY": False, "T-NEW": False})
        self.assertIn("由评审方在本 PR 中加入", coverage["T-NEW"][1])

    def test_doc_and_withdrawn_defects_need_no_replay(self):
        evidences = {"T-DOC": fixed("T-DOC", doc_only=True), "T-WD": fixed("T-WD", withdrawn_by="b" * 40)}
        self.assertEqual(evidence.replay_coverage(evidences, REGISTRY), {})

    def test_missing_replay_fails_the_harness_job(self):
        """harness job 以 evidence.py 的退回码为准：修复证据全部通过、但缺回放时仍须失败。"""
        evidences = {"T-NEW": fixed("T-NEW", before="fail", after="pass")}
        with mock.patch.object(evidence, "analyse", return_value=evidences), \
                mock.patch.object(evidence, "render_markdown", return_value=""), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(evidence.main(["--base", "x", "--no-run"]), 1)
            with mock.patch.object(evidence, "replay_cases", SimpleNamespace(CASES=[], GUARDED={}, DEFERRED={"T-NEW": "原因"})):
                self.assertEqual(evidence.main(["--base", "x", "--no-run"]), 0)
        self.assertIn("回放覆盖", out.getvalue())

    def test_repository_registry_reasons_are_written(self):
        from replay_cases import DEFERRED
        self.assertTrue(all(str(reason).strip() for reason in DEFERRED.values()))


class GuardP3Test(unittest.TestCase):
    def denied(self, command, role="designer"):
        return command_guard.check_command(command, role)

    def test_read_only_release_commands_are_allowed(self):
        for command in ("gh release list", "gh release view v0.8.0", "gh release list --limit 5 | head"):
            with self.subTest(command=command):
                self.assertEqual(self.denied(command), [])
        for command in ("gh release create v1", "gh release upload v1 x.zip", "gh release delete v1", "gh release edit v1", "gh release"):
            with self.subTest(command=command):
                self.assertTrue(self.denied(command))

    def test_registration_records_cannot_be_erased_by_any_agent(self):
        for command in (
            "gh issue delete 12 --yes",
            "gh issue edit 12 --remove-label escape",
            "gh pr edit 50 --remove-label budget-exceeded",
            "gh issue edit 12 --remove-label=audit",
            "gh label delete escalation --yes",
            "gh label edit class:K2 --name x",
        ):
            for role in ("designer", "implementer"):
                with self.subTest(command=command, role=role):
                    self.assertTrue(self.denied(command, role))
        for tool in ("mcp__github__delete_issue", "mcp__github__label_write"):
            with self.subTest(tool=tool):
                self.assertTrue(command_guard.evaluate({"tool_name": tool}, "designer", ROOT))

    def test_designer_may_close_audit_issues_but_implementer_may_not_touch_issues(self):
        for command in ("gh issue close 12", "gh issue edit 12 --add-label escape", "gh issue reopen 12",
                        "gh pr edit 50 --add-label class:K1", "gh label create x"):
            with self.subTest(command=command):
                self.assertEqual(self.denied(command, "designer"), [])
                self.assertTrue(self.denied(command, "implementer"))
        for command in ("gh issue list --label escape", "gh issue view 12", "gh issue create --title t --body b", "gh label list"):
            with self.subTest(command=command):
                self.assertEqual(self.denied(command, "implementer"), [])
        for tool in ("mcp__github__issue_write", "mcp__github__update_issue"):
            with self.subTest(tool=tool):
                self.assertTrue(command_guard.evaluate({"tool_name": tool}, "implementer", ROOT))
                self.assertEqual(command_guard.evaluate({"tool_name": tool}, "designer", ROOT), [])

    def test_implementer_cannot_edit_run_records(self):
        for path in ("docs/runs/2026-09-28-t005.json", str(ROOT / "docs/runs/x.md")):
            with self.subTest(path=path):
                reasons = command_guard.evaluate({"file_path": path}, "implementer", ROOT)
                self.assertTrue(any("运行记录" in reason for reason in reasons), reasons)
                self.assertEqual(command_guard.evaluate({"file_path": path}, "designer", ROOT), [])


APPROVE_AND_ISSUE_TOOLS = (
    "mcp__github__pull_request_review_write",
    "mcp__github__approve_pull_request",
    "mcp__github__issue_write",
    "mcp__github__delete_issue",
    "mcp__github__label_write",
)


class HostMatcherTest(unittest.TestCase):
    """B5：各宿主都要把批准类与议题、标签类工具交给守卫（此前 Zcode 只匹配合并工具）。"""

    def test_zcode_and_claude_matchers_cover_approve_issue_and_label_tools(self):
        zcode = json.loads((ROOT / ".zcode/config.json").read_text())["hooks"]["events"]["PreToolUse"][0]["matcher"]
        claude = [entry["matcher"] for entry in json.loads((ROOT / ".claude/settings.json").read_text())["hooks"]["PreToolUse"]]
        for tool in APPROVE_AND_ISSUE_TOOLS:
            with self.subTest(tool=tool):
                self.assertTrue(re.fullmatch(zcode, tool))
                self.assertTrue(any(re.fullmatch(matcher, tool) for matcher in claude))
        self.assertIsNone(re.fullmatch(zcode, "Read"))

    def test_opencode_plugin_forwards_approve_and_issue_tools(self):
        plugin = (ROOT / ".opencode/plugin/harness-guard.js").as_uri()
        script = OpenCodePluginTest.SCRIPT % (json.dumps(plugin), json.dumps(str(ROOT)))
        cases = [[tool, {}] for tool in ("github_approve_pull_request", "github_issue_write", "github_get_issue")]
        result = subprocess.run(["node", "--input-type=module", "-e", script, json.dumps(cases)],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), ["deny", "deny", "allow"])

    @unittest.skipUnless(_node_runs_typescript(), "node 不能直接运行 TypeScript")
    def test_pi_extension_forwards_approve_and_issue_tools(self):
        extension = (ROOT / ".pi/extensions/harness-guard.ts").as_uri()
        cases = [[tool, {}] for tool in ("github_approve_pull_request", "github_issue_write", "github_get_issue")]
        result = subprocess.run(["node", "--input-type=module", "-e", PiExtensionTest.SCRIPT % json.dumps(extension), json.dumps(cases)],
                                capture_output=True, text=True, cwd=ROOT, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), ["deny", "deny", "allow"])


if __name__ == "__main__":
    unittest.main()
