"""Daily work summary measured in tokens, aggregated from local session metadata.

token 三类口径：输入（新鲜输入+缓存写入）、缓存（缓存读取）、输出（含 reasoning）；
三类之和 = 合计，参与一切比较与计算（热力分级/排名/占比/趋势），三类在界面全部展示。
取消与出错轮次的消耗照计。只读取时间戳、标题、项目与数值字段，从不读取或存储
消息正文。过去日以报告文件固化（version=7），当日始终实时计算。
"""

import json
import os
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from inbox_sources import seconds
from inbox_store import effective_origin
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
REPORT_VERSION = 7
SEGMENT_GAP = 15 * 60  # 逐条时间戳来源：相邻消息间隔不超过 15 分钟视为同一段活动
CLASSES = ("input_tokens", "cache_tokens", "output_tokens")


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

    def spread(
        self,
        provider,
        sid,
        start,
        end,
        *,
        input=0,
        cache=0,
        output=0,
        title="",
        project="",
        fidelity="exact",
        state="unknown",
        turn_stamp=None,
        activity=None,
    ):
        """把一段区间（含三类 token 消耗）按天窗口交集比例分摊，跨零点不重复计数。

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
            task["input"] += input * fraction
            task["cache"] += cache * fraction
            task["output"] += output * fraction
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
        input=0,
        cache=0,
        output=0,
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
        task["input"] += input
        task["cache"] += cache
        task["output"] += output
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
    """(turns, titles, tokenized)；turns=(sid, start, end, 输入, 缓存, 输出, count_turn, activity)。
    activity 取自 model_usage 的逐请求时段（无该表则 None，整轮视为活动）。

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
    turns = []
    tokenized = True
    parents = {}
    requests = {}  # turn_id -> [(start, end)]：逐请求时段，轮内等待用户的空档不算活动
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
            for turn_id, started, completed in connection.execute(
                "SELECT turn_id,started_at,completed_at FROM model_usage"
            ):
                requests.setdefault(turn_id, []).append((started, completed))
        except sqlite3.Error:
            requests = {}
    finally:
        connection.close()
    now = time.time()
    for row in rows:
        sid, started, completed, input_tokens, cache_read = (
            row[0],
            row[1],
            row[2],
            row[3],
            row[4],
        )
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
        if tokenized:
            cache_creation, output, reasoning = row[5], row[6], row[7]
            # input_tokens 实测已含 cache_read；三类 = 输入(新鲜+缓存写入)/缓存读/输出。
            input_cls = max(int(input_tokens or 0) - int(cache_read or 0), 0) + int(
                cache_creation or 0
            )
            cache_cls = max(int(cache_read or 0), 0)
            output_cls = int(output or 0) + int(reasoning or 0)
        else:
            input_cls = cache_cls = output_cls = None
        activity = [
            (seconds(r_start), seconds(r_end) if r_end else now)
            for r_start, r_end in requests.get(row[8], ())
            if seconds(r_start) > 0
        ]
        activity = [(a, b) for a, b in activity if b >= a] or None
        turns.append(
            (
                target,
                start,
                end,
                input_cls,
                cache_cls,
                output_cls,
                not is_subagent,
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
                    if record.get("type") != "event_msg":
                        continue
                    payload = record.get("payload") or {}
                    kind = payload.get("type")
                    stamp = seconds(record.get("timestamp"))
                    if stamp <= 0:
                        continue
                    if kind == "token_count":
                        if stamp < window_start:
                            continue
                        usage = (payload.get("info") or {}).get(
                            "last_token_usage"
                        ) or {}
                        # 字段互斥（input+cached+cacheW+output+reasoning=total，已实测验证）。
                        input_cls = int(usage.get("input_tokens") or 0) + int(
                            usage.get("cache_write_input_tokens") or 0
                        )
                        events.append(
                            (
                                sid,
                                stamp,
                                input_cls,
                                int(usage.get("cached_input_tokens") or 0),
                                int(usage.get("output_tokens") or 0)
                                + int(usage.get("reasoning_output_tokens") or 0),
                            )
                        )
                    elif kind == "task_started" and stamp >= window_start:
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
    root = home / "Library/Application Support/Claude/claude-code-sessions"
    projects_root = home / ".claude/projects"
    if not root.exists() or not projects_root.exists():
        return [], []

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
                    usage = (record.get("message") or {}).get("usage")
                    if not isinstance(usage, dict):
                        continue
                    stamp = seconds(record.get("timestamp"))
                    if stamp <= 0 or stamp < window_start:
                        continue
                    input_cls = int(usage.get("input_tokens") or 0) + int(
                        usage.get("cache_creation_input_tokens") or 0
                    )
                    cache_cls = int(usage.get("cache_read_input_tokens") or 0)
                    output_cls = int(usage.get("output_tokens") or 0)
                    records.append((stamp, input_cls, cache_cls, output_cls))
        except OSError:
            return []
        return records

    transcripts = {path.stem: path for path in projects_root.rglob("*.jsonl")}
    sessions = {}
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
    """受管理 Pi 会话的逐条 assistant usage：(sid, stamp, 输入, 缓存, 输出)。"""
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
                    input_cls = int(usage.get("input") or 0) + int(
                        usage.get("cacheWrite") or 0
                    )
                    output_cls = int(usage.get("output") or 0) + int(
                        usage.get("reasoning") or 0
                    )
                    results.append(
                        (
                            row["session_id"],
                            stamp,
                            input_cls,
                            int(usage.get("cacheRead") or 0),
                            output_cls,
                        )
                    )
        except OSError:
            continue
    return results


def _kimi_data(home, window_start):
    """Kimi wire.jsonl 的 usage.record 逐轮消耗：(sid, stamp, 输入, 缓存, 输出)。"""
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
                    input_cls = int(usage.get("inputOther") or 0) + int(
                        usage.get("inputCacheCreation") or 0
                    )
                    output_cls = int(usage.get("output") or 0)
                    results.append(
                        (
                            sid,
                            stamp,
                            input_cls,
                            int(usage.get("inputCacheRead") or 0),
                            output_cls,
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
            input_cls = int(usage.get("input") or 0) + int(cache.get("write") or 0)
            cache_cls = int(cache.get("read") or 0)
            output_cls = int(usage.get("output") or 0) + int(
                usage.get("reasoning") or 0
            )
            if input_cls + cache_cls + output_cls <= 0:
                continue
            results.append((sid, stamp, input_cls, cache_cls, output_cls))
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
        有效 origin：目录规则（用户改判沉淀）优先于自动分类。"""
        row = store_rows.get((provider, sid))
        if effective_origin(origin_rules, row) != "agent":
            return False
        if agent_stats is not None and tokens > 0:
            for day, (low, high) in buckets.windows.items():
                if low <= stamp < high:
                    entry = agent_stats.setdefault(
                        day, {"tasks": set(), "total_tokens": 0.0}
                    )
                    entry["tasks"].add(sid)
                    entry["total_tokens"] += tokens
                    break
        return True

    turns, titles, tokenized = _zcode_data(home)
    for (
        sid,
        start,
        end,
        input_cls,
        cache_cls,
        output_cls,
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
            input=input_cls or 0,
            cache=cache_cls or 0,
            output=output_cls or 0,
            title=title,
            project=project,
            fidelity="exact" if tokenized else "unavailable",
            state=ZCODE_STATES.get(status) or state,
            turn_stamp=start if count_turn else None,
            activity=activity,
        )

    events, turn_starts, titles, projects = _codex_data(home, window_start)
    for sid, stamp, input_cls, cache_cls, output_cls in events:
        if excluded("codex", sid, stamp, input_cls + cache_cls + output_cls):
            continue
        title, project, state = known(
            "codex", sid, titles.get(sid, ""), projects.get(sid, "")
        )
        buckets.point(
            "codex",
            sid,
            stamp,
            input=input_cls,
            cache=cache_cls,
            output=output_cls,
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
        for record_stamp, input_cls, cache_cls, output_cls in records:
            if excluded(
                "claude", sid, record_stamp, input_cls + cache_cls + output_cls
            ):
                continue
            known_title, known_project, state = known("claude", sid, title, project)
            buckets.point(
                "claude",
                sid,
                record_stamp,
                input=input_cls,
                cache=cache_cls,
                output=output_cls,
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
            input=0,
            cache=0,
            output=0,
            title=title,
            project=project,
            fidelity="unavailable",
            state=state,
        )

    for sid, record_stamp, input_cls, cache_cls, output_cls in _pi_data(
        store, window_start
    ):
        if excluded("pi", sid, record_stamp, input_cls + cache_cls + output_cls):
            continue
        title, project, state = known("pi", sid)
        buckets.point(
            "pi",
            sid,
            record_stamp,
            input=input_cls,
            cache=cache_cls,
            output=output_cls,
            title=title,
            project=project,
            state=state,
        )

    for sid, record_stamp, input_cls, cache_cls, output_cls in _kimi_data(
        home, window_start
    ):
        if excluded("kimi", sid, record_stamp, input_cls + cache_cls + output_cls):
            continue
        title, project, state = known("kimi", sid)
        buckets.point(
            "kimi",
            sid,
            record_stamp,
            input=input_cls,
            cache=cache_cls,
            output=output_cls,
            title=title,
            project=project,
            state=state,
        )

    events, titles, projects = _opencode_data(store, home, window_start)
    for sid, record_stamp, input_cls, cache_cls, output_cls in events:
        if excluded("opencode", sid, record_stamp, input_cls + cache_cls + output_cls):
            continue
        title, project, state = known(
            "opencode", sid, titles.get(sid, ""), projects.get(sid, "")
        )
        buckets.point(
            "opencode",
            sid,
            record_stamp,
            input=input_cls,
            cache=cache_cls,
            output=output_cls,
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


def _add_usage(bucket, key, record):
    entry = bucket.setdefault(key, _empty_usage())
    entry["input_tokens"] += record["input_tokens"]
    entry["cache_tokens"] += record["cache_tokens"]
    entry["output_tokens"] += record["output_tokens"]
    entry["total_tokens"] += record["total_tokens"]


def build_report(day, records, generated_at, agent_excluded=None):
    sources, projects = {}, {}
    totals = _empty_usage()
    for record in records:
        _add_usage(sources, record["provider"], record)
        _add_usage(projects, record["project"] or NO_PROJECT, record)
        for key in CLASSES + ("total_tokens",):
            totals[key] += record[key]
    totals["tasks"] = len(records)
    totals["turns"] = sum(record["turns"] for record in records)
    totals["sources"] = sources
    totals["projects"] = projects
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


def load_report(root, date_text):
    path = reports_dir(root) / (date_text + ".json")
    try:
        report = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if (
        isinstance(report, dict)
        and report.get("version") == REPORT_VERSION
        and report.get("date") == date_text
    ):
        return report
    return None  # 旧版本报告视为缺失，触发重算。


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
        _write_report(store.root, report)
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
            _write_report(store.root, report)
        totals = report.get("totals", {})
        day_rows.append(
            {key: int(totals.get(key) or 0) for key in CLASSES}
            | {
                "date": text,
                "total_tokens": int(totals.get("total_tokens") or 0),
                "tasks": int(totals.get("tasks") or 0),
                "level": heat_level(int(totals.get("total_tokens") or 0)),
            }
        )
    merged_sources, merged_projects = {}, {}
    for offset in range(min(7, days)):
        day = today - timedelta(days=offset)
        report = (
            live_today if day == today else load_report(store.root, day.isoformat())
        )
        totals = (report or {}).get("totals", {})
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
        }
    )
    return {
        "generated_at": round(time.time()),
        "days": day_rows,
        "top_projects": top_projects,
        "week_sources": merged_sources,
        "today": today_row,
    }
