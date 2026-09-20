import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from inbox import setup_claude
from inbox_sources import collect_codex
from inbox_store import Store


class InboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / "state")

    def event(self, event_id, time, attention=True, state="idle", token=None):
        return self.store.event(
            "pi",
            "session-a",
            event_id=event_id,
            timestamp=time,
            state=state,
            attention=attention,
            token=token,
        )

    def test_duplicate_event_does_not_restore_acknowledged_item(self):
        self.event("done-1", 1)
        row = self.store.rows()[0]
        self.assertTrue(self.store.acknowledge(row["id"], row["revision"]))
        self.assertFalse(self.event("done-1", 1))
        self.assertEqual(self.store.rows(unread_only=True), [])

    def test_stale_ui_ack_cannot_clear_new_reply(self):
        self.event("done-1", 1)
        old = self.store.rows()[0]
        self.event("done-2", 2)
        self.assertFalse(self.store.acknowledge(old["id"], old["revision"]))
        self.assertTrue(self.store.rows()[0]["unread"])

    def test_late_completion_cannot_replace_new_running_turn(self):
        self.event("start-2", 5, False, "running")
        self.event("done-1", 4)
        self.assertEqual(self.store.rows()[0]["state"], "running")
        self.assertFalse(self.store.rows()[0]["unread"])

    def test_stale_event_does_not_burn_event_id(self):
        # 评审 R2：过期事件此前先登记账本再查时间戳，确定性 event_id 被烧毁——
        # 真实结束事件即使随后以更高时间戳重放也永远无法自愈。
        self.event("running-1", 10, False, "running")
        self.assertFalse(self.event("end-1", 9))
        self.assertEqual(self.store.rows()[0]["state"], "running")
        self.assertTrue(self.event("end-1", 11))
        self.assertEqual(self.store.rows()[0]["state"], "idle")

    def test_same_source_attention_marker_does_not_renotify_after_metadata_change(self):
        self.event("snapshot-1", 1, token="unread-at-1")
        row = self.store.rows()[0]
        self.store.acknowledge(row["id"], row["revision"])
        self.event("snapshot-2", 2, token="unread-at-1")
        self.assertFalse(self.store.rows()[0]["unread"])

    def test_closed_session_keeps_unreviewed_result(self):
        self.event("done", 1)
        self.event("end", 2, None, "closed")
        self.assertTrue(self.store.rows()[0]["unread"])
        self.assertEqual(self.store.rows()[0]["state"], "closed")

    def test_claude_setup_preserves_existing_hooks_and_is_idempotent(self):
        folder = self.root / "claude"
        folder.mkdir()
        config = folder / "settings.json"
        config.write_text(
            json.dumps(
                {
                    "model": "existing",
                    "hooks": {
                        "Stop": [
                            {
                                "hooks": [
                                    {"type": "command", "command": "existing-command"}
                                ]
                            }
                        ]
                    },
                }
            )
        )
        with patch.dict("os.environ", {"CLAUDE_CONFIG_DIR": str(folder)}):
            self.assertEqual(setup_claude(self.store.root), 6)
            content = config.read_text()
            self.assertEqual(setup_claude(self.store.root), 0)
            self.assertEqual(config.read_text(), content)
        self.assertEqual(json.loads(content)["model"], "existing")
        self.assertEqual(
            json.loads(content)["hooks"]["Stop"][0]["hooks"][0]["command"],
            "existing-command",
        )

    def test_codex_initial_history_is_quiet_then_new_completion_is_unread(self):
        home = self.root / "home"
        folder = home / ".codex/sessions/2026/09/14"
        folder.mkdir(parents=True)
        path = folder / "rollout-fixture.jsonl"

        def line(kind, payload, timestamp="2026-01-01T00:00:00Z"):
            return (
                json.dumps({"type": kind, "payload": payload, "timestamp": timestamp})
                + "\n"
            )

        path.write_text(
            line("session_meta", {"id": "fixture", "originator": "Codex Desktop"})
            + line(
                "event_msg",
                {
                    "type": "task_complete",
                    "turn_id": "old",
                    "last_agent_message": "secret",
                },
            )
        )
        collect_codex(self.store, home)
        self.assertFalse(self.store.rows()[0]["unread"])
        self.store.set_meta("started_at", 0)
        with path.open("a") as file:
            file.write(
                line(
                    "event_msg",
                    {
                        "type": "task_complete",
                        "turn_id": "new",
                        "last_agent_message": "secret",
                    },
                    "2026-02-01T00:00:00Z",
                )
            )
        collect_codex(self.store, home)
        self.assertTrue(self.store.rows()[0]["unread"])
        self.assertNotIn("secret", str(self.store.rows()))
        row = self.store.rows()[0]
        self.store.acknowledge(row["id"], row["revision"])
        collect_codex(self.store, home)
        self.assertFalse(self.store.rows()[0]["unread"])

    def test_fork_history_does_not_attribute_parent_events_to_child(self):
        from codex_rollout_events import RolloutReader

        path = self.root / "fork.jsonl"
        records = [
            {
                "type": "session_meta",
                "payload": {"id": "parent", "originator": "Codex Desktop"},
            },
            {
                "type": "event_msg",
                "payload": {"type": "task_complete", "turn_id": "parent-turn"},
            },
            {
                "type": "session_meta",
                "payload": {"id": "child", "originator": "Codex Desktop"},
            },
            {
                "type": "event_msg",
                "payload": {"type": "task_started", "turn_id": "child-turn"},
            },
        ]
        path.write_text("".join(json.dumps(row) + "\n" for row in records))
        events = RolloutReader(path, "child", allow_ancestry=True).poll()
        self.assertEqual(
            [(e["session_id"], e["turn_id"]) for e in events], [("child", "child-turn")]
        )
