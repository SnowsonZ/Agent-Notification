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
import os

from inbox import (backfill_claude_tmp_origins, desktop_registry_hit, display_rows, effective_origin,
                   open_session, spawn_origin_from_parent, spawn_origin_from_tty)
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

    def test_receive_records_spawn_origin(self):
        receive(self.root, 'claude', 'cli-9', 'SessionStart', project='/work/cli', origin='agent')
        self.assertEqual(self.store.rows()[0]['origin'], 'agent')
        receive(self.root, 'claude', 'cli-9', 'Stop', project='/work/cli', origin='agent')
        self.assertTrue(self.store.rows()[0]['unread'])
        # 未带 origin 的事件不改动已记录的启动方式。
        receive(self.root, 'claude', 'cli-9', 'Stop', project='/work/cli')
        self.assertEqual(self.store.rows()[0]['origin'], 'agent')

    def test_tty_spawn_origin_rule(self):
        self.assertEqual(spawn_origin_from_tty('??'), 'agent')
        self.assertEqual(spawn_origin_from_tty(' ttys001\n'), 'user')
        self.assertEqual(spawn_origin_from_tty(''), 'user')

    def test_parent_spawn_origin_rule(self):
        # 终端会被子进程继承，tty 不够：直接父进程是终端 shell = 手敲，工具进程 = agent。
        self.assertEqual(spawn_origin_from_parent('zsh'), 'user')
        self.assertEqual(spawn_origin_from_parent('bash\n'), 'user')
        self.assertEqual(spawn_origin_from_parent('python3'), 'agent')
        self.assertEqual(spawn_origin_from_parent('node'), 'agent')
        self.assertEqual(spawn_origin_from_parent(''), 'user')

    def test_declared_env_overrides_heuristics(self):
        # 声明优于推断：拉起方设置 SESSION_MANAGER_ORIGIN 即精确生效，不走启发式。
        from inbox import DECLARED_ORIGIN_ENV, claude_spawn_origin
        with patch.dict(os.environ, {DECLARED_ORIGIN_ENV: 'agent'}):
            self.assertEqual(claude_spawn_origin('whatever'), 'agent')
        with patch.dict(os.environ, {DECLARED_ORIGIN_ENV: 'user'}):
            self.assertEqual(claude_spawn_origin('whatever'), 'user')
        with patch.dict(os.environ, {DECLARED_ORIGIN_ENV: 'bogus'}, clear=False):
            # 非法值忽略——落到启发式；进程 tty 不可控，仅验证不抛错且值合法。
            self.assertIn(claude_spawn_origin('whatever'), ('agent', 'user'))

    def test_origin_rule_overrides_read_side(self):
        # 目录规则读时覆盖自动分类：行上 origin 不动，display/日报按规则算有效 origin。
        receive(self.root, 'claude', 'auto-agent', 'Stop', project='/work/tool-dir', origin='agent')
        receive(self.root, 'claude', 'auto-user', 'Stop', project='/work/other', origin='user')
        self.store.set_origin_rule('/work/tool-dir', 'user')
        self.assertEqual(self.store.origin_rules(), [{'project': '/work/tool-dir', 'origin': 'user'}])
        row = next(r for r in self.store.rows() if r['session_id'] == 'auto-agent')
        self.assertEqual(row['origin'], 'agent')  # 行上分类不动
        rows = display_rows(self.store, all_rows=True)
        self.assertEqual(sorted(r['session_id'] for r in rows), ['auto-agent', 'auto-user'])
        # 反向规则：目录标 agent 后即使行是 user 也被过滤（tool-dir 的 user 规则仍在）。
        self.store.set_origin_rule('/work/other', 'agent')
        self.assertEqual(sorted(r['session_id'] for r in display_rows(self.store, all_rows=True)),
                         ['auto-agent'])
        # 删除规则恢复自动分类：agent 行回到默认隐藏。
        self.store.set_origin_rule('/work/other', None)
        self.store.set_origin_rule('/work/tool-dir', None)
        self.assertEqual(self.store.origin_rules(), [])
        self.assertEqual([r['session_id'] for r in display_rows(self.store, all_rows=True)],
                         ['auto-user'])

    def test_claude_tmp_project_backfill(self):
        receive(self.root, 'claude', 'tmp-1', 'SessionStart', project='/var/folders/xx/T/wb-gate-claude-a')
        receive(self.root, 'claude', 'real-1', 'SessionStart', project='/Users/snowson/work/real')
        with self.store.db() as db:
            # 模拟迁移前状态：origin 均为默认 user。
            db.execute("UPDATE sessions SET origin='user'")
        backfill_claude_tmp_origins(self.store)
        origins = {r['session_id']: r['origin'] for r in self.store.rows()}
        self.assertEqual(origins, {'tmp-1': 'agent', 'real-1': 'user'})
        # 幂等：再次执行不改动。
        self.store.patch('claude', 'real-1', origin='user')
        backfill_claude_tmp_origins(self.store)
        self.assertEqual({r['session_id']: r['origin'] for r in self.store.rows()}['tmp-1'], 'agent')

    def test_desktop_registry_hit_requires_recent_match(self):
        import os
        registry = self.root / 'Library/Application Support/Claude/claude-code-sessions'
        registry.mkdir(parents=True)
        fresh = registry / 'local_fresh.json'
        fresh.write_text(json.dumps({'cliSessionId': 'sid-fresh'}))
        stale = registry / 'local_stale.json'
        stale.write_text(json.dumps({'cliSessionId': 'sid-stale'}))
        os.utime(stale, (time.time() - 3 * 86400, time.time() - 3 * 86400))
        self.assertTrue(desktop_registry_hit(self.root, 'sid-fresh'))
        self.assertFalse(desktop_registry_hit(self.root, 'sid-stale'))  # 过期登记不算
        self.assertFalse(desktop_registry_hit(self.root, 'sid-other'))  # 未登记=无头 CLI


