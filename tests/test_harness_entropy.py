"""熵治理（目标态设计阶段二 P8）：Swift 质量棘轮与文档链接、状态新鲜度检查。

全部在临时目录里构造样例，不读写本仓库的文件。
"""

import io
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".harness"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine.checks import docs_check, quality
from test_harness import TempRepo

SHORT_VIEW = """\
import SwiftUI

struct Row: View {
    let title: String
    var body: some View {
        Text(title)
    }
}

func helper(_ value: Int) -> Int {
    value + 1
}
"""


def long_function(lines: int) -> str:
    body = "\n".join(f"    total += {index}" for index in range(lines))
    return f"func grown() -> Int {{\n    var total = 0\n{body}\n    return total\n}}\n"


def nested_function(depth: int) -> str:
    opening = "".join("    " * level + "if flag {\n" for level in range(1, depth))
    closing = "".join("    " * level + "}\n" for level in reversed(range(1, depth)))
    return f"func nested(flag: Bool) {{\n{opening}{'    ' * depth}print(flag)\n{closing}}}\n"


class SwiftParseTest(unittest.TestCase):
    def test_counts_functions_inits_and_computed_properties(self):
        source = SHORT_VIEW + "final class Box {\n    init(value: Int) {\n        print(value)\n    }\n}\n"
        names = [name for name, _line, _body, _depth in quality.swift_functions(source)]
        self.assertEqual(names, ["body", "helper", "init"])

    def test_protocol_requirements_have_no_body(self):
        source = "protocol Runner {\n    func run() -> Int\n    var name: String { get }\n}\nfunc real() {\n}\n"
        functions = {name: body for name, _line, body, _depth in quality.swift_functions(source)}
        self.assertNotIn("run", functions)
        self.assertEqual(functions["real"], 2)

    def test_braces_in_strings_and_comments_are_ignored(self):
        source = (
            "func tricky() {\n"
            '    let a = "{ not a block {{"\n'
            "    // } 注释里的括号\n"
            "    /* { /* 嵌套 } */ } */\n"
            '    let b = #"raw } "quoted" {"#\n'
            '    let c = """\n    multi { line\n    """\n'
            '    let d = "escaped \\" { quote"\n'
            "}\n"
            "func after() {\n    print(1)\n}\n"
        )
        functions = {name: (body, depth) for name, _line, body, depth in quality.swift_functions(source)}
        self.assertEqual(functions["tricky"], (10, 1))
        self.assertEqual(functions["after"], (3, 1))

    def test_nesting_depth_counts_closures_and_control_flow(self):
        functions = {name: depth for name, _line, _body, depth in quality.swift_functions(nested_function(8))}
        self.assertEqual(functions["nested"], 8)


class SwiftRatchetTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.source = self.root / "native" / "Sample.swift"
        self.source.parent.mkdir()
        self.source.write_text(SHORT_VIEW + long_function(10))

    def test_lengthened_swift_function_fails_ratchet(self):
        baseline = quality.measure_swift(self.root)
        self.assertEqual(baseline["swift_long_functions"], 0)
        self.source.write_text(SHORT_VIEW + long_function(quality.SWIFT_LONG_FUNCTION))
        current = quality.measure_swift(self.root)
        self.assertEqual(current["swift_long_functions"], 1)
        regressions = quality.compare(current, baseline)
        self.assertEqual(len(regressions), 1)
        self.assertIn("swift_long_functions", regressions[0])

    def test_deeper_nesting_and_longer_file_fail_ratchet(self):
        baseline = quality.measure_swift(self.root)
        padding = "\n" * quality.SWIFT_LONG_FILE
        self.source.write_text(SHORT_VIEW + nested_function(quality.SWIFT_DEEP_NESTING + 1) + padding)
        current = quality.measure_swift(self.root)
        failed = {line.split(" ")[0] for line in quality.compare(current, baseline)}
        self.assertEqual(failed, {"swift_deep_functions", "swift_files_over_500"})

    def test_shrinking_passes(self):
        self.source.write_text(SHORT_VIEW + long_function(quality.SWIFT_LONG_FUNCTION))
        baseline = quality.measure_swift(self.root)
        self.source.write_text(SHORT_VIEW)
        self.assertEqual(quality.compare(quality.measure_swift(self.root), baseline), [])

    def test_swift_metrics_are_ratcheted(self):
        for name in ("swift_files_over_500", "swift_long_functions", "swift_deep_functions"):
            self.assertIn(name, quality.RATCHETED)


