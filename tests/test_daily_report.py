import json
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from daily_report import (
    build_report,
    generate_day,
    generate_overview,
    heat_level,
    load_report,
    render_markdown,
    scan_buckets,
    token_text,
)
from inbox_store import Store


def stamp(day, hour=0, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute).timestamp()


def iso(day, hour, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute).isoformat()


class DailyReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.store = Store(self.home / "state")
        self.day = date.today() - timedelta(days=3)
        self.prev = self.day - timedelta(days=1)

    def zcode_index(self, sid, title="z 任务", project="/work/proj"):
        index = self.home / ".zcode/v2/tasks-index.sqlite"
        index.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(index) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS tasks (task_id,title,workspace_path,"
                "task_status,unread_at,updated_at,archived,deleted,last_unread_at)"
            )
            db.execute(
                "INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?)",
                (sid, title, project, "completed", None, 0, 0, 0, 0),
            )

    def zcode_runtime(self, minimal=False):
        runtime = self.home / ".zcode/cli/db/db.sqlite"
        runtime.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(runtime) as db:
            if minimal:
                db.execute(
                    "CREATE TABLE IF NOT EXISTS turn_usage (session_id,turn_id,status,started_at,completed_at)"
                )
            else:
                db.execute(
                    "CREATE TABLE IF NOT EXISTS turn_usage (session_id,turn_id,status,started_at,"
                    "completed_at,input_tokens,cache_read_input_tokens,cache_creation_input_tokens,"
                    "output_tokens,reasoning_tokens)"
                )

    def zcode_turn(
        self,
        sid,
        turn_id,
        start,
        end,
        *,
        status="completed",
        fresh=0,
        cached=0,
        cache_creation=0,
        output=0,
        reasoning=0,
    ):
        self.zcode_runtime()
        with sqlite3.connect(self.home / ".zcode/cli/db/db.sqlite") as db:
            db.execute(
                "INSERT INTO turn_usage VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    sid,
                    turn_id,
                    status,
                    round(start * 1000),
                    round(end * 1000),
                    fresh + cached,
                    cached,
                    cache_creation,
                    output,
                    reasoning,
                ),
            )

    def zcode_requests(
        self, turn_id, intervals, *, model=None, tokens=None, models=None
    ):
        """model_usage 夹具（v8 十列 schema）。tokens 给出时逐请求附四项原始值
        （fresh, cache_write, cache_read, output, reasoning）；models 给出时逐请求
        指定 model_id（与 tokens 等长），用于多 model 拆分场景。"""
        runtime = self.home / ".zcode/cli/db/db.sqlite"
        with sqlite3.connect(runtime) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS model_usage (turn_id,session_id,model_id,"
                "input_tokens,cache_read_input_tokens,cache_creation_input_tokens,"
                "output_tokens,reasoning_tokens,started_at,completed_at)"
            )
            for index, (start, end) in enumerate(intervals):
                raw = tokens[index] if tokens else (None,) * 5
                per_model = models[index] if models else model
                db.execute(
                    "INSERT INTO model_usage VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        turn_id,
                        "sess",
                        per_model,
                        raw[0],
                        raw[1],
                        raw[2],
                        raw[3],
                        raw[4],
                        round(start * 1000),
                        round(end * 1000) if end else None,
                    ),
                )

    def zcode_parents(self, mapping):
        runtime = self.home / ".zcode/cli/db/db.sqlite"
        runtime.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(runtime) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS session (id TEXT PRIMARY KEY, parent_id TEXT)"
            )
            for sid, parent in mapping.items():
                db.execute("INSERT OR REPLACE INTO session VALUES (?,?)", (sid, parent))

    def codex_rollout(self, sid, lines, originator="Codex Desktop"):
        directory = self.home / ".codex/sessions/2026/09"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"rollout-{sid}.jsonl"
        body = [
            json.dumps(
                {
                    "type": "session_meta",
                    "payload": {
                        "id": sid,
                        "originator": originator,
                        "cwd": "/work/codex",
                    },
                }
            )
        ]
        for kind, moment, usage in lines:
            if kind == "turn_context":
                body.append(
                    json.dumps(
                        {
                            "type": "turn_context",
                            "payload": {"model": usage},
                            "timestamp": iso(*moment)
                            if isinstance(moment, tuple)
                            else moment,
                        }
                    )
                )
                continue
            payload = {"type": kind}
            if usage is not None:
                payload["info"] = {"last_token_usage": usage}
            body.append(
                json.dumps(
                    {
                        "type": "event_msg",
                        "payload": payload,
                        "timestamp": iso(*moment)
                        if isinstance(moment, tuple)
                        else moment,
                    }
                )
            )
        path.write_text("\n".join(body) + "\n")
        return path

    def codex_index(self, sid, name):
        path = self.home / ".codex/session_index.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"id": sid, "thread_name": name, "updated_at": iso(self.day, 12)}
            )
            + "\n"
        )

    def claude_session(
        self,
        sid,
        created,
        last,
        title="claude 会话",
        cwd="/work/claude",
        transcript=True,
        usage_lines=(),
        desktop=True,
    ):
        if desktop:
            root = (
                self.home / "Library/Application Support/Claude/claude-code-sessions/d"
            )
            root.mkdir(parents=True, exist_ok=True)
            (root / f"local_{sid}.json").write_text(
                json.dumps(
                    {
                        "cliSessionId": sid,
                        "sessionId": "local_" + sid,
                        "title": title,
                        "cwd": cwd,
                        "createdAt": round(created * 1000),
                        "lastActivityAt": round(last * 1000),
                    }
                )
            )
        (self.home / ".claude/projects/proj").mkdir(parents=True, exist_ok=True)
        if transcript:
            path = self.home / ".claude/projects/proj" / f"{sid}.jsonl"
            lines = [
                json.dumps(
                    {
                        "type": "assistant",
                        "sessionId": sid,
                        "timestamp": iso(*moment),
                        "message": {
                            "role": "assistant",
                            "usage": usage,
                            **({"model": item[2]} if len(item) > 2 and item[2] else {}),
                        },
                    }
                )
                for item in usage_lines
                for moment, usage in (item[:2],)
            ]
            path.write_text("\n".join(lines) + "\n")

    def pi_session(self, sid, records, title="Pi 调研"):
        self.store.ensure("pi", sid)
        self.store.patch("pi", sid, title=title, project="/work/pi")
        path = self.home / ".pi/agent/sessions/s" / f"{sid}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            json.dumps({"type": "session", "id": sid, "timestamp": iso(self.day, 8)})
        ]
        for item in records:
            moment, usage = item[:2]
            message = {"role": "assistant", "usage": usage}
            if len(item) > 2 and item[2]:
                message["model"] = item[2]
            if len(item) > 3 and item[3] is not None:
                message["usage"] = {**usage, "cost": {"total": item[3]}}
            lines.append(
                json.dumps(
                    {
                        "type": "message",
                        "timestamp": iso(*moment),
                        "message": message,
                    }
                )
            )
        path.write_text("\n".join(lines) + "\n")
        self.store.set_meta("pi-file:" + sid, str(path))

    def kimi_wire(self, sid, records):
        path = (
            self.home
            / ".kimi-code/sessions/wd_x"
            / f"session_{sid}"
            / "agents/main/wire.jsonl"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            json.dumps(
                {
                    "type": "usage.record",
                    "usage": usage,
                    "time": round(moment * 1000),
                    **({"model": item[2]} if len(item) > 2 and item[2] else {}),
                }
            )
            for item in records
            for moment, usage in (item[:2],)
        ]
        path.write_text("\n".join(lines) + "\n")

    def single(self, buckets, day=None):
        records = buckets[day or self.day]
        self.assertEqual(len(records), 1, records)
        return records[0]

    def test_zcode_three_classes_and_cancelled_turns_counted(self):
        self.zcode_index("sess_a", "收件箱日报", "/work/session-manager")
        # input(含缓存读)=1600, 缓存读=600, 缓存写=50, 输出=200, reasoning=10
        # 三类 = 输入 1050 / 缓存 600 / 输出 210，合计 1860
        self.zcode_turn(
            "sess_a",
            "t1",
            stamp(self.day, 10),
            stamp(self.day, 10, 30),
            fresh=1000,
            cached=600,
            cache_creation=50,
            output=200,
            reasoning=10,
        )
        self.zcode_turn(
            "sess_a",
            "t2",
            stamp(self.day, 11),
            stamp(self.day, 11, 10),
            status="cancelled",
            fresh=300,
        )
        record = self.single(scan_buckets(self.store, self.home, self.day, self.day))
        self.assertEqual(
            (record["input_tokens"], record["cache_tokens"], record["output_tokens"]),
            (1050 + 300, 600, 210),
        )
        self.assertEqual(
            (record["total_tokens"], record["turns"], record["fidelity"]),
            (2160, 2, "exact"),
        )
        self.assertEqual(record["project"], "/work/session-manager")

    def test_subagent_tokens_attribute_to_parent_task_without_counting_turns(self):
        self.zcode_index("sess_main")
        self.zcode_parents({"sess_main": None, "sess_subagent_agent_x": "sess_main"})
        self.zcode_turn(
            "sess_main", "t1", stamp(self.day, 9), stamp(self.day, 9, 30), fresh=1000
        )
        self.zcode_turn(
            "sess_subagent_agent_x",
            "sub1",
            stamp(self.day, 9, 10),
            stamp(self.day, 9, 20),
            fresh=500,
        )
        record = self.single(scan_buckets(self.store, self.home, self.day, self.day))
        self.assertEqual(
            (record["session_id"], record["input_tokens"]), ("sess_main", 1500)
        )
        self.assertEqual(record["turns"], 1)  # 子代理轮不计入父任务轮次

    def test_turn_tokens_split_proportionally_across_midnight(self):
        self.zcode_index("sess_a")
        self.zcode_turn(
            "sess_a",
            "t1",
            stamp(self.prev, 23),
            stamp(self.day, 1),
            fresh=3600,
            cached=3600,
            output=2400,
        )  # 三类合计 9600，两小时各半
        buckets = scan_buckets(self.store, self.home, self.prev, self.day)
        self.assertEqual(buckets[self.prev][0]["total_tokens"], 4800)
        self.assertEqual(buckets[self.day][0]["total_tokens"], 4800)
        self.assertEqual(buckets[self.day][0]["cache_tokens"], 1800)
        self.assertEqual(buckets[self.day][0]["turns"], 0)

    def test_zcode_without_token_columns_degrades_to_unavailable(self):
        self.zcode_index("sess_a")
        self.zcode_runtime(minimal=True)
        with sqlite3.connect(self.home / ".zcode/cli/db/db.sqlite") as db:
            db.execute(
                "INSERT INTO turn_usage VALUES (?,?,?,?,?)",
                (
                    "sess_a",
                    "t1",
                    "completed",
                    round(stamp(self.day, 9) * 1000),
                    round(stamp(self.day, 10) * 1000),
                ),
            )
        record = self.single(scan_buckets(self.store, self.home, self.day, self.day))
        self.assertEqual(
            (record["total_tokens"], record["fidelity"], record["turns"]),
            (0, "unavailable", 1),
        )

    def test_codex_tokens_bucketed_by_request_time(self):
        sid = "11111111-2222-3333-4444-555555555555"
        self.codex_index(sid, "codex 评审")
        self.codex_rollout(
            sid,
            [
                ("task_started", (self.day, 10), None),
                (
                    "token_count",
                    (self.day, 10, 5),
                    {
                        "input_tokens": 100,
                        "cached_input_tokens": 40,
                        "cache_write_input_tokens": 5,
                        "output_tokens": 20,
                        "reasoning_output_tokens": 3,
                    },
                ),
                (
                    "token_count",
                    (self.day, 10, 30),
                    {
                        "input_tokens": 200,
                        "cached_input_tokens": 0,
                        "cache_write_input_tokens": 0,
                        "output_tokens": 40,
                        "reasoning_output_tokens": 0,
                    },
                ),
                ("task_started", (self.day, 11), None),
            ],
        )
        record = self.single(scan_buckets(self.store, self.home, self.day, self.day))
        # 输入 = (100+5) + 200；缓存 = 40；输出 = (20+3) + 40；合计 408
        self.assertEqual(
            (record["input_tokens"], record["cache_tokens"], record["output_tokens"]),
            (305, 40, 63),
        )
        self.assertEqual((record["total_tokens"], record["turns"]), (408, 2))
        self.assertEqual(
            (record["title"], record["project"], record["fidelity"]),
            ("codex 评审", "/work/codex", "exact"),
        )

    def test_codex_cli_originator_rollouts_counted(self):
        # 收件箱 2026-09-16 起纳入 CLI/exec/workbench 来源，日报同口径；
        # 缺失该口径时当日 codex 会话在日报整体缺源（2026-09-17 实测回归）。
        self.codex_rollout(
            "wb-1",
            [
                (
                    "token_count",
                    (self.day, 10),
                    {
                        "input_tokens": 100,
                        "cached_input_tokens": 30,
                        "cache_write_input_tokens": 0,
                        "output_tokens": 20,
                        "reasoning_output_tokens": 0,
                    },
                ),
            ],
            originator="coding-agent-workbench",
        )
        self.codex_rollout(
            "exec-1",
            [
                ("task_started", (self.day, 11), None),
            ],
            originator="codex_exec",
        )
        records = [
            r
            for r in scan_buckets(self.store, self.home, self.day, self.day)[self.day]
            if r["provider"] == "codex"
        ]
        self.assertEqual(len(records), 2, records)
        by_sid = {r["session_id"]: r for r in records}
        self.assertEqual(
            (
                by_sid["wb-1"]["input_tokens"],
                by_sid["wb-1"]["cache_tokens"],
                by_sid["wb-1"]["output_tokens"],
                by_sid["wb-1"]["fidelity"],
            ),
            (100, 30, 20, "exact"),
        )
        self.assertEqual(by_sid["exec-1"]["turns"], 1)
        self.assertEqual(by_sid["exec-1"]["total_tokens"], 0)

    def test_pi_assistant_usage_parsed(self):
        self.pi_session(
            "pi-1",
            [
                (
                    (self.day, 9),
                    {
                        "input": 100,
                        "cacheWrite": 10,
                        "output": 30,
                        "reasoning": 5,
                        "cacheRead": 900,
                    },
                ),
                (
                    (self.day, 9, 30),
                    {
                        "input": 50,
                        "cacheWrite": 0,
                        "output": 20,
                        "reasoning": 0,
                        "cacheRead": 100,
                    },
                ),
            ],
        )
        record = self.single(scan_buckets(self.store, self.home, self.day, self.day))
        self.assertEqual(
            (record["input_tokens"], record["cache_tokens"], record["output_tokens"]),
            (160, 1000, 55),
        )
        self.assertEqual(
            (record["title"], record["project"], record["fidelity"]),
            ("Pi 调研", "/work/pi", "exact"),
        )

    def test_kimi_wire_records_counted(self):
        self.kimi_wire(
            "kimi-1",
            [
                (
                    stamp(self.day, 14),
                    {
                        "inputOther": 200,
                        "inputCacheCreation": 0,
                        "output": 50,
                        "inputCacheRead": 800,
                    },
                ),
                (
                    stamp(self.day, 15),
                    {
                        "inputOther": 100,
                        "inputCacheCreation": 20,
                        "output": 30,
                        "inputCacheRead": 400,
                    },
                ),
            ],
        )
        record = self.single(scan_buckets(self.store, self.home, self.day, self.day))
        self.assertEqual(
            (record["input_tokens"], record["cache_tokens"], record["output_tokens"]),
            (320, 1200, 80),
        )
        self.assertEqual(
            (record["total_tokens"], record["title"]), (1600, "Kimi · kimi-1")
        )

    def opencode_messages(
        self,
        sid,
        entries,
        *,
        title="OC 会话",
        directory="/work/oc",
        parent=None,
        managed=True,
    ):
        if managed:
            self.store.ensure("opencode", sid)  # 受管理口径：登记过的会话才计入日报。
        database = self.home / ".local/share/opencode/opencode.db"
        database.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(database) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS session (id text PRIMARY KEY, title text, directory text,"
                " time_updated integer, time_archived integer, parent_id text)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS message (id text PRIMARY KEY, session_id text,"
                " time_created integer, time_updated integer, data text)"
            )
            db.execute(
                "INSERT OR IGNORE INTO session VALUES (?,?,?,?,?,?)",
                (sid, title, directory, 0, None, parent),
            )
            for index, item in enumerate(entries):
                moment, usage = item[:2]
                stamp_ms = round(stamp(self.day, *moment) * 1000)
                data = {
                    "role": "assistant",
                    "time": {"created": stamp_ms - 1000, "completed": stamp_ms},
                    "tokens": usage,
                }
                if len(item) > 2 and item[2]:
                    data["modelID"] = item[2]
                if len(item) > 3 and item[3] is not None:
                    data["cost"] = item[3]
                db.execute(
                    "INSERT INTO message VALUES (?,?,?,?,?)",
                    (f"msg_{sid}_{index}", sid, stamp_ms, stamp_ms, json.dumps(data)),
                )

    def test_opencode_assistant_usage_counted(self):
        self.opencode_messages(
            "ses_1",
            [
                (
                    (10, 0),
                    {
                        "input": 100,
                        "output": 30,
                        "reasoning": 5,
                        "cache": {"read": 900, "write": 10},
                    },
                ),
                (
                    (10, 30),
                    {
                        "input": 50,
                        "output": 20,
                        "reasoning": 0,
                        "cache": {"read": 100, "write": 0},
                    },
                ),
            ],
        )
        # 同库但未经包装器登记的会话（如其它工具经 server 拉起）：不计入日报。
        self.opencode_messages(
            "ses_ghost",
            [
                (
                    (11, 0),
                    {"input": 999, "output": 999, "cache": {"read": 999, "write": 999}},
                ),
            ],
            managed=False,
        )
        record = self.single(scan_buckets(self.store, self.home, self.day, self.day))
        # 输入 = 100+10 + 50；缓存 = 900+100；输出 = 30+5 + 20；合计 1215
        self.assertEqual(
            (record["input_tokens"], record["cache_tokens"], record["output_tokens"]),
            (160, 1000, 55),
        )
        self.assertEqual(
            (record["total_tokens"], record["turns"], record["provider"]),
            (1215, 2, "opencode"),
        )
        self.assertEqual(
            (record["title"], record["project"], record["fidelity"]),
            ("OC 会话", "/work/oc", "exact"),
        )

    def test_opencode_subagent_tokens_not_counted(self):
        self.opencode_messages(
            "ses_sub",
            [
                (
                    (10, 0),
                    {"input": 100, "output": 30, "cache": {"read": 0, "write": 0}},
                ),
            ],
            parent="ses_parent",
        )
        records = scan_buckets(self.store, self.home, self.day, self.day)[self.day]
        self.assertEqual(records, [])

    def test_claude_transcript_tokens_and_missing_fallback(self):
        self.claude_session(
            "cli-1",
            stamp(self.day, 8),
            stamp(self.day, 12),
            cwd="/work/claude",
            transcript=True,
            usage_lines=[
                (
                    (self.day, 9),
                    {
                        "input_tokens": 100,
                        "cache_creation_input_tokens": 10,
                        "cache_read_input_tokens": 500,
                        "output_tokens": 20,
                    },
                ),
                (
                    (self.day, 10),
                    {
                        "input_tokens": 30,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 700,
                        "output_tokens": 40,
                    },
                ),
            ],
        )
        self.claude_session(
            "cli-2",
            stamp(self.day, 13),
            stamp(self.day, 14),
            cwd="/work/other",
            transcript=False,
        )
        records = scan_buckets(self.store, self.home, self.day, self.day)[self.day]
        by_sid = {record["session_id"]: record for record in records}
        self.assertEqual(
            (
                by_sid["cli-1"]["input_tokens"],
                by_sid["cli-1"]["cache_tokens"],
                by_sid["cli-1"]["output_tokens"],
            ),
            (140, 1200, 60),
        )
        self.assertEqual(
            (by_sid["cli-1"]["total_tokens"], by_sid["cli-1"]["fidelity"]),
            (1400, "exact"),
        )
        self.assertEqual(
            (by_sid["cli-2"]["total_tokens"], by_sid["cli-2"]["fidelity"]),
            (0, "unavailable"),
        )
        self.assertEqual(by_sid["cli-2"]["project"], "/work/other")

    def test_claude_cli_only_transcript_counted_once(self):
        # 未被桌面登记表登记的 CLI 直启转写：usage 照计；store 有已知行时补标题/项目。
        self.store.ensure("claude", "cli-only")
        self.store.patch("claude", "cli-only", title="CLI 会话", project="/work/cli")
        self.claude_session(
            "cli-only",
            stamp(self.day, 8),
            stamp(self.day, 12),
            transcript=True,
            usage_lines=[
                (
                    (self.day, 9),
                    {
                        "input_tokens": 100,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 50,
                        "output_tokens": 25,
                    },
                )
            ],
            desktop=False,
        )
        # 桌面登记停留在窗口前（lastActivityAt 过期）但转写仍在活跃：经补采计一次。
        self.claude_session(
            "stale-desktop",
            stamp(self.prev, 8),
            stamp(self.prev, 9),
            transcript=True,
            usage_lines=[
                (
                    (self.day, 10),
                    {
                        "input_tokens": 10,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                        "output_tokens": 5,
                    },
                )
            ],
            desktop=True,
        )
        # 未登记且窗口内无 assistant usage 的 CLI 转写：不产生任务。
        path = self.home / ".claude/projects/proj" / "no-usage.jsonl"
        path.write_text(
            json.dumps({"type": "user", "timestamp": iso(self.day, 9)}) + "\n"
        )
        records = scan_buckets(self.store, self.home, self.day, self.day)[self.day]
        self.assertEqual(
            sorted(r["session_id"] for r in records), ["cli-only", "stale-desktop"]
        )
        by_sid = {r["session_id"]: r for r in records}
        self.assertEqual(
            (
                by_sid["cli-only"]["input_tokens"],
                by_sid["cli-only"]["cache_tokens"],
                by_sid["cli-only"]["output_tokens"],
                by_sid["cli-only"]["fidelity"],
            ),
            (100, 50, 25, "exact"),
        )
        self.assertEqual(
            (by_sid["cli-only"]["title"], by_sid["cli-only"]["project"]),
            ("CLI 会话", "/work/cli"),
        )
        self.assertEqual(by_sid["stale-desktop"]["total_tokens"], 15)

    def test_claude_transcript_counted_without_desktop_install(self):
        # 评审 R6：桌面登记目录从未存在（纯 CLI 用户）——旧实现因
        # 「登记目录不存在」整段早退，全部 Claude 用量漏计。
        self.store.ensure("claude", "cli-only")
        self.store.patch("claude", "cli-only", title="CLI 会话", project="/work/cli")
        self.claude_session(
            "cli-only",
            stamp(self.day, 8),
            stamp(self.day, 12),
            transcript=True,
            usage_lines=[
                (
                    (self.day, 9),
                    {
                        "input_tokens": 100,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 50,
                        "output_tokens": 25,
                    },
                )
            ],
            desktop=False,
        )
        self.assertFalse((self.home / "Library/Application Support/Claude").exists())
        records = scan_buckets(self.store, self.home, self.day, self.day)[self.day]
        self.assertEqual([r["session_id"] for r in records], ["cli-only"])
        self.assertEqual(
            (
                records[0]["input_tokens"],
                records[0]["cache_tokens"],
                records[0]["output_tokens"],
                records[0]["fidelity"],
            ),
            (100, 50, 25, "exact"),
        )

    def test_agent_sessions_excluded_and_counted_in_footnote(self):
        # store 标记 agent 的会话不进日报（分类在收件箱注册侧完成，日报信任 store），
        # 其会话数与消耗进 agent_excluded 注脚，成本不失明。
        self.store.ensure("codex", "ag-1")
        self.store.patch("codex", "ag-1", origin="agent")
        self.codex_rollout(
            "ag-1",
            [
                (
                    "token_count",
                    (self.day, 10),
                    {
                        "input_tokens": 500,
                        "cached_input_tokens": 0,
                        "cache_write_input_tokens": 0,
                        "output_tokens": 100,
                        "reasoning_output_tokens": 0,
                    },
                ),
            ],
        )
        self.zcode_index("sess_u", "用户任务", "/work/u")
        self.zcode_turn(
            "sess_u", "t1", stamp(self.day, 10), stamp(self.day, 11), fresh=1000
        )
        stats = {}
        records = scan_buckets(
            self.store, self.home, self.day, self.day, agent_stats=stats
        )[self.day]
        self.assertEqual([r["session_id"] for r in records], ["sess_u"])
        self.assertEqual(stats[self.day]["tasks"], {("codex", "ag-1")})
        self.assertEqual(stats[self.day]["total_tokens"], 600)
        report = generate_day(self.store, self.home, self.day.isoformat(), refresh=True)
        self.assertEqual(report["version"], 8)
        self.assertEqual(report["agent_excluded"], {"tasks": 1, "total_tokens": 600})
        self.assertEqual([t["session_id"] for t in report["tasks"]], ["sess_u"])
        self.assertIn("1 个 agent 会话", render_markdown(report))
        # 无 agent 会话的日子不带该字段（干净 schema）。
        self.assertNotIn("agent_excluded", build_report(self.day, [], 0.0))

    def test_manual_override_excludes_from_daily_report(self):
        # 评审 R5 收尾：单条改判 agent 与目录规则同口径——从日报合计退出、进注脚。
        key = self.store.ensure("claude", "cli-ov")
        self.store.patch("claude", "cli-ov", title="改判会话", project="/work/ov")
        self.store.set_origin_override(key, "agent")
        self.claude_session(
            "cli-ov",
            stamp(self.day, 8),
            stamp(self.day, 12),
            transcript=True,
            usage_lines=[
                (
                    (self.day, 9),
                    {
                        "input_tokens": 10,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                        "output_tokens": 5,
                    },
                )
            ],
            desktop=False,
        )
        stats = {}
        records = scan_buckets(
            self.store, self.home, self.day, self.day, agent_stats=stats
        )[self.day]
        self.assertEqual(records, [])
        self.assertEqual(stats[self.day]["tasks"], {("claude", "cli-ov")})

    def test_origin_rule_overrides_report_scope(self):
        # 目录规则读时覆盖：行标 agent + 规则 user → 计入；行 user + 规则 agent → 排除。
        self.store.ensure("codex", "rule-user")
        self.store.patch("codex", "rule-user", origin="agent", project="/work/rule-u")
        self.codex_rollout(
            "rule-user",
            [
                (
                    "token_count",
                    (self.day, 10),
                    {
                        "input_tokens": 10,
                        "cached_input_tokens": 0,
                        "cache_write_input_tokens": 0,
                        "output_tokens": 5,
                        "reasoning_output_tokens": 0,
                    },
                ),
            ],
        )
        self.store.ensure("codex", "rule-agent")
        self.store.patch("codex", "rule-agent", origin="user", project="/work/rule-a")
        self.codex_rollout(
            "rule-agent",
            [
                (
                    "token_count",
                    (self.day, 11),
                    {
                        "input_tokens": 20,
                        "cached_input_tokens": 0,
                        "cache_write_input_tokens": 0,
                        "output_tokens": 0,
                        "reasoning_output_tokens": 0,
                    },
                ),
            ],
        )
        self.store.set_origin_rule("/work/rule-u", "user")
        self.store.set_origin_rule("/work/rule-a", "agent")
        stats = {}
        records = scan_buckets(
            self.store, self.home, self.day, self.day, agent_stats=stats
        )[self.day]
        self.assertEqual([r["session_id"] for r in records], ["rule-user"])
        self.assertEqual(sorted(stats[self.day]["tasks"]), [("codex", "rule-agent")])

    def test_heat_level_total_token_thresholds(self):
        values = (
            0,
            19_999_999,
            20_000_000,
            99_999_999,
            100_000_000,
            399_999_999,
            400_000_000,
        )
        self.assertEqual([heat_level(value) for value in values], [0, 1, 2, 2, 3, 3, 4])

    def test_token_text_units(self):
        self.assertEqual(
            [
                token_text(v)
                for v in (
                    0,
                    895,
                    6_594,
                    456_700,
                    613_613,
                    1_000_000,
                    55_703_404,
                    143_168_263,
                    553_010_996,
                    1_000_000_000,
                )
            ],
            ["0", "895", "6.6k", "457k", "614k", "1M", "55.7M", "143M", "553M", "1B"],
        )

    def test_generate_day_writes_v8_and_markdown(self):
        self.zcode_index("sess_a", "收件箱日报", "/work/session-manager")
        self.zcode_turn(
            "sess_a",
            "t1",
            stamp(self.day, 10),
            stamp(self.day, 12),
            fresh=40_000,
            cached=900_000,
            output=10_000,
        )
        report = generate_day(self.store, self.home, self.day.isoformat())
        self.assertEqual(report["version"], 8)
        self.assertEqual(
            (
                report["totals"]["input_tokens"],
                report["totals"]["cache_tokens"],
                report["totals"]["output_tokens"],
                report["totals"]["total_tokens"],
            ),
            (40_000, 900_000, 10_000, 950_000),
        )
        self.assertNotIn("active_seconds", report["tasks"][0])
        root = self.store.root / "reports"
        self.assertTrue((root / f"{self.day.isoformat()}.json").exists())
        markdown = (
            (root / f"{self.day.isoformat()}.json").with_suffix(".md").read_text()
        )
        self.assertIn(f"工作日报 · {self.day.isoformat()}", markdown)
        self.assertIn("合计 950k", markdown)
        self.assertIn("900k", markdown)
        self.assertIn("收件箱日报", markdown)
        self.assertEqual(
            load_report(self.store.root, self.day.isoformat())["version"], 8
        )
        # 过去日已定稿：再次查看直接读缓存，不重扫来源（新增轮次不会出现）。
        self.zcode_turn(
            "sess_a", "t2", stamp(self.day, 13), stamp(self.day, 14), fresh=1
        )
        again = generate_day(self.store, self.home, self.day.isoformat())
        self.assertEqual(again["totals"], report["totals"])
        self.assertEqual(again["generated_at"], report["generated_at"])

    def test_generate_day_keeps_today_live(self):
        today = date.today()
        self.zcode_index("sess_a")
        self.zcode_turn(
            "sess_a", "t1", stamp(today, 0, 5), stamp(today, 0, 10), fresh=10
        )
        report = generate_day(self.store, self.home, today.isoformat())
        self.assertEqual(report["totals"]["total_tokens"], 10)
        self.assertNotIn("path_md", report)
        self.assertIsNone(load_report(self.store.root, today.isoformat()))

    def test_task_segments_follow_real_activity_not_first_to_last_span(self):
        # Zcode：两轮相隔 6 小时，节奏带应得两段真实轮区间而非 09–17 一整条。
        self.zcode_index("sess_a")
        self.zcode_turn(
            "sess_a", "t1", stamp(self.day, 9), stamp(self.day, 9, 20), fresh=10
        )
        self.zcode_turn(
            "sess_a", "t2", stamp(self.day, 16), stamp(self.day, 17), fresh=10
        )
        # Claude：逐条消息时间戳，≤15 分钟聚成一段，跨大间隔分段。
        self.claude_session(
            "cli-1",
            stamp(self.day, 8),
            stamp(self.day, 13),
            usage_lines=[
                ((self.day, 8, 0), {"input_tokens": 1, "output_tokens": 1}),
                ((self.day, 8, 10), {"input_tokens": 1, "output_tokens": 1}),
                ((self.day, 8, 24), {"input_tokens": 1, "output_tokens": 1}),
                ((self.day, 12, 50), {"input_tokens": 1, "output_tokens": 1}),
            ],
        )
        records = scan_buckets(self.store, self.home, self.day, self.day)[self.day]
        by_sid = {record["session_id"]: record for record in records}
        self.assertEqual(
            by_sid["sess_a"]["segments"],
            [
                [stamp(self.day, 9), stamp(self.day, 9, 20)],
                [stamp(self.day, 16), stamp(self.day, 17)],
            ],
        )
        self.assertEqual(
            by_sid["cli-1"]["segments"],
            [
                [stamp(self.day, 8), stamp(self.day, 8, 24)],
                [stamp(self.day, 12, 50), stamp(self.day, 12, 50)],
            ],
        )

    def test_zcode_segments_use_model_requests_not_whole_turn(self):
        # 一轮 16:00–23:00 中间等用户批准 6 个多小时：节奏段只取两次真实请求；token 归属不变。
        self.zcode_index("sess_a")
        self.zcode_turn(
            "sess_a", "t1", stamp(self.day, 16), stamp(self.day, 23), fresh=100
        )
        self.zcode_requests(
            "t1",
            [
                (stamp(self.day, 16), stamp(self.day, 16, 10)),
                (stamp(self.day, 22, 50), stamp(self.day, 23)),
            ],
        )
        record = self.single(scan_buckets(self.store, self.home, self.day, self.day))
        self.assertEqual(
            record["segments"],
            [
                [stamp(self.day, 16), stamp(self.day, 16, 10)],
                [stamp(self.day, 22, 50), stamp(self.day, 23)],
            ],
        )
        self.assertEqual((record["total_tokens"], record["turns"]), (100, 1))
        self.assertEqual(
            (record["first_at"], record["last_at"]),
            (stamp(self.day, 16), stamp(self.day, 23)),
        )

    def test_segments_clamped_to_day_window_across_midnight(self):
        self.zcode_index("sess_a")
        self.zcode_turn(
            "sess_a", "t1", stamp(self.prev, 23), stamp(self.day, 1), fresh=10
        )
        buckets = scan_buckets(self.store, self.home, self.prev, self.day)
        self.assertEqual(
            buckets[self.prev][0]["segments"],
            [[stamp(self.prev, 23), stamp(self.day, 0)]],
        )
        self.assertEqual(
            buckets[self.day][0]["segments"], [[stamp(self.day, 0), stamp(self.day, 1)]]
        )

    def test_empty_day_renders_placeholder(self):
        report = generate_day(self.store, self.home, self.day.isoformat())
        self.assertIn("这一天没有会话记录", render_markdown(report))

    def test_overview_backfill_cache_invalidates_v1_and_ranks_week_projects(self):
        self.zcode_index("sess_a", project="/work/alpha")
        self.zcode_turn(
            "sess_a", "t1", stamp(self.day, 10), stamp(self.day, 11), fresh=600
        )
        self.zcode_turn(
            "sess_a", "t2", stamp(self.prev, 10), stamp(self.prev, 11), fresh=400
        )
        self.zcode_turn(
            "sess_a",
            "old",
            stamp(self.day - timedelta(days=8), 10),
            stamp(self.day - timedelta(days=8), 11),
            fresh=90_000_000,
        )
        overview = generate_overview(self.store, self.home, days=30, top=5)
        by_date = {row["date"]: row for row in overview["days"]}
        self.assertEqual(by_date[self.day.isoformat()]["total_tokens"], 600)
        self.assertEqual(by_date[self.day.isoformat()]["level"], 1)
        self.assertEqual(overview["days"][-1]["date"], date.today().isoformat())
        self.assertIsNotNone(load_report(self.store.root, self.day.isoformat()))
        self.assertIsNone(load_report(self.store.root, date.today().isoformat()))
        # Top 项目只看近 7 天：8 天前的 9000 万不参与排名。
        self.assertEqual([item["name"] for item in overview["top_projects"]], ["alpha"])
        self.assertEqual(overview["top_projects"][0]["total_tokens"], 1000)
        self.assertEqual(overview["top_projects"][0]["share"], 1.0)
        self.assertEqual(overview["week_sources"]["zcode"]["total_tokens"], 1000)
        self.assertEqual(overview["today"]["tasks"], 0)
        # 旧版本报告（无 version）视为缺失并重刷。
        stale = load_report(self.store.root, self.day.isoformat())
        stale_path = self.store.root / "reports" / f"{self.day.isoformat()}.json"
        stale.pop("version")
        stale_path.write_text(json.dumps(stale))
        self.assertIsNone(load_report(self.store.root, self.day.isoformat()))
        refreshed = generate_overview(self.store, self.home, days=30, top=5)
        self.assertEqual(
            {row["date"]: row for row in refreshed["days"]}[self.day.isoformat()][
                "total_tokens"
            ],
            600,
        )
        self.assertEqual(
            load_report(self.store.root, self.day.isoformat())["version"], 8
        )
        # 过去日读缓存：新增历史轮次不改变固化结果，总览与详情一致；只有 refresh 才重扫来源。
        self.zcode_turn(
            "sess_a", "t3", stamp(self.day, 15), stamp(self.day, 16), fresh=500
        )
        cached = {
            row["date"]: row
            for row in generate_overview(self.store, self.home, days=30, top=5)["days"]
        }
        self.assertEqual(cached[self.day.isoformat()]["total_tokens"], 600)
        self.assertEqual(
            generate_day(self.store, self.home, self.day.isoformat())["totals"][
                "total_tokens"
            ],
            600,
        )
        fresh = generate_day(self.store, self.home, self.day.isoformat(), refresh=True)
        self.assertEqual(fresh["totals"]["total_tokens"], 1100)
        self.assertEqual(
            load_report(self.store.root, self.day.isoformat())["totals"][
                "total_tokens"
            ],
            1100,
        )

    def test_overview_refreshes_past_day_snapshot_taken_before_day_end(self):
        # 当日结束前的快照缺晚间消耗：generated_at 早于当日结束的过去日在总览中补算一次后定稿。
        self.zcode_index("sess_a", project="/work/alpha")
        self.zcode_turn(
            "sess_a", "t1", stamp(self.day, 10), stamp(self.day, 11), fresh=600
        )
        snapshot = build_report(self.day, [], stamp(self.day, 20))
        (self.store.root / "reports").mkdir(parents=True, exist_ok=True)
        (self.store.root / "reports" / f"{self.day.isoformat()}.json").write_text(
            json.dumps(snapshot)
        )
        by_date = {
            row["date"]: row
            for row in generate_overview(self.store, self.home, days=30)["days"]
        }
        self.assertEqual(by_date[self.day.isoformat()]["total_tokens"], 600)
        finalized = load_report(self.store.root, self.day.isoformat())
        self.assertGreaterEqual(
            finalized["generated_at"], stamp(self.day + timedelta(days=1), 0)
        )
        # 定稿后不再随来源变化。
        self.zcode_turn(
            "sess_a", "t2", stamp(self.day, 15), stamp(self.day, 16), fresh=500
        )
        by_date = {
            row["date"]: row
            for row in generate_overview(self.store, self.home, days=30)["days"]
        }
        self.assertEqual(by_date[self.day.isoformat()]["total_tokens"], 600)

    # ---- v8：逐 model 拆分与 v7→v8 迁移（用量金额规范 §2–§3）----

    def test_zcode_reconciled_requests_split_by_model(self):
        # 对账一致：逐请求计量并按 model 归桶；轮次数每轮只记一次；节奏段取各请求区间。
        self.zcode_index("sess_a")
        self.zcode_turn(
            "sess_a",
            "t1",
            stamp(self.day, 10),
            stamp(self.day, 11),
            fresh=100,
            output=25,
        )
        self.zcode_requests(
            "t1",
            [
                (stamp(self.day, 10), stamp(self.day, 10, 10)),
                (stamp(self.day, 10, 40), stamp(self.day, 10, 50)),
            ],
            model="GLM-5.3-Flash",
            tokens=[(50, 0, 0, 10, 0), (50, 0, 0, 15, 0)],
        )
        record = self.single(scan_buckets(self.store, self.home, self.day, self.day))
        self.assertEqual(
            record["models"],
            {
                "glm-5.3-flash": {
                    "fresh_input": 100,
                    "cache_write": 0,
                    "cache_read": 0,
                    "output": 25,
                    "native_cost_usd": None,
                    "raw_names": ["GLM-5.3-Flash"],
                }
            },
        )
        self.assertEqual((record["total_tokens"], record["turns"]), (125, 1))
        self.assertEqual(
            record["segments"],
            [
                [stamp(self.day, 10), stamp(self.day, 10, 10)],
                [stamp(self.day, 10, 40), stamp(self.day, 10, 50)],
            ],
        )

    def test_zcode_reconciliation_mismatch_falls_back_to_turn_unknown(self):
        # 对账不一致：整轮退回轮级数据，model 记 unknown；节奏带仍取逐请求时段。
        self.zcode_index("sess_a")
        self.zcode_turn(
            "sess_a", "t1", stamp(self.day, 10), stamp(self.day, 11), fresh=100
        )
        self.zcode_requests(
            "t1",
            [
                (stamp(self.day, 10), stamp(self.day, 10, 10)),
                (stamp(self.day, 10, 40), stamp(self.day, 10, 50)),
            ],
            tokens=[(50, 0, 0, 10, 0), (50, 0, 0, 0, 0)],
        )
        record = self.single(scan_buckets(self.store, self.home, self.day, self.day))
        self.assertEqual(
            record["models"],
            {
                "unknown": {
                    "fresh_input": 100,
                    "cache_write": 0,
                    "cache_read": 0,
                    "output": 0,
                    "native_cost_usd": None,
                }
            },
        )
        self.assertEqual(record["total_tokens"], 100)
        self.assertEqual(
            record["segments"],
            [
                [stamp(self.day, 10), stamp(self.day, 10, 10)],
                [stamp(self.day, 10, 40), stamp(self.day, 10, 50)],
            ],
        )

    def test_zcode_multi_model_splits_by_request_share(self):
        # 多 model：轮级四项按各 model 请求四项占比逐项拆分，误差归占比最大者；
        # token 合计仍以轮级为准（规范 §2「Zcode 归属」2026-09-24 修订）。
        self.zcode_index("sess_a")
        self.zcode_turn(
            "sess_a",
            "t1",
            stamp(self.day, 10),
            stamp(self.day, 11),
            fresh=300,
            output=100,
        )  # 轮级四项 = (300, 0, 0, 100)
        self.zcode_requests(
            "t1",
            [
                (stamp(self.day, 10), stamp(self.day, 10, 20)),
                (stamp(self.day, 10, 30), stamp(self.day, 10, 40)),
            ],
            models=["model-a", "model-b"],
            tokens=[(90, 0, 0, 12, 0), (30, 0, 0, 4, 0)],  # 占比 a:b = 3:1
        )
        record = self.single(scan_buckets(self.store, self.home, self.day, self.day))
        models = record["models"]
        # 逐项拆分：输入 300*3/4=225（a）、75（b）；输出 100*3/4=75、25。合计守恒。
        self.assertEqual(models["model-a"]["fresh_input"], 225)
        self.assertEqual(models["model-b"]["fresh_input"], 75)
        self.assertEqual(models["model-a"]["output"], 75)
        self.assertEqual(models["model-b"]["output"], 25)
        self.assertEqual(
            sum(entry["fresh_input"] + entry["output"] for entry in models.values()),
            400,
        )
        self.assertEqual(record["total_tokens"], 400)

    def test_zcode_turn_without_model_usage_is_unknown(self):
        # 该轮没有 model_usage 记录：整轮 unknown（token 仍以轮级为准）。
        self.zcode_index("sess_a")
        self.zcode_turn("sess_a", "t1", stamp(self.day, 10), stamp(self.day, 10, 30), fresh=88)
        record = self.single(scan_buckets(self.store, self.home, self.day, self.day))
        self.assertEqual(record["models"]["unknown"]["fresh_input"], 88)
        self.assertEqual(record["total_tokens"], 88)

    def test_codex_model_from_turn_context_and_unknown_without(self):
        usage = {
            "input_tokens": 100,
            "cache_write_input_tokens": 10,
            "cached_input_tokens": 20,
            "output_tokens": 30,
            "reasoning_output_tokens": 5,
        }
        self.codex_rollout(
            "c1",
            [
                ("turn_context", (self.day, 9), "gpt-6-astra"),
                ("token_count", (self.day, 9, 5), usage),
            ],
        )
        self.codex_rollout("c2", [("token_count", (self.day, 10), usage)])
        records = scan_buckets(self.store, self.home, self.day, self.day)[self.day]
        by_sid = {record["session_id"]: record for record in records}
        self.assertEqual(
            by_sid["c1"]["models"],
            {
                "gpt-6-astra": {
                    "fresh_input": 100,
                    "cache_write": 10,
                    "cache_read": 20,
                    "output": 35,
                    "native_cost_usd": None,
                    "raw_names": ["gpt-6-astra"],
                }
            },
        )
        self.assertEqual(
            by_sid["c2"]["models"]["unknown"],
            {
                "fresh_input": 100,
                "cache_write": 10,
                "cache_read": 20,
                "output": 35,
                "native_cost_usd": None,
            },
        )

    def test_claude_pi_models_and_native_cost(self):
        self.claude_session(
            "cli-1",
            stamp(self.day, 8),
            stamp(self.day, 9),
            usage_lines=[
                (
                    (self.day, 9),
                    {
                        "input_tokens": 100,
                        "cache_creation_input_tokens": 10,
                        "cache_read_input_tokens": 20,
                        "output_tokens": 30,
                    },
                    "MiniMax-M3",
                )
            ],
        )
        self.pi_session(
            "pi-1",
            [
                (
                    (self.day, 10),
                    {
                        "input": 50,
                        "cacheWrite": 5,
                        "cacheRead": 8,
                        "output": 12,
                        "reasoning": 3,
                    },
                    "glm-5.3-flash",
                    0.0005,
                )
            ],
        )
        buckets = scan_buckets(self.store, self.home, self.day, self.day)
        report = build_report(self.day, buckets[self.day], time.time())
        self.assertEqual(
            report["totals"]["models"],
            {
                "minimax-m3": {
                    "fresh_input": 100,
                    "cache_write": 10,
                    "cache_read": 20,
                    "output": 30,
                    "native_cost_usd": None,
                },
                "glm-5.3-flash": {
                    "fresh_input": 50,
                    "cache_write": 5,
                    "cache_read": 8,
                    "output": 15,
                    "native_cost_usd": 0.0005,
                },
            },
        )

    def test_kimi_opencode_models_and_native_cost(self):
        self.kimi_wire(
            "k1",
            [
                (
                    stamp(self.day, 11),
                    {
                        "inputOther": 40,
                        "inputCacheCreation": 4,
                        "inputCacheRead": 6,
                        "output": 9,
                    },
                    "MiniMax-M3",
                )
            ],
        )
        self.opencode_messages(
            "oc1",
            [
                (
                    (12,),
                    {
                        "input": 30,
                        "output": 5,
                        "reasoning": 2,
                        "cache": {"write": 3, "read": 7},
                    },
                    "MiniMax-M2.7",
                    0.007,
                )
            ],
        )
        buckets = scan_buckets(self.store, self.home, self.day, self.day)
        report = build_report(self.day, buckets[self.day], time.time())
        self.assertEqual(report["totals"]["models"]["minimax-m3"]["output"], 9)
        entry = report["totals"]["models"]["minimax-m2.7"]
        self.assertEqual(
            (
                entry["fresh_input"],
                entry["cache_write"],
                entry["cache_read"],
                entry["output"],
            ),
            (30, 3, 7, 7),
        )
        self.assertEqual(entry["native_cost_usd"], 0.007)

    def test_unavailable_task_keeps_models_empty(self):
        self.claude_session(
            "cli-1",
            stamp(self.day, 8),
            stamp(self.day, 9),
            transcript=False,
        )
        record = self.single(scan_buckets(self.store, self.home, self.day, self.day))
        self.assertEqual(record["fidelity"], "unavailable")
        self.assertEqual(record["models"], {})

    def test_totals_models_merge_across_tasks_with_raw_names(self):
        usage = {
            "input_tokens": 10,
            "cache_write_input_tokens": 0,
            "cached_input_tokens": 0,
            "output_tokens": 1,
            "reasoning_output_tokens": 0,
        }
        self.codex_rollout(
            "c1",
            [
                ("turn_context", (self.day, 9), "zai-coding-plan/gpt-6-astra"),
                ("token_count", (self.day, 9, 5), usage),
            ],
        )
        self.codex_rollout(
            "c2",
            [
                ("turn_context", (self.day, 10), "GPT-6-Astra"),
                ("token_count", (self.day, 10, 5), usage),
            ],
        )
        buckets = scan_buckets(self.store, self.home, self.day, self.day)
        report = build_report(self.day, buckets[self.day], time.time())
        merged = report["totals"]["models"]["gpt-6-astra"]
        self.assertEqual(merged["fresh_input"], 20)
        # raw_names 只在任务级（规范 §3 totals.models 无此字段）。
        raw_names = sorted(
            name
            for record in buckets[self.day]
            for name in record["models"]["gpt-6-astra"]["raw_names"]
        )
        self.assertEqual(raw_names, ["GPT-6-Astra", "zai-coding-plan/gpt-6-astra"])

    def write_v7_report(self, total):
        day = self.day.isoformat()
        task = {
            "provider": "codex",
            "session_id": "old-1",
            "title": "旧任务",
            "project": "/work/x",
            "first_at": stamp(self.day, 9),
            "last_at": stamp(self.day, 10),
            "input_tokens": 100,
            "cache_tokens": 20,
            "output_tokens": 30,
            "total_tokens": total,
            "turns": 1,
            "state": "idle",
            "fidelity": "exact",
            "segments": [[stamp(self.day, 9), stamp(self.day, 10)]],
        }
        totals = {
            "input_tokens": 100,
            "cache_tokens": 20,
            "output_tokens": 30,
            "total_tokens": total,
            "tasks": 1,
            "turns": 1,
            "sources": {
                "codex": {
                    key: task[key]
                    for key in (
                        "input_tokens",
                        "cache_tokens",
                        "output_tokens",
                        "total_tokens",
                    )
                }
            },
            "projects": {
                "/work/x": {
                    key: task[key]
                    for key in (
                        "input_tokens",
                        "cache_tokens",
                        "output_tokens",
                        "total_tokens",
                    )
                }
            },
        }
        if total != 150:
            totals["total_tokens"] = total
            task["total_tokens"] = total
        report = {
            "version": 7,
            "date": day,
            "generated_at": stamp(self.day + timedelta(days=1), 0) + 100,
            "totals": totals,
            "tasks": [task],
        }
        directory = self.store.root / "reports"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{day}.json").write_text(json.dumps(report))
        return report

    def test_v7_migration_rewrites_to_unknown_when_sources_gone(self):
        # U2：v7 存在但来源已清理（v8 合计更少）→ 改用 v7 内容，unknown 拆分，不降级。
        self.write_v7_report(150)
        report = generate_day(self.store, self.home, self.day.isoformat())
        self.assertEqual(report["version"], 8)
        self.assertEqual(report.get("migrated_from"), 7)
        self.assertEqual(report["totals"]["total_tokens"], 150)
        self.assertEqual(
            report["tasks"][0]["models"],
            {
                "unknown": {
                    "fresh_input": 100,
                    "cache_write": 0,
                    "cache_read": 20,
                    "output": 30,
                }
            },
        )
        self.assertEqual(
            report["totals"]["models"]["unknown"],
            {
                "fresh_input": 100,
                "cache_write": 0,
                "cache_read": 20,
                "output": 30,
                "native_cost_usd": None,
            },
        )
        backup = self.store.root / "reports.v7.bak" / f"{self.day.isoformat()}.json"
        self.assertTrue(backup.exists())
        self.assertEqual(json.loads(backup.read_text())["version"], 7)
        # 迁移结果已固化：再次读取命中 v8 缓存。
        self.assertEqual(
            load_report(self.store.root, self.day.isoformat())["version"], 8
        )
    def test_v7_migration_uses_v8_when_not_smaller(self):
        # 按任务合并（§3.1.3 修订）：重扫任务用 v8，v7 独有任务恢复并标记。
        self.write_v7_report(10)
        self.zcode_index("sess_a")
        self.zcode_turn(
            "sess_a", "t1", stamp(self.day, 10), stamp(self.day, 10, 30), fresh=100
        )
        report = generate_day(self.store, self.home, self.day.isoformat())
        self.assertEqual(report["version"], 8)
        self.assertEqual(report.get("migrated_from"), 7)
        self.assertEqual(report["totals"]["total_tokens"], 110)
        restored = [task for task in report["tasks"] if task.get("restored_from") == 7]
        self.assertEqual([task["session_id"] for task in restored], ["old-1"])
        self.assertTrue((self.store.root / "reports.v7.bak").exists())

    def test_partial_cleanup_diff_becomes_unknown(self):
        # 同一任务 v8 合计小于 v7（部分来源被清理）：三类沿用 v7，差额记 unknown。
        self.write_v7_report(100)
        # v7 任务三类对齐 total=100（60 输入 / 0 缓存 / 40 输出）。
        day_path = self.store.root / "reports" / f"{self.day.isoformat()}.json"
        data = json.loads(day_path.read_text())
        task = data["tasks"][0]
        task.update(input_tokens=60, cache_tokens=0, output_tokens=40, total_tokens=100)
        for group in ("sources", "projects"):
            for entry in data["totals"][group].values():
                entry.update(input_tokens=60, cache_tokens=0, output_tokens=40, total_tokens=100)
        data["totals"].update(input_tokens=60, cache_tokens=0, output_tokens=40, total_tokens=100)
        day_path.write_text(json.dumps(data))
        # 重扫同 session_id（codex old-1）但只有 60 tokens
        usage = {
            "input_tokens": 40,
            "cache_write_input_tokens": 0,
            "cached_input_tokens": 0,
            "output_tokens": 20,
            "reasoning_output_tokens": 0,
        }
        self.codex_rollout("old-1", [("token_count", (self.day, 9, 30), usage)])
        report = generate_day(self.store, self.home, self.day.isoformat())
        task = next(t for t in report["tasks"] if t["session_id"] == "old-1")
        self.assertEqual(task["total_tokens"], 100)  # 沿用 v7 合计
        unknown = task["models"]["unknown"]
        # unknown = 重扫自身的 unknown（40f+20o，codex 无 turn_context）
        #          + 清理差额（20f + 20o）→ 60f / 40o，与沿用后的三类合计一致。
        self.assertEqual(unknown["fresh_input"], 60)
        self.assertEqual(unknown["output"], 40)
        self.assertEqual(report["totals"]["total_tokens"], 100)

    def test_refresh_does_not_degrade_restored_day(self):
        # 对已恢复日 --refresh：重扫更少也不降级（§3.1.4 长期约束）。
        self.write_v7_report(150)
        report = generate_day(self.store, self.home, self.day.isoformat())
        self.assertEqual(report["totals"]["total_tokens"], 150)
        # 再次 refresh（来源仍为空）：合计保持 150，不降为 0。
        again = generate_day(self.store, self.home, self.day.isoformat(), refresh=True)
        self.assertEqual(again["totals"]["total_tokens"], 150)
        self.assertEqual(again.get("migrated_from"), 7)

    def test_backup_fallback_when_current_report_missing(self):
        # 磁盘现有报告缺失时回退 reports.v7.bak（§3.1.4）。
        self.write_v7_report(150)
        backup = self.store.root / "reports.v7.bak"
        backup.mkdir(parents=True, exist_ok=True)
        shutil.copy2(
            self.store.root / "reports" / f"{self.day.isoformat()}.json",
            backup / f"{self.day.isoformat()}.json",
        )
        (self.store.root / "reports" / f"{self.day.isoformat()}.json").unlink()
        report = generate_day(self.store, self.home, self.day.isoformat())
        self.assertEqual(report["totals"]["total_tokens"], 150)
        self.assertEqual(report.get("migrated_from"), 7)

    def test_cost_level_thresholds_on_unsorted_amounts(self):
        # R17 回归：乱序金额排序后取 50/75/90 分位，各级分布正确。
        from daily_report import cost_level

        amounts = sorted([5, 100, 20, 8, 50, 200, 3, 12, 80, 1])  # 10 个样本乱序输入
        thresholds = [amounts[int(len(amounts) * q)] for q in (0.5, 0.75, 0.9)]
        self.assertEqual(thresholds, [20, 80, 200])  # int(10*q) 索引取 5/7/9 号样本
        levels = [cost_level(value, thresholds) for value in [1, 3, 5, 8, 12, 20, 50, 80, 100, 200]]
        self.assertEqual(levels, [1, 1, 1, 1, 1, 2, 2, 3, 3, 4])
        self.assertEqual(cost_level(0, thresholds), 0)
        self.assertEqual(cost_level(10, [0, 0, 0]), 0)  # 样本不足时不分级


