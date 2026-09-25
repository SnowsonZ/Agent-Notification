"""harness 结构护栏的测试：命令守卫规则、git 钩子真实回放、OpenCode 插件。

git 钩子用例在临时仓库里把 core.hooksPath 指向本仓库的 .githooks，用真实 git 命令回放
v0.8.0 X3（filter-repo 改写 main 与 tag）等场景，而不是只测判定函数。
"""

import json
import os
import re
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

    def test_remote_writes_and_split_override_variables(self):
        """PR7-R6：gh api / curl 写请求可绕过分支保护；覆盖变量拆开拼接可绕过检查。"""
        denied = [
            "gh api -X PATCH repos/o/r/git/refs/heads/main -f sha=abc -F force=true",
            "gh api --method PUT repos/o/r/contents/scripts/x.py -f message=m -f content=Zm9v",
            "gh api repos/o/r/git/refs -f ref=refs/heads/x -f sha=abc",
            "gh api --method=POST repos/o/r/releases",
            "curl -X PATCH https://api.github.com/repos/o/r/git/refs/heads/main -d '{}'",
            "X=HARNESS; export ${X}_ALLOW_TAG=1; git push origin v1",
            "export \"HARNESS\"'_ALLOW_REWRITE'=1",
            "env HAR\\NESS_SKIP_VERIFY=1 git push",
            # 只有去掉引号后才能识别的拆写（回放发现上面几条靠别的规则也能拦，没测到去引号这一步）
            "export HARN'ESS_ALL'OW_TAG=1",
            'gh a"pi" -X PATCH repos/o/r/git/refs/heads/main',
        ]
        allowed = [
            "gh api repos/o/r/pulls/7",
            "gh api -X GET repos/o/r/actions/runs",
            "gh pr view 7",
            "curl -s https://api.github.com/repos/o/r",
        ]
        for command in denied:
            with self.subTest(command=command):
                self.assertTrue(command_guard.check_command(command), command)
        for command in allowed:
            with self.subTest(command=command):
                self.assertEqual(command_guard.check_command(command), [], command)

    def test_curl_writes_in_any_argument_order(self):
        """PR7-R9：curl 写请求只在「URL 在前、方法在后」时被拦；动词在前、长选项、连写都会放行。"""
        denied = [
            "curl -X PATCH https://api.github.com/repos/o/r/git/refs/heads/main",
            "curl --request PATCH https://api.github.com/repos/o/r/git/refs/heads/main",
            "curl --request=put https://api.github.com/repos/o/r/contents/a.py",
            "curl -XPOST https://api.github.com/repos/o/r/releases",
            "curl -d @body https://api.github.com/repos/o/r/git/refs",
            "curl -d@body https://api.github.com/repos/o/r/git/refs",
            "curl --json @body https://api.github.com/repos/o/r/pulls",
            "curl -H 'Authorization: token x' --data-binary @b https://api.github.com/repos/o/r/merges",
        ]
        allowed = [
            "curl -s https://api.github.com/repos/o/r/pulls/7",
            "curl -D headers.txt https://api.github.com/repos/o/r",
            "curl -X GET https://api.github.com/repos/o/r/actions/runs",
            "curl -X POST https://example.com/hook -d x",
        ]
        for command in denied:
            with self.subTest(command=command):
                self.assertTrue(command_guard.check_command(command), command)
        for command in allowed:
            with self.subTest(command=command):
                self.assertEqual(command_guard.check_command(command), [], command)

    def test_agent_cannot_merge_pull_requests(self):
        """D4（用户 2026-09-25 决定）：R0/R1 由仓库 auto-merge 合并，R2 以上由用户合并，Agent 不自行合并。"""
        for command in ("gh pr merge 7 --squash", "gh pr merge --auto 7", "cd x && gh  pr  merge 7"):
            with self.subTest(command=command):
                self.assertTrue(command_guard.check_command(command), command)
        for command in ("gh pr view 7", "gh pr checks 7", "git merge origin/main"):
            with self.subTest(command=command):
                self.assertEqual(command_guard.check_command(command), [], command)
        for tool in ("mcp__github__merge_pull_request", "mcp__github__enable_pr_auto_merge", "github_merge_pr"):
            with self.subTest(tool=tool):
                self.assertTrue(command_guard.evaluate({"tool_name": tool, "tool_input": {}}), tool)
        for tool in ("mcp__github__pull_request_read", "mcp__github__disable_pr_auto_merge", "Bash"):
            with self.subTest(tool=tool):
                self.assertEqual(command_guard.evaluate({"tool_name": tool, "tool_input": {}}), [], tool)

    def test_claude_code_hook_covers_mcp_merge_tools(self):
        """D4：Claude Code 的 PreToolUse 除 Bash 外还要把 MCP 合并工具交给守卫。"""
        settings = json.loads((ROOT / ".claude/settings.json").read_text())
        matchers = [entry["matcher"] for entry in settings["hooks"]["PreToolUse"]]
        for tool in ("mcp__github__merge_pull_request", "mcp__github__enable_pr_auto_merge"):
            with self.subTest(tool=tool):
                self.assertTrue(any(re.fullmatch(matcher, tool) for matcher in matchers), matchers)

    def test_implementer_cannot_edit_verifiers(self):
        for path in (
            ".github/workflows/build.yml",
            "harness/verify.py",
            str(ROOT / ".githooks/pre-push"),
            ".pi/extensions/harness-guard.ts",
        ):
            with self.subTest(path=path):
                self.assertTrue(command_guard.evaluate({"file_path": path}, "implementer", ROOT))
                self.assertEqual(command_guard.evaluate({"file_path": path}, "designer", ROOT), [])
        self.assertEqual(command_guard.evaluate({"file_path": "scripts/inbox.py"}, "implementer", ROOT), [])

    def test_implementer_may_shrink_gap_list_but_not_edit_other_verifiers(self):
        """H0926-2：规范要求执行方补完测试后删缺口清单的行，守卫却把整个 harness/ 禁改（trial-001 卡在这里）。"""
        for path in ("harness/acceptance-gaps.txt", str(ROOT / "harness/acceptance-gaps.txt")):
            with self.subTest(path=path):
                self.assertEqual(command_guard.evaluate({"file_path": path}, "implementer", ROOT), [])
        # 回放用例是会被执行的判定器代码，追加一行即可删掉别的用例：仍只由评审方维护。
        for path in ("harness/replay_cases.py", "harness/rules.toml", "harness/acceptance.py"):
            with self.subTest(path=path):
                self.assertTrue(command_guard.evaluate({"file_path": path}, "implementer", ROOT))

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
            ["github_merge_pull_request", {"pullNumber": 7}],
        ]
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script, json.dumps(cases)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), ["deny", "allow", "deny", "allow", "allow", "deny"])



