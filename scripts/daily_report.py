"""Daily work summary measured in tokens, aggregated from local session metadata.

token 三类口径：输入（新鲜输入+缓存写入）、缓存（缓存读取）、输出（含 reasoning）；
三类之和 = 合计，参与一切比较与计算（热力分级/排名/占比/趋势），三类在界面全部展示。
取消与出错轮次的消耗照计。只读取时间戳、标题、项目与数值字段，从不读取或存储
消息正文。过去日以报告文件固化（version=8），当日始终实时计算。

v8 在 v7 基础上新增逐 model 用量（schema 见用量金额规范 §3）：每个来源产出
model_raw 与四项原始 token（fresh_input/cache_write/cache_read/output），
三类由四项推出；金额不写入报告，是读取时的派生值。
"""

import json
import os
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from inbox_sources import seconds
from inbox_store import effective_origin
from model_names import UNKNOWN, canonical
from providers import DISPLAY_NAMES as PROVIDER_NAMES

WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
FIDELITY_NAMES = {"exact": "精确", "unavailable": "无 token"}
ZCODE_STATES = {
    "completed": "idle",
    "error": "failed",
    "running": "running",
    "waiting": "waiting",
}
NO_PROJECT = "(无项目)"
REPORT_VERSION = 8
REPORT_V7 = 7
SEGMENT_GAP = 15 * 60  # 逐条时间戳来源：相邻消息间隔不超过 15 分钟视为同一段活动
CLASSES = ("input_tokens", "cache_tokens", "output_tokens")


def cost_level(cny_amount, thresholds):
    """按金额的热力分级（§7）：阈值取近 182 天有消耗日的 50/75/90 分位，
    由 generate_overview 算出后传入；无阈值（样本不足）时全部 0。"""
    if cny_amount <= 0 or not thresholds or thresholds[0] <= 0:
        return 0
    if cny_amount < thresholds[0]:
        return 1
    if cny_amount < thresholds[1]:
        return 2
    if cny_amount < thresholds[2]:
        return 3
    return 4


def heat_level(total_tokens):
    """GitHub 式五级强度（三类 token 合计，按本机 91 个活跃日分布校准）：
    无记录 / <2000万 / <1亿 / <4亿 / ≥4亿。"""
    if total_tokens <= 0:
        return 0
    if total_tokens < 20_000_000:
        return 1
    if total_tokens < 100_000_000:
        return 2
    if total_tokens < 400_000_000:
        return 3
    return 4


def token_text(value):
    """与 Swift tokenText 同规则：k/M/B；mantissa<100 保留小数否则取整，末尾零去除。"""
    value = int(value or 0)
    if value <= 0:
        return "0"
    if value < 1_000:
        return str(value)
    for factor, unit, decimals in (
        (1_000_000_000, "B", 2),
        (1_000_000, "M", 1),
        (1_000, "k", 1),
    ):
        if value >= factor:
            mantissa = value / factor
            text = f"{mantissa:.{decimals}f}" if mantissa < 100 else f"{mantissa:.0f}"
            if "." in text:
                text = text.rstrip("0").rstrip(".")
            return text + unit
    return str(value)


def parse_day(text):
    return datetime.strptime(text, "%Y-%m-%d").date()


def day_bounds(day):
    start = datetime(day.year, day.month, day.day).timestamp()
    following = day + timedelta(days=1)
    return start, datetime(following.year, following.month, following.day).timestamp()


def clock_text(stamp):
    return datetime.fromtimestamp(stamp).strftime("%H:%M")


MODEL_KEYS = ("fresh_input", "cache_write", "cache_read", "output")


def _empty_models_entry():
    return {
        "fresh_input": 0.0,
        "cache_write": 0.0,
        "cache_read": 0.0,
        "output": 0.0,
        "native_cost_usd": None,
        "_raw_names": [],
    }


class _Buckets:
    """Accumulates per-day task records during one read-only source scan."""

    def __init__(self, days):
        self.windows = {day: day_bounds(day) for day in days}
        self.tasks = {day: {} for day in days}

    def _task(self, day, provider, sid, title, project, fidelity, state):
        tasks = self.tasks[day]
        key = (provider, sid)
        task = tasks.get(key)
        if task is None:
            task = {
                "provider": provider,
                "session_id": sid,
                "title": title,
                "project": project,
                "fidelity": fidelity,
                "state": state,
                "first": None,
                "last": None,
                "input": 0.0,
                "cache": 0.0,
                "output": 0.0,
                "turns": 0,
                "segments": [],
                "models": {},
            }
            tasks[key] = task
        else:
            if title and not task["title"]:
                task["title"] = title
            if project and not task["project"]:
                task["project"] = project
            if state != "unknown" and task["state"] == "unknown":
                task["state"] = state
            if fidelity == "exact":
                task["fidelity"] = "exact"
        return task

    def _model(self, task, model_raw):
        """按规范名归并出该任务的 model 累加桶；原始写法逐字去重记入 raw_names（≤5）。"""
        models = task["models"]
        key = canonical(model_raw)
        entry = models.get(key)
        if entry is None:
            entry = _empty_models_entry()
            models[key] = entry
        raw = str(model_raw or "").strip()
        if raw and raw not in entry["_raw_names"] and len(entry["_raw_names"]) < 5:
            entry["_raw_names"].append(raw)
        return entry

    def _charge(
        self,
        task,
        *,
        fresh,
        cache_write,
        cache_read,
        output,
        model,
        native_cost,
        fraction=1.0,
    ):
        """四项原始 token 按分摊比例记入任务三类与 model 桶；三类由四项推出。"""
        task["input"] += (fresh + cache_write) * fraction
        task["cache"] += cache_read * fraction
        task["output"] += output * fraction
        if (
            model is None
            and not fresh
            and not cache_write
            and not cache_read
            and not output
        ):
            return
        entry = self._model(task, model)
        entry["fresh_input"] += fresh * fraction
        entry["cache_write"] += cache_write * fraction
        entry["cache_read"] += cache_read * fraction
        entry["output"] += output * fraction
        if native_cost is not None:
            entry["native_cost_usd"] = (entry["native_cost_usd"] or 0.0) + float(
                native_cost
            ) * fraction

    def spread(
        self,
        provider,
        sid,
        start,
        end,
        *,
        fresh=0,
        cache_write=0,
        cache_read=0,
        output=0,
        model=None,
        native_cost=None,
        title="",
        project="",
        fidelity="exact",
        state="unknown",
        turn_stamp=None,
        activity=None,
    ):
        """把一段区间（含四项原始 token 消耗）按天窗口交集比例分摊，跨零点不重复计数。

        activity：该区间内真实的请求时段列表；给出时节奏段只记这些（轮里等待用户批准/回答的
        空闲不算活动），否则整段视为活动。"""
        if end <= start:
            return
        for day, (low, high) in self.windows.items():
            first, last = max(start, low), min(end, high)
            if last <= first:
                continue
            task = self._task(day, provider, sid, title, project, fidelity, state)
            fraction = (last - first) / (end - start)
            self._charge(
                task,
                fresh=fresh,
                cache_write=cache_write,
                cache_read=cache_read,
                output=output,
                model=model,
                native_cost=native_cost,
                fraction=fraction,
            )
            if task["first"] is None or first < task["first"]:
                task["first"] = first
            if task["last"] is None or last > task["last"]:
                task["last"] = last
            for a_start, a_end in activity or [(start, end)]:
                a_first, a_last = max(a_start, low), min(a_end, high)
                if a_last >= a_first:
                    task["segments"].append([a_first, a_last])
            if turn_stamp is not None and low <= turn_stamp < high:
                task["turns"] += 1

    def point(
        self,
        provider,
        sid,
        stamp,
        *,
        fresh=0,
        cache_write=0,
        cache_read=0,
        output=0,
        model=None,
        native_cost=None,
        turn=False,
        title="",
        project="",
        fidelity="exact",
        state="unknown",
    ):
        """按消耗发生时刻归日（Codex/Pi/Kimi/Claude 的逐请求/逐消息口径）。"""
        day = datetime.fromtimestamp(stamp).date()
        if day not in self.windows:
            return
        task = self._task(day, provider, sid, title, project, fidelity, state)
        self._charge(
            task,
            fresh=fresh,
            cache_write=cache_write,
            cache_read=cache_read,
            output=output,
            model=model,
            native_cost=native_cost,
        )
        if task["first"] is None or stamp < task["first"]:
            task["first"] = stamp
        if task["last"] is None or stamp > task["last"]:
            task["last"] = stamp
        task["segments"].append([stamp, stamp])
        if turn:
            task["turns"] += 1


