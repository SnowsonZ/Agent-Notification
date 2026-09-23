"""跨周期用量聚合（用量金额规范 §6）：day / week / month / all 与 `inbox usage` 输出。

周期内已定稿的过去日读缓存，未定稿/缺失的过去日与今天合并一次扫描（不触发
全量重扫）；金额是读取时的派生值（§5）：本期合计、各维度与 series 均按每天
适用价格逐日计价后累加，期内有调价时合计与逐天之和一致。
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
from usage_cost import cost_for_models, empty_cost, load_fx, merge_cost


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
            agent_stats = {}  # 补录也要带 agent 统计（agent_excluded 注脚，R12）
            buckets = scan_buckets(
                store, home, min(missing), max(missing), agent_stats=agent_stats
            )
            for day in missing:
                records = buckets.get(day, [])
                excluded = set(agent_stats.get(day, {}).get("tasks", set()))
                report = build_report(
                    day,
                    records,
                    time.time(),
                    agent_excluded=agent_stats.get(day),
                )
                if day < today:
                    # 过去日（含无消耗日）固化，避免每次调用重复扫描无报告窗口；
                    # 落盘走合并关口（§3.1.3/4：按任务合并且不降级）。
                    finalize_day_report(
                        store.root, day, report, excluded_sessions=excluded
                    )
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


def _dimension_rows(groups, day_costs, tables, fx_rate):
    """by 维度行：{key, total_tokens, cost}，按 CNY 视图金额降序、同额按 token 降序。

    day_costs: {group_key: 累计金额对象}——由 collect 按逐日计价预先累加（§5）。"""
    from usage_cost import display_total

    rows = []
    for key, models in groups.items():
        tokens = sum(
            int(entry.get(name) or 0)
            for entry in models.values()
            for name in ("fresh_input", "cache_write", "cache_read", "output")
        )
        cost = day_costs.get(key, empty_cost())
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


def _per_key_buckets(models):
    """by.model 的分组：每个 model 一个桶（组内容即该 model 的聚合条目）。"""
    return {key: {key: entry} for key, entry in models.items()}


def collect(store, home, period, anchor_day=None, tables=None, shared=None):
    """`inbox usage` 单周期输出（§6）。period=all 返回 {day, week, month}。"""
    from usage_cost import PricingTables

    tables = tables or PricingTables.load(store.root)
    anchor = anchor_day or date.today()
    if period == "all":
        shared = shared or {"reports": {}, "live": {}}
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
    harness_costs, project_costs, model_costs = {}, {}, {}
    totals_cost = empty_cost()
    unpriced_models = []
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
            provider = task["provider"]
            project = task["project"] or NO_PROJECT
            _merge_models(
                harness_models.setdefault(provider, _models_accumulator()),
                task.get("models"),
            )
            _merge_models(
                project_models.setdefault(project, _models_accumulator()),
                task.get("models"),
            )
            # 逐日取价（§5）：维度金额按每天适用价格逐 model 计价后累加（R11）。
            for key, entry in (task.get("models") or {}).items():
                single, _, _ = cost_for_models({key: entry}, day, tables)
                merge_cost(harness_costs.setdefault(provider, empty_cost()), single)
                merge_cost(project_costs.setdefault(project, empty_cost()), single)
                merge_cost(model_costs.setdefault(key, empty_cost()), single)
        _merge_models(models, day_models)
        public_day = _public_models(day_models)
        day_cost, day_unpriced, _notes = cost_for_models(public_day, day, tables)
        unpriced_models.extend(day_unpriced)
        merge_cost(totals_cost, day_cost)
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

    totals["cost"] = totals_cost

    previous_totals = {"total_tokens": 0, "cost": empty_cost()}
    prev_first, prev_last = period_bounds(period, previous_anchor(period, anchor))
    previous_reports = _iter_reports(store, home, prev_first, prev_last, shared)
    for day in sorted(previous_reports):
        day_totals = previous_reports[day].get("totals", {})
        previous_totals["total_tokens"] += int(day_totals.get("total_tokens") or 0)
        day_models = _models_accumulator()
        for task in previous_reports[day].get("tasks") or []:
            _merge_models(day_models, task.get("models"))
        # 上期同样逐日取价（§5/R11）。
        day_cost, _, _ = cost_for_models(_public_models(day_models), day, tables)
        merge_cost(previous_totals["cost"], day_cost)

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
            "harness": _dimension_rows(
                harness_models, harness_costs, tables, fx["USD_CNY"]
            ),
            "model": _dimension_rows(
                _per_key_buckets(models), model_costs, tables, fx["USD_CNY"]
            ),
            "project": _dimension_rows(
                project_models, project_costs, tables, fx["USD_CNY"]
            ),
        },
        "fx": fx,
        "pricing": {
            "fetched_at": load_state_fetched_at(store.root),
            "unpriced_models": sorted(set(unpriced_models)),
        },
    }


def load_state_fetched_at(state_root):
    from pricing_fetch import load_state

    return load_state(state_root)["last_success"]
