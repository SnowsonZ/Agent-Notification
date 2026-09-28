"""派发脚本（harness/dispatch.py、dispatch_host.py）：用假执行方与假 GitHub 在临时仓库里走完整流程。

设计 6.4、7.5 的机器验收：认领冲突、守卫预检失败、超时、卡死、打转、超 CI 预算各按「失败时」处理；
成功路径写运行记录、开 PR；主目录不被改动；执行方拿不到 GitHub 令牌。真实 K2 任务的端到端运行另做。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))

import dispatch
import dispatch_host

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
}

TASKBOOK = """---
task: T005
class: K2
risk: R0
designer: claude-code
size: small
architecture: false
spec_refs: [DR14]
budget:
  wall_clock_min: 5
  ci_rounds: {ci_rounds}
  retries: 2
  tokens: null
rollback: git revert
---

# 任务：补一个测试

## 目标终态

DR14 有测试。

## 验收

| 编号 | 验收内容 | 证据类型 | 覆盖（测试名或验证步骤） |
|---|---|---|---|
| DR14 | 合并取较大值 | 单测 | `test_mod.Case` |
"""

SPEC = "| 编号 | 内容 | 证据类型 | 覆盖 |\n|---|---|---|---|\n| DR14 | 合并 | 单测 | `test_mod` |\n"

# 假执行方：按模式在槽位里做事。
FAKE = textwrap.dedent('''
    import subprocess, sys, time
    from pathlib import Path
    mode = sys.argv[1]
    def commit(name, text):
        Path(name).write_text(text)
        subprocess.run(["git", "add", name], check=True)
        subprocess.run(["git", "commit", "-q", "-m", "step\\n\\nTask: T005"], check=True)
    print('{"type": "session"}', flush=True)
    if mode == "commit":
        n = len(list(Path(".").glob("tests_new*.txt")))
        commit(f"tests_new{n}.txt", "ok")
    elif mode == "bad":
        n = len(list(Path(".").glob("bad*.txt")))
        commit(f"bad{n}.txt", "bad")
    elif mode == "sleep":
        while True:
            print("{}", flush=True); time.sleep(0.2)
    elif mode == "silent":
        time.sleep(60)
    elif mode == "note":
        Path("build/dispatch").mkdir(parents=True, exist_ok=True)
        Path("build/dispatch/escalation.md").write_text("- 可选方案与推荐：A\\n- 需要决定的问题：Q")
''')

# 假 verify：有 bad*.txt 即以同样的理由失败。
VERIFY = textwrap.dedent('''
    import sys
    from pathlib import Path
    if list(Path(".").glob("bad*.txt")):
        print("FAIL: test_mod.Case 断言失败 1 != 2"); sys.exit(1)
''')


class FakeHost:
    name = "fake"

    def __init__(self, script: Path, modes: list[str]):
        self.script, self.modes, self.prompts = script, list(modes), []

    def version(self):
        return "fake 1.0"

    def argv(self, prompt, guard):
        self.prompts.append(prompt)
        mode = self.modes.pop(0) if len(self.modes) > 1 else self.modes[0]
        return [sys.executable, str(self.script), mode]

    def parse(self, events):
        return "fake-model", {"input_tokens": 10, "output_tokens": 2}, {}


class FakeGitHub:
    def __init__(self, claimed=(), ci=(True,)):
        self.claimed, self.ci = set(claimed), list(ci)
        self.pushes, self.prs, self.comments, self.labels, self.issues = [], [], [], [], []

    def remote_branch_exists(self, branch):
        return branch in self.claimed

    def push(self, slot, branch):
        subprocess.run(["git", "push", "-q", "-u", "origin", branch], cwd=slot, check=True, capture_output=True)
        self.pushes.append(branch)

    def open_pr(self, slot, branch, title, body):
        self.prs.append((branch, title, body))
        return 7

    def comment(self, pr, body, label=None):
        self.comments.append((pr, body, label))

    def add_label(self, pr, label):
        self.labels.append((pr, label))

    def create_issue(self, title, body, labels):
        self.issues.append((title, body, labels))

    def wait_ci(self, branch, sha, timeout):
        ok = self.ci.pop(0) if len(self.ci) > 1 else self.ci[0]
        return ok, "" if ok else "CI 未通过：test_mod.Case"


class DispatchTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, GIT_ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        origin = self.tmp / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
        self.root = self.tmp / "repo"
        subprocess.run(["git", "clone", "-q", str(origin), str(self.root)], check=True, capture_output=True)
        self.write_task(ci_rounds=3)
        (self.root / "docs/specs").mkdir(parents=True)
        (self.root / "docs/specs/daily-report.md").write_text(SPEC)
        (self.root / "fake.py").write_text(FAKE)
        (self.root / "verify.py").write_text(VERIFY)
        (self.root / ".gitignore").write_text("build/\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "base")
        self.git("push", "-q", "origin", "HEAD:main")
        self.git("fetch", "-q", "origin")
        self.config = dispatch.Config(slots=2, slot_root=self.tmp / "slots", stall_seconds=30, poll_seconds=0.1,
                                      verify=[sys.executable, "verify.py"])
        (self.tmp / "slots").mkdir()
        guard = mock.patch.object(dispatch, "prepare_guard", return_value=(Path("guard.ts"), "abc123"))
        guard.start()
        self.addCleanup(guard.stop)

    def git(self, *args, cwd=None):
        return subprocess.run(["git", *args], cwd=cwd or self.root, check=True, capture_output=True, text=True).stdout

    def write_task(self, ci_rounds):
        (self.root / "docs/plans").mkdir(parents=True, exist_ok=True)
        (self.root / "docs/plans/task-005-new-test.md").write_text(TASKBOOK.format(ci_rounds=ci_rounds))

    def dispatcher(self, modes, github=None):
        host = FakeHost(self.root / "fake.py", modes)
        return dispatch.Dispatcher(self.root, self.config, github or FakeGitHub(), host), host

    def run_task(self, modes, github=None):
        runner, host = self.dispatcher(modes, github)
        code = runner.run("docs/plans/task-005-new-test.md")
        return code, runner, host

    def slot(self):
        return dispatch.slot_path(self.root, self.config, 1)

    def record(self, number=1):
        return json.loads((self.slot() / f"docs/runs/task-005-new-test/{number}.json").read_text())

    def test_success_writes_record_opens_pr_and_leaves_main_untouched(self):
        github = FakeGitHub()
        code, _, host = self.run_task(["commit"], github)
        self.assertEqual(code, 0)
        record = self.record()
        self.assertEqual((record["task"], record["exit"], record["gen_ai.request.model"]), ("T005", "ok", "fake-model"))
        self.assertEqual(record["gen_ai.usage.input_tokens"], 10)
        self.assertEqual(record["guard_ref"], "abc123")
        self.assertTrue((self.slot() / record["prompt_path"]).exists())
        self.assertEqual(len(github.prs), 1)
        self.assertIn("docs/runs/task-005-new-test/1.json", github.prs[0][2])
        self.assertEqual(github.pushes, ["task/005-new-test", "task/005-new-test"])  # 认领 + 结果
        self.assertIn("Task: T005", self.git("log", "-1", "--format=%B", cwd=self.slot()))
        self.assertEqual(self.git("status", "--porcelain"), "")  # 主目录不变
        self.assertIn("docs/plans/task-005-new-test.md", host.prompts[0])
        self.assertFalse(list((dispatch.state_dir(self.root) / "slots").glob("*.json")))  # 槽位已归还

    def test_executor_gets_no_github_token(self):
        seen = {}
        original = dispatch_host.run_monitored

        def spy(argv, cwd, env, *args, **kwargs):
            seen.update(env)
            return original(argv, cwd, env, *args, **kwargs)

        with mock.patch.dict(os.environ, {"GH_TOKEN": "secret-token", "GITHUB_TOKEN": "x"}), \
                mock.patch.object(dispatch_host, "run_monitored", spy):
            self.run_task(["commit"])
        self.assertNotIn("GH_TOKEN", seen)
        self.assertNotIn("GITHUB_TOKEN", seen)
        self.assertTrue(seen["GH_CONFIG_DIR"])
        self.assertEqual(seen["GIT_CONFIG_KEY_0"], "credential.helper")
        self.assertEqual(seen["GIT_AUTHOR_NAME"], dispatch.AGENT_LOGIN)

    def test_claimed_branch_stops_before_running(self):
        runner, host = self.dispatcher(["commit"], FakeGitHub(claimed={"task/005-new-test"}))
        with self.assertRaisesRegex(dispatch.Stop, "已被认领"):
            runner.run("docs/plans/task-005-new-test.md")
        self.assertEqual(host.prompts, [])

    def test_taskbook_not_on_main_is_refused(self):
        self.write_task(ci_rounds=2)  # 工作区改了，main 上还是旧版本
        runner, host = self.dispatcher(["commit"])
        with self.assertRaisesRegex(dispatch.Stop, "origin/main"):
            runner.run("docs/plans/task-005-new-test.md")
        self.assertEqual(host.prompts, [])

    def test_exempt_history_taskbook_is_refused(self):
        (self.root / "harness").mkdir(exist_ok=True)
        (self.root / "harness/taskbook-exempt.txt").write_text("docs/plans/task-005-new-test.md  # 历史\n")
        with self.assertRaisesRegex(dispatch.Stop, "豁免"):
            dispatch.admit("docs/plans/task-005-new-test.md", self.root)

    def test_timeout_escalates(self):
        runner, _ = self.dispatcher(["sleep"])
        runner.used_seconds = 5 * 60 - 1  # 预算只剩 1 秒
        github = runner.github
        self.assertEqual(runner.run("docs/plans/task-005-new-test.md"), 1)
        self.assertEqual(self.record()["exit"], "timeout")
        self.assertIn("超出时长预算", github.issues[0][0] + github.issues[0][1])
        self.assertEqual(github.issues[0][2], ["escalation"])
        self.assertEqual(github.prs, [])

    def test_stall_escalates(self):
        self.config.stall_seconds = 0.5
        code, runner, _ = self.run_task(["silent"])
        self.assertEqual(code, 1)
        self.assertEqual(self.record()["exit"], "stall")
        self.assertIn("卡死", runner.github.issues[0][1])

    def test_same_failure_twice_is_a_loop(self):
        code, runner, host = self.run_task(["bad"])
        self.assertEqual(code, 1)
        record = self.record()
        self.assertEqual(record["exit"], "loop")
        self.assertEqual(len(record["failure_signatures"]), 2)
        self.assertEqual(record["failure_signatures"][0], record["failure_signatures"][1])
        self.assertIn("断言失败", host.prompts[1])  # 第二轮带上了失败摘要
        self.assertIn("打转", runner.github.issues[0][1])

    def test_clarification_request_is_escalated_with_executor_note(self):
        code, runner, _ = self.run_task(["note"])
        self.assertEqual(code, 1)
        self.assertEqual(self.record()["exit"], "clarify")
        self.assertIn("需要决定的问题：Q", runner.github.issues[0][1])

    def test_ci_failure_retries_with_ci_summary_then_passes(self):
        github = FakeGitHub(ci=[False, True])
        code, _, host = self.run_task(["commit", "commit"], github)
        self.assertEqual(code, 0)
        self.assertIn("CI 未通过", host.prompts[1])  # 第二次派发带上 CI 失败摘要
        self.assertEqual((self.record(1)["exit"], self.record(2)["exit"]), ("ok", "ok"))
        self.assertEqual(self.record(2)["ci_rounds_before"], 1)
        self.assertEqual(len(github.prs), 1)  # 同一个 PR 上继续
        self.assertEqual(github.labels, [])

    def test_ci_over_budget_labels_and_escalates(self):
        self.write_task(ci_rounds=1)
        self.git("commit", "-q", "-am", "budget 1")
        self.git("push", "-q", "origin", "HEAD:main")
        self.git("fetch", "-q", "origin")
        github = FakeGitHub(ci=[False])
        code, _, _ = self.run_task(["commit"], github)
        self.assertEqual(code, 1)
        self.assertEqual(github.labels, [(7, "budget-exceeded")])
        self.assertEqual(github.comments[0][2], "escalation")
        self.assertIn("已达预算 1 轮", github.comments[0][1])

    def test_stop_flag_terminates_executor(self):
        flag = dispatch.state_dir(self.root) / "stop"
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text("now")
        code, _, _ = self.run_task(["sleep"])
        self.assertEqual(code, 1)
        self.assertEqual(self.record()["exit"], "stopped")

    def test_slot_is_named_after_the_main_checkout_from_any_worktree(self):
        """B21 实测：从设计方的 worktree 启动时，槽位曾以 worktree 名命名（…-sync-slot-1）。"""
        other = self.tmp / "designer-worktree"
        self.git("worktree", "add", "-q", "--detach", str(other), "origin/main")
        self.config.slot_root = None
        expected = (self.tmp / "repo-slot-2").resolve()
        self.assertEqual(dispatch.slot_path(other, self.config, 2), expected)
        self.assertEqual(dispatch.slot_path(self.root, self.config, 2), expected)

    def test_slots_are_exclusive_and_stale_locks_reclaimed(self):
        task = dispatch.admit("docs/plans/task-005-new-test.md", self.root)
        first = dispatch.acquire_slot(self.root, self.config, task)
        second = dispatch.acquire_slot(self.root, self.config, task)
        self.assertEqual((first[0], second[0]), (1, 2))
        with self.assertRaisesRegex(dispatch.Stop, "都在使用中"):
            dispatch.acquire_slot(self.root, self.config, task)
        lock = dispatch.state_dir(self.root) / "slots" / "1.json"
        lock.write_text(json.dumps({"pid": 999999999}))  # 持有者已退出
        self.assertEqual(dispatch.acquire_slot(self.root, self.config, task)[0], 1)


class GuardBundleTest(unittest.TestCase):
    """守卫从 origin/main 导出；导出的守卫不拒绝必拒绝的样例时停止派发。"""

    def setUp(self):
        patcher = mock.patch.dict(os.environ, GIT_ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        origin = self.tmp / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
        self.root = self.tmp / "repo"
        subprocess.run(["git", "clone", "-q", str(origin), str(self.root)], check=True, capture_output=True)

    def publish(self, broken=False):
        shutil.copytree(ROOT / "harness", self.root / "harness", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(ROOT / ".pi", self.root / ".pi")
        if broken:
            (self.root / "harness/command_guard.py").write_text("raise SystemExit(0)\n")
        for args in (["add", "-A"], ["commit", "-q", "-m", "guard"], ["push", "-q", "origin", "HEAD:main"],
                     ["fetch", "-q", "origin"]):
            subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True)

    def test_guard_comes_from_origin_main(self):
        self.publish()
        (self.root / "harness/command_guard.py").write_text("raise SystemExit(0)\n")  # 工作区被改：不影响
        extension, ref = dispatch.prepare_guard(self.root)
        self.assertTrue(extension.exists())
        self.assertIn(ref, str(extension))
        self.assertNotIn("SystemExit(0)", (extension.parents[2] / "harness/command_guard.py").read_text())

    def test_broken_guard_on_main_stops_dispatch(self):
        self.publish(broken=True)
        with self.assertRaisesRegex(dispatch.Stop, "守卫预检失败"):
            dispatch.prepare_guard(self.root)


class PiHostTest(unittest.TestCase):
    def test_argv_ignores_project_files_and_loads_guard_explicitly(self):
        argv = dispatch_host.PiHost("m1").argv("做事", Path("/g/harness-guard.ts"))
        self.assertEqual(argv[:4], ["pi", "-na", "-e", "/g/harness-guard.ts"])
        self.assertIn("--no-session", argv)
        self.assertEqual(argv[-3:], ["--model", "m1", "做事"])

    def test_parse_usage_model_and_guard_denials(self):
        events = [
            {"type": "message_end", "message": {"role": "assistant", "model": "glm", "usage": {
                "input": 100, "cacheRead": 50, "output": 20, "cost": {"total": 0.01}}}},
            {"type": "tool_execution_end", "isError": True, "result": {"content": [{"type": "text", "text":
                "harness 守卫拒绝了这次操作：\n  - 执行者不能编辑判定器与护栏\n  如确需执行……"}]}},
            {"type": "message_end", "message": {"role": "assistant", "model": "glm", "usage": {
                "input": 10, "output": 5, "cost": {"total": 0.002}}}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            path.write_text("\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\nnot json\n")
            model, usage, denials = dispatch_host.PiHost().parse(path)
        self.assertEqual(model, "glm")
        self.assertEqual(usage, {"input_tokens": 160, "output_tokens": 25, "cost": 0.012})
        self.assertEqual(denials, {"执行者不能编辑判定器与护栏": 1})


if __name__ == "__main__":
    unittest.main()