def merge_segments(segments, gap=SEGMENT_GAP):
    """把区间/时间点按时间排序后合并：重叠或间隔不超过 gap 的并为一段，供节奏带只画真实活动。"""
    merged = []
    for start, end in sorted(segments):
        if merged and start - merged[-1][1] <= gap:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [[round(start), round(end)] for start, end in merged]


def _zcode_data(home):
    """(entries, titles, tokenized)；entry=(sid, start, end, 四项, model_raw, count_turn, activity)。

    v8 逐请求计量（用量金额规范 §2）：按 turn 汇总 model_usage 的三类 token 与
    turn_usage 对账，一致才逐请求记录（model_raw=model_id，各自按请求区间分摊）；
    不一致或没有 model_usage 表时整轮退回轮级数据，model 记为 unknown。所有状态
    的请求都计入，包括出错、取消和重试。

    子代理轮次（sess_subagent_*）的 token 是独立消耗，经 session.parent_id 归属到
    父任务，但不计入父任务的轮次数。
    """
    index = home / ".zcode/v2/tasks-index.sqlite"
    titles = {}
    if index.exists():
        connection = sqlite3.connect(index.as_uri() + "?mode=ro", uri=True)
        try:
            for sid, title, project, status in connection.execute(
                "SELECT task_id,title,workspace_path,task_status FROM tasks"
            ):
                titles[sid] = (str(title or ""), project or "", str(status or ""))
        except sqlite3.Error:
            pass
        finally:
            connection.close()
    runtime = home / ".zcode/cli/db/db.sqlite"
    if not runtime.exists():
        return [], titles, False
    token_select = (
        "SELECT session_id,started_at,completed_at,input_tokens,cache_read_input_tokens,"
        "cache_creation_input_tokens,output_tokens,reasoning_tokens,turn_id FROM turn_usage"
    )
    minimal_select = "SELECT session_id,started_at,completed_at,NULL,NULL,NULL,NULL,NULL,turn_id FROM turn_usage"
    request_select = (
        "SELECT turn_id,session_id,model_id,input_tokens,cache_read_input_tokens,"
        "cache_creation_input_tokens,output_tokens,reasoning_tokens,started_at,completed_at "
        "FROM model_usage"
    )
    turns = []
    tokenized = True
    parents = {}
    requests = {}  # turn_id -> [请求行]：逐请求计量与对账；无该表为 None
    connection = sqlite3.connect(runtime.as_uri() + "?mode=ro", uri=True)
    try:
        try:
            rows = connection.execute(token_select).fetchall()
        except sqlite3.Error:
            tokenized = False
            rows = connection.execute(minimal_select).fetchall()
        try:
            parents = {
                sid: parent
                for sid, parent in connection.execute(
                    "SELECT id,parent_id FROM session"
                )
            }
        except sqlite3.Error:
            parents = {}
        try:
            for row in connection.execute(request_select):
                requests.setdefault(row[0], []).append(row[1:])
        except sqlite3.Error:
            requests = None
    finally:
        connection.close()
    now = time.time()

    def four(raw):
        """单条请求/轮的四项原始 token（规范 §2 Zcode 行）。"""
        inp, cache_read, cache_creation, output, reasoning = raw
        return (
            max(int(inp or 0) - int(cache_read or 0), 0),
            int(cache_creation or 0),
            max(int(cache_read or 0), 0),
            int(output or 0) + int(reasoning or 0),
        )

    def request_part(request):
        """request=(session_id, model_id, tokens(5), started_at, completed_at)。"""
        start = seconds(request[7])
        end = seconds(request[8]) if request[8] else None
        return request[1], four(request[2:7]), start, end

    for row in rows:
        sid, started, completed = row[0], row[1], row[2]
        is_subagent = isinstance(sid, str) and sid.startswith("sess_subagent_")
        target = parents.get(sid) if is_subagent else sid
        if not isinstance(target, str) or not target:
            if is_subagent:
                continue  # 无法归属的孤立子代理轮次，宁可不计也不猜归属。
            target = sid
        start = seconds(started)
        if start <= 0:
            continue
        end = seconds(completed) if completed else now
        count_turn = not is_subagent
        if not tokenized:
            turns.append(
                (target, start, end, None, None, None, None, None, count_turn, None)
            )
            continue
        turn_four = four(row[3:8])
        parts = [request_part(request) for request in (requests or {}).get(row[8], [])]
        # model 归属（规范 §2「Zcode 归属」2026-09-24 修订）：token 一律以轮级为准
        # （轮级 token 普遍大于逐请求之和，差额是未挂在该 turn_id 下的请求，逐项严格
        # 对账在真实数据上约 2/3 的轮失败）；model_usage 只用于确定 model。
        #   单一 model：整轮四项归它；多 model：按请求四项占比逐项拆分轮级 token，
        #   取整误差归占比最大的 model；无 model_usage：unknown。
        activity = [
            (part[2], part[3] or now)
            for part in parts
            if part[2] > 0 and (part[3] or now) >= part[2]
        ] or None
        if not parts:
            turns.append((target, start, end, *turn_four, None, count_turn, activity))
            continue
        request_sums = {}  # canonical -> 逐请求四项之和
        for model_id, req_four, _req_start, _req_end in parts:
            key = canonical(model_id)
            bucket = request_sums.setdefault(key, [0, 0, 0, 0])
            for index in range(4):
                bucket[index] += req_four[index]
        if len(request_sums) == 1:
            model_key = next(iter(request_sums))
            raw_models = [part[0] for part in parts if canonical(part[0]) == model_key]
            turns.append(
                (target, start, end, *turn_four, raw_models[0], count_turn, activity)
            )
            continue
        # 多 model：逐项按占比拆分轮级 token，取整误差归占比最大的 model。
        largest = max(request_sums, key=lambda key: sum(request_sums[key]))
        total_sums = [
            sum(bucket[index] for bucket in request_sums.values()) or 1
            for index in range(4)
        ]
        allocations = {
            key: [
                int(turn_four[index] * bucket[index] / total_sums[index])
                for index in range(4)
            ]
            for key, bucket in request_sums.items()
        }
        for index in range(4):
            remainder = turn_four[index] - sum(
                allocations[key][index] for key in allocations
            )
            allocations[largest][index] += remainder
        raw_by_key = {}
        for model_id, _req_four, _req_start, _req_end in parts:
            raw_by_key.setdefault(canonical(model_id), model_id)
        for key, values in allocations.items():
            if not any(values):
                continue  # 占比为零的 model 不产生记录（token 全在其它 model）。
            turns.append(
                (
                    target,
                    start,
                    end,
                    *values,
                    raw_by_key.get(key) or key,
                    count_turn and key == largest,  # 轮次数每轮只记一次
                    activity,
                )
            )
    return turns, titles, tokenized


