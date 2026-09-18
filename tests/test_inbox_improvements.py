import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout, redirect_stderr

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from inbox_store import Store
import inbox
from inbox import open_session
from pi_titles import read_title


class ImprovementsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'state')

    def attention(self):
        key = self.store.patch('zcode', 'sess_a', title='task', locator={'kind': 'zcode', 'task_id': 'sess_a'})
        self.store.event('zcode', 'sess_a', event_id='done', timestamp=1, state='idle', attention=True)
        return key

    def test_successful_open_acknowledges_displayed_revision(self):
        key = self.attention()
        with patch('inbox.subprocess.run', return_value=subprocess.CompletedProcess([], 0, '{}', '')), redirect_stdout(io.StringIO()):
            self.assertEqual(open_session(self.store, key, self.store.get(key)['revision']), 0)
        self.assertFalse(self.store.get(key)['unread'])

    def test_failed_open_leaves_attention(self):
        key = self.attention()
        with patch('inbox.subprocess.run', return_value=subprocess.CompletedProcess([], 1, '', 'refused')), redirect_stderr(io.StringIO()):
            self.assertEqual(open_session(self.store, key), 1)
        self.assertTrue(self.store.get(key)['unread'])

    def test_new_reply_during_open_is_not_acknowledged(self):
        key = self.attention()
        revision = self.store.get(key)['revision']
        def open_then_reply(*args, **kwargs):
            self.store.event('zcode', 'sess_a', event_id='new', timestamp=2, state='idle', attention=True)
            return subprocess.CompletedProcess([], 0, '{}', '')
        with patch('inbox.subprocess.run', side_effect=open_then_reply), redirect_stdout(io.StringIO()):
            open_session(self.store, key, revision)
        self.assertTrue(self.store.get(key)['unread'])

    def test_attention_raise_records_notification_time(self):
        self.attention()
        self.assertEqual(self.store.rows()[0]['attention_at'], 1)
        # 同 token 的重复事件不是新通知，保持原抬升时刻。
        self.store.event('zcode', 'sess_a', event_id='dup', timestamp=2, state='idle', attention=True, token='done')
        self.assertEqual(self.store.rows()[0]['attention_at'], 1)
        # 新 token 开启新的通知周期。
        self.store.event('zcode', 'sess_a', event_id='again', timestamp=3, state='idle', attention=True, token='next')
        self.assertEqual(self.store.rows()[0]['attention_at'], 3)

    def test_acknowledgement_freezes_processing_time(self):
        key = self.attention()
        with patch('inbox_store.time.time', return_value=50):
            self.assertTrue(self.store.acknowledge(key, self.store.get(key)['revision']))
        row = self.store.get(key)
        self.assertEqual(row['acknowledged_at'], 50)
        self.assertFalse(row['unread'])
        # 后续来源事件不得抹掉已完成的处理区间。
        self.store.event('zcode', 'sess_a', event_id='later', timestamp=60, state='running', attention=False)
        self.assertEqual(self.store.get(key)['acknowledged_at'], 50)
        # 新通知开启新周期，时长窗口随之重置。
        self.store.event('zcode', 'sess_a', event_id='again', timestamp=70, state='idle', attention=True, token='next')
        with patch('inbox_store.time.time', return_value=80):
            self.assertTrue(self.store.acknowledge(key, self.store.get(key)['revision']))
        row = self.store.get(key)
        self.assertEqual((row['attention_at'], row['acknowledged_at']), (70, 80))

    def test_failed_acknowledgement_records_no_time(self):
        key = self.attention()
        self.assertFalse(self.store.acknowledge(key, 999))
        row = self.store.get(key)
        self.assertEqual(row['acknowledged_at'], 0)
        self.assertTrue(row['unread'])

    def attention_for(self, provider, sid):
        key = self.store.patch(provider, sid, title='task', locator={'kind': 'zcode', 'task_id': sid})
        self.store.event(provider, sid, event_id='done-' + sid, timestamp=1, state='idle', attention=True)
        return key

    def test_batch_acknowledges_matching_revisions(self):
        self.attention_for('zcode', 'sess_a')
        self.attention_for('kimi', 'sess_b')
        snapshot = [(row['id'], row['revision']) for row in self.store.rows(unread_only=True)]
        self.assertEqual(len(self.store.acknowledge_batch(snapshot)), 2)
        self.assertEqual(self.store.rows(unread_only=True), [])

    def test_batch_ack_preserves_items_with_new_activity(self):
        self.attention_for('zcode', 'sess_a')
        self.attention_for('kimi', 'sess_b')
        snapshot = {row['session_id']: (row['id'], row['revision']) for row in self.store.rows(unread_only=True)}
        # 确认前 sess_a 到达新事件：旧快照的 revision 失配，该跳过的必须保留未读。
        self.store.event('zcode', 'sess_a', event_id='new', timestamp=2, state='idle', attention=True)
        acked = self.store.acknowledge_batch([snapshot['sess_a'], snapshot['sess_b']])
        self.assertEqual(acked, [snapshot['sess_b'][0]])
        self.assertTrue(self.store.get(snapshot['sess_a'][0])['unread'])
        self.assertFalse(self.store.get(snapshot['sess_b'][0])['unread'])

    def test_batch_ack_ignores_missing_rows(self):
        self.attention_for('zcode', 'sess_a')
        self.assertEqual(self.store.acknowledge_batch([('gone', 3)]), [])

    def test_ack_batch_cli_reports_counts(self):
        self.attention_for('zcode', 'sess_a')
        self.attention_for('kimi', 'sess_b')
        snapshot = [[row['id'], row['revision']] for row in self.store.rows(unread_only=True)]
        argv = ['inbox.py', '--root', str(self.root / 'state'), 'ack-batch', '--items', json.dumps(snapshot)]
        with patch('sys.argv', argv), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(inbox.main(), 0)
        self.assertEqual(json.loads(out.getvalue()), {'acknowledged': 2, 'skipped': 0})
        self.assertEqual(self.store.rows(unread_only=True), [])

    def test_ack_batch_cli_rejects_non_array_items(self):
        argv = ['inbox.py', '--root', str(self.root / 'state'), 'ack-batch', '--items', '{"id": 1}']
        with patch('sys.argv', argv), redirect_stderr(io.StringIO()) as err:
            self.assertEqual(inbox.main(), 1)
        self.assertEqual(json.loads(err.getvalue())['status'], 'error')

    def test_origin_cli_sets_row_and_rule(self):
        key = self.attention()
        argv = ['inbox.py', '--root', str(self.root / 'state'), 'origin', '--id', key,
                '--set', 'agent', '--rule-project', '/work/x']
        with patch('sys.argv', argv), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(inbox.main(), 0)
        self.assertEqual(json.loads(out.getvalue()), {'id': key, 'origin': 'agent',
                                                      'rule_project': '/work/x'})
        self.assertEqual(self.store.get(key)['origin'], 'agent')
        self.assertEqual(self.store.origin_rules(), [{'project': '/work/x', 'origin': 'agent'}])

    def test_all_rows_sort_by_activity_not_unread_or_error(self):
        self.store.event('pi', 'old', event_id='old', timestamp=1, state='failed', attention=True)
        self.store.event('pi', 'new', event_id='new', timestamp=3, state='idle', attention=False)
        self.assertEqual([r['session_id'] for r in self.store.rows()], ['new', 'old'])
        self.store.patch('pi', 'old', activity_at=4)
        self.assertEqual(self.store.rows()[0]['session_id'], 'old')
        self.assertEqual(self.store.rows()[0]['event_at'], 1)

    def test_pi_custom_name_overrides_first_user_request(self):
        path = self.root / 'pi.jsonl'
        records = [{'type': 'session', 'id': 'fixture', 'cwd': '/work/project'},
                   {'type': 'message', 'message': {'role': 'user', 'content': [{'type': 'text', 'text': 'Fix the login flow\nwith tests'}]}},
                   {'type': 'session_info', 'name': 'My explicit name'}]
        path.write_text(''.join(json.dumps(r) + '\n' for r in records))
        self.assertEqual(read_title(path, 'fixture'), 'My explicit name')
        path.write_text(''.join(json.dumps(r) + '\n' for r in records[:2]))
        self.assertEqual(read_title(path, 'fixture'), 'Fix the login flow with tests')
        with self.assertRaises(ValueError):
            read_title(path, 'other')
