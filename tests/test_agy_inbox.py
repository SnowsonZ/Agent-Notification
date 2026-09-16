import fcntl
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from inbox_store import Store
from session_binding import record_event, register


class AgyManagedTests(unittest.TestCase):
    """agy 受管理链路：agy-hook 适配器 → record_event → receive() → managed 定位。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'state')
        self.run_id = 'c' * 32
        lease = (self.store.root / (self.run_id + '.lock')).open('w')
        fcntl.flock(lease, fcntl.LOCK_EX)
        self.addCleanup(lease.close)
        register(self.store.root, 'pane-1', 'agy', self.run_id, '/dev/ttys999', 4242)
        self.env = {'SESSION_MANAGER_RUN_ID': self.run_id,
                    'SESSION_MANAGER_STATE': str(self.store.root)}

    def record(self, name, sid='9302aa80-13a0-4e46-af69-3b2200ac03db', cwd='/work/agy', env=None):
        record_event('agy', {'event': name, 'session_id': sid, 'cwd': cwd}, env=env or self.env)

    def test_hooks_drive_lifecycle_and_rebind_on_switch(self):
        self.record('UserPromptSubmit')
        row = self.store.rows()[0]
        self.assertEqual((row['provider'], row['session_id']), ('agy', '9302aa80-13a0-4e46-af69-3b2200ac03db'))
        self.assertEqual(row['locator'], {'kind': 'managed', 'run_id': self.run_id,
                                          'session_id': '9302aa80-13a0-4e46-af69-3b2200ac03db'})
        self.assertEqual(row['state'], 'running')

        self.record('Stop')
        row = self.store.rows()[0]
        self.assertEqual(row['state'], 'idle')
        self.assertTrue(row['unread'])

        # TUI 内切换会话：下一个回合把绑定改指向新会话（Pi 模型），旧会话收到 SessionEnd。
        self.record('UserPromptSubmit', sid='bbbbbbbb-13a0-4e46-af69-3b2200ac03db')
        self.assertEqual(self.store.rows()[0]['session_id'], 'bbbbbbbb-13a0-4e46-af69-3b2200ac03db')
        old = [r for r in self.store.rows() if r['session_id'] == '9302aa80-13a0-4e46-af69-3b2200ac03db'][0]
        self.assertEqual(old['state'], 'closed')

    def test_unmanaged_hook_payload_ignored(self):
        self.record('Stop', env={})
        self.assertEqual(self.store.rows(), [])

    def test_wrapper_exit_sends_session_end(self):
        self.record('UserPromptSubmit')
        record_event('agy', {'event': 'SessionEnd',
                             'session_id': '9302aa80-13a0-4e46-af69-3b2200ac03db'}, env=self.env)
        self.assertEqual(self.store.rows()[0]['state'], 'closed')

    def test_binding_leash_expired_rejected(self):
        from session_binding import validate
        self.record('UserPromptSubmit')
        self.lease = None
        with self.assertRaises(ValueError):
            validate(self.store.root, self.run_id, '9302aa80-13a0-4e46-af69-3b2200ac03db')


class AgyHookAdapterTests(unittest.TestCase):
    """agy-hook 子命令：agy 原始载荷（camelCase stdin）→ record_event 转译。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'state')
        self.run_id = 'd' * 32
        lease = (self.store.root / (self.run_id + '.lock')).open('w')
        fcntl.flock(lease, fcntl.LOCK_EX)
        self.addCleanup(lease.close)
        register(self.store.root, 'pane-1', 'agy', self.run_id, '/dev/ttys999', 4242)
        self.env = {**os.environ, 'SESSION_MANAGER_RUN_ID': self.run_id,
                    'SESSION_MANAGER_STATE': str(self.store.root)}
        self.script = Path(__file__).resolve().parents[1] / 'scripts/session_binding.py'

    def hook(self, event, payload):
        result = subprocess.run(
            [sys.executable, str(self.script), '--state-dir', str(self.store.root),
             'agy-hook', '--event', event],
            input=json.dumps(payload), capture_output=True, text=True, timeout=30, env=self.env)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_preinvocation_maps_to_running_with_workspace(self):
        self.hook('PreInvocation', {'conversationId': 'eeeeee00-13a0-4e46-af69-3b2200ac03db',
                                    'workspacePaths': ['/work/agy', '/other']})
        row = self.store.rows()[0]
        self.assertEqual(row['session_id'], 'eeeeee00-13a0-4e46-af69-3b2200ac03db')
        self.assertEqual(row['project'], '/work/agy')
        self.assertEqual(row['state'], 'running')

    def test_missing_conversation_id_is_silently_ignored(self):
        self.hook('Stop', {'workspacePaths': ['/work/agy']})
        self.assertEqual(self.store.rows(), [])