def _codex_data(home, window_start):
    """token_count 增量按请求时刻归日：(events, turn_starts, titles, projects)。

    来源与收件箱同口径：session_meta + 任意非空 originator（Desktop 与 CLI/exec/
    workbench 变体），2026-09-17 起不再限 Desktop——收件箱已纳入 CLI 会话而日报
    漏计，当日会话数对不上。
    """
    titles, projects = {}, {}
    index = home / ".codex/session_index.jsonl"
    if index.exists():
        with index.open(errors="replace") as file:
            for line in file:
                try:
                    record = json.loads(line)
                    titles[str(record["id"])] = str(record.get("thread_name") or "")
                except (ValueError, KeyError, TypeError):
                    continue
    root = home / ".codex/sessions"
    if not root.exists():
        return [], [], titles, projects
    events, turn_starts = [], []
    models = {}  # sid -> 该 sid 最近一条 turn_context 的 model（token_count 增量归属它）
    for path in root.rglob("rollout-*.jsonl"):
        try:
            if path.stat().st_mtime < window_start - 60:
                continue
            with path.open() as file:
                first = json.loads(file.readline())
            meta = first.get("payload", {})
            if first.get("type") != "session_meta" or not str(
                meta.get("originator") or ""
            ):
                continue
            sid = str(meta.get("id") or "")
            projects.setdefault(sid, str(meta.get("cwd") or ""))
        except (OSError, ValueError, KeyError, TypeError):
            continue
        try:
            with path.open(errors="replace") as file:
                for line in file:
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    kind = record.get("type")
                    if kind == "turn_context":
                        # 增量归属该增量之前最近一条 turn_context 的 model；
                        # 之前没有则记 None（报告为 unknown）。
                        models[sid] = (
                            str((record.get("payload") or {}).get("model") or "")
                            or None
                        )
                        continue
                    if kind != "event_msg":
                        continue
                    payload = record.get("payload") or {}
                    event_kind = payload.get("type")
                    stamp = seconds(record.get("timestamp"))
                    if stamp <= 0:
                        continue
                    if event_kind == "token_count":
                        if stamp < window_start:
                            continue
                        usage = (payload.get("info") or {}).get(
                            "last_token_usage"
                        ) or {}
                        # 字段互斥（input+cached+cacheW+output+reasoning=total，已实测验证）。
                        events.append(
                            (
                                sid,
                                stamp,
                                int(usage.get("input_tokens") or 0),
                                int(usage.get("cache_write_input_tokens") or 0),
                                int(usage.get("cached_input_tokens") or 0),
                                int(usage.get("output_tokens") or 0)
                                + int(usage.get("reasoning_output_tokens") or 0),
                                models.get(sid),
                            )
                        )
                    elif event_kind == "task_started" and stamp >= window_start:
                        turn_starts.append((sid, stamp))
        except OSError:
            continue
    return events, turn_starts, titles, projects


def _claude_data(home, window_start):
    """桌面会话 + Claude Code 转写逐条 usage：(exact, missing)。

    exact=(sid, [(stamp, 输入, 缓存, 输出)], title, project)
    missing=(sid, start, end, title, project)  # 无转写或无窗口内消耗
    桌面登记的会话按 cliSessionId 配转写；不在登记表的 CLI 直启会话转写
    （2026-09-17 起补采，与收件箱 claude CLI 接入同口径）按文件 mtime 预筛后
    直接解析，标题/项目由 store 已知行补齐，读不到就留空。
    """
    projects_root = home / ".claude/projects"
    if not projects_root.exists():
        return [], []
    # Desktop 登记目录是可选数据源：纯 CLI 用户只有转写（2026-09-21 评审 R6），
    # 登记目录缺失时跳过登记匹配，CLI 直启转写扫描照常执行。
    root = home / "Library/Application Support/Claude/claude-code-sessions"

    def transcript_usage(path):
        records = []
        try:
            with path.open(errors="replace") as file:
                for line in file:
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    if record.get("type") != "assistant":
                        continue
                    message = record.get("message") or {}
                    usage = message.get("usage")
                    if not isinstance(usage, dict):
                        continue
                    stamp = seconds(record.get("timestamp"))
                    if stamp <= 0 or stamp < window_start:
                        continue
                    records.append(
                        (
                            stamp,
                            int(usage.get("input_tokens") or 0),
                            int(usage.get("cache_creation_input_tokens") or 0),
                            int(usage.get("cache_read_input_tokens") or 0),
                            int(usage.get("output_tokens") or 0),
                            str(message.get("model") or "") or None,
                        )
                    )
        except OSError:
            return []
        return records

    transcripts = {path.stem: path for path in projects_root.rglob("*.jsonl")}
    sessions = {}
    if root.exists():
        for path in root.rglob("local_*.json"):
            try:
                if path.stat().st_size > 4_000_000:
                    continue
                data = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            sid = data.get("cliSessionId")
            if not isinstance(sid, str) or not sid:
                continue
            last = seconds(data.get("lastActivityAt"))
            if last <= 0 or last < window_start:
                continue
            created = seconds(data.get("createdAt")) or last
            prior = sessions.get(sid)
            if prior is None or last > prior[2]:
                sessions[sid] = (data, created, last)
    exact, missing = [], []
    for sid, (data, created, last) in sessions.items():
        desktop = data.get("sessionId", "")
        if not isinstance(desktop, str) or not desktop.startswith("local_"):
            continue
        title = str(data.get("title") or "")
        project = str(data.get("cwd") or "")
        transcript = transcripts.get(sid)
        records = transcript_usage(transcript) if transcript is not None else []
        if records:
            exact.append((sid, records, title, project))
        else:
            missing.append((sid, min(created, last), last, title, project))
    for path in projects_root.rglob("*.jsonl"):
        if path.stem in sessions:
            continue
        try:
            if path.stat().st_mtime < window_start - 60:
                continue
        except OSError:
            continue
        records = transcript_usage(path)
        if records:
            exact.append((path.stem, records, "", ""))
    return exact, missing


