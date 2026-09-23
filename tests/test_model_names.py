"""U4：model 规范名（用量金额规范 §2.1）——确定性变换，不做相似度匹配。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from model_names import UNKNOWN, canonical, raw_names_registry


class CanonicalTest(unittest.TestCase):
    def test_case_and_whitespace(self):
        self.assertEqual(canonical("  GLM-5.3-Flash  "), "glm-5.3-flash")
        self.assertEqual(canonical("GLM-5.3-FLASH"), "glm-5.3-flash")

    def test_prefix_stripped_to_last_slash(self):
        self.assertEqual(canonical("zai-coding-plan/glm-5.3-flash"), "glm-5.3-flash")
        self.assertEqual(canonical("a/b/c/model-x"), "model-x")

    def test_date_suffix_removed(self):
        self.assertEqual(canonical("claude-sonnet-4-5-20250929"), "claude-sonnet-4-5")
        self.assertEqual(canonical("gpt-5.6-terra@20260901"), "gpt-5.6-terra")

    def test_prefix_and_date_combined(self):
        self.assertEqual(
            canonical("openrouter/z-ai/glm-5.3-flash-20260801"), "glm-5.3-flash"
        )

    def test_empty_and_none_are_unknown(self):
        self.assertEqual(canonical(""), UNKNOWN)
        self.assertEqual(canonical(None), UNKNOWN)
        self.assertEqual(canonical("///"), UNKNOWN)

    def test_no_similarity_matching(self):
        # 相近但不相同的名字不合并：glide-mock 证明逐字比较，不是模糊匹配。
        self.assertNotEqual(canonical("glm-5.3-flash"), canonical("glm-5.3-fash"))
        self.assertNotEqual(canonical("glm-4.7"), canonical("glm-5.3"))

    def test_non_string_input(self):
        self.assertEqual(canonical(123), "123")


class RawNamesRegistryTest(unittest.TestCase):
    def test_dedup_preserves_first_seen_order(self):
        seen, add = raw_names_registry()
        for raw in (
            "GLM-5.3-Flash",
            "GLM-5.3-Flash",
            "glm-5.3-flash",
            "zai-coding-plan/glm-5.3-flash",
        ):
            add(raw)
        # 原始写法逐字去重：大小写不同算两种写法（规范 §2.1「出现过的原始写法」）。
        self.assertEqual(
            seen, ["GLM-5.3-Flash", "glm-5.3-flash", "zai-coding-plan/glm-5.3-flash"]
        )

    def test_limit_five(self):
        seen, add = raw_names_registry()
        for index in range(8):
            add(f"name-{index}")
        self.assertEqual(seen, [f"name-{index}" for index in range(5)])

    def test_blank_ignored(self):
        seen, add = raw_names_registry()
        add("  ")
        add("ok")
        self.assertEqual(seen, ["ok"])


if __name__ == "__main__":
    unittest.main()
