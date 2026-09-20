import sqlite3
import threading
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
            db.execute('CREATE TABLE part (id text primary key, message_id text, '
                       'session_id text, time_created integer, time_updated integer, data text)')

    def turn(self, turn_id='turn-new', status='completed', start=110, end=120, sid='sess_main'):
        with sqlite3.connect(self.runtime) as db:
            db.execute('INSERT INTO turn_usage VALUES (?,?,?,?,?)', (sid, turn_id, status, start, end))

    def part(self, session_id='sess_main', message_id='msg-live', updated=None):
        import time as _time
        if updated is None:
            updated = int(_time.time() * 1000)
        with sqlite3.connect(self.runtime) as db:
            db.execute('INSERT INTO part VALUES (?,?,?,?,?,?)',
                       ('part-1', message_id, session_id, updated, updated, '{}'))

    def test_streaming_part_flips_completed_task_to_running(self):
        # 实测失败拓扑（2026-09-20 用户反馈）：任务索引整轮停在 completed，live-running
        # 推断抓不到进行中的回合；part 表两分钟窗口内有更新即推 running。
        self.turn()
        collect_zcode(self.store, self.home)
        self.assertEqual(self.store.rows()[0]['state'], 'idle')
        self.part()
        collect_zcode(self.store, self.home)
        self.assertEqual(self.store.rows()[0]['state'], 'running')
        self.assertFalse(self.store.rows()[0]['unread'])  # 新一轮运行清除过期待处理

    def test_stale_part_keeps_terminal_state(self):
        self.turn()
        self.part(updated=200)
        collect_zcode(self.store, self.home)
        self.assertEqual(self.store.rows()[0]['state'], 'idle')

    def test_landed_turn_end_wins_over_stream_wall_clock(self):
        # 防挡门：流式事件时间戳是墙钟当下，若回合在窗口沿已结束并落行（完成时间晚于
        # 部件更新），结束事件必须胜出，不能被流式事件卡成永久 running。
        import time as _time
        now_ms = int(_time.time() * 1000)
        self.part(updated=now_ms - 1000)
        self.turn('turn-live', start=now_ms - 1000, end=now_ms + 60000)
        collect_zcode(self.store, self.home)
        self.assertEqual(self.store.rows()[0]['state'], 'idle')

    def test_child_session_part_does_not_flip_parent_task(self):
        self.turn()
        self.part(session_id='sess_child')
        collect_zcode(self.store, self.home)
        self.assertEqual(self.store.rows()[0]['state'], 'idle')

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

    def test_live_turn_shows_running_until_runtime_row_lands(self):
        self.turn()
        collect_zcode(self.store, self.home)
        row = self.store.rows()[0]
        self.assertTrue(row['unread'])
        self.store.acknowledge(row['id'], row['revision'])
        # New turn is live but turn_usage only records it once it ends; the
        # index carries the liveness instead.
        with sqlite3.connect(self.index) as db:
            db.execute("UPDATE tasks SET task_status='running',updated_at=200")
        collect_zcode(self.store, self.home)
        row = self.store.rows()[0]
        self.assertEqual(row['state'], 'running')
        self.assertFalse(row['unread'])
        before = row['revision']
        collect_zcode(self.store, self.home)
        self.assertEqual(self.store.rows()[0]['revision'], before)
        # The real end event must still override the inferred running.
        self.turn('turn-next', start=210, end=220)
        with sqlite3.connect(self.index) as db:
            db.execute("UPDATE tasks SET task_status='completed',updated_at=230")
        collect_zcode(self.store, self.home)
        row = self.store.rows()[0]
        self.assertEqual(row['state'], 'idle')
        self.assertTrue(row['unread'])

    def test_stale_running_index_cannot_override_recorded_completion(self):
        self.turn()
        collect_zcode(self.store, self.home)
        with sqlite3.connect(self.index) as db:
            db.execute("UPDATE tasks SET task_status='running',updated_at=120")
        collect_zcode(self.store, self.home)
        self.assertEqual(self.store.rows()[0]['state'], 'idle')
        self.assertTrue(self.store.rows()[0]['unread'])


class ZcodeConcurrentRefreshTests(unittest.TestCase):
    """实际失败拓扑：两个刷新进程同时见到 turn-clock 旗标缺失。SELECT 判存无仲裁的
    实现会双跑迁移 UPDATE（revision 双跳，废掉一个合法 ack 的 CAS）；rowcount 仲裁
    保证恰好一次。线程内各建 Store 连接模拟两进程并发。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        Store(self.home / "state").set_meta("started_at", 100)
        index = self.home / ".zcode/v2/tasks-index.sqlite"
        runtime = self.home / ".zcode/cli/db/db.sqlite"
        index.parent.mkdir(parents=True)
        runtime.parent.mkdir(parents=True)
        with sqlite3.connect(index) as db:
            db.execute('CREATE TABLE tasks (task_id,title,workspace_path,task_status,unread_at,updated_at,archived,deleted,last_unread_at)')
            db.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?)',
                       ('sess_main', 'task', '/fixture', 'completed', None, 500, 0, 0, 0))
        with sqlite3.connect(runtime) as db:
            db.execute('CREATE TABLE turn_usage (session_id,turn_id,status,started_at,completed_at)')
            db.execute('INSERT INTO turn_usage VALUES (?,?,?,?,?)',
                       ('sess_main', 'turn-1', 'completed', 110, 120))

    def test_turn_clock_migration_runs_exactly_once(self):
        barrier = threading.Barrier(2)
        errors = []

        def collect():
            try:
                store = Store(self.home / "state")
                barrier.wait(timeout=10)
                collect_zcode(store, self.home)
            except Exception as error:  # noqa: BLE001
                errors.append(repr(error))

        threads = [threading.Thread(target=collect) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        row = Store(self.home / "state").rows()[0]
        # 恰好一次：revision = 时钟迁移 1 + turn-start 1 + turn-end 1（确定性事件 id
        # 全局去重保证 start 先于 end 应用，时间戳守卫不会拒掉任何一方）。
        self.assertEqual(row["revision"], 3)
        self.assertEqual(row["state"], "idle")
        self.assertTrue(row["unread"])
