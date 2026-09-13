from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from zcode_task_state import lookup, verify_selection


class TaskIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'index.sqlite'
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE tasks (task_id, task_status, unread_at, updated_at, '
                       'workspace_path, archived, deleted, searchable_text, title)')
            db.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?)',
                       ('fixture', 'completed', 1, 2, '/work/test project', 0, 0, 'private transcript', 'fixture title'))

    def test_workspace_link_is_not_claimed_as_exact_navigation(self):
        before = self.path.read_bytes()
        result = lookup(self.path, 'fixture')
        self.assertFalse(result['exact_session_navigation'])
        self.assertIn('%20', result['workspace_url'])
        self.assertNotIn('private transcript', str(result))
        self.assertEqual(before, self.path.read_bytes())

    def test_id_injection_is_not_a_query(self):
        self.assertEqual(lookup(self.path, "' OR 1=1 --")['status'], 'not_found')

    def test_duplicate_task_id_is_ambiguous(self):
        with sqlite3.connect(self.path) as db:
            db.execute('INSERT INTO tasks SELECT * FROM tasks')
        self.assertEqual(lookup(self.path, 'fixture')['status'], 'ambiguous')

    def test_copied_ui_path_must_match_task_and_workspace(self):
        target = Path(self.tmp.name) / 'sess_fixture.zcode-session'
        target.mkdir()
        other = Path(self.tmp.name) / 'sess_other.zcode-session'
        other.mkdir()
        snapshot = {'status': 'found', 'task_id': 'sess_fixture', 'workspace_path': self.tmp.name}
        self.assertTrue(verify_selection(snapshot, str(target)))
        self.assertFalse(verify_selection(snapshot, str(other)))
        self.assertFalse(verify_selection(snapshot, 'sess_fixture.zcode-session'))

    def test_logical_copied_path_does_not_require_a_legacy_file(self):
        snapshot = {'status': 'found', 'task_id': 'sess_fixture', 'workspace_path': self.tmp.name}
        self.assertTrue(verify_selection(snapshot, str(Path(self.tmp.name) / 'sess_fixture.zcode-session')))


if __name__ == '__main__':
    unittest.main()
