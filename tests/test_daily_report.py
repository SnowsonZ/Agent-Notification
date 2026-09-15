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

    def zcode_parents(self, mapping):
        runtime = self.home / '.zcode/cli/db/db.sqlite'
        runtime.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(runtime) as db:
            db.execute('CREATE TABLE IF NOT EXISTS session (id TEXT PRIMARY KEY, parent_id TEXT)')
            for sid, parent in mapping.items():
                db.execute('INSERT OR REPLACE INTO session VALUES (?,?)', (sid, parent))

    def codex_rollout(self, sid, lines):
        directory = self.home / '.codex/sessions/2026/09'
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f'rollout-{sid}.jsonl'
        body = [json.dumps({'type': 'session_meta',
                            'payload': {'id': sid, 'originator': 'Codex Desktop', 'cwd': '/work/codex'}})]
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
                       usage_lines=()):
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

    def test_heat_level_total_token_thresholds(self):
        values = (0, 19_999_999, 20_000_000, 99_999_999, 100_000_000, 399_999_999, 400_000_000)
        self.assertEqual([heat_level(value) for value in values], [0, 1, 2, 2, 3, 3, 4])

    def test_token_text_units(self):
        self.assertEqual([token_text(v) for v in (0, 895, 6_594, 456_700, 613_613,
                                                  1_000_000, 55_703_404, 143_168_263,
                                                  553_010_996, 1_000_000_000)],
                         ['0', '895', '6.6k', '457k', '614k', '1M', '55.7M', '143M', '553M', '1B'])

    def test_generate_day_writes_v3_and_markdown(self):
        self.zcode_index('sess_a', '收件箱日报', '/work/session-manager')
        self.zcode_turn('sess_a', 't1', stamp(self.day, 10), stamp(self.day, 12),
                        fresh=40_000, cached=900_000, output=10_000)
        report = generate_day(self.store, self.home, self.day.isoformat())
        self.assertEqual(report['version'], 3)
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
        self.assertEqual(load_report(self.store.root, self.day.isoformat())['version'], 3)
        again = generate_day(self.store, self.home, self.day.isoformat())
        self.assertEqual(again['totals'], report['totals'])

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
        self.assertEqual(load_report(self.store.root, self.day.isoformat())['version'], 3)
        # 过去日读缓存：新增历史轮次不改变固化结果；单日重算（详情路径）取最新。
        self.zcode_turn('sess_a', 't3', stamp(self.day, 15), stamp(self.day, 16), fresh=500)
        cached = {row['date']: row for row in generate_overview(self.store, self.home, days=30, top=5)['days']}
        self.assertEqual(cached[self.day.isoformat()]['total_tokens'], 600)
        fresh = generate_day(self.store, self.home, self.day.isoformat())
        self.assertEqual(fresh['totals']['total_tokens'], 1100)

    def test_overview_refreshes_past_day_snapshot_taken_before_day_end(self):
        # 20:00 定时快照缺晚间消耗：generated_at 早于当日结束的过去日在总览中补算一次后定稿。
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