class AgyTitleCollectorTests(unittest.TestCase):
    def test_titles_patched_for_existing_rows_only(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        home = Path(tmp.name)
        summaries = home / '.gemini/antigravity-cli/conversation_summaries.db'
        summaries.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(summaries) as db:
            db.execute('CREATE TABLE conversation_summaries (conversation_id text, title text, preview text, workspace_uris text)')
            # agy 常见形态：title 空、真实标题在 preview。
            db.execute("INSERT INTO conversation_summaries VALUES ('9302aa80-1', '', '分析会话标题', '[]')")
            db.execute("INSERT INTO conversation_summaries VALUES ('untracked', '不入箱', '', '[]')")
        store = Store(home / 'state')
        store.patch('agy', '9302aa80-1', title=None)
        from inbox_sources import collect_agy_titles, refresh
        health = collect_agy_titles(store, home)
        self.assertEqual(health['status'], 'ok')
        self.assertEqual(health['titled'], 1)
        row = store.rows()[0]
        self.assertEqual(row['title'], '分析会话标题')
        # 非受管理会话不会因此入箱。
        self.assertEqual(len(store.rows()), 1)
        # 缺库时明确降级。
        summaries.unlink()
        self.assertEqual(refresh(store, home)['agy']['status'], 'unavailable')


class ManagedDirectorySweepTests(unittest.TestCase):
    """目录缺失的受管理行隐藏、目录恢复自动取消隐藏（用户决定：不可见+不可跳转）。"""

    def test_hides_missing_directory_and_unhides_on_restore(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        home = Path(tmp.name)
        store = Store(home / 'state')
        alive_dir = home / 'workspace'
        alive_dir.mkdir()
        gone_dir = home / 'gone'
        gone_dir.mkdir()
        store.patch('agy', 'agy-alive', project=str(alive_dir), locator={'kind': 'managed', 'run_id': 'a' * 32, 'session_id': 'agy-alive'})
        store.patch('agy', 'agy-gone', project=str(gone_dir), locator={'kind': 'managed', 'run_id': 'b' * 32, 'session_id': 'agy-gone'})
        store.patch('opencode', 'oc-gone', project=str(gone_dir), locator={'kind': 'managed', 'run_id': 'c' * 32, 'session_id': 'oc-gone'})
        from inbox_sources import _sweep_managed_directories
        gone_dir.rmdir()
        _sweep_managed_directories(store)
        visible = [row['session_id'] for row in store.rows()]
        self.assertEqual(visible, ['agy-alive'])
        gone_dir.mkdir()
        _sweep_managed_directories(store)
        self.assertEqual(len(store.rows()), 3)


class AgyInstallHooksTests(unittest.TestCase):
    def test_generates_plugin_and_installs_when_missing(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        plugin_root = Path(tmp.name) / 'plugin'
        with patch('session_binding.subprocess.run') as run:
            def fake_run(command, **kwargs):
                if command[1:] == ['plugin', 'list']:
                    return subprocess.CompletedProcess([], 0, '{"imports": []}', '')
                return subprocess.CompletedProcess([], 0, 'ok', '')
            run.side_effect = fake_run
            from session_binding import install_agy_hooks
            events = install_agy_hooks(plugin_root)
        self.assertEqual(events, ['PreInvocation', 'Stop'])
        hooks = json.loads((plugin_root / 'hooks.json').read_text())
        self.assertEqual(sorted(hooks), ['session-manager'])
        handlers = hooks['session-manager']
        self.assertEqual(sorted(handlers), ['PreInvocation', 'Stop'])
        self.assertIn('agy-hook --event PreInvocation', handlers['PreInvocation'][0]['command'])
        install_calls = [call for call in run.call_args_list if 'install' in call[0][0]]
        self.assertEqual(len(install_calls), 1)

    def test_skips_install_when_already_listed(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        plugin_root = Path(tmp.name) / 'plugin'
        with patch('session_binding.subprocess.run') as run:
            def fake_run(command, **kwargs):
                if command[1:] == ['plugin', 'list']:
                    return subprocess.CompletedProcess([], 0, '"name": "session-manager"', '')
                return subprocess.CompletedProcess([], 0, 'ok', '')
            run.side_effect = fake_run
            from session_binding import install_agy_hooks
            install_agy_hooks(plugin_root)
        install_calls = [call for call in run.call_args_list if 'install' in call[0][0]]
        self.assertEqual(install_calls, [])


if __name__ == '__main__':
    unittest.main()
