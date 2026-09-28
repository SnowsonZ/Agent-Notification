"""周报（harness/weekly.py）：指标齐全、样本不足的标注、突增标红、历史往返、发布到固定议题。

设计 14.4 的机器验收：周报包含 14.1 全部指标项（数据不足的标「样本不足」）；注入一组突增数据被标红。
GitHub 数据用替身，不访问网络。
"""

import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))

import weekly

END = dt.datetime(2026, 10, 5, 3, 0, tzinfo=dt.UTC)
METRICS_14_1 = [
    "无修改合并率", "逃逸缺陷率", "变更失败率", "复杂度趋势", "变异得分", "pass^3", "合格交付数", "交付周期",
    "CI 轮次", "人工干预率", "升级率", "升级精度", "升级处理时长", "人审覆盖率", "每个合格交付的成本", "守卫拒绝",
]


def pr(number, merged="2026-10-01T10:00:00Z", auto=True, klass="K2", branch="task/005-x", reviews=(), commits=()):
    return {
        "number": number, "title": f"PR {number}", "labels": [{"name": f"class:{klass}"}], "mergedAt": merged,
        "createdAt": merged, "headRefName": branch, "author": {"login": "Snowson"},
        "mergedBy": {"login": "app/github-actions" if auto else "SnowsonZ"}, "reviews": list(reviews),
        "commits": list(commits), "mergeCommit": None,
    }


class FakeGitHub:
    def __init__(self, prs=(), escapes=(), escalations=(), audits=(), quality=(), builds=()):
        self.prs, self.escapes, self.escalations, self.audits = list(prs), list(escapes), list(escalations), list(audits)
        self.quality, self.builds, self.calls = list(quality), list(builds), []

    def __call__(self, *args):
        self.calls.append(args)
        if args[:2] == ("pr", "list"):
            if "--head" in args:
                return json.dumps([{"number": item["number"]} for item in self.prs if item["headRefName"] == args[args.index("--head") + 1]])
            if "--label" in args:
                return json.dumps([{"number": item["number"]} for item in self.prs])
            return json.dumps(self.prs)
        if args[:2] == ("pr", "view"):
            found = next(item for item in self.prs if str(item["number"]) == args[2])
            return json.dumps({**found, "state": "MERGED"})
        if args[:2] == ("issue", "list"):
            label = args[args.index("--label") + 1]
            return json.dumps({"escape": self.escapes, "escalation": self.escalations, "audit": self.audits}.get(label, []))
        if args[:2] == ("run", "list"):
            return json.dumps(self.quality if "quality" in args else self.builds)
        return "[]"


class WeeklyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "harness").mkdir()
        (self.root / "harness/quality-baseline.json").write_text('{"complex_functions": 20, "files_over_800": 1}')
        (self.root / "harness/mutation-baseline.json").write_text('{"热力金额分级": 1.0}')
        runs = self.root / "docs/runs/task-005-x"
        runs.mkdir(parents=True)
        (runs / "1.json").write_text(json.dumps({
            "task": "T005", "branch": "task/005-x", "started_at": "2026-10-01T09:00:00+00:00",
            "ended_at": "2026-10-01T09:20:00+00:00", "retries": 1, "cost": 0.04,
            "guard_denials": {"执行者不能编辑判定器与护栏": 2},
        }))

    def build(self, gh, comments=None):
        return weekly.build(END, gh, self.root, comments)

    def test_report_lists_every_metric_and_marks_missing_samples(self):
        text = self.build(FakeGitHub())
        for name in METRICS_14_1:
            with self.subTest(metric=name):
                self.assertIn(f"| {name} |", text)
        self.assertIn("| 无修改合并率 | 样本不足 |", text)
        self.assertIn("| pass^3 | 样本不足", text)
        self.assertIn("<!-- weekly-data", text)

    def test_metrics_from_prs_records_and_issues(self):
        review = {"author": {"login": "SnowsonZ"}, "state": "APPROVED", "submittedAt": "2026-10-01T09:30:00Z"}
        changed = {"author": {"login": "SnowsonZ"}, "state": "CHANGES_REQUESTED", "submittedAt": "2026-10-01T09:30:00Z"}
        gh = FakeGitHub(
            prs=[pr(57), pr(60, auto=False, klass="K4", branch="task/006-y", reviews=[review]),
                 pr(61, auto=False, klass="K4", branch="task/007-z", reviews=[changed])],
            escapes=[{"number": 1, "title": "逃逸", "body": "引入：#60", "labels": [], "createdAt": "2026-10-02T00:00:00Z"}],
            builds=[{"headSha": "a", "status": "completed", "event": "pull_request"},
                    {"headSha": "b", "status": "completed", "event": "pull_request"}],
        )
        text = self.build(gh)
        self.assertIn("| 无修改合并率 | 1/2（样本少，只报计数） |", text)
        self.assertIn("| 逃逸缺陷率 | 1/3（样本少，只报计数）", text)
        self.assertIn("| 合格交付数 | 2（", text)  # #60 被逃逸议题指认
        self.assertIn("| CI 轮次 | 中位 2 轮（1 个任务 PR） |", text)
        self.assertIn("| 交付周期 | 中位 1.0 小时（1 个派发任务） |", text)
        self.assertIn("执行者不能编辑判定器与护栏 2", text)
        self.assertIn("要求修改 1 次，批准 1 次", text)
        self.assertIn("token $0.040 + 人工约 18 分钟", text)
        self.assertIn("K4 2/2（样本少，只报计数）", text)

    def test_spikes_against_previous_weeks(self):
        history = [{"week": f"2026-W3{n}", "guard_denials": 1, "escapes": 0, "escalations": 2} for n in range(6, 10)]
        flagged = weekly.spikes({"guard_denials": 2, "escapes": 3, "escalations": 3}, history)
        self.assertEqual(len(flagged), 2)
        self.assertIn("守卫拒绝", flagged[0])  # 1 → 2：翻倍
        self.assertIn("逃逸登记", flagged[1])  # 0 → 3
        self.assertEqual(weekly.spikes({"escapes": 2}, history), [])  # 0 → 2：噪声，不标
        self.assertEqual(weekly.spikes({"escapes": 9}, []), [])  # 第一期没有历史

    def test_injected_spike_is_flagged_at_the_top(self):
        comments = [{"body": f"<!-- weekly-data {json.dumps({'week': f'2026-W3{n}', 'guard_denials': 0, 'retries_ci': 0})} -->"}
                    for n in range(6, 10)]
        (self.root / "docs/runs/task-005-x/2.json").write_text(json.dumps({
            "task": "T005", "branch": "task/005-x", "ended_at": "2026-10-02T00:00:00+00:00", "retries": 2,
            "guard_denials": {"覆盖变量只供人使用": 5},
        }))
        text = self.build(FakeGitHub(), comments)
        top = text.split("### 质量")[0]
        self.assertIn("🔴 **守卫拒绝**：本周 7", top)
        self.assertIn("🔴 **重试与 CI 轮次**：本周 3", top)

    def test_history_round_trip_skips_current_week(self):
        text = self.build(FakeGitHub())
        key = json.loads(weekly.DATA_MARK.search(text)[1])["week"]
        comments = [{"body": text}, {"body": "<!-- weekly-data {\"week\": \"2026-W39\", \"escapes\": 1} -->"}]
        self.assertEqual(weekly.history_from_comments(comments, key), [{"week": "2026-W39", "escapes": 1}])

    def test_trial_rows_include_history_and_dispatched_tasks(self):
        (self.root / "docs/runs/history.json").write_text('{"_说明": "x", "T004": {"prs": [39]}}')
        text = self.build(FakeGitHub(prs=[pr(39, branch="task/004-y"), pr(57)]))
        self.assertIn("| T004 | #39（merged）", text)
        self.assertIn("| T005 | #57（merged）", text)

    def test_publish_creates_then_updates_the_fixed_issue(self):
        calls = []

        def gh(*args):
            calls.append(args)
            if args[:2] == ("issue", "list"):
                return json.dumps([{"number": 70, "title": weekly.REPORT_TITLE}]) if len(calls) > 3 else "[]"
            if args[:2] == ("issue", "create"):
                return "https://github.com/o/r/issues/70\n"
            return ""

        self.assertEqual(weekly.publish("报告一", gh), 70)
        self.assertEqual([call[:2] for call in calls], [("issue", "list"), ("issue", "create"), ("issue", "comment")])
        calls.clear()
        calls.extend([()] * 3)
        self.assertEqual(weekly.publish("报告二", gh), 70)
        self.assertEqual([call[:2] for call in calls[3:]], [("issue", "list"), ("issue", "edit"), ("issue", "comment")])

    def test_labels_are_created_but_never_rewritten(self):
        calls = []

        def gh(*args):
            calls.append(args)
            raise RuntimeError("already exists")

        weekly.ensure_labels(gh)
        self.assertTrue(all("--force" not in call for call in calls))
        self.assertIn("escalation:needed", [call[2] for call in calls])


if __name__ == "__main__":
    unittest.main()
