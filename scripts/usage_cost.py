"""价格查找与金额计算（用量金额规范 §4–§5）。

三层价格表：用户覆盖 > 国产官方（随包）> 公开价格（自动拉取，缺失回退随包快照）。
金额按原币分币种存放，不在计算层换算；报告不写金额，金额是读取时的派生值。
"""

import json
from datetime import date, datetime
from pathlib import Path

from model_names import UNKNOWN

DEFAULT_FX = 7.10
MILLION = 1_000_000
MODEL_KEYS = ("fresh_input", "cache_write", "cache_read", "output")
COST_CLASSES = ("input", "cache", "output")


def _load_json(path):
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


class PricingTables:
    """按层保存价格表；匹配主键与 aliases 均为小写精确比较（§4.2）。"""

    def __init__(self, override=None, official=None, fetched=None, snapshot=None):
        self.layers = (
            ("override", override or {}),
            ("official", official or {}),
            ("fetched", fetched or {}),
            ("snapshot", snapshot or {}),
        )

    @classmethod
    def load(cls, state_root, package_dir=None):
        state_root = Path(state_root)
        package_dir = (
            Path(package_dir) if package_dir else Path(__file__).parent / "pricing"
        )
        pricing_dir = state_root / "pricing"
        fetched = _load_json(pricing_dir / "fetched.json")
        return cls(
            override=_load_json(pricing_dir / "override.json") or {},
            official=_load_json(package_dir / "cny-official.json") or {},
            fetched=fetched,
            snapshot=(
                None
                if fetched is not None
                else _load_json(package_dir / "litellm-snapshot.json")
            ),
        )

    @staticmethod
    def _matches(entry, candidate):
        if not isinstance(entry, dict):
            return False
        aliases = entry.get("aliases") or []
        return any(str(alias).strip().lower() == candidate for alias in aliases)

    def lookup(self, candidate):
        """单候选名按「覆盖 > 官方 > 拉取 > 快照」查找（主键或 aliases 精确相等）。"""
        for name, data in self.layers:
            entry = data.get(candidate)
            if isinstance(entry, dict):
                return entry, name
            for other in data.values():
                if isinstance(other, dict) and self._matches(other, candidate):
                    return other, name
        return None, None


def candidate_names(model_key, raw_names):
    """查找候选名（§4.2）：① 规范名；② raw 原样（小写）；③ raw 去最后一个 / 前缀。"""
    names = [model_key]
    for raw in raw_names or ():
        text = str(raw or "").strip().lower()
        if text and text not in names:
            names.append(text)
        if "/" in text:
            tail = text.rsplit("/", 1)[1]
            if tail and tail not in names:
                names.append(tail)
    return [name for name in names if name and name != UNKNOWN]


def price_for_day(entry, day):
    """某天适用的四项单价（§4.1：history 按 until 升序，取第一个 until > D 的段）。

    返回 (prices, notes)；缓存写入/读取缺失时按 input 价计（§4.1 回退，
    notes 供 pricing check 标注）。"""
    notes = []
    price_keys = ("input", "cache_write", "cache_read", "output")
    base = {key: entry.get(key) for key in price_keys}
    for segment in entry.get("history") or []:
        try:
            until_day = datetime.strptime(str(segment.get("until")), "%Y-%m-%d").date()
        except ValueError:
            continue
        if until_day > day:
            for key in price_keys:
                if segment.get(key) is not None:
                    base[key] = segment[key]
            break
    input_price = base["input"]
    if input_price is not None:
        for key in ("cache_write", "cache_read"):
            if base[key] is None:
                base[key] = input_price
                notes.append(f"{key} falls back to input price")
    return base, notes


def money_text(value, currency="CNY"):
    """金额文字（§5），与 Swift moneyText 同规则、测试用例相同。

    `$1,234.56` / `¥1,234.56` 两位小数千分位；`0 < value < 0.01` 显示 `<$0.01`；
    等于 0 显示 `$0.00`；value 为 None（无可定价 token）显示 `—`。"""
    if value is None:
        return "—"
    symbol = "$" if currency == "USD" else "¥"
    if abs(value) < 1e-9:
        return f"{symbol}0.00"
    if abs(value) < 0.01:
        return f"<{symbol}0.01"
    return f"{symbol}{value:,.2f}"


def empty_cost():
    """金额对象（§5）：按原币分币种存放。native_fallback 仅在发生原生兜底时出现
    （§4.3 的 model 级金额不属于三类，PR 偏离与疑点已说明）。"""
    return {
        "input": {"USD": 0.0, "CNY": 0.0},
        "cache": {"USD": 0.0, "CNY": 0.0},
        "output": {"USD": 0.0, "CNY": 0.0},
        "unpriced_tokens": 0,
    }


def _add(target, class_name, currency, amount):
    bucket = target.setdefault(class_name, {"USD": 0.0, "CNY": 0.0})
    bucket[currency] = bucket.get(currency, 0.0) + amount


