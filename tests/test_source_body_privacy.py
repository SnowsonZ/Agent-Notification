"""DR14对其余来源的「正文不提取、不持久化、不输出」测试（任务 E12）。

对收件箱与日报实际采集的每个来源（Codex、Zcode、Kimi、Pi、OpenCode、agy）构造
真实格式夹具，在用户消息与助手消息正文里放唯一标记串，经产品代码采集（收件箱
采集，涉及用量的来源再走 generate_day 生成日报）后断言：标记串不出现在日报输出
（报告对象 ensure_ascii=False 序列化与落盘 json/md），也不出现在存储目录任何文件
的字节中。

各来源标题字段的性质（与 docs/specs/unified-inbox.md、daily-report.md 口径一致）：
- Codex/Zcode/OpenCode/agy：标题来自来源自有的标题元数据（session_index 的
  thread_name、任务索引的 title 列、opencode session 表与插件的 session_title、
  summaries 库的 title/preview），采集器不读消息正文；
- Kimi：无收件箱采集入口（inbox_sources.py 无 kimi 采集器），日报只读 wire.jsonl
  的 usage.record 行，正文行在内存解析后按 type 过滤丢弃；
- Pi：展示标题按 unified-inbox.md「优先自定义会话名，否则截取首条用户消息最多
  80 字符」从会话文件限量读取，与 claude CLI 属同一量级的受控展示摘要；本测试
  证明 80 字符窗口之外的正文、其余用户消息与助手消息不得落库或出现在报告。
"""

import fcntl
import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from daily_report import generate_day
from inbox_sources import collect_agy_titles, collect_codex, collect_zcode
from inbox_store import Store
from pi_titles import collect_pi_titles
from session_binding import record_event, register


def stamp(day, hour=0, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute).timestamp()


def iso(day, hour, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute).isoformat()


