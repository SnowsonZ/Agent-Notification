"""Model 名称规范化（用量金额规范 §2.1）。

只做确定性变换：去首尾空白、转小写、取最后一个 / 之后的部分、去掉末尾
8 位日期后缀。不做相似度匹配；结果为空一律记为 unknown。
"""

import re

UNKNOWN = "unknown"

_DATE_SUFFIX = re.compile(r"[-@]\d{8}$")


def canonical(raw):
    text = str(raw or "").strip().lower()
    if not text:
        return UNKNOWN
    if "/" in text:
        text = text.rsplit("/", 1)[1]
    text = _DATE_SUFFIX.sub("", text).strip()
    return text or UNKNOWN


def raw_names_registry(limit=5):
    """构造 raw_names 收集器：按首次出现顺序去重，最多保留 limit 个原始写法。"""
    seen = []

    def add(raw):
        text = str(raw or "").strip()
        if not text or text in seen or len(seen) >= limit:
            return
        seen.append(text)

    return seen, add