class DocsCheckTest(unittest.TestCase):
    def setUp(self):
        self.repo = TempRepo()
        self.addCleanup(self.repo.close)
        self.repo.write("docs/specs/target.md", "# 目标\n")
        self.repo.write("scripts/tool.py", "")

    def run_check(self, now=None):
        output = io.StringIO()
        with redirect_stdout(output):
            code = docs_check.main([], root=self.repo.path, now=now)
        return code, output.getvalue()

    def test_broken_relative_link_fails(self):
        self.repo.write("docs/plans/a.md", "见[规格](../specs/missing.md)与[目标](../specs/target.md)。\n")
        self.repo.commit("docs")
        code, output = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("docs/plans/a.md:1: 断链 ../specs/missing.md", output)
        self.assertNotIn("target.md", output)

    def test_reference_and_root_relative_links_are_checked(self):
        self.repo.write("README.md", "[坏]: docs/nothing.md\n见 [脚本](/scripts/tool.py)，[根坏](/scripts/gone.py)。\n")
        self.repo.commit("docs")
        code, output = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("README.md:1: 断链 docs/nothing.md", output)
        self.assertIn("README.md:2: 断链 /scripts/gone.py", output)
        self.assertNotIn("/scripts/tool.py", output)

    def test_anchors_external_links_and_code_are_not_reported(self):
        self.repo.write(
            "AGENTS.md",
            "[外链](https://example.com/x.md) [邮件](mailto:a@example.com) [锚点](#小节)\n"
            "[带锚点](docs/specs/target.md#一节) [带空格](docs/specs/target.md \"标题\")\n"
            "行内代码 `[不是链接](nowhere.md)` 不算。\n"
            "```\n[代码块](nowhere.md)\n```\n",
        )
        self.repo.commit("docs")
        code, output = self.run_check()
        self.assertEqual(code, 0, output)

    def test_anchor_on_missing_file_still_fails(self):
        self.repo.write("docs/a.md", "[x](gone.md#一节)\n")
        self.repo.commit("docs")
        code, output = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("gone.md#一节", output)

    def test_untracked_and_out_of_scope_markdown_is_ignored(self):
        self.repo.write("docs/untracked.md", "[x](gone.md)\n")
        self.repo.write("scripts/notes.md", "[x](gone.md)\n")
        self.repo.commit("scope", "scripts/notes.md", "docs/specs/target.md")
        code, _output = self.run_check()
        self.assertEqual(code, 0)

    def test_stale_unfinished_status_is_reported_without_failing(self):
        self.repo.write("docs/plans/old.md", "# 旧计划\n\n状态：**待执行**（2026-08-01 定稿）。\n")
        self.repo.write("docs/plans/done.md", "# 完成\n\n状态：已完成。后续不再「进行中」。\n")
        self.repo.write("docs/plans/list.md", "- 某行里提到状态：进行中，不是文档状态\n")
        self.repo.commit("docs")
        future = time.time() + 31 * 86400
        code, output = self.run_check(now=future)
        self.assertEqual(code, 0)
        self.assertIn("docs/plans/old.md：状态「待执行", output)
        self.assertNotIn("done.md", output)
        self.assertNotIn("list.md", output)

    def test_recent_unfinished_status_is_not_reported(self):
        self.repo.write("docs/plans/new.md", "日期：2026-09-28 · 状态：草案\n")
        self.repo.commit("docs")
        code, output = self.run_check()
        self.assertEqual(code, 0)
        self.assertIn("状态陈旧：无", output)
        self.assertEqual(docs_check.unfinished_status("日期：2026-09-28 · 状态：草案\n"), "草案")


if __name__ == "__main__":
    unittest.main()