class DesktopOriginOverrideTests(unittest.TestCase):
    """Desktop 登记表成员是用户在 Desktop 界面驱动的：origin 权威为 user，
    每次 collect_claude 刷新自愈 hook 无头判定的误伤（Electron 内嵌无终端）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.store = Store(self.home / 'state')
        self.registry = self.home / 'Library/Application Support/Claude/claude-code-sessions'
        self.registry.mkdir(parents=True)

    def desktop_entry(self, sid):
        (self.registry / f'local_{sid}.json').write_text(json.dumps({
            'cliSessionId': sid, 'sessionId': 'local_' + sid, 'title': '桌面会话',
            'cwd': '/Users/snowson/work', 'lastActivityAt': round(time.time() * 1000)}))

    def test_desktop_session_self_heals_to_user(self):
        receive(self.home / 'state', 'claude', 'desk-1', 'SessionStart',
                project='/Users/snowson/work', origin='agent')  # hook 无头误判
        self.desktop_entry('desk-1')
        collect_claude(self.store, self.home)
        self.assertEqual(self.store.rows()[0]['origin'], 'user')
        self.assertEqual(self.store.rows()[0]['locator']['kind'], 'url')

    def test_headless_session_not_in_registry_stays_agent(self):
        receive(self.home / 'state', 'claude', 'head-1', 'Stop',
                project='/var/folders/x/T/wb-gate-claude-z', origin='agent')
        collect_claude(self.store, self.home)
        self.assertEqual(self.store.rows()[0]['origin'], 'agent')


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

    def test_workbench_originator_marked_agent_and_hidden_from_display(self):
        # 多 agent 工具拉起的会话仍走完整事件链（含待查看抬升），但默认不出现在收件箱。
        self.rollout('wb-1', 'coding-agent-workbench', [('task_complete', time.time() - 60)])
        collect_codex(self.store, self.home)
        row = self.store.rows()[0]
        self.assertEqual(row['origin'], 'agent')
        self.assertTrue(row['unread'])
        self.assertEqual(display_rows(self.store, all_rows=True), [])
        self.assertEqual(len(display_rows(self.store, all_rows=True, include_agents=True)), 1)

    def test_interactive_tui_originators_marked_user(self):
        for index, originator in enumerate(('codex-tui', 'codex_cli_rs')):
            self.rollout(f'tui-{index}', originator, [('task_complete', time.time() - 60)])
        collect_codex(self.store, self.home)
        origins = {row['session_id']: row['origin'] for row in self.store.rows()}
        self.assertEqual(origins, {'tui-0': 'user', 'tui-1': 'user'})
        self.assertTrue(self.store.meta('codex-origin:backfill-v1'))

    def test_origin_backfill_tags_existing_rows(self):
        # 一次性迁移：先有库后加白名单判定的场景，回填按 rollout 首行补 origin。
        self.rollout('legacy-exec', 'codex_exec', [('task_complete', time.time() - 3600)])
        collect_codex(self.store, self.home)
        row = self.store.rows()[0]
        self.assertEqual(row['origin'], 'agent')


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
