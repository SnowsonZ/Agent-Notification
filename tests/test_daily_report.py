from datetime import date, datetime, timedelta
from pathlib import Path
import json
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from daily_report import (build_report, generate_day, generate_overview, heat_level,
                          load_report, render_markdown, scan_buckets, token_text)
from inbox_store import Store


def stamp(day, hour=0, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute).timestamp()


def iso(day, hour, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute).isoformat()


class DailyReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.store = Store(self.home / 'state')
        self.day = date.today() - timedelta(days=3)
        self.prev = self.day - timedelta(days=1)

    def zcode_index(self, sid, title='z 任务', project='/work/proj'):
        index = self.home / '.zcode/v2/tasks-index.sqlite'
        index.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(index) as db:
            db.execute('CREATE TABLE IF NOT EXISTS tasks (task_id,title,workspace_path,'
                       'task_status,unread_at,updated_at,archived,deleted,last_unread_at)')
            db.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?)',
                       (sid, title, project, 'completed', None, 0, 0, 0, 0))

    def zcode_runtime(self, minimal=False):
        runtime = self.home / '.zcode/cli/db/db.sqlite'
        runtime.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(runtime) as db:
            if minimal:
                db.execute('CREATE TABLE IF NOT EXISTS turn_usage (session_id,turn_id,status,started_at,completed_at)')
            else:
                db.execute('CREATE TABLE IF NOT EXISTS turn_usage (session_id,turn_id,status,started_at,'
                           'completed_at,input_tokens,cache_read_input_tokens,cache_creation_input_tokens,'
                           'output_tokens,reasoning_tokens)')

    def zcode_turn(self, sid, turn_id, start, end, *, status='completed', fresh=0, cached=0,
                   cache_creation=0, output=0, reasoning=0):
        self.zcode_runtime()
        with sqlite3.connect(self.home / '.zcode/cli/db/db.sqlite') as db:
            db.execute('INSERT INTO turn_usage VALUES (?,?,?,?,?,?,?,?,?,?)',
                       (sid, turn_id, status, round(start * 1000), round(end * 1000),
                        fresh + cached, cached, cache_creation, output, reasoning))

    def zcode_requests(self, turn_id, intervals):
        runtime = self.home / '.zcode/cli/db/db.sqlite'
        with sqlite3.connect(runtime) as db:
            db.execute('CREATE TABLE IF NOT EXISTS model_usage (turn_id,started_at,completed_at)')
            for start, end in intervals:
                db.execute('INSERT INTO model_usage VALUES (?,?,?)',
                           (turn_id, round(start * 1000), round(end * 1000) if end else None))

    def zcode_parents(self, mapping):
        runtime = self.home / '.zcode/cli/db/db.sqlite'
        runtime.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(runtime) as db:
            db.execute('CREATE TABLE IF NOT EXISTS session (id TEXT PRIMARY KEY, parent_id TEXT)')
            for sid, parent in mapping.items():
                db.execute('INSERT OR REPLACE INTO session VALUES (?,?)', (sid, parent))

    def codex_rollout(self, sid, lines, originator='Codex Desktop'):
        directory = self.home / '.codex/sessions/2026/09'
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f'rollout-{sid}.jsonl'
        body = [json.dumps({'type': 'session_meta',
                            'payload': {'id': sid, 'originator': originator, 'cwd': '/work/codex'}})]
        for kind, moment, usage in lines:
            payload = {'type': kind}
            if usage is not None:
                payload['info'] = {'last_token_usage': usage}
            body.append(json.dumps({'type': 'event_msg', 'payload': payload,
                                    'timestamp': iso(*moment) if isinstance(moment, tuple) else moment}))
        path.write_text('\n'.join(body) + '\n')
        return path

    def codex_index(self, sid, name):
        path = self.home / '.codex/session_index.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'id': sid, 'thread_name': name, 'updated_at': iso(self.day, 12)}) + '\n')

    def claude_session(self, sid, created, last, title='claude 会话', cwd='/work/claude', transcript=True,
                       usage_lines=(), desktop=True):
        if desktop:
            root = self.home / 'Library/Application Support/Claude/claude-code-sessions/d'
            root.mkdir(parents=True, exist_ok=True)
            (root / f'local_{sid}.json').write_text(json.dumps({
                'cliSessionId': sid, 'sessionId': 'local_' + sid, 'title': title, 'cwd': cwd,
                'createdAt': round(created * 1000), 'lastActivityAt': round(last * 1000)}))
        if transcript:
            path = self.home / '.claude/projects/proj' / f'{sid}.jsonl'
            path.parent.mkdir(parents=True, exist_ok=True)
            lines = [json.dumps({'type': 'assistant', 'sessionId': sid, 'timestamp': iso(*moment),
                                 'message': {'role': 'assistant', 'usage': usage}}) for moment, usage in usage_lines]
            path.write_text('\n'.join(lines) + '\n')

    def pi_session(self, sid, records, title='Pi 调研'):
        self.store.ensure('pi', sid)
        self.store.patch('pi', sid, title=title, project='/work/pi')
        path = self.home / '.pi/agent/sessions/s' / f'{sid}.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps({'type': 'session', 'id': sid, 'timestamp': iso(self.day, 8)})]
        for moment, usage in records:
            lines.append(json.dumps({'type': 'message', 'timestamp': iso(*moment),
                                     'message': {'role': 'assistant', 'usage': usage}}))
        path.write_text('\n'.join(lines) + '\n')
        self.store.set_meta('pi-file:' + sid, str(path))

    def kimi_wire(self, sid, records):
        path = self.home / '.kimi-code/sessions/wd_x' / f'session_{sid}' / 'agents/main/wire.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps({'type': 'usage.record', 'usage': usage, 'time': round(moment * 1000)})
                 for moment, usage in records]
        path.write_text('\n'.join(lines) + '\n')

    def single(self, buckets, day=None):
        records = buckets[day or self.day]
        self.assertEqual(len(records), 1, records)
        return records[0]

    def test_zcode_three_classes_and_cancelled_turns_counted(self):
        self.zcode_index('sess_a', '收件箱日报', '/work/session-manager')
        # input(含缓存读)=1600, 缓存读=600, 缓存写=50, 输出=200, reasoning=10
        # 三类 = 输入 1050 / 缓存 600 / 输出 210，合计 1860
        self.zcode_turn('sess_a', 't1', stamp(self.day, 10), stamp(self.day, 10, 30),
                        fresh=1000, cached=600, cache_creation=50, output=200, reasoning=10)
        self.zcode_turn('sess_a', 't2', stamp(self.day, 11), stamp(self.day, 11, 10),
                        status='cancelled', fresh=300)
        record = self.single(scan_buckets(self.store, self.home, self.day, self.day))
        self.assertEqual((record['input_tokens'], record['cache_tokens'], record['output_tokens']),
                         (1050 + 300, 600, 210))
        self.assertEqual((record['total_tokens'], record['turns'], record['fidelity']), (2160, 2, 'exact'))
        self.assertEqual(record['project'], '/work/session-manager')

    def test_subagent_tokens_attribute_to_parent_task_without_counting_turns(self):
        self.zcode_index('sess_main')
        self.zcode_parents({'sess_main': None, 'sess_subagent_agent_x': 'sess_main'})
        self.zcode_turn('sess_main', 't1', stamp(self.day, 9), stamp(self.day, 9, 30), fresh=1000)
        self.zcode_turn('sess_subagent_agent_x', 'sub1', stamp(self.day, 9, 10), stamp(self.day, 9, 20), fresh=500)
        record = self.single(scan_buckets(self.store, self.home, self.day, self.day))
        self.assertEqual((record['session_id'], record['input_tokens']), ('sess_main', 1500))
        self.assertEqual(record['turns'], 1)  # 子代理轮不计入父任务轮次

    def test_turn_tokens_split_proportionally_across_midnight(self):
        self.zcode_index('sess_a')
        self.zcode_turn('sess_a', 't1', stamp(self.prev, 23), stamp(self.day, 1),
                        fresh=3600, cached=3600, output=2400)  # 三类合计 9600，两小时各半
        buckets = scan_buckets(self.store, self.home, self.prev, self.day)
        self.assertEqual(buckets[self.prev][0]['total_tokens'], 4800)
        self.assertEqual(buckets[self.day][0]['total_tokens'], 4800)
        self.assertEqual(buckets[self.day][0]['cache_tokens'], 1800)
        self.assertEqual(buckets[self.day][0]['turns'], 0)

    def test_zcode_without_token_columns_degrades_to_unavailable(self):
        self.zcode_index('sess_a')
        self.zcode_runtime(minimal=True)
        with sqlite3.connect(self.home / '.zcode/cli/db/db.sqlite') as db:
            db.execute('INSERT INTO turn_usage VALUES (?,?,?,?,?)',
                       ('sess_a', 't1', 'completed', round(stamp(self.day, 9) * 1000), round(stamp(self.day, 10) * 1000)))
        record = self.single(scan_buckets(self.store, self.home, self.day, self.day))
        self.assertEqual((record['total_tokens'], record['fidelity'], record['turns']), (0, 'unavailable', 1))

    def test_codex_tokens_bucketed_by_request_time(self):
        sid = '11111111-2222-3333-4444-555555555555'
        self.codex_index(sid, 'codex 评审')
        self.codex_rollout(sid, [
            ('task_started', (self.day, 10), None),
            ('token_count', (self.day, 10, 5), {'input_tokens': 100, 'cached_input_tokens': 40,
                                                'cache_write_input_tokens': 5, 'output_tokens': 20,
                                                'reasoning_output_tokens': 3}),
            ('token_count', (self.day, 10, 30), {'input_tokens': 200, 'cached_input_tokens': 0,
                                                 'cache_write_input_tokens': 0, 'output_tokens': 40,
                                                 'reasoning_output_tokens': 0}),
            ('task_started', (self.day, 11), None),
        ])
        record = self.single(scan_buckets(self.store, self.home, self.day, self.day))
        # 输入 = (100+5) + 200；缓存 = 40；输出 = (20+3) + 40；合计 408
        self.assertEqual((record['input_tokens'], record['cache_tokens'], record['output_tokens']),
                         (305, 40, 63))
        self.assertEqual((record['total_tokens'], record['turns']), (408, 2))
        self.assertEqual((record['title'], record['project'], record['fidelity']),
                         ('codex 评审', '/work/codex', 'exact'))

    def test_codex_cli_originator_rollouts_counted(self):
        # 收件箱 2026-09-16 起纳入 CLI/exec/workbench 来源，日报同口径；
        # 缺失该口径时当日 codex 会话在日报整体缺源（2026-09-17 实测回归）。
        self.codex_rollout('wb-1', [
            ('token_count', (self.day, 10), {'input_tokens': 100, 'cached_input_tokens': 30,
                                             'cache_write_input_tokens': 0, 'output_tokens': 20,
                                             'reasoning_output_tokens': 0}),
        ], originator='coding-agent-workbench')
        self.codex_rollout('exec-1', [
            ('task_started', (self.day, 11), None),
        ], originator='codex_exec')
        records = [r for r in scan_buckets(self.store, self.home, self.day, self.day)[self.day]
                   if r['provider'] == 'codex']
        self.assertEqual(len(records), 2, records)
        by_sid = {r['session_id']: r for r in records}
        self.assertEqual((by_sid['wb-1']['input_tokens'], by_sid['wb-1']['cache_tokens'],
                          by_sid['wb-1']['output_tokens'], by_sid['wb-1']['fidelity']), (100, 30, 20, 'exact'))
        self.assertEqual(by_sid['exec-1']['turns'], 1)
        self.assertEqual(by_sid['exec-1']['total_tokens'], 0)

    def test_pi_assistant_usage_parsed(self):
        self.pi_session('pi-1', [
            ((self.day, 9), {'input': 100, 'cacheWrite': 10, 'output': 30, 'reasoning': 5, 'cacheRead': 900}),
            ((self.day, 9, 30), {'input': 50, 'cacheWrite': 0, 'output': 20, 'reasoning': 0, 'cacheRead': 100}),
        ])
        record = self.single(scan_buckets(self.store, self.home, self.day, self.day))
        self.assertEqual((record['input_tokens'], record['cache_tokens'], record['output_tokens']),
                         (160, 1000, 55))
        self.assertEqual((record['title'], record['project'], record['fidelity']), ('Pi 调研', '/work/pi', 'exact'))

    def test_kimi_wire_records_counted(self):
        self.kimi_wire('kimi-1', [
            (stamp(self.day, 14), {'inputOther': 200, 'inputCacheCreation': 0, 'output': 50, 'inputCacheRead': 800}),
            (stamp(self.day, 15), {'inputOther': 100, 'inputCacheCreation': 20, 'output': 30, 'inputCacheRead': 400}),
        ])
        record = self.single(scan_buckets(self.store, self.home, self.day, self.day))
        self.assertEqual((record['input_tokens'], record['cache_tokens'], record['output_tokens']),
                         (320, 1200, 80))
        self.assertEqual((record['total_tokens'], record['title']), (1600, 'Kimi · kimi-1'))

    def opencode_messages(self, sid, entries, *, title='OC 会话', directory='/work/oc', parent=None,
                          managed=True):
        if managed:
            self.store.ensure('opencode', sid)  # 受管理口径：登记过的会话才计入日报。
        database = self.home / '.local/share/opencode/opencode.db'
        database.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(database) as db:
            db.execute('CREATE TABLE IF NOT EXISTS session (id text PRIMARY KEY, title text, directory text,'
                       ' time_updated integer, time_archived integer, parent_id text)')
            db.execute('CREATE TABLE IF NOT EXISTS message (id text PRIMARY KEY, session_id text,'
                       ' time_created integer, time_updated integer, data text)')
            db.execute('INSERT OR IGNORE INTO session VALUES (?,?,?,?,?,?)', (sid, title, directory, 0, None, parent))
            for index, (moment, usage) in enumerate(entries):
                stamp_ms = round(stamp(self.day, *moment) * 1000)
                data = {'role': 'assistant', 'time': {'created': stamp_ms - 1000, 'completed': stamp_ms},
                        'tokens': usage}
                db.execute('INSERT INTO message VALUES (?,?,?,?,?)',
                           (f'msg_{sid}_{index}', sid, stamp_ms, stamp_ms, json.dumps(data)))

    def test_opencode_assistant_usage_counted(self):
        self.opencode_messages('ses_1', [
            ((10, 0), {'input': 100, 'output': 30, 'reasoning': 5, 'cache': {'read': 900, 'write': 10}}),
            ((10, 30), {'input': 50, 'output': 20, 'reasoning': 0, 'cache': {'read': 100, 'write': 0}}),
        ])
        # 同库但未经包装器登记的会话（如其它工具经 server 拉起）：不计入日报。
        self.opencode_messages('ses_ghost', [
            ((11, 0), {'input': 999, 'output': 999, 'cache': {'read': 999, 'write': 999}}),
        ], managed=False)
        record = self.single(scan_buckets(self.store, self.home, self.day, self.day))
        # 输入 = 100+10 + 50；缓存 = 900+100；输出 = 30+5 + 20；合计 1215
        self.assertEqual((record['input_tokens'], record['cache_tokens'], record['output_tokens']),
                         (160, 1000, 55))
        self.assertEqual((record['total_tokens'], record['turns'], record['provider']), (1215, 2, 'opencode'))
        self.assertEqual((record['title'], record['project'], record['fidelity']),
                         ('OC 会话', '/work/oc', 'exact'))

    def test_opencode_subagent_tokens_not_counted(self):
        self.opencode_messages('ses_sub', [
            ((10, 0), {'input': 100, 'output': 30, 'cache': {'read': 0, 'write': 0}}),
        ], parent='ses_parent')
        records = scan_buckets(self.store, self.home, self.day, self.day)[self.day]
        self.assertEqual(records, [])

    def test_claude_transcript_tokens_and_missing_fallback(self):
        self.claude_session('cli-1', stamp(self.day, 8), stamp(self.day, 12), cwd='/work/claude', transcript=True,
                            usage_lines=[((self.day, 9), {'input_tokens': 100, 'cache_creation_input_tokens': 10,
                                                          'cache_read_input_tokens': 500, 'output_tokens': 20}),
                                         ((self.day, 10), {'input_tokens': 30, 'cache_creation_input_tokens': 0,
                                                           'cache_read_input_tokens': 700, 'output_tokens': 40})])
        self.claude_session('cli-2', stamp(self.day, 13), stamp(self.day, 14), cwd='/work/other', transcript=False)
        records = scan_buckets(self.store, self.home, self.day, self.day)[self.day]
        by_sid = {record['session_id']: record for record in records}
        self.assertEqual((by_sid['cli-1']['input_tokens'], by_sid['cli-1']['cache_tokens'],
                          by_sid['cli-1']['output_tokens']), (140, 1200, 60))
        self.assertEqual((by_sid['cli-1']['total_tokens'], by_sid['cli-1']['fidelity']), (1400, 'exact'))
        self.assertEqual((by_sid['cli-2']['total_tokens'], by_sid['cli-2']['fidelity']), (0, 'unavailable'))
        self.assertEqual(by_sid['cli-2']['project'], '/work/other')

    def test_claude_cli_only_transcript_counted_once(self):
        # 未被桌面登记表登记的 CLI 直启转写：usage 照计；store 有已知行时补标题/项目。
        self.store.ensure('claude', 'cli-only')
        self.store.patch('claude', 'cli-only', title='CLI 会话', project='/work/cli')
        self.claude_session('cli-only', stamp(self.day, 8), stamp(self.day, 12), transcript=True,
                            usage_lines=[((self.day, 9), {'input_tokens': 100, 'cache_creation_input_tokens': 0,
                                                          'cache_read_input_tokens': 50, 'output_tokens': 25})],
                            desktop=False)
        # 桌面登记停留在窗口前（lastActivityAt 过期）但转写仍在活跃：经补采计一次。
        self.claude_session('stale-desktop', stamp(self.prev, 8), stamp(self.prev, 9), transcript=True,
                            usage_lines=[((self.day, 10), {'input_tokens': 10, 'cache_creation_input_tokens': 0,
                                                           'cache_read_input_tokens': 0, 'output_tokens': 5})],
                            desktop=True)
        # 未登记且窗口内无 assistant usage 的 CLI 转写：不产生任务。
        path = self.home / '.claude/projects/proj' / 'no-usage.jsonl'
        path.write_text(json.dumps({'type': 'user', 'timestamp': iso(self.day, 9)}) + '\n')
        records = scan_buckets(self.store, self.home, self.day, self.day)[self.day]
        self.assertEqual(sorted(r['session_id'] for r in records), ['cli-only', 'stale-desktop'])
        by_sid = {r['session_id']: r for r in records}
        self.assertEqual((by_sid['cli-only']['input_tokens'], by_sid['cli-only']['cache_tokens'],
                          by_sid['cli-only']['output_tokens'], by_sid['cli-only']['fidelity']),
                         (100, 50, 25, 'exact'))
        self.assertEqual((by_sid['cli-only']['title'], by_sid['cli-only']['project']), ('CLI 会话', '/work/cli'))
        self.assertEqual(by_sid['stale-desktop']['total_tokens'], 15)

    def test_agent_sessions_excluded_and_counted_in_footnote(self):
        # store 标记 agent 的会话不进日报（分类在收件箱注册侧完成，日报信任 store），
        # 其会话数与消耗进 agent_excluded 注脚，成本不失明。
        self.store.ensure('codex', 'ag-1')
        self.store.patch('codex', 'ag-1', origin='agent')
        self.codex_rollout('ag-1', [
            ('token_count', (self.day, 10), {'input_tokens': 500, 'cached_input_tokens': 0,
                                             'cache_write_input_tokens': 0, 'output_tokens': 100,
                                             'reasoning_output_tokens': 0}),
        ])
        self.zcode_index('sess_u', '用户任务', '/work/u')
        self.zcode_turn('sess_u', 't1', stamp(self.day, 10), stamp(self.day, 11), fresh=1000)
        stats = {}
        records = scan_buckets(self.store, self.home, self.day, self.day, agent_stats=stats)[self.day]
        self.assertEqual([r['session_id'] for r in records], ['sess_u'])
        self.assertEqual(stats[self.day]['tasks'], {'ag-1'})
        self.assertEqual(stats[self.day]['total_tokens'], 600)
        report = generate_day(self.store, self.home, self.day.isoformat(), refresh=True)
        self.assertEqual(report['version'], 7)
        self.assertEqual(report['agent_excluded'], {'tasks': 1, 'total_tokens': 600})
        self.assertEqual([t['session_id'] for t in report['tasks']], ['sess_u'])
        self.assertIn('1 个 agent 会话', render_markdown(report))
        # 无 agent 会话的日子不带该字段（干净 schema）。
        self.assertNotIn('agent_excluded', build_report(self.day, [], 0.0))

    def test_origin_rule_overrides_report_scope(self):
        # 目录规则读时覆盖：行标 agent + 规则 user → 计入；行 user + 规则 agent → 排除。
        self.store.ensure('codex', 'rule-user')
        self.store.patch('codex', 'rule-user', origin='agent', project='/work/rule-u')
        self.codex_rollout('rule-user', [
            ('token_count', (self.day, 10), {'input_tokens': 10, 'cached_input_tokens': 0,
                                             'cache_write_input_tokens': 0, 'output_tokens': 5,
                                             'reasoning_output_tokens': 0}),
        ])
        self.store.ensure('codex', 'rule-agent')
        self.store.patch('codex', 'rule-agent', origin='user', project='/work/rule-a')
        self.codex_rollout('rule-agent', [
            ('token_count', (self.day, 11), {'input_tokens': 20, 'cached_input_tokens': 0,
                                             'cache_write_input_tokens': 0, 'output_tokens': 0,
                                             'reasoning_output_tokens': 0}),
        ])
        self.store.set_origin_rule('/work/rule-u', 'user')
        self.store.set_origin_rule('/work/rule-a', 'agent')
        stats = {}
        records = scan_buckets(self.store, self.home, self.day, self.day, agent_stats=stats)[self.day]
        self.assertEqual([r['session_id'] for r in records], ['rule-user'])
        self.assertEqual(sorted(stats[self.day]['tasks']), ['rule-agent'])

    def test_heat_level_total_token_thresholds(self):
        values = (0, 19_999_999, 20_000_000, 99_999_999, 100_000_000, 399_999_999, 400_000_000)
        self.assertEqual([heat_level(value) for value in values], [0, 1, 2, 2, 3, 3, 4])

    def test_token_text_units(self):
        self.assertEqual([token_text(v) for v in (0, 895, 6_594, 456_700, 613_613,
                                                  1_000_000, 55_703_404, 143_168_263,
                                                  553_010_996, 1_000_000_000)],
                         ['0', '895', '6.6k', '457k', '614k', '1M', '55.7M', '143M', '553M', '1B'])

    def test_generate_day_writes_v7_and_markdown(self):
        self.zcode_index('sess_a', '收件箱日报', '/work/session-manager')
        self.zcode_turn('sess_a', 't1', stamp(self.day, 10), stamp(self.day, 12),
                        fresh=40_000, cached=900_000, output=10_000)
        report = generate_day(self.store, self.home, self.day.isoformat())
        self.assertEqual(report['version'], 7)
        self.assertEqual((report['totals']['input_tokens'], report['totals']['cache_tokens'],
                          report['totals']['output_tokens'], report['totals']['total_tokens']),
                         (40_000, 900_000, 10_000, 950_000))
        self.assertNotIn('active_seconds', report['tasks'][0])
        root = self.store.root / 'reports'
        self.assertTrue((root / f'{self.day.isoformat()}.json').exists())
        markdown = (root / f'{self.day.isoformat()}.json').with_suffix('.md').read_text()
        self.assertIn(f'工作日报 · {self.day.isoformat()}', markdown)
        self.assertIn('合计 950k', markdown)
        self.assertIn('900k', markdown)
        self.assertIn('收件箱日报', markdown)
        self.assertEqual(load_report(self.store.root, self.day.isoformat())['version'], 7)
        # 过去日已定稿：再次查看直接读缓存，不重扫来源（新增轮次不会出现）。
        self.zcode_turn('sess_a', 't2', stamp(self.day, 13), stamp(self.day, 14), fresh=1)
        again = generate_day(self.store, self.home, self.day.isoformat())
        self.assertEqual(again['totals'], report['totals'])
        self.assertEqual(again['generated_at'], report['generated_at'])

    def test_generate_day_keeps_today_live(self):
        today = date.today()
        self.zcode_index('sess_a')
        self.zcode_turn('sess_a', 't1', stamp(today, 0, 5), stamp(today, 0, 10), fresh=10)
        report = generate_day(self.store, self.home, today.isoformat())
        self.assertEqual(report['totals']['total_tokens'], 10)
        self.assertNotIn('path_md', report)
        self.assertIsNone(load_report(self.store.root, today.isoformat()))

    def test_task_segments_follow_real_activity_not_first_to_last_span(self):
        # Zcode：两轮相隔 6 小时，节奏带应得两段真实轮区间而非 09–17 一整条。
        self.zcode_index('sess_a')
        self.zcode_turn('sess_a', 't1', stamp(self.day, 9), stamp(self.day, 9, 20), fresh=10)
        self.zcode_turn('sess_a', 't2', stamp(self.day, 16), stamp(self.day, 17), fresh=10)
        # Claude：逐条消息时间戳，≤15 分钟聚成一段，跨大间隔分段。
        self.claude_session('cli-1', stamp(self.day, 8), stamp(self.day, 13),
                            usage_lines=[((self.day, 8, 0), {'input_tokens': 1, 'output_tokens': 1}),
                                         ((self.day, 8, 10), {'input_tokens': 1, 'output_tokens': 1}),
                                         ((self.day, 8, 24), {'input_tokens': 1, 'output_tokens': 1}),
                                         ((self.day, 12, 50), {'input_tokens': 1, 'output_tokens': 1})])
        records = scan_buckets(self.store, self.home, self.day, self.day)[self.day]
        by_sid = {record['session_id']: record for record in records}
        self.assertEqual(by_sid['sess_a']['segments'],
                         [[stamp(self.day, 9), stamp(self.day, 9, 20)], [stamp(self.day, 16), stamp(self.day, 17)]])
        self.assertEqual(by_sid['cli-1']['segments'],
                         [[stamp(self.day, 8), stamp(self.day, 8, 24)], [stamp(self.day, 12, 50), stamp(self.day, 12, 50)]])

    def test_zcode_segments_use_model_requests_not_whole_turn(self):
        # 一轮 16:00–23:00 中间等用户批准 6 个多小时：节奏段只取两次真实请求；token 归属不变。
        self.zcode_index('sess_a')
        self.zcode_turn('sess_a', 't1', stamp(self.day, 16), stamp(self.day, 23), fresh=100)
        self.zcode_requests('t1', [(stamp(self.day, 16), stamp(self.day, 16, 10)),
                                   (stamp(self.day, 22, 50), stamp(self.day, 23))])
        record = self.single(scan_buckets(self.store, self.home, self.day, self.day))
        self.assertEqual(record['segments'], [[stamp(self.day, 16), stamp(self.day, 16, 10)],
                                              [stamp(self.day, 22, 50), stamp(self.day, 23)]])
        self.assertEqual((record['total_tokens'], record['turns']), (100, 1))
        self.assertEqual((record['first_at'], record['last_at']), (stamp(self.day, 16), stamp(self.day, 23)))

    def test_segments_clamped_to_day_window_across_midnight(self):
        self.zcode_index('sess_a')
        self.zcode_turn('sess_a', 't1', stamp(self.prev, 23), stamp(self.day, 1), fresh=10)
        buckets = scan_buckets(self.store, self.home, self.prev, self.day)
        self.assertEqual(buckets[self.prev][0]['segments'], [[stamp(self.prev, 23), stamp(self.day, 0)]])
        self.assertEqual(buckets[self.day][0]['segments'], [[stamp(self.day, 0), stamp(self.day, 1)]])

    def test_empty_day_renders_placeholder(self):
        report = generate_day(self.store, self.home, self.day.isoformat())
        self.assertIn('这一天没有会话记录', render_markdown(report))

    def test_overview_backfill_cache_invalidates_v1_and_ranks_week_projects(self):
        self.zcode_index('sess_a', project='/work/alpha')
        self.zcode_turn('sess_a', 't1', stamp(self.day, 10), stamp(self.day, 11), fresh=600)
        self.zcode_turn('sess_a', 't2', stamp(self.prev, 10), stamp(self.prev, 11), fresh=400)
        self.zcode_turn('sess_a', 'old', stamp(self.day - timedelta(days=8), 10),
                        stamp(self.day - timedelta(days=8), 11), fresh=90_000_000)
        overview = generate_overview(self.store, self.home, days=30, top=5)
        by_date = {row['date']: row for row in overview['days']}
        self.assertEqual(by_date[self.day.isoformat()]['total_tokens'], 600)
        self.assertEqual(by_date[self.day.isoformat()]['level'], 1)
        self.assertEqual(overview['days'][-1]['date'], date.today().isoformat())
        self.assertIsNotNone(load_report(self.store.root, self.day.isoformat()))
        self.assertIsNone(load_report(self.store.root, date.today().isoformat()))
        # Top 项目只看近 7 天：8 天前的 9000 万不参与排名。
        self.assertEqual([item['name'] for item in overview['top_projects']], ['alpha'])
        self.assertEqual(overview['top_projects'][0]['total_tokens'], 1000)
        self.assertEqual(overview['top_projects'][0]['share'], 1.0)
        self.assertEqual(overview['week_sources']['zcode']['total_tokens'], 1000)
        self.assertEqual(overview['today']['tasks'], 0)
        # 旧版本报告（无 version）视为缺失并重刷。
        stale = load_report(self.store.root, self.day.isoformat())
        stale_path = self.store.root / 'reports' / f'{self.day.isoformat()}.json'
        stale.pop('version')
        stale_path.write_text(json.dumps(stale))
        self.assertIsNone(load_report(self.store.root, self.day.isoformat()))
        refreshed = generate_overview(self.store, self.home, days=30, top=5)
        self.assertEqual({row['date']: row for row in refreshed['days']}[self.day.isoformat()]['total_tokens'], 600)
        self.assertEqual(load_report(self.store.root, self.day.isoformat())['version'], 7)
        # 过去日读缓存：新增历史轮次不改变固化结果，总览与详情一致；只有 refresh 才重扫来源。
        self.zcode_turn('sess_a', 't3', stamp(self.day, 15), stamp(self.day, 16), fresh=500)
        cached = {row['date']: row for row in generate_overview(self.store, self.home, days=30, top=5)['days']}
        self.assertEqual(cached[self.day.isoformat()]['total_tokens'], 600)
        self.assertEqual(generate_day(self.store, self.home, self.day.isoformat())['totals']['total_tokens'], 600)
        fresh = generate_day(self.store, self.home, self.day.isoformat(), refresh=True)
        self.assertEqual(fresh['totals']['total_tokens'], 1100)
        self.assertEqual(load_report(self.store.root, self.day.isoformat())['totals']['total_tokens'], 1100)

    def test_overview_refreshes_past_day_snapshot_taken_before_day_end(self):
        # 当日结束前的快照缺晚间消耗：generated_at 早于当日结束的过去日在总览中补算一次后定稿。
        self.zcode_index('sess_a', project='/work/alpha')
        self.zcode_turn('sess_a', 't1', stamp(self.day, 10), stamp(self.day, 11), fresh=600)
        snapshot = build_report(self.day, [], stamp(self.day, 20))
        (self.store.root / 'reports').mkdir(parents=True, exist_ok=True)
        (self.store.root / 'reports' / f'{self.day.isoformat()}.json').write_text(json.dumps(snapshot))
        by_date = {row['date']: row for row in generate_overview(self.store, self.home, days=30)['days']}
        self.assertEqual(by_date[self.day.isoformat()]['total_tokens'], 600)
        finalized = load_report(self.store.root, self.day.isoformat())
        self.assertGreaterEqual(finalized['generated_at'], stamp(self.day + timedelta(days=1), 0))
        # 定稿后不再随来源变化。
        self.zcode_turn('sess_a', 't2', stamp(self.day, 15), stamp(self.day, 16), fresh=500)
        by_date = {row['date']: row for row in generate_overview(self.store, self.home, days=30)['days']}
        self.assertEqual(by_date[self.day.isoformat()]['total_tokens'], 600)


if __name__ == '__main__':
    unittest.main()
