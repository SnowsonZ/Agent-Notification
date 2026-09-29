"""任务书准入检查（harness/taskbook.py）与按类别判级（.harness/engine/routing/risk.py）、合同对执行方只读（command_guard）。

设计文档第四节 4.5 的机器验收：缺头部、验收未挂编号、类别与风险不相容的样例任务书各被拒绝；
执行方编辑任务书被守卫拒绝；任务书按类别判级；历史任务 002–004 补头部或登记豁免后全绿。
"""

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".harness"))
sys.path.insert(0, str(ROOT / "tests"))

from engine.checks import taskbook
from engine.core.common import load_rules
from engine.guards import command_guard
from engine.routing import risk
from test_harness import TempRepo

SPEC_IDS = {"DR14": "docs/specs/daily-report.md", "U4": "docs/specs/usage-cost.md"}


def header(**overrides):
    fields = {
        "task": "T005",
        "class": "K4",
        "risk": "R2",
        "designer": "codex",
        "size": "medium",
        "architecture": "false",
        "spec_refs": "[DR14]",
        "budget": "\n  wall_clock_min: 60\n  ci_rounds: 3\n  retries: 2\n  tokens: null",
        "rollback": "git revert",
    }
    fields.update(overrides)
    lines = [f"{key}: {value}" if not value.startswith("\n") else f"{key}:{value}"
             for key, value in fields.items() if value is not None]
    return "---\n" + "\n".join(lines) + "\n---\n"


BODY = textwrap.dedent(
    """
    # 任务：样例

    ## 目标终态

    日报合并正确。

    ## 非目标与禁止动作

    - 不改快照格式。

    ## 前置条件（不满足就停下报告）

    - main 上 verify 通过。

    ## 验收

    | 编号 | 验收内容 | 证据类型 | 覆盖（测试名或验证步骤） |
    |---|---|---|---|
    | DR14 | 合并取较大值 | 单测 | `test_daily_report.MergeTest` |

    ## 步骤与提交顺序

    | # | 改动 | 涉及文件 | 验证方式 | 对应验收 |
    |---|---|---|---|---|
    | 1 | 修复合并 | `scripts/daily_report.py`、`tests/test_daily_report.py` | `bin/verify` | DR14 |
    """
)


def check(text, path="docs/plans/task-005-sample.md", root=ROOT, exempt=False):
    return taskbook.check_text(text, path, SPEC_IDS, root, load_rules(), exempt=exempt).errors