def merge_cost(target, addition):
    """把 addition 累加进 target 金额对象（就地修改并返回）。"""
    for class_name in COST_CLASSES:
        for currency, amount in (addition.get(class_name) or {}).items():
            _add(target, class_name, currency, amount)
    target["unpriced_tokens"] = int(target.get("unpriced_tokens") or 0) + int(
        addition.get("unpriced_tokens") or 0
    )
    for key, value in (addition.get("native_fallback") or {}).items():
        bucket = target.setdefault("native_fallback", {"USD": 0.0, "CNY": 0.0})
        bucket[key] = bucket.get(key, 0.0) + float(value)
    return target


def model_cost(tokens, entry, day):
    """单 model 单日金额（§5）：金额 = token × 每百万单价，按条目原币存放。

    entry 为 None 表示未定价：token 计入 unpriced_tokens，不产生金额。"""
    cost = empty_cost()
    if entry is None:
        cost["unpriced_tokens"] = sum(int(tokens.get(key) or 0) for key in MODEL_KEYS)
        return cost
    prices, _notes = price_for_day(entry, day)
    currency = str(entry.get("currency") or "USD").upper()
    amounts = {
        "input": (
            int(tokens.get("fresh_input") or 0) * (prices["input"] or 0.0)
            + int(tokens.get("cache_write") or 0) * (prices["cache_write"] or 0.0)
        )
        / MILLION,
        "cache": int(tokens.get("cache_read") or 0)
        * (prices["cache_read"] or 0.0)
        / MILLION,
        "output": int(tokens.get("output") or 0) * (prices["output"] or 0.0) / MILLION,
    }
    for class_name, amount in amounts.items():
        _add(cost, class_name, currency, amount)
    return cost


def cost_for_models(models, day, tables):
    """报告级金额：遍历 models（canonical → 四项 + raw_names + native_cost_usd）。

    返回 (cost, unpriced_models, notes)。查找（§4.2）逐候选名跨层尝试；
    未命中但有 native_cost_usd 时按该美元金额兜底（§4.3）；unknown 一律未定价。"""
    cost = empty_cost()
    unpriced_models, notes = [], []
    for key, entry in (models or {}).items():
        tokens = {name: int(entry.get(name) or 0) for name in MODEL_KEYS}
        hit, layer = None, None
        if key != UNKNOWN:
            for candidate in candidate_names(key, entry.get("raw_names")):
                hit, layer = tables.lookup(candidate)
                if hit is not None:
                    break
        if hit is not None:
            merge_cost(cost, model_cost(tokens, hit, day))
            for note in price_for_day(hit, day)[1]:
                notes.append(f"{key} ({layer}): {note}")
        else:
            native = entry.get("native_cost_usd")
            if native is not None and key != UNKNOWN:
                bucket = cost.setdefault("native_fallback", {"USD": 0.0, "CNY": 0.0})
                bucket["USD"] = bucket.get("USD", 0.0) + float(native)
                notes.append(f"{key}: native cost fallback")
            else:
                cost["unpriced_tokens"] += sum(tokens.values())
                if key != UNKNOWN:
                    unpriced_models.append(key)
    return cost, sorted(set(unpriced_models)), notes


def convert_display(cost, currency, rate):
    """展示换算（§5）：CNY 视图 = USD×rate + CNY；USD 视图 = USD + CNY/rate。

    返回 (input, cache, output, native_fallback) 的单币种展示值。"""

    def mix(name):
        usd = float(cost.get(name, {}).get("USD") or 0.0)
        cny = float(cost.get(name, {}).get("CNY") or 0.0)
        return usd * rate + cny if currency == "CNY" else usd + cny / rate

    return mix("input"), mix("cache"), mix("output"), mix("native_fallback")


def display_total(cost, currency, rate):
    """合计展示值（三类 + native 兜底；未定价 token 不折算金额）。"""
    input_v, cache_v, output_v, fallback_v = convert_display(cost, currency, rate)
    return input_v + cache_v + output_v + fallback_v


def load_fx(state_root):
    path = Path(state_root) / "pricing" / "fx.json"
    data = _load_json(path) or {}
    try:
        rate = float(data.get("USD_CNY"))
    except (TypeError, ValueError):
        rate = DEFAULT_FX
    return {"USD_CNY": rate, "as_of": str(data.get("as_of") or "")}


def set_fx(state_root, rate):
    """写入手动汇率，as_of 自动记当天（§5）。"""
    rate = float(rate)
    if rate <= 0:
        raise ValueError("fx rate must be positive")
    directory = Path(state_root) / "pricing"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = {"USD_CNY": rate, "as_of": date.today().isoformat()}
    path = directory / "fx.json"
    temporary = path.with_name("." + path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False) + "\n")
    temporary.replace(path)
    return payload
