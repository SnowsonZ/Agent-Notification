"""CLI 黄金快照：固定夹具上的 `inbox usage --json` 输出与 tests/golden/ 逐字段一致。

用途：行为不变的重构（Risk: R1）以「黄金快照零差异」为判据；输出有意改变时重新生成：
    UPDATE_GOLDEN=1 python3 -m unittest test_golden      （在 tests/ 下运行）
改动 tests/golden/ 会被 harness/risk.py 标记为行为变化（R2），需评审确认。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "golden"
sys.path.insert(0, str(ROOT / "scripts"))

from daily_report import build_report, day_bounds, finalize_day_report

VOLATILE = {"generated_at", "is_current"}


def task(provider, session, model, fresh, cache_read, output, project="/work/demo"):
    return {
        "provider": provider,
        "session_id": session,
        "title": f"{provider} 任务",
        "project": project,
        "first_at": 0,
        "last_at": 1,
        "input_tokens": fresh,
        "cache_tokens": cache_read,
        "output_tokens": output,
        "total_tokens": fresh + cache_read + output,
        "turns": 3,
        "state": "idle",
        "fidelity": "exact",
        "segments": [],
        "models": {
            model: {"fresh_input": fresh, "cache_write": 0, "cache_read": cache_read, "output": output, "native_cost_usd": None}
        },
    }


FIXTURE = {
    date(2026, 7, 29): [task("pi", "p0", "glm-5.3-flash", 400_000, 0, 100_000)],
    date(2026, 8, 3): [
        task("pi", "p1", "glm-5.3-flash", 1_000_000, 200_000, 300_000),
        task("codex", "c1", "deepseek-v4-pro", 500_000, 2_000_000, 150_000, project="/work/other"),
    ],
    date(2026, 8, 4): [task("zcode", "z1", "k3", 250_000, 0, 50_000)],
    date(2026, 8, 6): [task("pi", "p2", "glm-5.3-flash", 2_000_000, 0, 600_000)],
}


def normalize(value):
    if isinstance(value, dict):
        return {key: normalize(item) for key, item in value.items() if key not in VOLATILE}
    if isinstance(value, list):
        return [normalize(item) for item in value]
    return value


class UsageGoldenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        base = Path(cls._tmp.name)
        cls.root, cls.home = base / "root", base / "home"
        cls.home.mkdir()
        for day, records in FIXTURE.items():
            report = build_report(day, records, 0.0)
            report["generated_at"] = day_bounds(day)[1] + 100
            finalize_day_report(cls.root, day, report)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def run_cli(self, *args):
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "inbox.py"), "--root", str(self.root), *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            env={**os.environ, "HOME": str(self.home), "PYTHONDONTWRITEBYTECODE": "1"},
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return normalize(json.loads(completed.stdout))

    def assert_golden(self, name, payload):
        path = GOLDEN / name
        text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if os.environ.get("UPDATE_GOLDEN") == "1":
            GOLDEN.mkdir(exist_ok=True)
            path.write_text(text, encoding="utf-8")
            return
        self.assertTrue(path.exists(), f"缺少黄金文件 {path.name}；用 UPDATE_GOLDEN=1 生成")
        expected = path.read_text(encoding="utf-8")
        if text != expected:
            self.fail(
                f"{name} 与黄金快照不一致：CLI 输出变了。若是有意的行为变化，用 UPDATE_GOLDEN=1 重新生成，"
                "该改动会被判为 R2（行为变化）交评审确认。"
            )

    def test_usage_week(self):
        self.assert_golden("usage-week.json", self.run_cli("usage", "--period", "week", "--date", "2026-08-05", "--json"))

    def test_usage_month(self):
        self.assert_golden("usage-month.json", self.run_cli("usage", "--period", "month", "--date", "2026-08-05", "--json"))


if __name__ == "__main__":
    unittest.main()