class SourceBodyPrivacyTests(unittest.TestCase):
    """标记串只放在消息正文与来源自有数据里，采集后必须一个都不带走。"""

    CODEX_USER = "E12PRV-codex-user-正文标记泄漏探测串"
    CODEX_ASSISTANT = "E12PRV-codex-assistant-正文标记泄漏探测串"
    CODEX_TITLE = "Codex 隐私夹具标题"
    ZCODE_USER = "E12PRV-zcode-user-正文标记泄漏探测串"
    ZCODE_ASSISTANT = "E12PRV-zcode-assistant-正文标记泄漏探测串"
    ZCODE_TITLE = "Zcode 隐私夹具标题"
    KIMI_USER = "E12PRV-kimi-user-正文标记泄漏探测串"
    KIMI_ASSISTANT = "E12PRV-kimi-assistant-正文标记泄漏探测串"
    PI_TAIL = "E12PRV-pi-首条消息八十字符之后-正文标记泄漏探测串"
    PI_SECOND = "E12PRV-pi-second-第二条用户消息正文标记泄漏探测串"
    PI_ASSISTANT = "E12PRV-pi-assistant-正文标记泄漏探测串"
    OPENCODE_USER = "E12PRV-opencode-user-正文标记泄漏探测串"
    OPENCODE_ASSISTANT = "E12PRV-opencode-assistant-正文标记泄漏探测串"
    OPENCODE_TITLE = "OpenCode 隐私夹具标题"
    AGY_USER = "E12PRV-agy-user-正文标记泄漏探测串"
    AGY_ASSISTANT = "E12PRV-agy-assistant-正文标记泄漏探测串"
    AGY_TITLE = "agy 隐私夹具标题"
    AGY_UNTRACKED = "E12PRV-agy-untracked-未受管理会话标记泄漏探测串"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.store = Store(self.home / "state")
        self.day = date.today() - timedelta(days=3)

    def assert_store_bytes_free_of(self, markers):
        for path in sorted(self.store.root.rglob("*")):
            if not path.is_file():
                continue
            blob = path.read_bytes()
            for marker in markers:
                self.assertNotIn(marker.encode("utf-8"), blob, str(path))

    def assert_markers_absent(self, report, markers):
        """日报对象（ensure_ascii=False，标记含中文时默认转义会让断言落空）、
        落盘 markdown 与存储目录任何文件的字节都不得含标记串。"""
        serialized = json.dumps(report, ensure_ascii=False)
        markdown = (
            self.store.root / "reports" / f"{self.day.isoformat()}.md"
        ).read_text()
        for marker in markers:
            self.assertNotIn(marker, serialized)
            self.assertNotIn(marker, markdown)
        self.assert_store_bytes_free_of(markers)

    # ---------- Codex：rollout 正文行 + index 标题元数据 ----------

    def codex_fixtures(self, sid):
        directory = self.home / ".codex/sessions/2026/09"
        directory.mkdir(parents=True)
        body = [
            json.dumps(
                {
                    "type": "session_meta",
                    "payload": {"id": sid, "originator": "Codex Desktop", "cwd": "/work/codex"},
                }
            ),
            # rollout 里的消息正文行（response_item 与 event_msg 两种形态）。
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": self.CODEX_USER}],
                    },
                    "timestamp": iso(self.day, 9),
                }
            ),
            json.dumps(
                {
                    "type": "event_msg",
                    "payload": {"type": "user_message", "message": self.CODEX_USER},
                    "timestamp": iso(self.day, 9),
                }
            ),
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": self.CODEX_ASSISTANT}],
                    },
                    "timestamp": iso(self.day, 9, 5),
                }
            ),
            json.dumps(
                {
                    "type": "event_msg",
                    "payload": {"type": "agent_message", "message": self.CODEX_ASSISTANT},
                    "timestamp": iso(self.day, 9, 5),
                }
            ),
            json.dumps(
                {
                    "type": "turn_context",
                    "payload": {"model": "model-fixture-1"},
                    "timestamp": iso(self.day, 9),
                }
            ),
            json.dumps(
                {
                    "type": "event_msg",
                    "payload": {"type": "task_started", "turn_id": "turn-1"},
                    "timestamp": iso(self.day, 9),
                }
            ),
            json.dumps(
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "last_token_usage": {
                                "input_tokens": 1000,
                                "cache_write_input_tokens": 50,
                                "cached_input_tokens": 400,
                                "output_tokens": 200,
                                "reasoning_output_tokens": 20,
                            }
                        },
                    },
                    "timestamp": iso(self.day, 9, 30),
                }
            ),
            json.dumps(
                {
                    "type": "event_msg",
                    "payload": {"type": "task_complete", "turn_id": "turn-1"},
                    "timestamp": iso(self.day, 9, 31),
                }
            ),
        ]
        (directory / f"rollout-{sid}.jsonl").write_text("\n".join(body) + "\n")
        # 标题来自 Desktop 侧栏索引（来源自有元数据），与正文无关。
        index = self.home / ".codex/session_index.jsonl"
        index.write_text(
            json.dumps(
                {"id": sid, "thread_name": self.CODEX_TITLE, "updated_at": iso(self.day, 9, 31)}
            )
            + "\n"
        )

    def test_codex_bodies_stay_out_of_store_and_report(self):
        self.codex_fixtures("codex-privacy-1")
        health = collect_codex(self.store, self.home)
        self.assertEqual(health["status"], "ok")
        row = next(r for r in self.store.rows() if r["provider"] == "codex")
        self.assertEqual(row["title"], self.CODEX_TITLE)
        report = generate_day(self.store, self.home, self.day.isoformat())
        task = next(t for t in report["tasks"] if t["provider"] == "codex")
        self.assertEqual(task["title"], self.CODEX_TITLE)
        self.assertEqual(
            (task["input_tokens"], task["cache_tokens"], task["output_tokens"]),
            (1050, 400, 220),
        )
        self.assert_markers_absent(report, (self.CODEX_USER, self.CODEX_ASSISTANT))

    # ---------- Zcode：part 正文列 + 任务索引标题元数据 ----------

    def zcode_fixtures(self, sid):
        index = self.home / ".zcode/v2/tasks-index.sqlite"
        index.parent.mkdir(parents=True)
        with sqlite3.connect(index) as db:
            db.execute(
                "CREATE TABLE tasks (task_id,title,workspace_path,task_status,"
                "unread_at,updated_at,archived,deleted,last_unread_at)"
            )
            db.execute(
                "INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?)",
                (sid, self.ZCODE_TITLE, "/work/zcode", "completed", None, 0, 0, 0, 0),
            )
        runtime = self.home / ".zcode/cli/db/db.sqlite"
        runtime.parent.mkdir(parents=True)
        started = round(stamp(self.day, 10) * 1000)
        ended = round(stamp(self.day, 10, 30) * 1000)
        with sqlite3.connect(runtime) as db:
            db.execute(
                "CREATE TABLE turn_usage (session_id,turn_id,status,started_at,"
                "completed_at,input_tokens,cache_read_input_tokens,"
                "cache_creation_input_tokens,output_tokens,reasoning_tokens)"
            )
            db.execute(
                "CREATE TABLE model_usage (turn_id,session_id,model_id,input_tokens,"
                "cache_read_input_tokens,cache_creation_input_tokens,output_tokens,"
                "reasoning_tokens,started_at,completed_at)"
            )
            db.execute(
                "CREATE TABLE part (id text primary key, message_id text, "
                "session_id text, time_created integer, time_updated integer, data text)"
            )
            db.execute(
                "INSERT INTO turn_usage VALUES (?,?,?,?,?,?,?,?,?,?)",
                (sid, "turn-1", "completed", started, ended, 1000, 400, 50, 200, 20),
            )
            db.execute(
                "INSERT INTO model_usage VALUES (?,?,?,?,?,?,?,?,?,?)",
                (sid, "turn-1", "model-fixture-1", 1000, 400, 50, 200, 20, started, ended),
            )
            # part.data 是流式正文部件：采集器只取 session_id/message_id/time_updated
            # 三列，正文列必须留在库里。
            db.execute(
                "INSERT INTO part VALUES (?,?,?,?,?,?)",
                ("p1", "m1", sid, started, started, json.dumps({"text": self.ZCODE_USER})),
            )
            db.execute(
                "INSERT INTO part VALUES (?,?,?,?,?,?)",
                (
                    "p2",
                    "m2",
                    sid,
                    ended,
                    ended,
                    json.dumps({"text": self.ZCODE_ASSISTANT}),
                ),
            )

    def test_zcode_bodies_stay_out_of_store_and_report(self):
        self.zcode_fixtures("sess_privacy")
        health = collect_zcode(self.store, self.home)
        self.assertEqual(health["status"], "ok")
        row = next(r for r in self.store.rows() if r["provider"] == "zcode")
        self.assertEqual(row["title"], self.ZCODE_TITLE)
        report = generate_day(self.store, self.home, self.day.isoformat())
        task = next(t for t in report["tasks"] if t["provider"] == "zcode")
        self.assertEqual(task["title"], self.ZCODE_TITLE)
        self.assertEqual(
            (task["input_tokens"], task["cache_tokens"], task["output_tokens"]),
            (650, 400, 220),
        )
        self.assert_markers_absent(report, (self.ZCODE_USER, self.ZCODE_ASSISTANT))

    # ---------- Kimi：wire.jsonl 正文行与 usage.record 并存 ----------

    def test_kimi_bodies_stay_out_of_store_and_report(self):
        sid = "kimi-privacy-1"
        path = (
            self.home
            / ".kimi-code/sessions/wd_privacy"
            / f"session_{sid}"
            / "agents/main/wire.jsonl"
        )
        path.parent.mkdir(parents=True)
        lines = [
            json.dumps(
                {
                    "type": "message",
                    "role": "user",
                    "content": self.KIMI_USER,
                    "time": round(stamp(self.day, 10) * 1000),
                }
            ),
            json.dumps(
                {
                    "type": "message",
                    "role": "assistant",
                    "content": self.KIMI_ASSISTANT,
                    "time": round(stamp(self.day, 10, 5) * 1000),
                }
            ),
            json.dumps(
                {
                    "type": "usage.record",
                    "usage": {
                        "inputOther": 1000,
                        "inputCacheCreation": 50,
                        "inputCacheRead": 400,
                        "output": 220,
                    },
                    "time": round(stamp(self.day, 10, 6) * 1000),
                    "model": "model-fixture-1",
                }
            ),
        ]
        path.write_text("\n".join(lines) + "\n")
        report = generate_day(self.store, self.home, self.day.isoformat())
        task = next(t for t in report["tasks"] if t["provider"] == "kimi")
        self.assertEqual(
            (task["input_tokens"], task["cache_tokens"], task["output_tokens"]),
            (1050, 400, 220),
        )
        self.assert_markers_absent(report, (self.KIMI_USER, self.KIMI_ASSISTANT))

    # ---------- Pi：标题只许首条用户消息 ≤80 字符，其余正文不得外泄 ----------

    def pi_fixtures(self, sid):
        self.store.ensure("pi", sid)
        self.store.patch("pi", sid, project="/work/pi")
        path = self.home / ".pi/agent/sessions/s" / f"{sid}.jsonl"
        path.parent.mkdir(parents=True)
        prefix = ("Pi 隐私标题：" + "x" * 100)[:80]
        self.assertEqual(len(prefix), 80)
        lines = [
            json.dumps(
                {"type": "session", "id": sid, "timestamp": iso(self.day, 8), "cwd": "/work/pi"}
            ),
            json.dumps(
                {
                    "type": "message",
                    "timestamp": iso(self.day, 8, 1),
                    "message": {"role": "user", "content": prefix + self.PI_TAIL},
                }
            ),
            json.dumps(
                {
                    "type": "message",
                    "timestamp": iso(self.day, 8, 2),
                    "message": {"role": "user", "content": self.PI_SECOND},
                }
            ),
            json.dumps(
                {
                    "type": "message",
                    "timestamp": iso(self.day, 9),
                    "message": {
                        "role": "assistant",
                        "model": "model-fixture-1",
                        "content": [{"type": "text", "text": self.PI_ASSISTANT}],
                        "usage": {
                            "input": 1000,
                            "cacheWrite": 50,
                            "cacheRead": 400,
                            "output": 200,
                            "reasoning": 20,
                        },
                    },
                }
            ),
        ]
        path.write_text("\n".join(lines) + "\n")
        self.store.set_meta("pi-file:" + sid, str(path))
        return prefix

    def test_pi_bodies_beyond_bounded_title_stay_out_of_store_and_report(self):
        prefix = self.pi_fixtures("pi-privacy-1")
        health = collect_pi_titles(self.store, self.home)
        self.assertEqual(health["status"], "ok")
        row = next(r for r in self.store.rows() if r["provider"] == "pi")
        # 落库的只有 ≤80 字符的展示摘要（unified-inbox.md 的 Pi 标题口径）。
        self.assertEqual(row["title"], prefix)
        report = generate_day(self.store, self.home, self.day.isoformat())
        task = next(t for t in report["tasks"] if t["provider"] == "pi")
        self.assertEqual(task["title"], prefix)
        self.assertEqual(
            (task["input_tokens"], task["cache_tokens"], task["output_tokens"]),
            (1050, 400, 220),
        )
        self.assert_markers_absent(
            report, (self.PI_TAIL, self.PI_SECOND, self.PI_ASSISTANT)
        )

    # ---------- OpenCode：库内消息正文 + 插件事件标题元数据 ----------

    def test_opencode_bodies_stay_out_of_store_and_report(self):
        sid = "ses_privacy"
        run_id = "f" * 32
        lease = (self.store.root / (run_id + ".lock")).open("w")
        fcntl.flock(lease, fcntl.LOCK_EX)
        self.addCleanup(lease.close)
        register(self.store.root, "pane-1", "opencode", run_id, "/dev/ttys999", 4242)
        record_event(
            "opencode",
            {
                "event": "SessionStart",
                "session_id": sid,
                "session_title": self.OPENCODE_TITLE,
                "cwd": "/work/oc",
            },
            env={
                "SESSION_MANAGER_RUN_ID": run_id,
                "SESSION_MANAGER_STATE": str(self.store.root),
            },
        )
        database = self.home / ".local/share/opencode/opencode.db"
        database.parent.mkdir(parents=True)
        created = round(stamp(self.day, 10) * 1000)
        completed = round(stamp(self.day, 10, 30) * 1000)
        with sqlite3.connect(database) as db:
            db.execute(
                "CREATE TABLE session (id text PRIMARY KEY, title text, directory text,"
                " time_updated integer, time_archived integer, parent_id text)"
            )
            db.execute(
                "CREATE TABLE message (id text PRIMARY KEY, session_id text,"
                " time_created integer, time_updated integer, data text)"
            )
            db.execute(
                "INSERT INTO session VALUES (?,?,?,?,?,?)",
                (sid, self.OPENCODE_TITLE, "/work/oc", 0, None, None),
            )
            db.execute(
                "INSERT INTO message VALUES (?,?,?,?,?)",
                (
                    "msg-1",
                    sid,
                    created,
                    created,
                    json.dumps(
                        {
                            "role": "user",
                            "parts": [{"type": "text", "text": self.OPENCODE_USER}],
                            "time": {"created": created - 1000, "completed": created},
                        }
                    ),
                ),
            )
            db.execute(
                "INSERT INTO message VALUES (?,?,?,?,?)",
                (
                    "msg-2",
                    sid,
                    completed,
                    completed,
                    json.dumps(
                        {
                            "role": "assistant",
                            "modelID": "model-fixture-1",
                            "parts": [{"type": "text", "text": self.OPENCODE_ASSISTANT}],
                            "time": {"created": completed - 1000, "completed": completed},
                            "tokens": {
                                "input": 1000,
                                "output": 200,
                                "reasoning": 20,
                                "cache": {"read": 400, "write": 50},
                            },
                        }
                    ),
                ),
            )
        row = next(r for r in self.store.rows() if r["provider"] == "opencode")
        self.assertEqual(row["title"], self.OPENCODE_TITLE)
        report = generate_day(self.store, self.home, self.day.isoformat())
        task = next(t for t in report["tasks"] if t["provider"] == "opencode")
        self.assertEqual(task["title"], self.OPENCODE_TITLE)
        self.assertEqual(
            (task["input_tokens"], task["cache_tokens"], task["output_tokens"]),
            (1050, 400, 220),
        )
        self.assert_markers_absent(report, (self.OPENCODE_USER, self.OPENCODE_ASSISTANT))

    # ---------- agy：summaries 库标题元数据；会话正文文件从不读取 ----------

    def test_agy_bodies_stay_out_of_store(self):
        sid = "agy-privacy-1"
        self.store.patch(
            "agy",
            sid,
            title=None,
            project="/work/agy",
            locator={"kind": "managed", "run_id": "a" * 32, "session_id": sid},
        )
        summaries = self.home / ".gemini/antigravity-cli/conversation_summaries.db"
        summaries.parent.mkdir(parents=True)
        with sqlite3.connect(summaries) as db:
            db.execute(
                "CREATE TABLE conversation_summaries (conversation_id text, title text,"
                " preview text, workspace_uris text)"
            )
            db.execute(
                "INSERT INTO conversation_summaries VALUES (?,?,?,?)",
                (sid, self.AGY_TITLE, "agy 隐私夹具预览", "[]"),
            )
            # 未受管理的会话不得因此入箱，其字段更不得落库。
            db.execute(
                "INSERT INTO conversation_summaries VALUES (?,?,?,?)",
                ("untracked-conv", self.AGY_UNTRACKED, "", "[]"),
            )
        # agy 自身的会话正文存储：产品代码没有任何入口读取该文件。
        transcript = self.home / ".gemini/antigravity-cli/conversations" / f"{sid}.json"
        transcript.parent.mkdir(parents=True)
        transcript.write_text(
            json.dumps(
                {
                    "messages": [
                        {"role": "user", "content": self.AGY_USER},
                        {"role": "assistant", "content": self.AGY_ASSISTANT},
                    ]
                }
            )
        )
        health = collect_agy_titles(self.store, self.home)
        self.assertEqual(health["status"], "ok")
        self.assertEqual(health["titled"], 1)
        rows = self.store.rows()
        self.assertEqual([r["provider"] for r in rows], ["agy"])
        self.assertEqual(rows[0]["title"], self.AGY_TITLE)
        # agy 不参与日报，断言范围为存储目录全部文件的字节。
        self.assert_store_bytes_free_of(
            (self.AGY_USER, self.AGY_ASSISTANT, self.AGY_UNTRACKED)
        )


if __name__ == "__main__":
    unittest.main()
