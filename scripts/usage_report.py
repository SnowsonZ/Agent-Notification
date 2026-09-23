"""跨周期用量聚合（用量金额规范 §6）：day / week / month / all 与 `inbox usage` 输出。

周期内已定稿的过去日读缓存，未定稿/缺失的过去日与今天合并一次扫描（不触发
全量重扫）；金额是读取时的派生值，按周期聚合 models 后统一计价。
"""

import time
from datetime import date, timedelta

from daily_report import (
    NO_PROJECT,
    _finalized,
    build_report,
    finalize_day_report,
    load_report,
    scan_buckets,
)
from usage_cost import (
    cost_for_models,
    display_total,
    empty_cost,
    load_fx,
)


def period_bounds(period, anchor):
    """周期首尾（含端点）。周按 ISO 周一至周日，月按自然月，归日按本机时区。"""
    if period == "day":
        return anchor, anchor
    if period == "week":
        start = anchor - timedelta(days=anchor.weekday())
        return start, start + timedelta(days=6)
    if period == "month":
        start = anchor.replace(day=1)
        following = (start + timedelta(days=32)).replace(day=1)
        return start, following - timedelta(days=1)
    raise ValueError(f"unknown period: {period}")


def previous_anchor(period, anchor):
    if period == "day":
        return anchor - timedelta(days=1)
    if period == "week":
        return anchor - timedelta(days=7)
    return (anchor.replace(day=1) - timedelta(days=1)).replace(day=1)


def _iter_reports(store, home, first_day, last_day, shared=None):
    """{day: report}：已定稿读缓存，缺失/未定稿与今天合并扫描一次。

    shared 跨周期复用文件解析与今天的实时扫描（period=all 三个周期共用，
    避免同一天重复解析与扫描）。"""
    shared = shared if shared is not None else {"reports": {}, "live": {}}
    today = date.today()
    days = [
        first_day + timedelta(days=n) for n in range((last_day - first_day).days + 1)
    ]
    cached, pending = {}, []
    for day in days:
        if day > today:
            continue
        if day not in shared["reports"]:
            shared["reports"][day] = load_report(store.root, day.isoformat())
        report = shared["reports"][day]
        if day < today and _finalized(report, day):
            cached[day] = report
        else:
            pending.append(day)
    live = {}
    if pending:
        missing = [day for day in pending if day not in shared["live"]]
        if missing:
            buckets = scan_buckets(store, home, min(missing), max(missing))
            for day in missing:
                records = buckets.get(day, [])
                report = build_report(day, records, time.time())
                if day < today:
                    # 过去日（含无消耗日）固化，避免每次调用重复扫描无报告窗口；
                    # 走迁移关口：该日存在 v7 时按 §3.1 备份并对比。
                    finalize_day_report(store.root, day, report)
                    shared["reports"][day] = report
                shared["live"][day] = report
        for day in pending:
            if day in shared["live"]:
                live[day] = shared["live"][day]
    return {
        day: cached.get(day) or live[day]
        for day in days
        if day in cached or day in live
    }


def _models_accumulator():
    return {}


def _merge_models(target, models):
    """合并 task 级 models：token 累加，raw_names 并集保序（至多 5），native 累加。"""
    for key, entry in (models or {}).items():
        bucket = target.setdefault(
            key,
            {
                "fresh_input": 0,
                "cache_write": 0,
                "cache_read": 0,
                "output": 0,
                "native_cost_usd": None,
                "_raw": [],
            },
        )
        for name in ("fresh_input", "cache_write", "cache_read", "output"):
            bucket[name] += int(entry.get(name) or 0)
        cost = entry.get("native_cost_usd")
        if cost is not None:
            bucket["native_cost_usd"] = (bucket["native_cost_usd"] or 0.0) + float(cost)
        for raw in entry.get("raw_names") or ():
            if raw not in bucket["_raw"] and len(bucket["_raw"]) < 5:
                bucket["_raw"].append(raw)
    return target


def _public_models(models):
    output = {}
    for key, entry in models.items():
        public = {
            name: entry[name]
            for name in ("fresh_input", "cache_write", "cache_read", "output")
        }
        public["native_cost_usd"] = entry["native_cost_usd"]
        if entry["_raw"]:
            public["raw_names"] = list(entry["_raw"])
        output[key] = public
    return output


