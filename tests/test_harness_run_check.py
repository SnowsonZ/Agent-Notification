"""运行记录与任务归属的 CI 侧复核（harness/run_check.py）及其在合并路由中的判定（policy.py 第 5 条）。

设计 16.1 P5 的机器验收：缺记录或超预算的样例 PR 被标记且不自动合并。
"""

import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT / "tests"))

import policy
import r1_checks
import run_check
from test_harness import TempRepo

HEADER = """---
task: T005
class: K2
risk: R0
designer: claude-code
size: small
architecture: false
spec_refs: []
no_spec_reason: 样例
budget:
  wall_clock_min: 30
  ci_rounds: 2
  retries: 2
  tokens: null
rollback: git revert
---

# 任务：样例
"""
BRANCH = "task/005-sample"
PROMPT = "你是本仓库的执行方……\n"


def record(**overrides):
    data = {
        "task": "T005", "class": "K2", "attempt": 1, "branch": BRANCH, "gen_ai.agent.name": "pi",
        "host_version": "0.85.1", "gen_ai.request.model": "glm", "prompt_sha256": hashlib.sha256(PROMPT.encode()).hexdigest(),
        "prompt_path": "docs/runs/task-005-sample/1.prompt.md", "guard_ref": "abc", "started_at": "s", "ended_at": "e",
        "exit": "ok", "retries": 0, "failure_signatures": [], "guard_denials": {},
    }
    data.update(overrides)
    return {key: value for key, value in data.items() if value is not None}


class RunCheckTest(unittest.TestCase):
    def setUp(self):
        self.repo = TempRepo()
        self.addCleanup(self.repo.close)
        self.repo.write("docs/plans/task-005-sample.md", HEADER)
        self.repo.write("tests/test_a.py", "A = 1\n")
        self.base = self.repo.commit("base")
        self.repo.git("checkout", "-q", "-b", BRANCH)

    def work(self, trailer="Task: T005"):
        self.repo.write("tests/test_new.py", "B = 2\n")
        self.repo.git("add", "-A")
        self.repo.git("commit", "-q", "-m", f"tests\n\n{trailer}")

    def add_record(self, data=None, prompt=PROMPT, number=1):
        self.repo.write(f"docs/runs/task-005-sample/{number}.prompt.md", prompt)
        (self.repo.path / f"docs/runs/task-005-sample/{number}.json").write_text(json.dumps(data or record()))
        self.repo.git("add", "-A")
        self.repo.git("commit", "-q", "-m", "run\n\nTask: T005")

    def findings(self, branch=BRANCH, rounds=1):
        return run_check.check(self.base, "HEAD", branch, self.repo.path, rounds=rounds)

    def failed(self, **kwargs):
        return {item.name: item.reason for item in self.findings(**kwargs) if not item.ok}

    def test_dispatched_pr_passes(self):
        self.work()
        self.add_record()
        self.assertEqual(self.failed(), {})

    def test_designer_branch_without_task_is_not_applicable(self):
        self.work(trailer="")
        self.assertIsNone(self.findings(branch="task/harness-p5"))

    def test_task_trailer_makes_any_branch_applicable(self):
        self.work()
        self.assertIn("运行记录", self.failed(branch="feature/x"))

    def test_missing_record_is_flagged(self):
        self.work()
        self.assertIn("缺运行记录", self.failed()["运行记录"])

    def test_commit_without_task_trailer_is_flagged(self):
        self.work(trailer="")
        self.add_record()
        self.assertIn("没有 `Task: T005`", self.failed()["归属"])

    def test_bad_records_are_flagged(self):
        cases = {
            "exit": (record(exit="loop"), PROMPT, "退出方式为 loop"),
            "missing field": (record(guard_ref=None), PROMPT, "缺字段：guard_ref"),
            "task": (record(task="T006"), PROMPT, "task='T006'"),
            "branch": (record(branch="task/other"), PROMPT, "branch='task/other'"),
            "prompt": (record(), PROMPT + "改过", "sha256 与记录不一致"),
        }
        for name, (data, prompt, fragment) in cases.items():
            with self.subTest(name=name):
                self.repo.git("checkout", "-q", "-B", BRANCH, self.base)
                self.work()
                self.add_record(data, prompt)
                self.assertIn(fragment, self.failed()["运行记录"])

    def test_latest_record_decides(self):
        self.work()
        self.add_record(record(exit="loop"))
        self.add_record(record(attempt=2, prompt_path="docs/runs/task-005-sample/2.prompt.md"), number=2)
        self.assertEqual(self.failed(), {})

    def test_ci_rounds_over_budget_or_unreadable(self):
        self.work()
        self.add_record()
        self.assertIn("超出预算", self.failed(rounds=3)["CI 轮次"])
        self.assertIn("读不到", self.failed(rounds=None)["CI 轮次"])
        self.assertEqual(self.failed(rounds=2), {})

    def test_ci_rounds_count_distinct_completed_heads(self):
        runs = [
            {"headSha": "a", "status": "completed"},
            {"headSha": "a", "status": "completed"},  # 重跑同一提交
            {"headSha": "b", "status": "completed"},
            {"headSha": "c", "status": "in_progress"},
        ]
        self.assertEqual(run_check.ci_rounds(runs), 2)

    def test_policy_routes_flagged_task_prs_to_user(self):
        """缺记录与超预算的任务 PR 都不自动合并；合格的照常合并。"""
        self.work()

        def gh(runs):
            def call(*args):
                if args[:2] == ("pr", "view"):
                    return json.dumps({"labels": []})
                if args[:2] == ("run", "list"):
                    return json.dumps([{"headSha": sha, "status": "completed", "event": "pull_request"} for sha in runs])
                return "[]"
            return call

        autonomy = r1_checks.load_autonomy()
        missing = policy.gather(self.base, "HEAD", 9, self.repo.path, autonomy, gh(["a"]), branch=BRANCH)
        rules = {rule.name: rule for rule in policy.decide(missing, autonomy)}
        self.assertFalse(rules["运行记录"].ok)
        self.assertIn("缺运行记录", rules["运行记录"].reason)
        self.add_record()
        over = policy.gather(self.base, "HEAD", 9, self.repo.path, autonomy, gh(["a", "b", "c"]), branch=BRANCH)
        self.assertIn("超出预算", {rule.name: rule for rule in policy.decide(over, autonomy)}["运行记录"].reason)
        good = policy.gather(self.base, "HEAD", 9, self.repo.path, autonomy, gh(["a"]), branch=BRANCH)
        self.assertTrue(all(rule.ok for rule in policy.decide(good, autonomy)), policy.decide(good, autonomy))


def _has(commit: str) -> bool:
    return subprocess.run(["git", "cat-file", "-e", commit], cwd=ROOT, capture_output=True, check=False).returncode == 0


@unittest.skipUnless(_has("e0c4694^2"), "需要完整历史（T005 的实现 PR #57）")
class RealRecordTest(unittest.TestCase):
    """B21 的真实运行记录（PR #57）按 run_check 的口径合格：检查器与派发脚本写出的格式一致。"""

    def test_t005_record(self):
        findings = run_check.check("e0c4694^1", "e0c4694^2", "task/005-cost-level-boundaries", ROOT, with_ci=False)
        self.assertEqual([item.name for item in findings if not item.ok], [])


if __name__ == "__main__":
    unittest.main()
