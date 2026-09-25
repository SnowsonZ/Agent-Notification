"""验收映射检查器（harness/acceptance.py）的测试：在临时目录里构造规格与测试。"""

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))

import acceptance

SPEC = """\
# 示例

| 编号 | 验收内容 | 证据类型 | 覆盖 |
|---|---|---|---|
| EX1 | 有测试 | 单测 | `test_example.ExampleTest.test_ok` |
| EX2 | 真机 | 真机 UI | 用户验收 |
| EX3 | 引用 Swift | 单测 + 真机 UI | `tests/Example.swift#precondition(ok)` |
{extra}
"""
TEST_MODULE = """\
import unittest


class ExampleTest(unittest.TestCase):
    def test_ok(self):
        pass
"""


class AcceptanceCheckTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "docs" / "specs").mkdir(parents=True)
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_example.py").write_text(TEST_MODULE)
        (self.root / "tests" / "Example.swift").write_text("precondition(ok)\n")
        acceptance._test_symbols.cache_clear()

    def check(self, extra="", gaps=None):
        (self.root / "docs" / "specs" / "example.md").write_text(textwrap.dedent(SPEC.format(extra=extra)))
        return acceptance.check(self.root, gaps or {})

    def test_valid_spec_passes(self):
        items, errors = self.check()
        self.assertEqual(errors, [])
        self.assertEqual([item.id for item in items], ["EX1", "EX2", "EX3"])
        self.assertEqual([item.id for item in items if item.manual], ["EX2", "EX3"])

    def test_missing_test_reference_fails(self):
        _, errors = self.check("| EX4 | 引用不存在 | 单测 | `test_example.ExampleTest.test_missing` |")
        self.assertTrue(any("覆盖引用不存在" in error for error in errors))

    def test_automatable_without_test_needs_registered_gap(self):
        _, errors = self.check("| EX4 | 没有测试 | 夹具 | |")
        self.assertTrue(any("没有测试" in error for error in errors))
        _, errors = self.check("| EX4 | 没有测试 | 夹具 | |", gaps={"EX4": "待补"})
        self.assertEqual(errors, [])

    def test_gap_must_be_removed_once_covered(self):
        """缺口清单只能缩减：已有测试的条目仍登记为缺口即失败。"""
        _, errors = self.check(gaps={"EX1": "旧登记"})
        self.assertTrue(any("请从 acceptance-gaps.txt 删除" in error for error in errors))

    def test_unknown_evidence_type_and_duplicate_id(self):
        _, errors = self.check("| EX1 | 重复编号 | 拍脑袋 | |")
        self.assertTrue(any("重复" in error for error in errors))
        self.assertTrue(any("不在词表中" in error for error in errors))

    def test_swift_snippet_must_exist(self):
        _, errors = self.check("| EX4 | Swift | 单测 | `tests/Example.swift#precondition(missing)` |")
        self.assertTrue(any("覆盖引用不存在" in error for error in errors))


class RepositoryAcceptanceTest(unittest.TestCase):
    def test_repository_specs_pass(self):
        acceptance._test_symbols.cache_clear()
        items, errors = acceptance.check()
        self.assertEqual(errors, [])
        specs = {item.spec for item in items}
        self.assertEqual(len(specs), len(list((ROOT / "docs" / "specs").glob("*.md"))) - 1)  # delivery-harness 本身无验收表


if __name__ == "__main__":
    unittest.main()