class DailyReportPrivacyTests(unittest.TestCase):
    """DR14 端到端：收件箱采集（标题 ≤80 入库）之后生成日报，报告对象与落盘
    json/md 只允许携带截断后的标题；首条消息八十字符之后的尾部、其余用户消息
    正文与 assistant 正文不得出现在日报输出与存储目录任何文件中。"""

    TAIL = "DR14-泄漏-首条消息八十字符之后"
    SECOND = "DR14-泄漏-第二条用户消息正文"
    ASSISTANT = "DR14-泄漏-助手消息正文"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.store = Store(self.home / "state")
        self.day = date.today() - timedelta(days=3)

    def assert_no_markers(self, markers, *texts):
        for text in texts:
            for marker in markers:
                self.assertNotIn(marker, text)

    def test_report_output_and_store_carry_no_message_bodies(self):
        from inbox_sources import collect_claude

        sid = "cccccccc-2222-3333-4444-555555555555"
        prefix = ("DR14 日报标题：" + "x" * 100)[:80]
        self.assertEqual(len(prefix), 80)
        self.store.patch("claude", sid, locator={"kind": "cli", "cwd": "/work/cli"})
        path = self.home / ".claude/projects/proj" / f"{sid}.jsonl"
        path.parent.mkdir(parents=True)
        lines = [
            json.dumps(
                {
                    "type": "user",
                    "isMeta": True,
                    "message": {"content": "注入上下文 " + self.SECOND},
                    "sessionId": sid,
                }
            ),
            json.dumps(
                {
                    "type": "user",
                    "message": {"content": prefix + self.TAIL},
                    "sessionId": sid,
                }
            ),
            json.dumps(
                {
                    "type": "user",
                    "message": {"content": "第二条用户消息 " + self.SECOND},
                    "sessionId": sid,
                }
            ),
            json.dumps(
                {
                    "type": "assistant",
                    "sessionId": sid,
                    "timestamp": iso(self.day, 9),
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": self.ASSISTANT}],
                        "usage": {
                            "input_tokens": 100,
                            "cache_creation_input_tokens": 10,
                            "cache_read_input_tokens": 500,
                            "output_tokens": 20,
                        },
                    },
                }
            ),
        ]
        path.write_text("\n".join(lines) + "\n")
        collect_claude(self.store, self.home)  # 收件箱侧先把 ≤80 标题写入 store
        self.assertEqual(self.store.rows()[0]["title"], prefix)
        report = generate_day(self.store, self.home, self.day.isoformat())
        tasks = [task for task in report["tasks"] if task["provider"] == "claude"]
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["title"], prefix)
        # ensure_ascii=False：标记含中文，默认转义会让断言落空。
        serialized = json.dumps(report, ensure_ascii=False)
        reports = self.store.root / "reports"
        markdown = (reports / f"{self.day.isoformat()}.md").read_text()
        self.assert_no_markers([self.TAIL, self.SECOND, self.ASSISTANT], serialized, markdown)
        for file in sorted(self.store.root.rglob("*")):
            if not file.is_file():
                continue
            blob = file.read_bytes()
            for marker in (self.TAIL, self.SECOND, self.ASSISTANT):
                self.assertNotIn(marker.encode("utf-8"), blob, str(file))


