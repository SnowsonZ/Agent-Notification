"""LiteLLM 公开价格拉取、转换防护与自适应节奏（用量金额规范 §4.4–§4.5）。

网络请求只有对上游价格 JSON 的一个 GET，超时 20 秒，不携带任何本机数据；
转换与状态机全部可注入时钟与传输函数，单测不触网。
"""

import hashlib
import json
import math
import time
import urllib.request
from datetime import date
from pathlib import Path

SOURCE_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)
PRICE_FIELDS = ("input", "cache_write", "cache_read", "output")
FIELD_MAP = {
    "input_cost_per_token": "input",
    "output_cost_per_token": "output",
    "cache_read_input_token_cost": "cache_read",
    "cache_creation_input_token_cost": "cache_write",
}
KEEP_MODES = {"chat", "responses"}
DAY_SECONDS = 86_400


def _read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def _price_jump(old, new):
    """与旧值相比任一单价变化超过 10 倍（§4.4 防护；双向都算）。"""
    for field in PRICE_FIELDS:
        old_value, new_value = old.get(field), new.get(field)
        if old_value and new_value:
            ratio = new_value / old_value
            if ratio > 10 or ratio < 0.1:
                return True
    return False


def transform(raw, previous=None, today=None):
    """上游 JSON → 价格条目（§4.4）。返回 (entries, guards)。

    只保留 mode ∈ {chat, responses} 且有 input_cost_per_token 的条目；单价的
    负数/非有限值丢弃该条目；与旧值相比跳变超 10 倍的沿用旧值并记入 guards。"""
    today = today or date.today()
    previous = previous if isinstance(previous, dict) else {}
    entries, guards = {}, []

    def name_key(raw_name):
        return str(raw_name).strip().lower()
    for key, value in raw.items():
        if not isinstance(value, dict) or value.get("mode") not in KEEP_MODES:
            continue
        if value.get("input_cost_per_token") is None:
            continue
        prices, bad = {}, False
        for source, target in FIELD_MAP.items():
            number = value.get(source)
            if number is None:
                continue
            number = float(number) * 1e6
            if not math.isfinite(number) or number < 0:
                bad = True
                break
            prices[target] = number
        if bad or "input" not in prices:
            # R19（§4.4 修订）：非法数值同样保留上一版条目（含 history），
            # 上一版没有该条目时才丢弃（丢弃也记原因）。
            key_name = name_key(key)
            old = previous.get(key_name)
            if isinstance(old, dict):
                entries[key_name] = json.loads(json.dumps(old))
                guards.append(f"{key}: invalid price, kept old value")
            else:
                guards.append(f"{key}: invalid price, dropped (no previous entry)")
            continue
        name = str(key).strip().lower()
        if not name:
            continue
        prices["currency"] = "USD"
        if "/" in name and name.rsplit("/", 1)[1]:
            prices["aliases"] = [name.rsplit("/", 1)[1]]
        old = previous.get(name)
        if isinstance(old, dict) and _price_jump(old, prices):
            # 防护（§4.4 修订）：原样保留上一版条目（含 history），不删除。
            if isinstance(old, dict):
                entries[name] = json.loads(json.dumps(old))
            guards.append(f"{name}: price jump >10x, kept old value")
            continue
        if isinstance(old, dict):
            if any(old.get(field) != prices.get(field) for field in PRICE_FIELDS):
                # 单价变化：旧价追加进 history（until=本次生效日期）。
                history = list(old.get("history") or [])
                history.append(
                    {
                        "until": today.isoformat(),
                        **{
                            field: old[field]
                            for field in PRICE_FIELDS
                            if old.get(field) is not None
                        },
                    }
                )
                prices["history"] = history
            else:
                # 单价不变：原样继承旧条目的 history（§4.4 修订），否则历史丢失
                # 且会被误判为内容变化。
                if old.get("history"):
                    prices["history"] = old["history"]
        entries[name] = prices
    entries, guards = _drop_conflicting_aliases(entries, guards)
    return entries, guards