class TaskbookAdmissionTest(unittest.TestCase):
    def assertRejected(self, errors, fragment):
        self.assertTrue(any(fragment in error for error in errors), errors)

    def test_well_formed_taskbook_passes(self):
        self.assertEqual(check(header() + BODY), [])

    def test_template_passes_admission(self):
        """模板本身就是一份合格的样例：模板与检查器不会各改各的。"""
        text = (ROOT / "docs/templates/task.md").read_text(encoding="utf-8")
        self.assertEqual(taskbook.check_text(text, "docs/plans/task-000-x.md", taskbook.spec_ids()).errors, [])

    def test_missing_header_is_rejected(self):
        self.assertRejected(check(BODY), "缺少 YAML 头部")
        self.assertRejected(check(header(designer=None) + BODY), "缺少字段 designer")

    def test_acceptance_row_must_link_a_spec(self):
        """任务 002–004 的编号都填了「—」（审计 G7）。"""
        self.assertRejected(check(header() + BODY.replace("| DR14 | 合并", "| — | 合并")), "须为规格验收编号")
        self.assertRejected(check(header() + BODY.replace("| DR14 | 合并", "| XX9 | 合并")), "不是现役规格中的验收编号")
        unlinked = header(spec_refs="[]", no_spec_reason="纯重构") + BODY.replace("| DR14 | 合并", "| 不挂规格：纯重构 | 合并")
        self.assertEqual(check(unlinked), [])
        self.assertRejected(check(header(spec_refs="[]") + BODY), "no_spec_reason")
        self.assertRejected(check(header(spec_refs="[DR14, U4]") + BODY), "没有出现在验收表里")

    def test_new_spec_id_must_exist_in_that_spec(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = root / "docs/specs/daily-report.md"
            spec.parent.mkdir(parents=True)
            spec.write_text("| 编号 | 内容 | 证据类型 | 覆盖 |\n|---|---|---|---|\n| DR99 | 新需求 | 单测 | `test_x` |\n")
            text = header(spec_refs="[DR99]") + BODY.replace("| DR14 | 合并", "| 新增:docs/specs/daily-report.md#DR99 | 合并")
            self.assertEqual(check(text, root=root), [])
            self.assertRejected(check(text.replace("#DR99", "#DR98").replace("[DR99]", "[DR98]"), root=root), "没有新增的编号 DR98")

    def test_evidence_kind_and_coverage(self):
        self.assertRejected(check(header() + BODY.replace("| 单测 |", "| 变异 |")), "不在词表中")
        self.assertRejected(check(header() + BODY.replace("`test_daily_report.MergeTest`", "执行方填写")), "写明测试名或命令")
        manual = header() + BODY.replace("| 单测 | `test_daily_report.MergeTest` |", "| 真机 UI | 打开 App 查看 |")
        self.assertEqual(check(manual), [])

    def test_class_and_risk_must_be_compatible(self):
        self.assertRejected(check(header(**{"class": "K2", "risk": "R2"}) + BODY), "不相容")
        self.assertRejected(check(header(**{"class": "K3", "risk": "R0"}) + BODY), "不相容")
        self.assertRejected(check(header(**{"class": "K9"}) + BODY), "class 取值非法")

    def test_budget_limits(self):
        over = "\n  wall_clock_min: 60\n  ci_rounds: 5\n  retries: 3\n  tokens: null"
        errors = check(header(budget=over) + BODY)
        self.assertRejected(errors, "ci_rounds = 5 超过上限 3")
        self.assertRejected(errors, "retries = 3 超过上限 2")

    def test_medium_task_needs_all_sections_but_small_does_not(self):
        body = BODY.split("## 非目标")[0] + "## 验收" + BODY.split("## 验收")[1].split("## 步骤")[0]
        errors = check(header() + body)
        for name in ("非目标", "前置条件", "步骤与提交顺序"):
            self.assertRejected(errors, f"缺少「{name}」")
        self.assertEqual(check(header(size="small") + body), [])

    def test_steps_cross_check_class_risk_and_architecture(self):
        guard = header() + BODY.replace("`scripts/daily_report.py`", "`.harness/engine/routing/risk.py`")
        errors = check(guard)
        self.assertRejected(errors, "class 应为 K7")
        self.assertRejected(errors, "risk 应为 R3")
        self.assertEqual(check(header(**{"class": "K7", "risk": "R3"}) + BODY.replace("`scripts/daily_report.py`", "`.harness/engine/routing/risk.py`")), [])
        two_modules = BODY.replace("`tests/test_daily_report.py`", "`native/InboxPolicy.swift`")
        self.assertRejected(check(header() + two_modules), "应声明 architecture: true")
        self.assertEqual(check(header(architecture="true") + two_modules), [])
        migration = BODY.replace("`tests/test_daily_report.py`", "`scripts/migrations.py`")
        self.assertRejected(check(header(risk="R3", architecture="false") + migration), "架构级路径")

    def test_task_number_matches_file_name(self):
        self.assertRejected(check(header(task="T006") + BODY), "与文件名编号 005 不一致")

    def test_exempt_history_only_needs_a_complete_header(self):
        legacy = header(**{"class": "K2", "risk": "R3"}) + "## 验收\n\n| 编号 | 证据类型 |\n| — | 变异 |\n"
        self.assertEqual(check(legacy, exempt=True), [])
        self.assertRejected(check(BODY, exempt=True), "缺少 YAML 头部")

    def test_header_parser_subset(self):
        parsed, body = taskbook.parse_header(header(spec_refs="[DR14, U4]  # 注释") + "正文\n")
        self.assertEqual(parsed["spec_refs"], ["DR14", "U4"])
        self.assertIs(parsed["architecture"], False)
        self.assertEqual(parsed["budget"], {"wall_clock_min": 60, "ci_rounds": 3, "retries": 2, "tokens": None})
        self.assertEqual(body.strip(), "正文")
        with self.assertRaises(taskbook.HeaderError):
            taskbook.parse_header("---\ntask T005\n---\n")
        with self.assertRaises(taskbook.HeaderError):
            taskbook.parse_header("---\ntask: T005\n")

    def test_exempt_list_entries_must_exist_and_give_a_reason(self):
        reports = taskbook.check_all(ROOT, exempt={"docs/plans/task-999-none.md": "x", "docs/plans/task-002-test-gaps.md": ""})
        errors = [error for report in reports for error in report.errors]
        self.assertRejected(errors, "登记的任务书不存在")
        self.assertRejected(errors, "须写明原因")


class TaskbookRiskTest(unittest.TestCase):
    """2026-09-28 决定 1：任务书按类别审；模板与待办清单须经用户审。"""

    def setUp(self):
        self.repo = TempRepo()
        self.addCleanup(self.repo.close)
        self.repo.write("docs/plans/backlog.md", "b\n")
        self.repo.write("docs/templates/task.md", "t\n")
        self.repo.write(".harness/state/taskbook-exempt.txt", "docs/plans/task-002-a.md  # a\ndocs/plans/task-003-b.md  # b\n")
        self.base = self.repo.commit("base")
        self.repo.git("checkout", "-q", "-b", "work")

    def level(self, path, content):
        self.repo.git("checkout", "-q", "-B", "work", self.base)
        self.repo.write(path, content)
        self.repo.commit("change")
        report = risk.classify(self.base, "HEAD", cwd=self.repo.path, rules=load_rules())
        return report.label, report.files[0].reason

    def test_business_and_fix_taskbooks_auto_merge(self):
        for klass, level in (("K4", "R2"), ("K5", "R2"), ("K2", "R0"), ("K3", "R1")):
            with self.subTest(klass=klass):
                label, _ = self.level("docs/plans/task-005-x.md", header(**{"class": klass, "risk": level}) + BODY)
                self.assertEqual(label, "R0")

    def test_guard_process_and_architecture_taskbooks_need_user_review(self):
        cases = {
            "K7": header(**{"class": "K7", "risk": "R3"}),
            "K8": header(**{"class": "K8", "risk": "R3"}),
            "architecture": header(architecture="true"),
            "bad header": header(**{"class": "K2", "risk": "R2"}),
            "no header": "",
        }
        for name, head in cases.items():
            with self.subTest(name=name):
                self.assertEqual(self.level("docs/plans/task-005-x.md", head + BODY)[0], "R2")

    def test_deleting_a_taskbook_needs_user_review(self):
        self.repo.write("docs/plans/task-005-x.md", header() + BODY)
        self.base = self.repo.commit("task")
        self.repo.git("rm", "-q", "docs/plans/task-005-x.md")
        self.repo.commit("rm")
        self.assertEqual(risk.classify(self.base, "HEAD", cwd=self.repo.path, rules=load_rules()).label, "R2")

    def test_templates_and_backlog_need_user_review(self):
        self.assertEqual(self.level("docs/templates/task.md", "t2\n")[0], "R2")
        self.assertEqual(self.level("docs/plans/backlog.md", "b2\n")[0], "R2")

    def test_exempt_list_may_only_shrink(self):
        self.assertEqual(self.level(".harness/state/taskbook-exempt.txt", "docs/plans/task-002-a.md  # a\n")[0], "R0")
        grown = "docs/plans/task-002-a.md  # a\ndocs/plans/task-003-b.md  # b\ndocs/plans/task-009-c.md  # c\n"
        self.assertEqual(self.level(".harness/state/taskbook-exempt.txt", grown)[0], "R3")


class ContractGuardTest(unittest.TestCase):
    def test_implementer_cannot_edit_taskbooks_or_specs(self):
        for path in ("docs/plans/task-005-x.md", "docs/specs/daily-report.md", str(ROOT / "docs/specs/usage-cost.md")):
            with self.subTest(path=path):
                reasons = command_guard.evaluate({"file_path": path}, "implementer", ROOT)
                self.assertTrue(any("合同" in reason for reason in reasons), reasons)
                self.assertEqual(command_guard.evaluate({"file_path": path}, "designer", ROOT), [])

    def test_implementer_may_still_edit_code_tests_and_other_docs(self):
        for path in ("scripts/daily_report.py", "tests/test_daily_report.py", "docs/plans/backlog.md", "README.md"):
            with self.subTest(path=path):
                self.assertEqual(command_guard.evaluate({"file_path": path}, "implementer", ROOT), [])


if __name__ == "__main__":
    unittest.main()
