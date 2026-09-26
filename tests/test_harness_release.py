"""发版核对与服务端配置的一致性测试。"""

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import release_check
from test_harness import TempRepo

BUILD = 'BUNDLE_SHORT_VERSION = "{short}"\nBUNDLE_VERSION = "{build}"\n'


class ReleaseCheckTest(unittest.TestCase):
    def setUp(self):
        self.repo = TempRepo()
        self.addCleanup(self.repo.close)
        self.repo.write("scripts/build_inbox_app.py", BUILD.format(short="0.8.0", build="24"))
        self.repo.commit("v0.8.0")
        self.repo.git("tag", "v0.8.0")

    def bump(self, short, build):
        self.repo.write("scripts/build_inbox_app.py", BUILD.format(short=short, build=build))
        return self.repo.commit(f"release {short}")

    def check(self, tag, commit="HEAD", main="main"):
        return release_check.check(tag, commit, main, cwd=self.repo.path)

    def test_consistent_release_passes(self):
        self.bump("0.8.1", "25")
        self.assertEqual(self.check("v0.8.1"), [])

    def test_tag_must_match_short_version(self):
        self.bump("0.8.1", "25")
        self.assertIn("不一致", self.check("v0.9.0")[0])

    def test_build_number_must_increase(self):
        """AGENTS.md：两处版本号不能只改其一。"""
        self.bump("0.8.1", "24")
        self.assertIn("没有大于上一个 tag", self.check("v0.8.1")[0])

    def test_tag_must_be_on_main(self):
        self.repo.git("checkout", "-q", "-b", "side")
        self.bump("0.8.1", "25")
        problems = self.check("v0.8.1", main="main")
        self.assertTrue(any("不在 main 上" in problem for problem in problems))

    def test_real_build_script_is_parseable(self):
        versions = release_check.read_versions((ROOT / "scripts/build_inbox_app.py").read_text())
        self.assertRegex(versions["BUNDLE_SHORT_VERSION"], r"^\d+\.\d+\.\d+$")
        self.assertRegex(versions["BUNDLE_VERSION"], r"^\d+$")


class ServerConfigTest(unittest.TestCase):
    """ruleset 要求的检查名必须是 workflow 里真实存在的 job，改名时两边一起改。"""

    def test_required_checks_exist_as_jobs(self):
        workflow = (ROOT / ".github/workflows/build.yml").read_text()
        jobs = set(re.findall(r"^  ([a-z][\w-]*):\n", workflow, flags=re.MULTILINE))
        ruleset = json.loads((ROOT / ".github/rulesets/main.json").read_text())
        rules = {rule["type"]: rule for rule in ruleset["rules"]}
        contexts = {item["context"] for item in rules["required_status_checks"]["parameters"]["required_status_checks"]}
        self.assertTrue(contexts <= jobs, f"{contexts - jobs} 不是 workflow 中的 job")
        self.assertIn("non_fast_forward", rules)
        self.assertEqual(ruleset["bypass_actors"], [])

    def test_tag_ruleset_blocks_moving_and_deleting(self):
        ruleset = json.loads((ROOT / ".github/rulesets/release-tags.json").read_text())
        self.assertEqual({rule["type"] for rule in ruleset["rules"]}, {"deletion", "non_fast_forward", "update"})
        self.assertEqual(ruleset["conditions"]["ref_name"]["include"], ["refs/tags/v*"])

    def test_release_job_requires_environment_approval(self):
        workflow = (ROOT / ".github/workflows/build.yml").read_text()
        release = workflow.split("\n  release:\n", 1)[1]
        self.assertIn("environment: release", release)
        self.assertIn("needs: [build, harness]", release)

    def test_auto_merge_judges_with_main_and_merges_only_evaluated_head(self):
        """E1：自动合并的判定必须跑 main 上的定义与 harness，PR 改自己的 workflow 或规则影响不到；
        只合并评估过的那个提交；fork 与失败的运行不处理。"""
        workflow = (ROOT / ".github/workflows/auto-merge.yml").read_text()
        trigger = workflow.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
        self.assertIn("workflow_run:", trigger)
        self.assertNotIn("pull_request", trigger)  # pull_request 与 pull_request_target 都会执行或暴露 PR 的定义
        self.assertIn("ref: ${{ github.event.repository.default_branch }}", workflow)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", workflow)
        self.assertIn("github.event.workflow_run.head_repository.full_name == github.repository", workflow)
        self.assertIn('harness/risk.py --base origin/main --head "$HEAD_SHA" --github', workflow)
        self.assertIn("auto_merge: ${{ steps.risk.outputs.auto_merge }}", workflow)
        self.assertIn('--match-head-commit "$HEAD_SHA"', workflow)
        self.assertEqual(workflow.count("run: git fetch --no-tags origin"), 1)
        self.assertNotRegex(workflow, r"run: (python|bash|sh|\./)\S*\s+(?!harness/risk\.py)")

    def test_auto_merge_approval_needs_main_only_environment(self):
        """D3：ruleset 要求非推送者批准；R0/R1 由 App 批准，App 凭据只在判定为 R0/R1 后、
        在只允许 main 使用的 environment 中取得，批准绑定评估过的提交。"""
        workflow = (ROOT / ".github/workflows/auto-merge.yml").read_text()
        judge, merge = workflow.split("\n  merge:\n", 1)
        self.assertIn("needs: judge", merge)
        self.assertIn("if: needs.judge.outputs.auto_merge == 'true'", merge)
        self.assertIn("environment: auto-merge", merge)
        self.assertNotIn("secrets.", judge)
        self.assertIn("uses: actions/create-github-app-token@", merge)
        self.assertIn('-f commit_id="$HEAD_SHA" -f event=APPROVE', merge)
        ruleset = json.loads((ROOT / ".github/rulesets/main.json").read_text())
        review = next(rule for rule in ruleset["rules"] if rule["type"] == "pull_request")["parameters"]
        self.assertGreaterEqual(review["required_approving_review_count"], 1)
        self.assertTrue(review["require_last_push_approval"])
        self.assertTrue(review["dismiss_stale_reviews_on_push"])

    def test_non_auto_merge_prs_request_owner_review(self):
        """用户 2026-09-26 要求：需要用户审核的 PR（R2 及以上）要指定其为评审人。
        只请求评审、不批准，也不进入 App 凭据所在的 environment。"""
        workflow = (ROOT / ".github/workflows/auto-merge.yml").read_text()
        job = workflow.split("\n  request-review:\n", 1)[1].split("\n  merge:\n", 1)[0]
        self.assertIn("needs: judge", job)
        self.assertIn("if: needs.judge.outputs.auto_merge == 'false'", job)
        self.assertIn('--add-reviewer "$OWNER"', job)
        self.assertIn("OWNER: ${{ github.repository_owner }}", job)
        self.assertNotIn("environment:", job)
        self.assertNotIn("secrets.", job)
        self.assertNotIn("APPROVE", job)


if __name__ == "__main__":
    unittest.main()