def _drop_conflicting_aliases(entries, guards):
    """别名只有在所有指向它的条目四项单价完全相同时才保留（§4.4 修订）；
    有分歧的别名全部丢弃并记入 guards（pricing check 展示）。"""
    alias_owners = {}
    for name, entry in entries.items():
        for alias in entry.get("aliases") or []:
            alias_owners.setdefault(alias, []).append(name)
    for alias, owners in alias_owners.items():
        if len(owners) < 2:
            continue
        reference = entries[owners[0]]
        consistent = all(
            all(entries[name].get(field) == reference.get(field) for field in PRICE_FIELDS)
            for name in owners[1:]
        )
        if consistent:
            continue
        for name in owners:
            aliases = entries[name].get("aliases") or []
            entries[name]["aliases"] = [item for item in aliases if item != alias]
        guards.append(f"alias {alias}: conflicting prices dropped ({', '.join(owners)})")
    return entries, guards


def _default_download(url, timeout):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        if getattr(response, "status", 200) != 200:
            raise ValueError(f"http status {response.status}")
        return response.read()


def load_state(state_root):
    data = _read_json(Path(state_root) / "pricing" / "fetch-state.json") or {}
    try:
        interval = int(data.get("interval_days"))
    except (TypeError, ValueError):
        interval = 7
    return {
        "interval_days": interval if interval > 0 else 7,
        "same_streak": int(data.get("same_streak") or 0),
        "last_success": float(data.get("last_success") or 0),
        "next_due": float(data.get("next_due") or 0),
        "content_hash": str(data.get("content_hash") or ""),
        "last_error": str(data.get("last_error") or ""),
    }


def _save_state(state_root, state):
    directory = Path(state_root) / "pricing"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / "fetch-state.json"
    temporary = path.with_name("." + path.name + ".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True) + "\n")
    temporary.replace(path)


def _save_fetched(state_root, entries):
    directory = Path(state_root) / "pricing"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / "fetched.json"
    temporary = path.with_name("." + path.name + ".tmp")
    temporary.write_text(
        json.dumps(entries, ensure_ascii=False, sort_keys=True, indent=1) + "\n"
    )
    temporary.replace(path)


def fetch(
    state_root,
    *,
    now=None,
    auto=False,
    timeout=20,
    url=SOURCE_URL,
    download=None,
    today=None,
):
    """拉取一次并按 §4.5 更新状态；返回状态摘要。

    auto=True 时未到期直接跳过（App 后台触发只有一个入口）；手动调用不受到期
    限制。download 可注入（单测不触网）。"""
    now = time.time() if now is None else now
    today = today or date.today()
    state = load_state(state_root)
    if auto and now < state["next_due"]:
        return {"skipped": True, "reason": "not due"}
    try:
        body = (download or _default_download)(url, timeout)
        raw = json.loads(body)
        if not isinstance(raw, dict):
            raise TypeError("payload is not a JSON object")
    except (OSError, ValueError, TypeError) as error:
        # 网络/解析失败：整次拉取算失败（§4.4）
        # HTTPError/URLError 是 OSError 子类，JSON 解析失败是 ValueError。
        state["next_due"] = now + DAY_SECONDS
        state["last_error"] = f"{type(error).__name__}: {error}"
        _save_state(state_root, state)
        return {"skipped": False, "ok": False, "error": state["last_error"]}

    previous = _read_json(Path(state_root) / "pricing" / "fetched.json")
    entries, guards = transform(raw, previous, today)
    digest = hashlib.sha256(
        json.dumps(entries, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    if digest == state["content_hash"]:
        state["same_streak"] += 1
        if state["same_streak"] >= 2:
            state["interval_days"] = min(state["interval_days"] * 2, 30)
            state["same_streak"] = 0
        changed = False
    else:
        state["interval_days"] = 7
        state["same_streak"] = 0
        changed = True
        _save_fetched(state_root, entries)
    state["last_success"] = now
    state["next_due"] = now + state["interval_days"] * DAY_SECONDS
    state["content_hash"] = digest
    state["last_error"] = "; ".join(guards)
    _save_state(state_root, state)
    return {
        "skipped": False,
        "ok": True,
        "changed": changed,
        "interval_days": state["interval_days"],
        "same_streak": state["same_streak"],
        "guards": guards,
    }
