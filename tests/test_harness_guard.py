"""harness 结构护栏的测试：命令守卫规则、git 钩子真实回放、OpenCode 插件。

git 钩子用例在临时仓库里把 core.hooksPath 指向本仓库的 .githooks，用真实 git 命令回放
v0.8.0 X3（filter-repo 改写 main 与 tag）等场景，而不是只测判定函数。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import command_guard
from common import clean_git_env
from test_harness import GIT_ENV, TempRepo

# CI 用 venv 解释器运行、venv/bin 不在 PATH 上：也在解释器旁边找。
_SIBLING = Path(sys.executable).parent / "git-filter-repo"
FILTER_REPO = shutil.which("git-filter-repo") or (str(_SIBLING) if _SIBLING.exists() else None)

DENY = [
    "git push --force origin feature",
    "git push -f origin feature",
    "git push --force-with-lease origin feature",
    "git push origin +feature",
    "git push --tags",
    "git push --mirror origin",
    "git push origin main",
    "git push origin HEAD:main",
    "git push origin v0.9.0",
    "git push origin refs/tags/v0.9.0",
    "git filter-repo --force --message-callback x",
    "git filter-branch --tree-filter x",
    "git-filter-repo --force",
    "cd /tmp/r && /usr/local/bin/git-filter-repo --force",
    "git tag v0.9.0",
    "git tag -d v0.8.0",
    "git tag -f v0.8.0 HEAD",
    "git update-ref refs/heads/main HEAD~1",
    "git commit --no-verify -m x",
    "git commit -nm x",
    "git -c core.hooksPath=/dev/null commit -m x",
    "git config core.hooksPath /dev/null",
    "git config --local --unset core.hooksPath",
    "git config core.hookspath .x",
    "HARNESS_ALLOW_TAG=1 git push origin v1",
    "HARNESS_SKIP_VERIFY=1 git push",
    "git reset --hard HEAD~1",
    "git clean -fdx",
    "rm -rf ~/work",
    "rm -rf $HOME/x",
    "rm -rf ../sibling",
    "rm -rf /tmp/x /Users/x/project",
    "gh release create v1",
    "gh run delete 123",
    "gh api -X DELETE repos/o/r/actions/runs/1",
    "cd x && git push --force",
]
ALLOW = [
    "git push -u origin claude/feature",
    "git push -n origin feature",
    "git push origin feature-main",
    "git tag",
    "git tag -l 'v*'",
    "git commit -m 'fix main'",
    "git clean -fdx -e scratch/iterm-probe-venv",
    "git clean -fd",
    "rm -rf build/verify",
    "rm -rf /tmp/claude-0/scratch",
    "python3 -m unittest discover -s tests",
    "git reset --soft HEAD~1",
    "python3 -m pip show git-filter-repo",
    "git config --get core.hooksPath",
    "git config --local --get core.hooksPath && git status",
]


class CommandGuardTest(unittest.TestCase):
    def test_denied_commands(self):
        for command in DENY:
            with self.subTest(command=command):
                self.assertTrue(command_guard.check_command(command), command)

    def test_allowed_commands(self):
        for command in ALLOW:
            with self.subTest(command=command):
                self.assertEqual(command_guard.check_command(command), [], command)

    def test_implementer_cannot_edit_verifiers(self):
        for path in (".github/workflows/build.yml", "harness/verify.py", str(ROOT / ".githooks/pre-push")):
            with self.subTest(path=path):
                self.assertTrue(command_guard.evaluate({"file_path": path}, "implementer", ROOT))
                self.assertEqual(command_guard.evaluate({"file_path": path}, "designer", ROOT), [])
        self.assertEqual(command_guard.evaluate({"file_path": "scripts/inbox.py"}, "implementer", ROOT), [])

    def test_payload_shapes(self):
        claude = {"tool_name": "Bash", "tool_input": {"command": "git push --tags"}}
        codex_list = {"tool_name": "shell", "tool_input": {"command": ["bash", "-lc", "git push --tags"]}}
        opencode = {"filePath": "harness/rules.toml"}
        self.assertTrue(command_guard.evaluate(claude))
        self.assertTrue(command_guard.evaluate(codex_list))
        self.assertTrue(command_guard.evaluate(opencode, "implementer", ROOT))

    def test_cli_exit_codes(self):
        def run(payload, *args):
            return subprocess.run(
                [sys.executable, str(ROOT / "harness/command_guard.py"), *args],
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                check=False,
            )

        denied = run({"tool_name": "Bash", "tool_input": {"command": "git push --tags"}})
        self.assertEqual(denied.returncode, 2)
        self.assertIn("批量推送", denied.stderr)
        allowed = run({"tool_name": "Bash", "tool_input": {"command": "git status"}})
        self.assertEqual(allowed.returncode, 0)
        garbage = subprocess.run(
            [sys.executable, str(ROOT / "harness/command_guard.py")],
            input="not json",
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(garbage.returncode, 2)


class HookedRepo(TempRepo):
    """装好 harness git 钩子的临时仓库；初始提交在 main 上（显式人工覆盖）。"""

    def __init__(self):
        super().__init__()
        self.git("config", "core.hooksPath", str(ROOT / ".githooks"))
        self.write("README.md", "x\n")
        self.run_git("add", "-A")
        self.run_git("commit", "-q", "-m", "init", env={"HARNESS_ALLOW_MAIN": "1"})

    def run_git(self, *args, env=None):
        return subprocess.run(
            ["git", *args],
            cwd=self.path,
            capture_output=True,
            text=True,
            env=clean_git_env({**GIT_ENV, **(env or {})}),
            check=False,
        )

    def sha(self, ref):
        return self.run_git("rev-parse", ref).stdout.strip()


class GitGuardTest(unittest.TestCase):
    def setUp(self):
        self.repo = HookedRepo()
        self.addCleanup(self.repo.close)

    def commit(self, name, env=None):
        self.repo.write(name, name + "\n")
        self.repo.run_git("add", name)
        return self.repo.run_git("commit", "-q", "-m", name, env=env)

    def test_commit_on_main_is_rejected(self):
        result = self.commit("a.txt")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("保护分支 main", result.stderr)

    def test_commit_on_feature_branch_is_allowed(self):
        self.repo.run_git("checkout", "-q", "-b", "feature")
        self.assertEqual(self.commit("a.txt").returncode, 0)

    def test_staged_snapshot_is_rejected_by_pre_commit(self):
        """V080-R1 回放（git 层）：真实快照被 git add -A 带进提交。"""
        self.repo.run_git("checkout", "-q", "-b", "feature")
        self.repo.write("widget/snapshot.json", "{}\n")
        self.repo.run_git("add", "-A")
        result = self.repo.run_git("commit", "-q", "-m", "snapshot")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("禁止路径", result.stderr)

    def test_amend_on_main_is_rejected(self):
        before = self.repo.sha("main")
        result = self.repo.run_git("commit", "-q", "--amend", "-m", "rewritten", env={"HARNESS_ALLOW_MAIN": "1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("改写保护分支", result.stderr)
        self.assertEqual(self.repo.sha("main"), before)

    def test_fast_forward_of_main_is_allowed(self):
        self.assertEqual(self.commit("b.txt", env={"HARNESS_ALLOW_MAIN": "1"}).returncode, 0)

    def test_moving_or_deleting_tag_is_rejected(self):
        self.repo.run_git("tag", "v1")
        self.commit("c.txt", env={"HARNESS_ALLOW_MAIN": "1"})
        moved = self.repo.run_git("tag", "-f", "v1")
        self.assertNotEqual(moved.returncode, 0)
        self.assertIn("移动已有 tag", moved.stderr)
        deleted = self.repo.run_git("tag", "-d", "v1")
        self.assertNotEqual(deleted.returncode, 0)
        self.assertIn("删除已有 tag", deleted.stderr)

    def test_override_is_explicit(self):
        before = self.repo.sha("main")
        result = self.repo.run_git(
            "commit", "-q", "--amend", "-m", "x", env={"HARNESS_ALLOW_MAIN": "1", "HARNESS_ALLOW_REWRITE": "1"}
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotEqual(self.repo.sha("main"), before)

    @unittest.skipUnless(FILTER_REPO, "未安装 git-filter-repo（requirements-dev.txt）")
    def test_filter_repo_cannot_rewrite_main_or_tags(self):
        """V080-X3 回放：filter-repo 改写本地 main 与全部 tag。"""
        self.repo.run_git("tag", "v1")
        main_before, tag_before = self.repo.sha("main"), self.repo.sha("v1")
        path = f"{Path(FILTER_REPO).parent}{os.pathsep}{os.environ.get('PATH', '')}"
        result = self.repo.run_git(
            "filter-repo", "--force", "--message-callback", "return message + b'x'", env={"PATH": path}
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.repo.sha("main"), self.repo.sha("v1")), (main_before, tag_before))


class TamperedRulesTest(unittest.TestCase):
    """PR7-R3：执行者改掉工作区的 harness/rules.toml（去掉受保护分支），守卫仍按 origin/main 的规则拒绝。"""

    def setUp(self):
        self.repo = TempRepo()
        self.addCleanup(self.repo.close)
        (self.repo.path / "harness").mkdir()
        for name in ("common.py", "git_guard.py", "hygiene.py", "rules.toml"):
            shutil.copy2(ROOT / "harness" / name, self.repo.path / "harness" / name)
        shutil.copytree(ROOT / ".githooks", self.repo.path / ".githooks")
        self.env = {"HARNESS_ALLOW_MAIN": "1", "HARNESS_SKIP_VERIFY": "1"}
        self.run_git("add", "-A")
        self.run_git("commit", "-q", "-m", "guarded main")
        self.run_git("config", "core.hooksPath", ".githooks")
        self.remote = tempfile.TemporaryDirectory()
        self.addCleanup(self.remote.cleanup)
        subprocess.run(["git", "init", "-q", "--bare", self.remote.name], env=clean_git_env(GIT_ENV), check=True)
        self.run_git("remote", "add", "origin", self.remote.name)
        self.assertEqual(self.run_git("push", "-q", "origin", "main").returncode, 0)
        self.run_git("fetch", "-q", "origin")

    def run_git(self, *args, env=None):
        return subprocess.run(
            ["git", *args],
            cwd=self.repo.path,
            capture_output=True,
            text=True,
            env=clean_git_env({**GIT_ENV, **self.env, **(env or {})}),
            check=False,
        )

    def test_tampered_rules_do_not_unprotect_main(self):
        rules = self.repo.path / "harness" / "rules.toml"
        rules.write_text(rules.read_text().replace('protected_branches = ["main"]', "protected_branches = []"))
        result = self.run_git("commit", "-q", "--amend", "-m", "rewritten")
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("改写保护分支", result.stderr)
        self.assertIn("与 origin/main 不一致", result.stderr)


class PrePushTest(unittest.TestCase):
    def setUp(self):
        self.repo = HookedRepo()
        self.addCleanup(self.repo.close)
        self.remote = tempfile.TemporaryDirectory()
        self.addCleanup(self.remote.cleanup)
        subprocess.run(["git", "init", "-q", "--bare", self.remote.name], env=clean_git_env(GIT_ENV), check=True)
        self.repo.run_git("remote", "add", "origin", self.remote.name)
        self.repo.run_git("checkout", "-q", "-b", "feature")
        self.repo.write("f.txt", "1\n")
        self.repo.run_git("add", "f.txt")
        self.repo.run_git("commit", "-q", "-m", "f1")

    def test_feature_branch_push_is_allowed(self):
        result = self.repo.run_git("push", "-q", "origin", "feature")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_push_to_main_is_rejected(self):
        result = self.repo.run_git("push", "-q", "origin", "feature:main")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("直接推送保护分支", result.stderr)

    def test_push_tag_is_rejected(self):
        self.repo.run_git("tag", "v9")
        result = self.repo.run_git("push", "-q", "origin", "v9")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("推 tag 会触发发布", result.stderr)

    def test_push_with_real_home_path_is_rejected(self):
        """卫生检查在推送前覆盖本次推送的改动，不必等 CI（与 CI 的 --range 同口径）。"""
        self.repo.run_git("push", "-q", "origin", "feature")
        self.repo.write("g.py", "PATH = '/Users/" + "bob/project'\n")
        self.repo.run_git("add", "g.py")
        self.repo.run_git("commit", "-q", "--no-verify", "-m", "g")  # 模拟跳过 pre-commit 的提交
        result = self.repo.run_git("push", "-q", "origin", "feature")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("本机路径", result.stderr)

    def test_force_push_is_rejected(self):
        self.repo.run_git("push", "-q", "origin", "feature")
        self.repo.run_git("commit", "-q", "--amend", "-m", "f1 rewritten")
        result = self.repo.run_git("push", "-q", "--force", "origin", "feature")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("强制推送", result.stderr)


@unittest.skipUnless(shutil.which("node"), "未安装 node")
class OpenCodePluginTest(unittest.TestCase):
    """在 node 中加载插件并调用 tool.execute.before；OpenCode 真实加载另需实测。"""

    SCRIPT = """
import { HarnessGuard } from %s;
const hooks = await HarnessGuard({ worktree: %s });
const cases = JSON.parse(process.argv.at(-1));
const out = [];
for (const [tool, args] of cases) {
  try { await hooks["tool.execute.before"]({ tool }, { args }); out.push("allow"); }
  catch (error) { out.push("deny"); }
}
console.log(JSON.stringify(out));
"""

    def test_plugin_blocks_dangerous_calls(self):
        plugin = (ROOT / ".opencode/plugin/harness-guard.js").as_uri()
        script = self.SCRIPT % (json.dumps(plugin), json.dumps(str(ROOT)))
        cases = [
            ["bash", {"command": "git push --force origin x"}],
            ["bash", {"command": "git status"}],
            ["edit", {"filePath": "harness/verify.py"}],
            ["edit", {"filePath": "scripts/inbox.py"}],
            ["read", {"filePath": "harness/verify.py"}],
        ]
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script, json.dumps(cases)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), ["deny", "allow", "deny", "allow", "allow"])


if __name__ == "__main__":
    unittest.main()