# ---- 合并关口的精确合同与总览金额分级（2026-09-26 任务 002，只新增） -----------------------


class MergeDayTasksContractTests(unittest.TestCase):
    """变异测试缺口：性质测试只断言合并的内部一致与不降级，以下精确行为此前无测试。"""

    def _task(self, provider, session, input_tokens, cache_tokens, output_tokens, **extra):
        record = {
            "provider": provider,
            "session_id": session,
            "title": "t",
            "project": "/work/x",
            "input_tokens": input_tokens,
            "cache_tokens": cache_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + cache_tokens + output_tokens,
            "fidelity": "exact",
            "models": {
                "m": {
                    "fresh_input": input_tokens,
                    "cache_write": 0,
                    "cache_read": cache_tokens,
                    "output": output_tokens,
                }
            },
        }
        record.update(extra)
        return record

    def test_equal_totals_with_different_classes_take_rescan(self):
        # 三类合计相等但类别不同：采用重扫记录原样，不走差额补 unknown（<= 不能弱化为 <）。
        from daily_report import _merge_day_tasks

        old = self._task("pi", "s1", 100, 0, 100)
        new = self._task("pi", "s1", 0, 200, 0)
        merged, restored = _merge_day_tasks({"tasks": [new]}, {"tasks": [old]}, set())
        self.assertFalse(restored)
        self.assertEqual(merged, [new])

    def test_downgrade_protection_caps_each_class_at_max(self):
        # 重扫合计更小时：每一类恰等于 max(现有, 重扫)，正差额如数记入 unknown，
        # model 明细与三类合计保持一致（H0925-4 的量化口径）。
        from daily_report import _merge_day_tasks

        old = self._task("pi", "s1", 0, 100, 0)
        new = self._task("pi", "s1", 0, 30, 0)
        merged, _ = _merge_day_tasks({"tasks": [new]}, {"tasks": [old]}, set())
        task = merged[0]
        self.assertEqual(
            (task["input_tokens"], task["cache_tokens"], task["output_tokens"]),
            (0, 100, 0),
        )
        self.assertEqual(task["total_tokens"], 100)
        self.assertEqual(
            task["models"]["m"],
            {"fresh_input": 0, "cache_write": 0, "cache_read": 30, "output": 0},
        )
        self.assertEqual(
            task["models"]["unknown"],
            {"fresh_input": 0, "cache_write": 0, "cache_read": 70, "output": 0},
        )

    def test_zero_total_restored_task_dropped_unless_unavailable(self):
        # 只在现有报告中的任务：合计为 0 丢弃，除非 fidelity 为 unavailable；
        # 合计为 1 的任务必须保留（> 0 不能弱化为 > 1）。
        from daily_report import _merge_day_tasks

        zero = self._task("pi", "z1", 0, 0, 0)
        unavailable = self._task("pi", "z2", 0, 0, 0, fidelity="unavailable")
        tiny = self._task("pi", "z3", 1, 0, 0)
        merged, _ = _merge_day_tasks(
            {"tasks": []}, {"tasks": [zero, unavailable, tiny]}, set()
        )
        self.assertEqual(
            {(task["provider"], task["session_id"]) for task in merged},
            {("pi", "z2"), ("pi", "z3")},
        )


