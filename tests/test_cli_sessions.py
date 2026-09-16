import fcntl
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from inbox import display_rows, open_session
from inbox_sources import collect_claude, collect_codex
from inbox_store import Store, receive


class ClaudeCliVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root)  # 与 receive(root, ...) 同库

    def test_hook_session_with_cwd_becomes_visible_cli_row(self):
        receive(self.root, 'claude', 'cli-1', 'SessionStart', project='/work/cli')
        row = self.store.rows()[0]
        self.assertEqual(row['project'], '/work/cli')
        self.assertEqual(row['locator'], {'kind': 'cli', 'cwd': '/work/cli'})
        self.assertFalse(row['hidden'])

    def test_hook_session_without_cwd_stays_hidden(self):
        receive(self.root, 'claude', 'cli-2', 'SessionStart', project=None)
        self.assertEqual(self.store.rows(), [])
        key = self.store.ensure('claude', 'cli-2')
        self.assertTrue(self.store.get.__self__)  # store 可用性占位
        with self.store.db() as db:
            row = db.execute('SELECT hidden FROM sessions WHERE id=?', (key,)).fetchone()
        self.assertEqual(row[0], 1)

    def test_lifecycle_events_flow_after_visibility(self):
        receive(self.root, 'claude', 'cli-1', 'SessionStart', project='/work/cli')
        receive(self.root, 'claude', 'cli-1', 'UserPromptSubmit', project='/work/cli')
        self.assertEqual(self.store.rows()[0]['state'], 'running')
        receive(self.root, 'claude', 'cli-1', 'Stop', project='/work/cli')
        row = self.store.rows()[0]
        self.assertEqual(row['state'], 'idle')
        self.assertTrue(row['unread'])


class ClaudeCliTitleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.store = Store(self.home / 'state')
        # collect_claude 需要 Desktop 元数据根存在才会走到迁移/标题逻辑。
        (self.home / 'Library/Application Support/Claude/claude-code-sessions').mkdir(parents=True)

    def test_title_from_first_user_message_with_preview_fallback_order(self):
        sid = '11111111-2222-3333-4444-555555555555'
        transcript = self.home / '.claude/projects/proj' / f'{sid}.jsonl'
        transcript.parent.mkdir(parents=True)
        lines = [
            json.dumps({'type': 'user', 'isMeta': True, 'message': {'content': '注入的上下文不应当标题'},
                        'sessionId': sid}),
            json.dumps({'type': 'user', 'message': {'content': '<command>跳过命令行</command>'},
                        'sessionId': sid}),
            json.dumps({'type': 'user', 'message': {'content': [
                {'type': 'text', 'text': '真实的  首条提问\n换行也保留'}]}, 'sessionId': sid}),
        ]
        transcript.write_text('\n'.join(lines) + '\n')
        self.store.patch('claude', sid, locator={'kind': 'cli', 'cwd': '/work/cli'})
        self.store.patch('claude', sid, title=None)
        health = collect_claude(self.store, self.home)
        self.assertEqual(health['cli_titled'], 1)
        row = self.store.rows()[0]
        self.assertEqual(row['title'], '真实的 首条提问 换行也保留')

    def test_missing_transcript_cached_negative(self):
        sid = '22222222-2222-3333-4444-555555555555'
        self.store.patch('claude', sid, locator={'kind': 'cli', 'cwd': '/work/cli'})
        health = collect_claude(self.store, self.home)
        self.assertEqual(health['cli_titled'], 0)
        self.assertTrue(self.store.meta('claude-cli-title-missing:' + sid))


class CodexCliInclusionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.store = Store(self.home / 'state')
        (self.home / '.codex/sessions/2026').mkdir(parents=True)
        (self.home / '.codex').mkdir(exist_ok=True)
        # 预置 CLI 基线为一小时前：此刻之后的完成应抬升待查看。
        self.store.set_meta('codex-cli:baseline', time.time() - 3600)

    def rollout(self, sid, originator, events):
        path = self.home / '.codex/sessions/2026' / f'rollout-{sid}.jsonl'
        body = [json.dumps({'type': 'session_meta',
                            'payload': {'id': sid, 'originator': originator, 'cwd': '/work/codexcli'}})]
        for kind, stamp in events:
            body.append(json.dumps({'type': 'event_msg',
                                    'payload': {'type': kind, 'event_id': f'{sid}:{kind}:{stamp}',
                                                'turn_id': 'turn-1'},
                                    'timestamp': stamp}))
        path.write_text('\n'.join(body) + '\n')

    def test_cli_originator_session_included_with_resume_locator(self):
        done = time.time() - 60
        self.rollout('cli-codex-1', 'codex_cli_rs',
                     [('task_started', done - 30), ('task_complete', done)])
        health = collect_codex(self.store, self.home)
        self.assertEqual(health['status'], 'ok')
        row = self.store.rows()[0]
        self.assertEqual(row['session_id'], 'cli-codex-1')
        self.assertEqual(row['state'], 'idle')
        self.assertTrue(row['unread'])  # 基线后的完成 → 待查看
        self.assertEqual(row['locator']['kind'], 'cli')
        self.assertEqual(row['locator']['cwd'], '/work/codexcli')
        self.assertTrue(row['locator']['file'].endswith('rollout-cli-codex-1.jsonl'))
        self.assertTrue(display_rows(self.store, all_rows=True)[0]['open_available'])

    def test_historical_cli_completion_before_baseline_stays_silent(self):
        self.rollout('cli-codex-old', 'codex_exec',
                     [('task_started', time.time() - 7200), ('task_complete', time.time() - 7100)])
        collect_codex(self.store, self.home)
        row = self.store.rows()[0]
        self.assertFalse(row['unread'])

    def test_desktop_originator_still_uses_url_locator(self):
        sid = 'dddddddd-1111-2222-3333-444444444444'
        self.rollout(sid, 'Codex Desktop', [('task_complete', time.time() - 60)])
        collect_codex(self.store, self.home)
        row = self.store.rows()[0]
        self.assertEqual(row['locator']['kind'], 'url')


class CliOpenTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.store = Store(self.home / 'state')
        self.demo = self.home / 'work' / 'demo'
        self.demo.mkdir(parents=True)

    def cli_row(self, provider):
        self.store.patch(provider, 'sess-cli', project=str(self.demo),
                         locator={'kind': 'cli', 'cwd': str(self.demo)})
        return self.store.rows()[0]

    def test_open_focuses_live_process_by_command_match(self):
        # 经包装器恢复的会话进程命令行携带会话 ID：直接聚焦原标签，不再新开。
        row = self.cli_row('claude')
        with patch('inbox.ttys_for_command', return_value=['ttys011']) as ttys, \
                patch('inbox.iterm_select_tty', return_value=True) as focus, \
                patch('inbox.launch_agent') as launch:
            with patch('sys.stdout'):
                code = open_session(self.store, row['id'], row['revision'])
        self.assertEqual(code, 0)
        ttys.assert_called_once_with(('--resume', 'sess-cli'))
        focus.assert_called_once_with('ttys011')
        launch.assert_not_called()

    def test_open_focuses_by_open_session_file_fd(self):
        # 用户自行启动的会话（命令行无会话 ID）：运行中持有转写/rollout fd，按 fd 定位。
        row = self.cli_row('codex')
        self.store.patch('codex', 'sess-cli',
                         locator={'kind': 'cli', 'cwd': str(self.demo), 'file': '/sessions/rollout-x.jsonl'})
        row = self.store.rows()[0]
        with patch('inbox.ttys_for_command', return_value=[]), \
                patch('inbox.ttys_for_open_file', return_value=['ttys009']) as filettys, \
                patch('inbox.iterm_select_tty', return_value=True) as focus, \
                patch('inbox.launch_agent') as launch:
            with patch('sys.stdout'):
                code = open_session(self.store, row['id'], row['revision'])
        self.assertEqual(code, 0)
        filettys.assert_called_once_with('/sessions/rollout-x.jsonl')
        focus.assert_called_once_with('ttys009')
        launch.assert_not_called()

    def test_claude_resume_dispatch(self):
        row = self.cli_row('claude')
        with patch('inbox.launch_agent') as launch:
            with patch('sys.stdout'):
                code = open_session(self.store, row['id'], row['revision'])
        self.assertEqual(code, 0)
        self.assertEqual(launch.call_args[0], ('claude', str(self.demo)))
        self.assertEqual(launch.call_args[1], {'args': ('--resume', 'sess-cli')})

    def test_codex_resume_dispatch(self):
        row = self.cli_row('codex')
        with patch('inbox.launch_agent') as launch:
            with patch('sys.stdout'):
                code = open_session(self.store, row['id'], row['revision'])
        self.assertEqual(code, 0)
        self.assertEqual(launch.call_args[0], ('codex', str(self.demo)))
        self.assertEqual(launch.call_args[1], {'args': ('resume', 'sess-cli')})


if __name__ == '__main__':
    unittest.main()