def _pi_data(store, window_start):
    """受管理 Pi 会话的逐条 assistant usage：(sid, stamp, 四项, model, native_cost)。"""
    results = []
    for row in store.rows():
        if row["provider"] != "pi":
            continue
        registered = store.meta("pi-file:" + row["session_id"])
        if not isinstance(registered, str):
            continue
        path = Path(registered)
        if not path.exists() or path.stat().st_size > 32_000_000:
            continue
        try:
            with path.open(errors="replace") as file:
                for line in file:
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    message = record.get("message") or {}
                    usage = message.get("usage") if isinstance(message, dict) else None
                    if (
                        not isinstance(usage, dict)
                        or message.get("role") != "assistant"
                    ):
                        continue
                    stamp = seconds(record.get("timestamp"))
                    if stamp <= 0 or stamp < window_start:
                        continue
                    cost = usage.get("cost")
                    total = cost.get("total") if isinstance(cost, dict) else None
                    results.append(
                        (
                            row["session_id"],
                            stamp,
                            int(usage.get("input") or 0),
                            int(usage.get("cacheWrite") or 0),
                            int(usage.get("cacheRead") or 0),
                            int(usage.get("output") or 0)
                            + int(usage.get("reasoning") or 0),
                            str(message.get("model") or "") or None,
                            float(total) if isinstance(total, (int, float)) else None,
                        )
                    )
        except OSError:
            continue
    return results


def _kimi_data(home, window_start):
    """Kimi wire.jsonl 的 usage.record 逐轮消耗：(sid, stamp, 四项, model)。"""
    root = home / ".kimi-code/sessions"
    if not root.exists():
        return []
    results = []
    for path in root.rglob("wire.jsonl"):
        try:
            if path.stat().st_mtime < window_start - 60:
                continue
            sid = next(
                part[len("session_") :]
                for part in path.parts
                if part.startswith("session_")
            )
        except (OSError, StopIteration):
            continue
        try:
            with path.open(errors="replace") as file:
                for line in file:
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    if record.get("type") != "usage.record":
                        continue
                    usage = record.get("usage") or {}
                    stamp = int(record.get("time") or 0) / 1000
                    if stamp <= 0 or stamp < window_start:
                        continue
                    results.append(
                        (
                            sid,
                            stamp,
                            int(usage.get("inputOther") or 0),
                            int(usage.get("inputCacheCreation") or 0),
                            int(usage.get("inputCacheRead") or 0),
                            int(usage.get("output") or 0),
                            str(record.get("model") or "") or None,
                        )
                    )
        except OSError:
            continue
    return results


def _opencode_data(store, home, window_start):
    """opencode 逐条 assistant 消息 usage：(sid, stamp, 输入, 缓存, 输出)；附标题/项目。

    口径=受管理（2026-09-17 起，与 pi/kimi 同构、与收件箱对账）：只统计经包装器
    注册到收件箱的会话；workbench 等其它工具经 server 拉起的会话既不进收件箱也
    不计入日报（库内无创建者身份字段，实测 agent/workspace/metadata/delivery 均
    无信号）。子代理会话（parent_id 非空）的消息无法归属到父任务，按"宁可不计"
    跳过；消费时刻优先取回合完成时间（data.time.completed），缺失退回消息更新时间。"""
    managed = {
        row["session_id"] for row in store.rows() if row["provider"] == "opencode"
    }
    path = home / ".local/share/opencode/opencode.db"
    titles, projects = {}, {}
    if not managed or not path.exists():
        return [], titles, projects
    results = []
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        for sid, title, directory, parent in connection.execute(
            "SELECT id,title,directory,parent_id FROM session"
        ):
            if parent or sid not in managed:
                continue
            titles[sid] = title or ""
            projects[sid] = directory or ""
        for sid, updated, data in connection.execute(
            "SELECT session_id,time_updated,data FROM message"
        ):
            if sid not in titles:
                continue
            try:
                record = json.loads(data)
            except (ValueError, TypeError):
                continue
            if not isinstance(record, dict) or record.get("role") != "assistant":
                continue
            usage = (
                record.get("tokens") if isinstance(record.get("tokens"), dict) else {}
            )
            cache = usage.get("cache") if isinstance(usage.get("cache"), dict) else {}
            when = record.get("time") if isinstance(record.get("time"), dict) else {}
            stamp = seconds(when.get("completed") or updated)
            if stamp <= 0 or stamp < window_start:
                continue
            fresh = int(usage.get("input") or 0)
            cache_write = int(cache.get("write") or 0)
            cache_read = int(cache.get("read") or 0)
            output = int(usage.get("output") or 0) + int(usage.get("reasoning") or 0)
            if fresh + cache_write + cache_read + output <= 0:
                continue
            cost = record.get("cost")
            results.append(
                (
                    sid,
                    stamp,
                    fresh,
                    cache_write,
                    cache_read,
                    output,
                    str(record.get("modelID") or "") or None,
                    float(cost) if isinstance(cost, (int, float)) else None,
                )
            )
    except sqlite3.Error:
        return [], titles, projects
    finally:
        connection.close()
    return results, titles, projects


