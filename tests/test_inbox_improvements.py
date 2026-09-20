import io
import json
from pathlib import Path
import subprocess
import threading
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


class StoreConnectionAndPatchTests(unittest.TestCase):
    """连接复用与「值不变不写」优化后的行为契约。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "state"
        self.store = Store(self.root)

    def test_toplevel_blocks_share_connection_nested_get_fresh(self):
        with self.store.db() as outer:
            with self.store.db() as inner:
                self.assertIsNot(outer, inner)  # 嵌套回退一次性连接，防内外层事务互相提交
        with self.store.db() as again:
            self.assertIs(again, outer)  # 顶层顺序调用复用缓存连接

    def test_unchanged_patch_writes_nothing(self):
        args = dict(
            title="任务",
            project="/work/p",
            locator={"kind": "zcode", "task_id": "s1"},
            hidden=False,
            activity_at=100,
        )
        self.store.patch("zcode", "s1", **args)
        observer = Store(self.root)

        def version():
            # data_version 只随其它连接的提交变化，是「是否真的写库」的观察窗。
            with observer.db() as db:
                return db.execute("PRAGMA data_version").fetchone()[0]

        before = version()
        self.store.patch("zcode", "s1", **args)  # 全同值
        self.store.patch("zcode", "s1", activity_at=50)  # activity 只升不降
        self.store.patch("zcode", "s1")  # 全空参数
        self.assertEqual(version(), before)
        self.store.patch("zcode", "s1", title="新标题")
        self.assertGreater(version(), before)

    def test_patch_semantics_unchanged(self):
        key = self.store.patch(
            "pi",
            "p1",
            title="t1",
            locator={"kind": "managed", "run_id": "r"},
            origin="agent",
        )
        row = self.store.get(key)
        self.assertEqual(row["title"], "t1")
        self.assertEqual(row["locator"], {"kind": "managed", "run_id": "r"})
        self.assertEqual(row["origin"], "agent")
        self.store.patch("pi", "p1", title="t2", hidden=True)
        with self.store.db() as db:
            raw = db.execute(
                "SELECT title, hidden FROM sessions WHERE id=?", (key,)
            ).fetchone()
        self.assertEqual(raw["title"], "t2")
        self.assertEqual(raw["hidden"], 1)
        # 新行（不带任何字段）的默认标题与 ensure 契约一致。
        key2 = self.store.patch("kimi", "k9")
        self.assertEqual(self.store.get(key2)["title"], "kimi · k9")


class ConcurrentFirstPatchTests(unittest.TestCase):
    """实际失败拓扑：hook 进程与采集器同时首次发现同一会话（两个连接并发 INSERT）。
    修复前的裸 INSERT 在此交错下抛 IntegrityError（进程级实测 30 轮 28 轮命中，
    hook 容错会把该事件静默丢弃）；OR IGNORE 仲裁后任何交错都不再抛错。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "state"

    def test_concurrent_first_patch_never_raises(self):
        rounds = 30
        barrier = threading.Barrier(2)
        errors = []

        def hammer(role):
            store = Store(self.root)  # 线程内建：sqlite 连接默认限创建线程使用
            try:
                for i in range(rounds):
                    barrier.wait(timeout=10)
                    store.patch(
                        "claude",
                        f"race-{i}",
                        title=f"t-{role}",
                        project="/tmp",
                        locator={"kind": "cli", "cwd": "/tmp"},
                    )
            except Exception as error:  # noqa: BLE001 - 记录任意失败用于断言
                errors.append(f"{role}: {error!r}")

        threads = [threading.Thread(target=hammer, args=(r,)) for r in ("A", "B")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        rows = {r["session_id"]: r for r in Store(self.root).rows()}
        self.assertEqual(len(rows), rounds)
        for i in range(rounds):
            row = rows[f"race-{i}"]
            self.assertIn(row["title"], ("t-A", "t-B"))
            self.assertEqual(row["locator"], {"kind": "cli", "cwd": "/tmp"})
