import sqlite3
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from inbox_store import Store
from inbox_sources import collect_zcode


class ZcodeInboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.store = Store(self.home / 'state')
        self.store.set_meta('started_at', 100)
        self.index = self.home / '.zcode/v2/tasks-index.sqlite'
        self.runtime = self.home / '.zcode/cli/db/db.sqlite'
        self.index.parent.mkdir(parents=True)
        self.runtime.parent.mkdir(parents=True)
        with sqlite3.connect(self.index) as db:
            db.execute('CREATE TABLE tasks (task_id,title,workspace_path,task_status,unread_at,updated_at,archived,deleted,last_unread_at)')
            db.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?)',
                       ('sess_main', 'task', '/fixture', 'completed', None, 500, 0, 0, 0))
        with sqlite3.connect(self.runtime) as db:
            db.execute('CREATE TABLE turn_usage (session_id,turn_id,status,started_at,completed_at)')

    def turn(self, turn_id='turn-new', status='completed', start=110, end=120, sid='sess_main'):
        with sqlite3.connect(self.runtime) as db:
            db.execute('INSERT INTO turn_usage VALUES (?,?,?,?,?)', (sid, turn_id, status, start, end))

    def test_completed_turn_with_no_native_unread_enters_inbox(self):
        self.turn()
        collect_zcode(self.store, self.home)
        self.assertTrue(self.store.rows()[0]['unread'])
        self.assertEqual(self.store.rows()[0]['state'], 'idle')

    def test_old_snapshot_clock_cannot_hide_recovered_completion(self):
        self.store.event('zcode', 'sess_main', event_id='legacy-snapshot', timestamp=500,
                         state='idle', attention=False)
        self.turn()
        collect_zcode(self.store, self.home)
        self.assertTrue(self.store.rows()[0]['unread'])
        self.assertEqual(self.store.rows()[0]['event_at'], 120)

    def test_native_read_marker_does_not_acknowledge_our_inbox(self):
        self.turn()
        with sqlite3.connect(self.index) as db:
            db.execute('UPDATE tasks SET unread_at=125,last_unread_at=125')
        collect_zcode(self.store, self.home)
        with sqlite3.connect(self.index) as db:
            db.execute('UPDATE tasks SET unread_at=NULL,updated_at=700')
        collect_zcode(self.store, self.home)
        self.assertTrue(self.store.rows()[0]['unread'])

    def test_acknowledgement_survives_refresh_and_metadata_changes(self):
        self.turn()
        collect_zcode(self.store, self.home)
        row = self.store.rows()[0]
        self.store.acknowledge(row['id'], row['revision'])
        with sqlite3.connect(self.index) as db:
            db.execute('UPDATE tasks SET title=?,updated_at=?', ('renamed', 600))
        collect_zcode(self.store, self.home)
        self.assertFalse(self.store.rows()[0]['unread'])
        self.turn('turn-next', start=610, end=620)
        collect_zcode(self.store, self.home)
        self.assertTrue(self.store.rows()[0]['unread'])

    def test_old_turns_and_child_sessions_do_not_flood_inbox(self):
        self.turn('old', start=10, end=20)
        self.turn('child', sid='sess_child')
        collect_zcode(self.store, self.home)
        self.assertEqual(self.store.rows(unread_only=True), [])
        self.assertEqual(len(self.store.rows()), 1)

    def test_failed_turn_is_attention_but_cancel_is_not(self):
        self.turn(status='error')
        collect_zcode(self.store, self.home)
        self.assertTrue(self.store.rows()[0]['unread'])
        self.assertEqual(self.store.rows()[0]['state'], 'failed')
        self.turn('cancel', status='cancelled', start=130, end=140)
        collect_zcode(self.store, self.home)
        self.assertFalse(self.store.rows()[0]['unread'])
        self.assertEqual(self.store.rows()[0]['state'], 'interrupted')