def _dimension_rows(groups, tables, day, fx_rate):
    """by 维度行：{key, total_tokens, cost}，按 CNY 视图金额降序、同额按 token 降序。"""
    rows = []
    for key, models in groups.items():
        tokens = sum(
            int(entry.get(name) or 0)
            for entry in models.values()
            for name in ("fresh_input", "cache_write", "cache_read", "output")
        )
        cost, _unpriced, _notes = cost_for_models(_public_models(models), day, tables)
        rows.append(
            {
                "key": key,
                "total_tokens": tokens,
                "cost": cost,
                "_sort": display_total(cost, "CNY", fx_rate),
            }
        )
    rows.sort(key=lambda row: (-row["_sort"], -row["total_tokens"], row["key"]))
    for row in rows:
        row.pop("_sort")
    return rows


def collect(store, home, period, anchor_day=None, tables=None, shared=None):
    """`inbox usage` 单周期输出（§6）。period=all 返回 {day, week, month}。"""
    from usage_cost import PricingTables

    tables = tables or PricingTables.load(store.root)
    anchor = anchor_day or date.today()
    if period == "all":
        shared = {"reports": {}, "live": {}}
        return {
            name: collect(store, home, name, anchor, tables=tables, shared=shared)
            for name in ("day", "week", "month")
        }

    first_day, last_day = period_bounds(period, anchor)
    reports = _iter_reports(store, home, first_day, last_day, shared)
    fx = load_fx(store.root)

    totals = {
        "input_tokens": 0,
        "cache_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "tasks": 0,
    }
    models = _models_accumulator()
    harness_models, project_models = {}, {}
    series = []
    for day in sorted(reports):
        report = reports[day]
        day_totals = report.get("totals", {})
        for name in (
            "input_tokens",
            "cache_tokens",
            "output_tokens",
            "total_tokens",
            "tasks",
        ):
            totals[name] += int(day_totals.get(name) or 0)
        day_models = _models_accumulator()
        for task in report.get("tasks") or []:
            _merge_models(day_models, task.get("models"))
            harness = harness_models.setdefault(task["provider"], _models_accumulator())
            _merge_models(harness, task.get("models"))
            project = project_models.setdefault(
                task["project"] or NO_PROJECT, _models_accumulator()
            )
            _merge_models(project, task.get("models"))
        _merge_models(models, day_models)
        day_cost, _unpriced, _notes = cost_for_models(
            _public_models(day_models), day, tables
        )
        series.append(
            {
                "date": day.isoformat(),
                "total_tokens": int(day_totals.get("total_tokens") or 0),
                "input_tokens": int(day_totals.get("input_tokens") or 0),
                "cache_tokens": int(day_totals.get("cache_tokens") or 0),
                "output_tokens": int(day_totals.get("output_tokens") or 0),
                "cost": day_cost,
            }
        )

    cost, unpriced_models, _notes = cost_for_models(
        _public_models(models), anchor, tables
    )
    totals["cost"] = cost

    previous_totals = {"total_tokens": 0, "cost": empty_cost()}
    prev_first, prev_last = period_bounds(period, previous_anchor(period, anchor))
    previous_reports = _iter_reports(store, home, prev_first, prev_last, shared)
    prev_models = _models_accumulator()
    for day in sorted(previous_reports):
        day_totals = previous_reports[day].get("totals", {})
        previous_totals["total_tokens"] += int(day_totals.get("total_tokens") or 0)
        for task in previous_reports[day].get("tasks") or []:
            _merge_models(prev_models, task.get("models"))
    prev_cost, _prev_unpriced, _prev_notes = cost_for_models(
        _public_models(prev_models), prev_first, tables
    )
    previous_totals["cost"] = prev_cost

    return {
        "period": period,
        "start": first_day.isoformat(),
        "end": last_day.isoformat(),
        "is_current": last_day >= date.today(),
        "generated_at": round(time.time()),
        "totals": totals,
        "previous": previous_totals,
        "series": series,
        "by": {
            "harness": _dimension_rows(harness_models, tables, anchor, fx["USD_CNY"]),
            "model": _dimension_rows(
                _per_key_buckets(models), tables, anchor, fx["USD_CNY"]
            ),
            "project": _dimension_rows(project_models, tables, anchor, fx["USD_CNY"]),
        },
        "fx": fx,
        "pricing": {
            "fetched_at": load_state_fetched_at(store.root),
            "unpriced_models": unpriced_models,
        },
    }


def _per_key_buckets(models):
    """by.model 的分组：每个 model 一个桶（桶内容即该 model 的聚合条目）。"""
    return {key: {key: entry} for key, entry in models.items()}


def load_state_fetched_at(state_root):
    from pricing_fetch import load_state

    return load_state(state_root)["last_success"]