class OverviewCostLevelTests(unittest.TestCase):
    """V080-R7：热力图按金额着色——阈值来自每日金额分位，分级不得恒为 0。"""

    def test_cost_levels_graded_by_daily_amount(self):
        from daily_report import day_bounds, finalize_day_report

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            store = Store(home / "state")
            pricing = store.root / "pricing"
            pricing.mkdir(parents=True, exist_ok=True)
            (pricing / "override.json").write_text(
                json.dumps(
                    {"glm-x": {"input": 1.0, "output": 2.0, "currency": "CNY"}}
                )
            )
            today = date.today()
            for amount in range(1, 11):
                day = today - timedelta(days=11 - amount)
                tokens = amount * 1_000_000
                record = {
                    "provider": "pi",
                    "session_id": f"s{amount}",
                    "title": "t",
                    "project": "/work/x",
                    "first_at": 0,
                    "last_at": 1,
                    "input_tokens": tokens,
                    "cache_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": tokens,
                    "turns": 1,
                    "state": "idle",
                    "fidelity": "exact",
                    "segments": [],
                    "models": {
                        "glm-x": {
                            "fresh_input": tokens,
                            "cache_write": 0,
                            "cache_read": 0,
                            "output": 0,
                            "native_cost_usd": None,
                        }
                    },
                }
                report = build_report(day, [record], 0.0)
                report["generated_at"] = day_bounds(day)[1] + 100
                finalize_day_report(store.root, day, report)
            overview = generate_overview(store, home, days=11)
            levels = {row["date"]: row["cost_level"] for row in overview["days"]}
            # 10 个有消耗日的金额恰为 1..10 元：50/75/90 分位 = 6/8/10，分级覆盖 1–4。
            self.assertNotEqual(set(levels.values()), {0})  # 缺陷形态：全为 0
            self.assertEqual(levels[(today - timedelta(days=10)).isoformat()], 1)
            self.assertEqual(levels[(today - timedelta(days=5)).isoformat()], 2)
            self.assertEqual(levels[(today - timedelta(days=3)).isoformat()], 3)
            self.assertEqual(levels[(today - timedelta(days=1)).isoformat()], 4)
            self.assertEqual(levels[today.isoformat()], 0)  # 无消耗日仍为 0


if __name__ == "__main__":
    unittest.main()