def scan_buckets(store, home, first_day, last_day, agent_stats=None):
    """一次只读扫描；返回 {day: [task records]}。

    agent 拉起的会话（store origin=agent，见 inbox_sources.HUMAN_ORIGINATORS 与
    inbox.claude_spawn_origin）不进日报；其会话数与 token 消耗累计进 agent_stats
    （{day: {'tasks': set, 'total_tokens': float}}，可选参数）供注脚展示，成本不失明。"""
    days = [
        first_day + timedelta(days=n) for n in range((last_day - first_day).days + 1)
    ]
    buckets = _Buckets(days)
    window_start = buckets.windows[first_day][0]
    store_rows = {(row["provider"], row["session_id"]): row for row in store.rows()}
    origin_rules = store.origin_rule_index()
    origin_overrides = store.origin_overrides()

    def known(provider, sid, title="", project=""):
        row = store_rows.get((provider, sid))
        state = row["state"] if row else "unknown"
        if not title and row:
            title = row["title"] or ""
        if not project and row:
            project = row["project"] or ""
        return title, project, state

    def excluded(provider, sid, stamp, tokens):
        """agent 会话返回 True 并把消耗记入注脚；无 store 行或有效 origin=user 放行。
        有效 origin：单条改判 > 目录规则（用户改判沉淀）优先于自动分类（评审 R5）。"""
        row = store_rows.get((provider, sid))
        if effective_origin(origin_rules, row, origin_overrides) != "agent":
            return False
        if agent_stats is not None and tokens > 0:
            for day, (low, high) in buckets.windows.items():
                if low <= stamp < high:
                    entry = agent_stats.setdefault(
                        day, {"tasks": set(), "total_tokens": 0.0}
                    )
                    entry["tasks"].add((provider, sid))
                    entry["total_tokens"] += tokens
                    break
        return True

    turns, titles, tokenized = _zcode_data(home)
    for (
        sid,
        start,
        end,
        fresh,
        cache_write,
        cache_read,
        output,
        model_raw,
        count_turn,
        activity,
    ) in turns:
        title, project, status = titles.get(sid, ("", "", ""))
        title, project, state = known("zcode", sid, title, project)
        buckets.spread(
            "zcode",
            sid,
            start,
            end,
            fresh=fresh or 0,
            cache_write=cache_write or 0,
            cache_read=cache_read or 0,
            output=output or 0,
            model=model_raw,
            title=title,
            project=project,
            fidelity="exact" if tokenized else "unavailable",
            state=ZCODE_STATES.get(status) or state,
            turn_stamp=start if count_turn else None,
            activity=activity,
        )

    events, turn_starts, titles, projects = _codex_data(home, window_start)
    for sid, stamp, fresh, cache_write, cache_read, output, model_raw in events:
        if excluded("codex", sid, stamp, fresh + cache_write + cache_read + output):
            continue
        title, project, state = known(
            "codex", sid, titles.get(sid, ""), projects.get(sid, "")
        )
        buckets.point(
            "codex",
            sid,
            stamp,
            fresh=fresh,
            cache_write=cache_write,
            cache_read=cache_read,
            output=output,
            model=model_raw,
            title=title,
            project=project,
            state=state,
        )
    for sid, stamp in turn_starts:
        if excluded("codex", sid, stamp, 0):
            continue
        title, project, state = known(
            "codex", sid, titles.get(sid, ""), projects.get(sid, "")
        )
        buckets.point(
            "codex", sid, stamp, turn=True, title=title, project=project, state=state
        )

    exact, missing = _claude_data(home, window_start)
    for sid, records, title, project in exact:
        for (
            record_stamp,
            fresh,
            cache_write,
            cache_read,
            output,
            model_raw,
        ) in records:
            if excluded(
                "claude", sid, record_stamp, fresh + cache_write + cache_read + output
            ):
                continue
            known_title, known_project, state = known("claude", sid, title, project)
            buckets.point(
                "claude",
                sid,
                record_stamp,
                fresh=fresh,
                cache_write=cache_write,
                cache_read=cache_read,
                output=output,
                model=model_raw,
                title=known_title,
                project=known_project,
                state=state,
            )
    for sid, start, end, title, project in missing:
        if excluded("claude", sid, start, 0):
            continue
        title, project, state = known("claude", sid, title, project)
        buckets.spread(
            "claude",
            sid,
            start,
            end,
            title=title,
            project=project,
            fidelity="unavailable",
            state=state,
        )

    for (
        sid,
        record_stamp,
        fresh,
        cache_write,
        cache_read,
        output,
        model_raw,
        native_cost,
    ) in _pi_data(store, window_start):
        if excluded("pi", sid, record_stamp, fresh + cache_write + cache_read + output):
            continue
        title, project, state = known("pi", sid)
        buckets.point(
            "pi",
            sid,
            record_stamp,
            fresh=fresh,
            cache_write=cache_write,
            cache_read=cache_read,
            output=output,
            model=model_raw,
            native_cost=native_cost,
            title=title,
            project=project,
            state=state,
        )

    for (
        sid,
        record_stamp,
        fresh,
        cache_write,
        cache_read,
        output,
        model_raw,
    ) in _kimi_data(home, window_start):
        if excluded(
            "kimi", sid, record_stamp, fresh + cache_write + cache_read + output
        ):
            continue
        title, project, state = known("kimi", sid)
        buckets.point(
            "kimi",
            sid,
            record_stamp,
            fresh=fresh,
            cache_write=cache_write,
            cache_read=cache_read,
            output=output,
            model=model_raw,
            title=title,
            project=project,
            state=state,
        )

    events, titles, projects = _opencode_data(store, home, window_start)
    for (
        sid,
        record_stamp,
        fresh,
        cache_write,
        cache_read,
        output,
        model_raw,
        native_cost,
    ) in events:
        if excluded(
            "opencode", sid, record_stamp, fresh + cache_write + cache_read + output
        ):
            continue
        title, project, state = known(
            "opencode", sid, titles.get(sid, ""), projects.get(sid, "")
        )
        buckets.point(
            "opencode",
            sid,
            record_stamp,
            fresh=fresh,
            cache_write=cache_write,
            cache_read=cache_read,
            output=output,
            model=model_raw,
            native_cost=native_cost,
            turn=True,
            title=title,
            project=project,
            state=state,
        )

    reports = {}
    for day, tasks in buckets.tasks.items():
        records = []
        for task in tasks.values():
            if task["first"] is None:
                continue
            input_tokens = round(task["input"])
            cache_tokens = round(task["cache"])
            output_tokens = round(task["output"])
            total = input_tokens + cache_tokens + output_tokens
            if total <= 0 and task["turns"] == 0 and task["fidelity"] != "unavailable":
                continue
            records.append(
                {
                    "provider": task["provider"],
                    "session_id": task["session_id"],
                    "title": task["title"]
                    or f"{PROVIDER_NAMES.get(task['provider'], task['provider'])} · {task['session_id'][:12]}",
                    "project": task["project"],
                    "first_at": round(task["first"]),
                    "last_at": round(task["last"]),
                    "input_tokens": input_tokens,
                    "cache_tokens": cache_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": total,
                    "turns": task["turns"],
                    "state": task["state"],
                    "fidelity": task["fidelity"],
                    "segments": merge_segments(task["segments"]),
                    "models": _public_models(task["models"]),
                }
            )
        records.sort(
            key=lambda item: (
                -item["total_tokens"],
                item["provider"],
                item["session_id"],
            )
        )
        reports[day] = records
    return reports