def _node_runs_typescript() -> bool:
    if not shutil.which("node"):
        return False
    probe = subprocess.run(["node", "-p", "process.features.typescript"], capture_output=True, text=True, check=False)
    return probe.stdout.strip() in {"strip", "transform"}


@unittest.skipUnless(_node_runs_typescript(), "node 不能直接运行 TypeScript")
class PiExtensionTest(unittest.TestCase):
    """在 node 中加载 Pi 扩展并调用 tool_call 处理器；Pi 真实加载另需实测（规范 §6）。"""

    SCRIPT = """
const mod = await import(%s);
const handlers = {};
mod.default({ on: (name, handler) => { handlers[name] = handler; } });
const cases = JSON.parse(process.argv.at(-1));
const out = [];
for (const [toolName, input] of cases) {
  const verdict = await handlers["tool_call"]({ toolName, input });
  out.push(verdict && verdict.block ? "deny" : "allow");
}
console.log(JSON.stringify(out));
"""

    def test_extension_blocks_dangerous_calls(self):
        extension = (ROOT / ".pi/extensions/harness-guard.ts").as_uri()
        cases = [
            ["bash", {"command": "git push --force origin x"}],
            ["bash", {"command": "git status"}],
            ["edit", {"path": "harness/verify.py"}],
            ["write", {"path": ".pi/extensions/harness-guard.ts"}],
            ["edit", {"path": "scripts/inbox.py"}],
            ["read", {"path": "harness/verify.py"}],
            ["github_merge_pull_request", {"pullNumber": 7}],
        ]
        result = subprocess.run(
            ["node", "--input-type=module", "-e", self.SCRIPT % json.dumps(extension), json.dumps(cases)],
            capture_output=True,
            text=True,
            cwd=ROOT,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), ["deny", "allow", "deny", "deny", "allow", "allow", "deny"])


if __name__ == "__main__":
    unittest.main()
