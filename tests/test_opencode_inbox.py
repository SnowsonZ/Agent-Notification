import fcntl
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from inbox import open_session
from inbox_store import Store
from session_binding import record_event, register

REPO = Path(__file__).resolve().parents[1]


class OpenCodeManagedTests(unittest.TestCase):
    """opencode 受管理链路：插件事件 → record_event → receive() → managed 定位。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'state')
        self.run_id = 'a' * 32
        lease = (self.store.root / (self.run_id + '.lock')).open('w')
        fcntl.flock(lease, fcntl.LOCK_EX)
        self.addCleanup(lease.close)
        register(self.store.root, 'pane-1', 'opencode', self.run_id, '/dev/ttys999', 4242)
        self.demo = self.root / 'work' / 'demo'
        self.demo.mkdir(parents=True, exist_ok=True)
        self.env = {'SESSION_MANAGER_RUN_ID': self.run_id,
                    'SESSION_MANAGER_STATE': str(self.store.root)}

    def event(self, name, sid='ses_a', **extra):
        record_event('opencode', {'event': name, 'session_id': sid,
                                  'session_title': extra.pop('title', 'OC 会话'),
                                  'cwd': extra.pop('cwd', str(self.demo)), **extra}, env=self.env)

    def test_plugin_events_drive_full_lifecycle(self):
        self.event('SessionStart')
        row = self.store.rows()[0]
        self.assertEqual((row['provider'], row['session_id']), ('opencode', 'ses_a'))
        self.assertEqual(row['title'], 'OC 会话')
        self.assertEqual(row['project'], str(self.demo))
        self.assertEqual(row['locator'], {'kind': 'managed', 'run_id': self.run_id, 'session_id': 'ses_a'})

        self.event('UserPromptSubmit')
        self.assertEqual(self.store.rows()[0]['state'], 'running')

        self.event('Stop')
        row = self.store.rows()[0]
        self.assertEqual(row['state'], 'idle')
        self.assertTrue(row['unread'])

        self.event('PermissionRequest')
        self.assertEqual(self.store.rows()[0]['state'], 'waiting')

        self.event('StopFailure')
        row = self.store.rows()[0]
        self.assertEqual(row['state'], 'failed')
        self.assertTrue(row['unread'])

        self.event('UserPromptSubmit')
        self.assertEqual(self.store.rows()[0]['state'], 'running')
        self.assertFalse(self.store.rows()[0]['unread'])

        self.event('SessionEnd')
        self.assertEqual(self.store.rows()[0]['state'], 'closed')

    def test_unmanaged_events_ignored(self):
        record_event('opencode', {'event': 'Stop', 'session_id': 'ses_x'}, env={})
        self.assertEqual(self.store.rows(), [])

    def test_open_uses_binding_probe(self):
        self.event('SessionStart')
        self.event('Stop')
        row = self.store.rows(unread_only=True)[0]
        with patch('inbox.subprocess.run') as run:
            run.return_value = subprocess.CompletedProcess([], 0, json.dumps({'activation_call_completed': True}), '')
            with patch('sys.stdout'):
                code = open_session(self.store, row['id'], row['revision'])
        self.assertEqual(code, 0)
        command = run.call_args[0][0]
        self.assertTrue(any('iterm_probe.py' in part for part in command))
        self.assertEqual(command[command.index('--run-id') + 1], self.run_id)
        self.assertEqual(command[command.index('--agent-session-id') + 1], 'ses_a')
        self.assertFalse(self.store.get(row['id'])['unread'])

    def test_open_resumes_when_binding_dead(self):
        # TUI 关闭后运行锁释放：跳转经包装器在新标签受管理恢复目标会话。
        self.event('SessionStart')
        self.event('Stop')
        row = self.store.rows(unread_only=True)[0]
        self.store.root.joinpath(self.run_id + '.lock').unlink()  # 释放锁 → 绑定死亡
        with patch('inbox.subprocess.run') as run, \
             patch('inbox.launch_agent') as launch, patch('sys.stdout') as output:
            run.return_value = subprocess.CompletedProcess([], 1, '', 'refused')
            code = open_session(self.store, row['id'], row['revision'])
        self.assertEqual(code, 0)
        self.assertEqual(launch.call_args[0], ('opencode', str(self.demo)))
        self.assertEqual(launch.call_args[1], {'args': ('--session', 'ses_a')})
        self.assertTrue(any('managed_resume' in str(call) for call in output.write.call_args_list))

    def test_open_prefers_live_rebound_binding(self):
        # 会话在新标签恢复后行定位滞后：应改用同会话的新活绑定聚焦，而不是再开一个。
        self.event('SessionStart')
        row = self.store.rows()[0]
        self.store.root.joinpath(self.run_id + '.lock').unlink()
        new_run = 'f' * 32
        from session_binding import register
        new_lease_path = self.store.root / (new_run + '.lock')
        lease = new_lease_path.open('w')
        fcntl.flock(lease, fcntl.LOCK_EX)
        self.addCleanup(lease.close)
        register(self.store.root, 'pane-2', 'opencode', new_run, '/dev/ttys998', 1717)
        with patch('inbox._live_binding_for', return_value=new_run) as lookup, \
                patch('inbox.subprocess.run') as run:
            run.side_effect = [subprocess.CompletedProcess([], 1, '', 'refused'),
                               subprocess.CompletedProcess([], 0, json.dumps({'activation_call_completed': True}), '')]
            with patch('sys.stdout'):
                code = open_session(self.store, row['id'], row['revision'])
        self.assertEqual(code, 0)
        lookup.assert_called_once()
        command = run.call_args[0][0]
        self.assertEqual(command[command.index('--run-id') + 1], new_run)

    def test_live_binding_lookup_matches_session_and_excludes_dead(self):
        from inbox import _live_binding_for
        from session_binding import register
        self.event('SessionStart', sid='ses_a')
        # 存活（fake ps 走不通，直接 patch alive 语义为 True）
        with patch('session_binding.alive', return_value=True):
            self.assertEqual(_live_binding_for(self.store.root, 'opencode', 'ses_a',
                                               exclude=self.run_id), None)
            new_run = '9' * 32
            lease = (self.store.root / (new_run + '.lock')).open('w')
            fcntl.flock(lease, fcntl.LOCK_EX)
            self.addCleanup(lease.close)
            register(self.store.root, 'pane-9', 'opencode', new_run, '/dev/ttys996', 4243)
            with patch('session_binding.alive', return_value=True):
                # 注册时 session_id 为 NULL，不匹配
                self.assertEqual(_live_binding_for(self.store.root, 'opencode', 'ses_a',
                                                   exclude=self.run_id), None)
                from session_binding import connect
                with connect(self.store.root) as db:
                    db.execute('UPDATE bindings SET session_id=? WHERE run_id=?', ('ses_a', new_run))
                self.assertEqual(_live_binding_for(self.store.root, 'opencode', 'ses_a',
                                                   exclude=self.run_id), new_run)
                with self.assertRaises(ValueError):
                    _live_binding_for(self.store.root, 'opencode', 'ses_a')  # exclude 未传 → 两个都算 → 歧义

    def test_open_resumes_pi_and_kimi_with_session_flag(self):
        # 四家受管理 CLI 全部支持按 ID 恢复（实测 help）：绑定死亡统一走受管理恢复。
        from session_binding import register
        lifecycle = {'pi': ('session_start', 'agent_settled'),
                     'kimi': ('SessionStart', 'Stop')}
        for provider, run_id, sid in (('pi', '1' * 32, 'pi-sess-uuid'),
                                      ('kimi', '2' * 32, 'session_kimi-uuid')):
            lease = (self.store.root / (run_id + '.lock')).open('w')
            fcntl.flock(lease, fcntl.LOCK_EX)
            self.addCleanup(lease.close)
            register(self.store.root, 'pane-x', provider, run_id, '/dev/ttys995', 5151)
            start_event, end_event = lifecycle[provider]
            provider_env = {'SESSION_MANAGER_RUN_ID': run_id,
                            'SESSION_MANAGER_STATE': str(self.store.root)}
            record_event(provider, {'event': start_event, 'session_id': sid,
                                    'cwd': str(self.demo)}, env=provider_env)
            record_event(provider, {'event': end_event, 'session_id': sid}, env=provider_env)
            row = self.store.rows()[0]
            Path(self.store.root / (run_id + '.lock')).unlink()
            with patch('inbox.subprocess.run') as run, \
                 patch('inbox.launch_agent') as launch, patch('sys.stdout'):
                run.return_value = subprocess.CompletedProcess([], 1, '', 'refused')
                code = open_session(self.store, row['id'], row['revision'])
            self.assertEqual(code, 0, provider)
            self.assertEqual(launch.call_args[0], (provider, str(self.demo)))
            self.assertEqual(launch.call_args[1], {'args': ('--session', sid)})

    def test_open_resumes_agy_with_conversation_flag(self):
        from session_binding import register
        run_id = 'e' * 32
        lease = (self.store.root / (run_id + '.lock')).open('w')
        fcntl.flock(lease, fcntl.LOCK_EX)
        self.addCleanup(lease.close)
        register(self.store.root, 'pane-1', 'agy', run_id, '/dev/ttys997', 4242)
        env = {'SESSION_MANAGER_RUN_ID': run_id, 'SESSION_MANAGER_STATE': str(self.store.root)}
        record_event('agy', {'event': 'UserPromptSubmit', 'session_id': 'conv-1',
                             'cwd': str(self.demo)}, env=env)
        record_event('agy', {'event': 'Stop', 'session_id': 'conv-1'}, env=env)
        row = self.store.rows(unread_only=True)[0]
        Path(self.store.root / (run_id + '.lock')).unlink()
        with patch('inbox.subprocess.run') as run:
            run.return_value = subprocess.CompletedProcess([], 1, '', 'refused')
            with patch('inbox.launch_agent') as launch, patch('sys.stdout'):
                open_session(self.store, row['id'], row['revision'])
        self.assertEqual(launch.call_args[0], ('agy', str(self.demo)))
        self.assertEqual(launch.call_args[1], {'args': ('--conversation', 'conv-1')})


class OpenCodePluginTests(unittest.TestCase):
    """scripts/opencode_capture.js 真实上报路径：PYTHON 指向落盘 reporter，走 spawn+stdin。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        out = Path(self.tmp.name) / 'reports.jsonl'
        reporter = Path(self.tmp.name) / 'reporter.js'
        reporter.write_text(
            'let body = "";\n'
            'process.stdin.on("data", (chunk) => (body += chunk));\n'
            'process.stdin.on("end", () => require("fs").appendFileSync(process.env.OUT, body + "\\n"));\n')
        harness = Path(self.tmp.name) / 'harness.mjs'
        plugin = REPO / 'scripts' / 'opencode_capture.js'
        harness.write_text(
            f'const mod = await import({json.dumps("file:" + str(plugin))});\n'
            'const hooks = await mod.default();\n'
            'for (const line of process.argv.slice(2)) await hooks.event({ event: JSON.parse(line) });\n')
        self.harness, self.out, self.reporter = harness, out, reporter
        self.env = {'SESSION_MANAGER_RUN_ID': 'b' * 32, 'SESSION_MANAGER_PYTHON': 'node',
                    'SESSION_MANAGER_BINDING_SCRIPT': str(reporter), 'OUT': str(out)}

    def feed(self, *events):
        import os
        payload = [json.dumps(event) for event in events]
        result = subprocess.run(['node', str(self.harness), *payload], capture_output=True, text=True,
                                timeout=30, env={**os.environ, **self.env}, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return [json.loads(line) for line in self.out.read_text().splitlines() if line.strip()]

    def test_sdk_events_map_to_inbox_names(self):
        reports = self.feed(
            {'type': 'session.updated', 'properties': {'info': {'id': 'ses_1', 'title': 'T', 'directory': '/w'}}},
            {'type': 'chat.message', 'properties': {'sessionID': 'ses_1'}},
            {'type': 'session.idle', 'properties': {'sessionID': 'ses_1'}},
            {'type': 'permission.updated', 'properties': {'type': 'ask', 'sessionID': 'ses_1'}},
            {'type': 'permission.updated', 'properties': {'type': 'allow', 'sessionID': 'ses_1'}},
        )
        self.assertEqual([(r['event'], r['session_id']) for r in reports], [
            ('SessionStart', 'ses_1'), ('UserPromptSubmit', 'ses_1'), ('Stop', 'ses_1'),
            ('PermissionRequest', 'ses_1')])
        self.assertEqual(reports[0]['session_title'], 'T')
        self.assertEqual(reports[0]['cwd'], '/w')

    def test_assistant_error_maps_to_failure(self):
        reports = self.feed(
            {'type': 'message.updated', 'properties': {'info': {'role': 'assistant', 'error': {'name': 'X'}, 'sessionID': 'ses_1'}}},
            {'type': 'message.updated', 'properties': {'info': {'role': 'assistant', 'sessionID': 'ses_1'}}},
            {'type': 'message.updated', 'properties': {'info': {'role': 'user', 'sessionID': 'ses_1'}}},
        )
        self.assertEqual([r['event'] for r in reports], ['StopFailure'])

    def test_unmanaged_plugin_returns_no_hooks(self):
        import os
        result = subprocess.run(['node', '-e',
                                 (f'const m = await import({json.dumps("file:" + str(REPO / "scripts/opencode_capture.js"))});'
                                  ' const h = await m.default(); console.log(JSON.stringify(h));')],
                                capture_output=True, text=True, timeout=30,
                                env={k: v for k, v in os.environ.items()
                                     if not k.startswith('SESSION_MANAGER_')}, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {})


class PassivePurgeTests(unittest.TestCase):
    def test_migration_removes_directory_rows_once(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = Store(Path(tmp.name) / 'state')
        store.patch('opencode', 'ses_old', title='旧被动行', project='/gone',
                    locator={'kind': 'directory', 'directory': '/gone'})
        from inbox_sources import refresh
        refresh(store, Path(tmp.name) / 'home')
        self.assertEqual([r for r in store.rows() if r['provider'] == 'opencode'], [])
        removed = store.meta('opencode:managed-migration')['removed']
        refresh(store, Path(tmp.name) / 'home')
        self.assertEqual(store.meta('opencode:managed-migration')['removed'], removed)


if __name__ == '__main__':
    unittest.main()