def _empty_usage():
    return {"input_tokens": 0, "cache_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def _public_models(models):
    """task 内部 model 桶 → 报告字段（规范 §3）：四项取整；raw_names 保序，至多 5 个。"""
    result = {}
    for key, entry in models.items():
        public = {
            "fresh_input": round(entry["fresh_input"]),
            "cache_write": round(entry["cache_write"]),
            "cache_read": round(entry["cache_read"]),
            "output": round(entry["output"]),
            "native_cost_usd": entry["native_cost_usd"],
        }
        if entry["_raw_names"]:
            public["raw_names"] = list(entry["_raw_names"])
        result[key] = public
    return result


def _merge_models(target, models):
    """task 的 models 归并进汇总（totals.models），native_cost_usd 有值才累加。"""
    for key, entry in (models or {}).items():
        bucket = target.setdefault(
            key,
            {
                "fresh_input": 0,
                "cache_write": 0,
                "cache_read": 0,
                "output": 0,
                "native_cost_usd": None,
            },
        )
        for name in MODEL_KEYS:
            bucket[name] += int(entry.get(name) or 0)
        cost = entry.get("native_cost_usd")
        if cost is not None:
            bucket["native_cost_usd"] = (bucket["native_cost_usd"] or 0.0) + float(cost)


def _add_usage(bucket, key, record):
    entry = bucket.setdefault(key, _empty_usage())
    entry["input_tokens"] += record["input_tokens"]
    entry["cache_tokens"] += record["cache_tokens"]
    entry["output_tokens"] += record["output_tokens"]
    entry["total_tokens"] += record["total_tokens"]


def build_report(day, records, generated_at, agent_excluded=None):
    sources, projects, models = {}, {}, {}
    totals = _empty_usage()
    for record in records:
        _add_usage(sources, record["provider"], record)
        _add_usage(projects, record["project"] or NO_PROJECT, record)
        _merge_models(models, record.get("models"))
        for key in CLASSES + ("total_tokens",):
            totals[key] += record[key]
    totals["tasks"] = len(records)
    totals["turns"] = sum(record["turns"] for record in records)
    totals["sources"] = sources
    totals["projects"] = projects
    totals["models"] = models
    report = {
        "version": REPORT_VERSION,
        "date": day.isoformat(),
        "generated_at": round(generated_at),
        "totals": totals,
        "tasks": records,
    }
    if agent_excluded and (
        agent_excluded.get("tasks") or agent_excluded.get("total_tokens")
    ):
        report["agent_excluded"] = {
            "tasks": len(agent_excluded.get("tasks", ())),
            "total_tokens": int(agent_excluded.get("total_tokens") or 0),
        }
    return report


def render_markdown(report):
    day = parse_day(report["date"])
    totals = report["totals"]
    lines = [f"# 工作日报 · {report['date']} {WEEKDAYS[day.weekday()]}", ""]
    lines += [
        (
            f"合计 {token_text(totals['total_tokens'])} tokens（输入 {token_text(totals['input_tokens'])} · "
            f"缓存 {token_text(totals['cache_tokens'])} · 输出 {token_text(totals['output_tokens'])}）"
            f" · {totals['tasks']} 个任务 · {totals['turns']} 轮"
        ),
        "",
    ]
    excluded = report.get("agent_excluded")
    if excluded:
        lines += [
            (
                f"> 另有 {excluded['tasks']} 个 agent 会话（其它工具拉起）合计 "
                f"{token_text(excluded['total_tokens'])} tokens 未计入。"
            ),
            "",
        ]
    if not report["tasks"]:
        lines += ["这一天没有会话记录。", ""]
    else:
        lines += ["## 来源", ""]
        for name, entry in sorted(
            totals["sources"].items(), key=lambda item: -item[1]["total_tokens"]
        ):
            lines.append(
                f"- {PROVIDER_NAMES.get(name, name)} 合计 {token_text(entry['total_tokens'])}"
                f"（输入 {token_text(entry['input_tokens'])} · 缓存 {token_text(entry['cache_tokens'])}"
                f" · 输出 {token_text(entry['output_tokens'])}）"
            )
        lines += ["", "## 项目", ""]
        for name, entry in sorted(
            totals["projects"].items(), key=lambda item: -item[1]["total_tokens"]
        ):
            lines.append(
                f"- {Path(name).name} 合计 {token_text(entry['total_tokens'])} tokens"
            )
        lines += [
            "",
            "## 任务",
            "",
            "| 任务 | 来源 | 项目 | 起止 | 输入 | 缓存 | 输出 | 合计 | 轮次 | 口径 |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        for record in report["tasks"]:
            span = (
                f"{clock_text(record['first_at'])}–{clock_text(record['last_at'])}"
                if record["last_at"] > record["first_at"]
                else clock_text(record["first_at"])
            )
            lines.append(
                "| {title} | {provider} | {project} | {span} | {inp} | {cache} | {out} | {total} | {turns} | {fidelity} |".format(
                    title=record["title"].replace("|", "\\|"),
                    provider=PROVIDER_NAMES.get(record["provider"], record["provider"]),
                    project=Path(record["project"]).name if record["project"] else "—",
                    span=span,
                    inp=token_text(record["input_tokens"]),
                    cache=token_text(record["cache_tokens"]),
                    out=token_text(record["output_tokens"]),
                    total=token_text(record["total_tokens"]),
                    turns=record["turns"] or "—",
                    fidelity=FIDELITY_NAMES[record["fidelity"]],
                )
            )
        lines += [""]
    lines += [
        (
            "> 三类之和参与全部统计：输入=新鲜输入+缓存写入，缓存=缓存读取，输出=含 reasoning；"
            "取消/出错轮次照计。数据仅来自本地会话元数据，不读取会话正文。"
        ),
        "",
    ]
    return "\n".join(lines)


# 金额是读取时的派生值（§5），不写入固化报告。
from usage_cost import (
    PricingTables,
    cost_for_models,
    empty_cost,
    load_fx,
    merge_cost,
)


def attach_costs(report, tables):
    """给 v8 报告附金额（§6，读取时派生，不写入固化报告）：task / totals /
    totals.sources / totals.projects / totals.models 各附金额对象。
    sources/projects/models 从 tasks 聚合，与任务级候选名（raw_names）语义一致。"""
    day = parse_day(report["date"])
    totals_cost = empty_cost()
    source_costs, project_costs, model_costs = {}, {}, {}
    notes, unpriced_models = [], []
    for task in report.get("tasks") or []:
        cost, unpriced, task_notes = cost_for_models(task.get("models"), day, tables)
        task["cost"] = cost
        notes.extend(task_notes)
        unpriced_models.extend(unpriced)
        merge_cost(totals_cost, cost)
        merge_cost(source_costs.setdefault(task["provider"], empty_cost()), cost)
        merge_cost(
            project_costs.setdefault(task["project"] or NO_PROJECT, empty_cost()),
            cost,
        )
        for key, entry in (task.get("models") or {}).items():
            single, _, _ = cost_for_models({key: entry}, day, tables)
            merge_cost(model_costs.setdefault(key, empty_cost()), single)
    totals = report.setdefault("totals", {})
    totals["cost"] = totals_cost
    for name, cost in source_costs.items():
        if name in (totals.get("sources") or {}):
            totals["sources"][name]["cost"] = cost
    for name, cost in project_costs.items():
        if name in (totals.get("projects") or {}):
            totals["projects"][name]["cost"] = cost
    for key, cost in model_costs.items():
        if key in (totals.get("models") or {}):
            totals["models"][key]["cost"] = cost
    return totals_cost, sorted(set(unpriced_models)), notes


def reports_dir(root):
    return Path(root) / "reports"


def _write_report(root, report):
    directory = reports_dir(root)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    payloads = (
        (directory / (report["date"] + ".md"), render_markdown(report)),
        (
            directory / (report["date"] + ".json"),
            json.dumps(report, ensure_ascii=False, indent=1) + "\n",
        ),
    )
    for path, body in payloads:
        temporary = path.with_name("." + path.name + ".tmp")
        temporary.write_text(body)
        os.replace(temporary, path)


def _read_report_version(root, date_text, version):
    path = reports_dir(root) / (date_text + ".json")
    try:
        report = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if (
        isinstance(report, dict)
        and report.get("version") == version
        and report.get("date") == date_text
    ):
        return report
    return None


def load_report(root, date_text):
    return _read_report_version(root, date_text, REPORT_VERSION)


def _backup_v7_reports(root):
    """首次遇到 v7 报告时整目录备份（规范 §3.1 只做一次）；目录已存在就跳过。"""
    directory = Path(root) / "reports.v7.bak"
    if directory.exists():
        return
    directory.mkdir(parents=True, mode=0o700)
    for path in sorted(reports_dir(root).glob("*.json")):
        try:
            report = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(report, dict) or report.get("version") != REPORT_V7:
            continue
        for source in (path, path.with_suffix(".md")):
            if source.exists():
                (directory / source.name).write_bytes(source.read_bytes())


def _task_key(record):
    return record.get("provider"), record.get("session_id")


def _task_total(record):
    return (
        int(record.get("input_tokens") or 0)
        + int(record.get("cache_tokens") or 0)
        + int(record.get("output_tokens") or 0)
    )


def _v7_unknown_models(record):
    """v7 任务 → unknown 拆分（§3.1.3）。"""
    return {
        UNKNOWN: {
            "fresh_input": int(record.get("input_tokens") or 0),
            "cache_write": 0,
            "cache_read": int(record.get("cache_tokens") or 0),
            "output": int(record.get("output_tokens") or 0),
        }
    }


def _merge_day_tasks(report, base, excluded_sessions):
    """重扫报告与磁盘现有报告按 (provider, session_id) 合并，不降级（§3.1.3/4）。

    - 重扫中存在的任务：采用重扫记录；其三类合计小于现有报告时，沿用现有三类
      合计，差额记 unknown。
    - 只在现有报告存在的任务：恢复（v7 任务拆 unknown 并标记 restored_from；
      判定为 agent 的会话不恢复——正当排除，重扫注脚已含）。
    返回 (merged_records, restored_v7)。"""
    base_map = {_task_key(record): record for record in base.get("tasks") or []}
    rescan_map = {_task_key(record): record for record in report.get("tasks") or []}
    merged, restored = [], False
    for key, record in rescan_map.items():
        old = base_map.get(key)
        if old is None or _task_total(old) <= _task_total(record):
            merged.append(record)
            continue
        # 来源部分被清理：三类合计沿用现有报告，差额记 unknown。
        merged_record = dict(record)
        diff = {
            "fresh_input": max(
                int(old.get("input_tokens") or 0)
                - int(record.get("input_tokens") or 0),
                0,
            ),
            "cache_write": 0,
            "cache_read": max(
                int(old.get("cache_tokens") or 0)
                - int(record.get("cache_tokens") or 0),
                0,
            ),
            "output": max(
                int(old.get("output_tokens") or 0)
                - int(record.get("output_tokens") or 0),
                0,
            ),
        }
        models = dict(merged_record.get("models") or {})
        unknown = models.get(UNKNOWN) or {
            "fresh_input": 0,
            "cache_write": 0,
            "cache_read": 0,
            "output": 0,
        }
        for name in MODEL_KEYS:
            unknown[name] = int(unknown.get(name) or 0) + diff[name]
        models[UNKNOWN] = unknown
        merged_record["models"] = models
        merged_record["input_tokens"] = int(old.get("input_tokens") or 0)
        merged_record["cache_tokens"] = int(old.get("cache_tokens") or 0)
        merged_record["output_tokens"] = int(old.get("output_tokens") or 0)
        merged_record["total_tokens"] = _task_total(old)
        merged.append(merged_record)
    for key, old in base_map.items():
        if key in rescan_map:
            continue
        if key in excluded_sessions:
            continue  # 判定为 agent：正当排除，不恢复。
        record = dict(old)
        is_v7 = old.get("version") == REPORT_V7 or "models" not in old
        if is_v7:
            record["models"] = _v7_unknown_models(old)
            record["restored_from"] = REPORT_V7
        if _task_total(record) > 0 or record.get("fidelity") == "unavailable":
            merged.append(record)
        if is_v7:
            restored = True
    merged.sort(
        key=lambda item: (
            -item.get("total_tokens", 0),
            item.get("provider", ""),
            item.get("session_id", ""),
        )
    )
    return merged, restored


def _merge_day_report(report, base, excluded_sessions):
    """合并任务并用 build_report 重算 totals；返回 (new_report, restored_v7)。"""
    merged, restored = _merge_day_tasks(report, base, excluded_sessions)
    if (
        not restored
        and len(merged) == len(report.get("tasks") or [])
        and base.get("version") == REPORT_VERSION
    ):
        # 没有恢复发生且任务集不变：直接用重扫报告。
        return report, False
    restored_flag = restored or any("restored_from" in record for record in merged)
    new_report = build_report(
        parse_day(report["date"]), merged, report.get("generated_at") or time.time()
    )
    new_report["generated_at"] = (
        report.get("generated_at") or new_report["generated_at"]
    )
    if restored_flag:
        new_report["migrated_from"] = REPORT_V7
    return new_report, restored_flag


def finalize_day_report(
    root, day, report, *, excluded_sessions=frozenset(), store=None, home=None
):
    """过去日报告落盘关口：按任务合并且不降级（规范 §3.1.3/4，长期约束）。

    与磁盘上该日的现有报告（任意版本）合并；磁盘缺失时回退 reports.v7.bak/。
    excluded_sessions 是本次扫描被判为 agent 的 (provider, session_id) 集合。
    今天的报告不参与。"""
    day_text = day.isoformat()
    current = _read_report_version(root, day_text, REPORT_VERSION)
    legacy = _read_report_version(root, day_text, REPORT_V7)
    if legacy is not None:
        _backup_v7_reports(root)
    base = current or legacy
    if base is None:
        # 回退 reports.v7.bak（§3.1.4）：备份目录平铺，不走 reports/ 拼接。
        backup_path = Path(root) / "reports.v7.bak" / f"{day_text}.json"
        try:
            backup = json.loads(backup_path.read_text())
        except (OSError, ValueError):
            backup = None
        if (
            isinstance(backup, dict)
            and backup.get("version") == REPORT_V7
            and backup.get("date") == day_text
        ):
            base = backup
    if base is not None:
        report, _restored = _merge_day_report(report, base, excluded_sessions)
    _write_report(root, report)
    return report


def generate_day(store, home, date_text=None, *, refresh=False):
    """单日报告。过去日优先返回已定稿缓存（版本匹配且在该日结束后生成），缺失/未定稿或 refresh 才扫描来源并落盘；
    今天始终实时重算、不落盘，次日按未定稿规则补算定稿。"""
    day = parse_day(date_text) if date_text else date.today()
    path_md = str(reports_dir(store.root) / (day.isoformat() + ".md"))
    if day < date.today() and not refresh:
        cached = load_report(store.root, day.isoformat())
        if _finalized(cached, day):
            cached["path_md"] = path_md
            return cached
    agent_stats = {}
    records = scan_buckets(store, home, day, day, agent_stats=agent_stats).get(day, [])
    report = build_report(
        day, records, time.time(), agent_excluded=agent_stats.get(day)
    )
    if day < date.today():
        report = finalize_day_report(store.root, day, report)
        report["path_md"] = path_md
    return report


def _finalized(report, day):
    """过去日报告在该日结束后生成才算定稿；当日结束前的快照缺晚间消耗，次日补算一次。"""
    return report is not None and report.get("generated_at", 0) >= day_bounds(day)[1]


def generate_overview(store, home, *, days=182, top=5):
    """热力图总量（三类合计）+ 近 7 天 Top 项目与来源；缺失/未定稿的过去日自动补录。

    来源扫描只覆盖最早未定稿日至今，常态下仅今天（毫秒级），避免每次打开重读半年转写。
    """
    today = date.today()
    first_day = today - timedelta(days=days - 1)
    cached, pending = {}, [today]
    for offset in range(days - 1):
        day = first_day + timedelta(days=offset)
        report = load_report(store.root, day.isoformat())
        if _finalized(report, day):
            cached[day] = report
        else:
            pending.append(day)
    agent_stats = {}
    buckets = scan_buckets(store, home, min(pending), today, agent_stats=agent_stats)
    live_today = build_report(
        today,
        buckets.get(today, []),
        time.time(),
        agent_excluded=agent_stats.get(today),
    )
    day_rows = []
    pricing_tables = PricingTables.load(store.root)
    # §7 热力图按金额着色：定稿报告不存金额（§3），逐日从 totals.models 现算
    # CNY 视图金额（汇率读 load_fx），阈值取有消耗日金额的 50/75/90 分位。
    fx_rate = load_fx(store.root)["USD_CNY"]

    def cny_view(cost):
        total = 0.0
        for bucket in ("input", "cache", "output", "native_fallback"):
            entries = cost.get(bucket) or {}
            total += (
                float(entries.get("CNY") or 0)
                + float(entries.get("USD") or 0) * fx_rate
            )
        return total

    day_cost_map = {}
    amounts = []
    for offset in range(days):
        day = first_day + timedelta(days=offset)
        report = live_today if day == today else cached.get(day)
        models = ((report or {}).get("totals") or {}).get("models") or {}
        day_cost = cost_for_models(models, day, pricing_tables)[0]
        day_cost_map[day] = day_cost
        value = cny_view(day_cost)
        if value > 0:
            amounts.append(value)
    amounts.sort()  # R17：分位数必须基于有序样本（此前按日期顺序取值导致分级错误）。
    thresholds = (
        [amounts[int(len(amounts) * q)] for q in (0.5, 0.75, 0.9)]
        if len(amounts) >= 8
        else [0.0, 0.0, 0.0]
    )
    for offset in range(days):
        day = first_day + timedelta(days=offset)
        text = day.isoformat()
        report = live_today if day == today else cached.get(day)
        if report is None:
            report = build_report(
                day,
                buckets.get(day, []),
                time.time(),
                agent_excluded=agent_stats.get(day),
            )
            report = finalize_day_report(
                store.root,
                day,
                report,
                excluded_sessions=set(agent_stats.get(day, {}).get("tasks", set())),
            )
        totals = report.get("totals", {})
        day_cost = day_cost_map[day]
        cny_total = cny_view(day_cost)
        day_rows.append(
            {key: int(totals.get(key) or 0) for key in CLASSES}
            | {
                "date": text,
                "total_tokens": int(totals.get("total_tokens") or 0),
                "tasks": int(totals.get("tasks") or 0),
                "level": heat_level(int(totals.get("total_tokens") or 0)),
                "cost": day_cost,
                "cost_level": cost_level(cny_total, thresholds),
            }
        )
    merged_sources, merged_projects, week_models = {}, {}, {}
    for offset in range(min(7, days)):
        day = today - timedelta(days=offset)
        report = (
            live_today if day == today else load_report(store.root, day.isoformat())
        )
        totals = (report or {}).get("totals", {})
        _merge_models(week_models, totals.get("models") or {})
        for target, merged in (
            (totals.get("sources") or {}, merged_sources),
            (totals.get("projects") or {}, merged_projects),
        ):
            for name, entry in target.items():
                bucket = merged.setdefault(name, _empty_usage())
                for key in CLASSES + ("total_tokens",):
                    bucket[key] += int(entry.get(key) or 0)
    denominator = sum(entry["total_tokens"] for entry in merged_projects.values()) or 1
    ranked = sorted(
        merged_projects.items(), key=lambda item: (-item[1]["total_tokens"], item[0])
    )[: max(1, top)]
    top_projects = [
        {
            "project": name,
            "name": Path(name).name,
            **entry,
            "share": round(entry["total_tokens"] / denominator, 3),
        }
        for name, entry in ranked
    ]
    today_totals = live_today["totals"]
    today_cost, _, _ = cost_for_models(
        today_totals.get("models") or {}, today, pricing_tables
    )
    week_cost, _, _ = cost_for_models(week_models, today, pricing_tables)
    today_row = {key: today_totals[key] for key in CLASSES}
    today_row.update(
        {
            "date": today.isoformat(),
            "total_tokens": today_totals["total_tokens"],
            "tasks": today_totals["tasks"],
            "turns": today_totals["turns"],
            "sources": sum(
                1
                for entry in today_totals["sources"].values()
                if entry["total_tokens"] > 0
            ),
            "level": heat_level(today_totals["total_tokens"]),
            "cost": today_cost,
        }
    )
    return {
        "generated_at": round(time.time()),
        "days": day_rows,
        "top_projects": top_projects,
        "week_sources": merged_sources,
        "week_models": {"models": week_models, "cost": week_cost},
        "today": today_row,
    }
