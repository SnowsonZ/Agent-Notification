"""Swift 模型解码键名检查（源码级，Linux 也能跑）。

H0926-7：native/ 下 Decodable 结构体统一用 JSONDecoder 的 convertFromSnakeCase
解码，JSON 键 `session_id` 转出的键是 `sessionId`；属性名里出现连续大写
（如 `sessionID`、`URL`）就对不上键，可选属性不报错、静默为 nil。这里静态
拦截这类命名：除非结构体显式声明了 CodingKeys（自己负责映射），存储属性名
不允许出现连续大写。
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DECODABLE_STRUCT_RE = re.compile(r"\bstruct\s+(?P<name>\w+)\s*:[^{]*\b(?:Decodable|Codable)\b[^{]*\{")
PROPERTY_RE = re.compile(r"\b(?:let|var)\s+(?P<name>\w+)\s*:")
CONSECUTIVE_UPPERCASE_RE = re.compile(r"[A-Z]{2}")
CODING_KEYS_RE = re.compile(r"^\s*enum\s+CodingKeys\b")
NESTED_DECLARATION_RE = re.compile(r"^\s*(?:struct|enum|extension|class|actor|func|init)\b")


def top_level_members(body: str) -> list[str]:
    """按花括号深度切出结构体第一层成员（语句分隔符：换行与分号）。"""
    members: list[str] = []
    depth = 0
    current = ""
    for char in body:
        if depth == 0 and char in ";\n":
            members.append(current)
            current = ""
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        current += char
    members.append(current)
    return members


def iter_decodable_structs(source: str):
    """yield (结构体名, 花括号配对的声明体)。"""
    for match in DECODABLE_STRUCT_RE.finditer(source):
        opening = match.end() - 1
        depth = 0
        for index in range(opening, len(source)):
            if source[index] == "{":
                depth += 1
            elif source[index] == "}":
                depth -= 1
                if depth == 0:
                    yield match.group("name"), source[opening + 1:index]
                    break


def is_stored_property(member: str, match: re.Match) -> bool:
    """计算属性（var id: String { ... }）不参与解码，不算。"""
    head = member[match.end():].split("\n", 1)[0].split(";", 1)[0]
    return "=" in head or "{" not in head


class SwiftDecodablePropertyNamingTest(unittest.TestCase):
    """H0926-7：convertFromSnakeCase 解码下属性名不允许连续大写，除非显式声明 CodingKeys。"""

    def test_no_consecutive_uppercase_properties_without_coding_keys(self):
        problems = []
        for path in sorted((ROOT / "native").rglob("*.swift")):
            source = path.read_text(encoding="utf-8")
            for struct_name, body in iter_decodable_structs(source):
                members = top_level_members(body)
                explicit_keys = any(CODING_KEYS_RE.match(member) for member in members)
                if explicit_keys:
                    continue
                for member in members:
                    # 嵌套类型与方法体单独判定（Decodable 的嵌套结构体会被再次扫到）。
                    if NESTED_DECLARATION_RE.match(member):
                        continue
                    for match in PROPERTY_RE.finditer(member):
                        property_name = match.group("name")
                        if CONSECUTIVE_UPPERCASE_RE.search(property_name) and is_stored_property(member, match):
                            relative = path.relative_to(ROOT)
                            problems.append(
                                f"{relative}: {struct_name}.{property_name} 含连续大写，"
                                "convertFromSnakeCase 转不出该键；改名或显式声明 CodingKeys"
                            )
        self.assertEqual(
            problems,
            [],
            "发现 convertFromSnakeCase 解码下对不上键的属性名（H0926-7）：\n" + "\n".join(problems),
        )


if __name__ == "__main__":
    unittest.main()
