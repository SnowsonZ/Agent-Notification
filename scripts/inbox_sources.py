"""Read-only collectors for the locally verified desktop formats."""
from collections import defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from uuid import UUID
from urllib.parse import quote
from codex_rollout_events import RolloutReader
from inbox_store import Store

# 人工启动方式白名单（rollout session_meta.originator）：桌面应用与交互式 TUI
# （codex-tui=新版 TUI，codex_cli_rs=旧版 CLI；两者都是终端手敲形态）。
# 白名单之外（workbench/ACP/codex_exec 等程序化拉起）标记 agent：不通知、不进待查看、
# 日报默认不计。新入口被误判时把 originator 加进这里即可（2026-09-17 近 7 天实测分布）。
HUMAN_ORIGINATORS = {'Codex Desktop', 'codex-tui', 'codex_cli_rs'}


def spawn_origin(originator):
    return 'user' if originator in HUMAN_ORIGINATORS else 'agent'


def seconds(value):
    if isinstance(value, (int, float)):
        return value / 1000 if value > 100_000_000_000 else value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
        except ValueError:
            pass
    return 0


def collect_codex(store, home):
    root = home / '.codex/sessions'
    if not root.exists():
        return {'status': 'unavailable', 'reason': 'session directory missing'}
    titles = {}
    activity = {}
    index = home / '.codex/session_index.jsonl'
    if index.exists():
        for line in index.open():
            try:
                record = json.loads(line)
                titles[record['id']] = str(record['thread_name'])[:300]
                activity[record['id']] = seconds(record.get('updated_at'))
            except (ValueError, KeyError, TypeError):
                continue
    errors, changed = 0, 0
    if not store.meta('codex-cli:error-cache-reset'):
        # 一次性迁移：①CLI 纳入的中间态曾把非 Desktop 文件以 originator mismatch
        # 缓存为错误，清掉让其重解析（Desktop 混合身份文件保持缓存不动）；
        # ②存量 cli 行的 locator 补 rollout 文件路径（供跳转做 fd 聚焦）。
        # 老游标没有来源记录，读 rollout 首行判定一次后即写入缓存。
        with store.db() as db:
            cached = db.execute("SELECT key, value FROM metadata WHERE key LIKE 'codex-cursor:%'").fetchall()
        for row in cached:
            value = json.loads(row['value'])
            path = Path(row['key'].replace('codex-cursor:', ''))
            if value.get('error') or value.get('cli-migrated'):
                continue
            try:
                originator = json.loads(path.open(errors='replace').readline()).get('payload', {}).get('originator')
            except (OSError, ValueError):
                continue
            if originator == 'Codex Desktop':
                value['cli-migrated'] = True
                with store.db() as db:
                    db.execute('UPDATE metadata SET value=? WHERE key=?', (json.dumps(value), row['key']))
                continue
            sid = value.get('sid')
            if sid:
                store.patch('codex', sid, locator={'kind': 'cli', 'cwd': '', 'file': str(path)})
            value['cli-migrated'] = True
            with store.db() as db:
                db.execute('UPDATE metadata SET value=? WHERE key=?', (json.dumps(value), row['key']))
        store.set_meta('codex-cli:error-cache-reset', True)
    if not store.meta('codex-origin:backfill-v1'):
        # 一次性回填存量行的启动方式：逐 rollout 读首行 originator（游标缓存的文件
        # 不会在主循环重读首行）。sid 推导与主循环一致（文件名 UUID 优先）避免重复行。
        for path in root.rglob('rollout-*.jsonl'):
            try:
                first = json.loads(path.open(errors='replace').readline())
            except (OSError, ValueError):
                continue
            meta = first.get('payload', {})
            if first.get('type') != 'session_meta':
                continue
            sid = str(meta.get('id') or '')
            try:
                sid = str(UUID(path.stem[-36:]))
            except ValueError:
                pass
            if sid:
                store.patch('codex', sid, origin=spawn_origin(str(meta.get('originator') or '')))
        store.set_meta('codex-origin:backfill-v1', True)
    baseline = store.meta('started_at')
    for path in root.rglob('rollout-*.jsonl'):
        key, signature = None, None
        try:
            stat = path.stat()
            key = 'codex-cursor:' + str(path)
            prior = store.meta(key)
            signature = [stat.st_ino, stat.st_mtime_ns, stat.st_size]
            if prior and prior['signature'] == signature:
                if prior.get('error'):
                    errors += 1
                    continue
                if prior['sid'] in titles:
                    store.patch('codex', prior['sid'], title=titles[prior['sid']], activity_at=activity.get(prior['sid']))
                continue
            with path.open() as file:
                first = json.loads(file.readline())
            meta = first.get('payload', {})
            if first.get('type') != 'session_meta':
                continue
            originator = meta.get('originator')
            desktop = originator == 'Codex Desktop'
            cli_origin = bool(originator) and not desktop
            if not desktop and not cli_origin:
                continue
            if cli_origin:
                # CLI/exec 会话首次纳入时以当下为提醒基线：历史完成不轰炸。
                cli_baseline = store.meta('codex-cli:baseline')
                if cli_baseline is None:
                    cli_baseline = time.time()
                    store.set_meta('codex-cli:baseline', cli_baseline)
            sid = meta['id']
            try:
                sid = str(UUID(path.stem[-36:]))
            except ValueError:
                pass
            reader = RolloutReader(path, sid, allow_ancestry=True,
                                   originator=originator if cli_origin else None)
            if prior and prior.get('sid') == sid:
                reader.offset, reader.identity, reader.session = prior['offset'], tuple(prior['identity']), prior.get('validated_session', sid)
            batch = reader.poll()
            project = reader.metadata.get('cwd', '') if reader.metadata else None
            if desktop:
                locator = {'kind': 'url', 'url': 'codex://threads/' + quote(sid, safe='')}
            else:
                # CLI/exec 会话：按会话 ID 经 `codex resume` 恢复；
                # 携带 rollout 路径供「已打开则聚焦」做 fd 匹配。
                locator = {'kind': 'cli', 'cwd': project or '', 'file': str(path)}
            attention_baseline = cli_baseline if cli_origin else baseline
            store.patch('codex', sid, title=titles.get(sid), project=project, activity_at=activity.get(sid),
                        locator=locator, origin=spawn_origin(str(originator or '')))
            for event in batch:
                stamp = seconds(event['timestamp'])
                state = {'task_started': 'running', 'task_complete': 'idle', 'turn_aborted': 'interrupted'}[event['event']]
                # Import old history without flooding the new inbox.
                attention = event['event'] == 'task_complete' and stamp >= attention_baseline
                store.event('codex', sid, event_id=event['event_id'], timestamp=stamp,
                            state=state, attention=attention, token=event['turn_id'])
            store.set_meta(key, {'signature': signature, 'sid': sid, 'offset': reader.offset, 'identity': reader.identity,
                                 'validated_session': reader.session, 'cli': cli_origin, 'file': str(path) if cli_origin else None})
            changed += 1
        except (OSError, ValueError, KeyError, TypeError) as error:
            errors += 1
            if key is not None and signature is not None:
                store.set_meta(key, {'signature': signature, 'error': type(error).__name__})
    return {'status': 'degraded' if errors else 'ok', 'changed_files': changed, 'errors': errors}


def collect_claude(store, home):
    root = home / 'Library/Application Support/Claude/claude-code-sessions'
    if not root.exists():
        return {'status': 'unavailable', 'reason': 'desktop metadata missing'}
    grouped = defaultdict(list)
    errors = 0
    for path in root.rglob('local_*.json'):
        try:
            if path.stat().st_size > 4_000_000:
                errors += 1
                continue
            data = json.loads(path.read_text())
            if isinstance(data.get('cliSessionId'), str):
                grouped[data['cliSessionId']].append(data)
        except (OSError, ValueError, AttributeError):
            errors += 1
    for sid, records in grouped.items():
        if len(records) != 1 or errors:
            store.patch('claude', sid, origin='user',
                        locator={'kind': 'unavailable', 'reason': 'desktop identity ambiguous or incomplete'})
            continue
        data = records[0]
        desktop = data.get('sessionId', '')
        if not desktop.startswith('local_'):
            continue
        # Desktop 登记表成员 = 用户在 Desktop 界面驱动，origin 权威覆盖为 user
        #（hook 侧无头判定可能误判 Electron 内嵌形态，此处每次刷新自愈）。
        store.patch('claude', sid, origin='user',
                    title=str(data.get('title') or 'Claude · ' + sid[:12])[:300],
                    project=data.get('cwd', ''), hidden=bool(data.get('isArchived')), activity_at=seconds(data.get('lastActivityAt')),
                    locator={'kind': 'url', 'url': 'claude://code/continue?session=' + quote(desktop, safe='')})
        if data.get('error'):
            stamp = seconds(data.get('errorAt') or data.get('lastActivityAt'))
            store.event('claude', sid, event_id=f'claude-error:{sid}:{stamp}', timestamp=stamp,
                        state='failed', attention=stamp >= store.meta('started_at'))
    # claude CLI 直启会话（hook 创建、无桌面元数据）：旧版被隐藏，迁移为可见的
    # cli 定位（cwd 已由 hook 事件写入 project）；标题从转写首条用户消息提取。
    with store.db() as db:
        legacy = db.execute(
            "SELECT session_id, project FROM sessions WHERE provider='claude' "
            "AND locator='{}' AND hidden=1 AND project!=''").fetchall()
    for sid, project in legacy:
        store.patch('claude', sid, locator={'kind': 'cli', 'cwd': project},
                    project=project, hidden=False)
    titled = 0
    for row in store.rows():
        if row['provider'] != 'claude' or row['locator'].get('kind') != 'cli':
            continue
        if not row['locator'].get('file'):
            transcript = next((home / '.claude/projects').glob(f"*/{row['session_id']}.jsonl"), None)
            if transcript:
                locator = dict(row['locator'], file=str(transcript))
                store.patch('claude', row['session_id'], locator=locator)
            elif not store.meta('claude-cli-title-missing:' + row['session_id']):
                store.set_meta('claude-cli-title-missing:' + row['session_id'], True)
        if row['title'] and not row['title'].startswith('claude ·'):
            continue
        title = _claude_cli_title(home, row['session_id'], store)
        if title:
            store.patch('claude', row['session_id'], title=title)
            titled += 1
    return {'status': 'degraded' if errors else 'ok', 'sessions': len(grouped), 'errors': errors,
            'cli_titled': titled,
            'note': 'Live status requires hooks in sessions started after setup'}


def _claude_cli_title(home, sid, store, limit=80):
    """claude CLI 转写首条用户消息作展示标题（≤80 字符）；找不到转写返回 None 并记忆，
    避免每次刷新重复全目录查找。"""
    if store.meta('claude-cli-title-missing:' + sid):
        return None
    transcript = next((home / '.claude/projects').glob(f'*/{sid}.jsonl'), None)
    if transcript is None:
        store.set_meta('claude-cli-title-missing:' + sid, True)
        return None
    with transcript.open(errors='replace') as file:
        for line in file:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if record.get('type') != 'user' or record.get('isMeta'):
                continue
            message = record.get('message')
            content = message.get('content') if isinstance(message, dict) else None
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                text = ' '.join(block.get('text', '') for block in content
                                if isinstance(block, dict) and block.get('type') == 'text')
            else:
                continue
            text = ' '.join(text.split())
            if text and not text.startswith('<'):
                return text[:limit]
    return None


def collect_zcode(store, home):
    path = home / '.zcode/v2/tasks-index.sqlite'
    if not path.exists():
        return {'status': 'unavailable', 'reason': 'task index missing'}
    connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    try:
        rows = connection.execute('SELECT task_id,title,workspace_path,task_status,unread_at,updated_at,archived,deleted,last_unread_at FROM tasks').fetchall()
    finally:
        connection.close()
    latest_turns = {}
    runtime = home / '.zcode/cli/db/db.sqlite'
    turn_source_error = None
    if runtime.exists():
        connection = sqlite3.connect(runtime.as_uri() + '?mode=ro', uri=True)
        try:
            for turn in connection.execute('SELECT session_id,turn_id,status,started_at,completed_at FROM turn_usage ORDER BY started_at,turn_id'):
                latest_turns[turn[0]] = turn
        except sqlite3.Error:
            turn_source_error = 'turn_usage schema unavailable'
        finally:
            connection.close()
    else:
        turn_source_error = 'turn_usage database missing'
    baseline = store.meta('started_at')
    matched = 0
    for sid, title, project, status, unread, updated, archived, deleted, last_unread in rows:
        store.patch('zcode', sid, title=str(title or sid)[:300], project=project or '',
                    hidden=bool(archived or deleted), activity_at=seconds(updated), locator={'kind': 'zcode', 'task_id': sid})
        state = {'completed': 'idle', 'error': 'failed', 'running': 'running', 'waiting': 'waiting'}.get(status, 'unknown')
        if sid in latest_turns:
            matched += 1
            # v1 used task updated_at as the event clock, which also advances on
            # renames/views. Switch clocks once so real completion is not hidden.
            with store.db() as db:
                migrated = db.execute('INSERT OR IGNORE INTO metadata VALUES (?,?)', ('zcode-turn-clock:' + sid, 'true')).rowcount
                if migrated:
                    db.execute("UPDATE sessions SET event_at=0,revision=revision+1 WHERE provider='zcode' AND session_id=?", (sid,))
            _, turn_id, turn_status, started, completed = latest_turns[sid]
            start = seconds(started)
            store.event('zcode', sid, event_id=f'zcode-turn-start:{sid}:{turn_id}', timestamp=start,
                        state='running', attention=False if start >= baseline else None)
            end_applied = False
            if completed is not None and turn_status in ('completed', 'error', 'cancelled'):
                end = seconds(completed)
                final_state = {'completed': 'idle', 'error': 'failed', 'cancelled': 'interrupted'}[turn_status]
                attention = (turn_status != 'cancelled') if end >= baseline else None
                end_applied = store.event('zcode', sid, event_id=f'zcode-turn-end:{sid}:{turn_id}:{turn_status}:{completed}',
                                          timestamp=end, state=final_state, attention=attention, token=f'{turn_id}:{turn_status}')
            # Runtime rows land only when a turn ends, so while a turn is live
            # (including permission waits) the latest row is still the previous
            # turn and its terminal state would stick. The index does carry
            # liveness; trust it only when it advanced past the recorded end,
            # and not in the same refresh where that end first landed — a
            # lagging index must not flip a just-recorded completion back to
            # running. The stamp never exceeds that end, so the real next end
            # event can always override.
            if status == 'running' and not end_applied and completed is not None and seconds(updated) > seconds(completed):
                store.event('zcode', sid, event_id=f'zcode-live-running:{sid}:{turn_id}:{completed}',
                            timestamp=seconds(completed), state='running', attention=None)
        else:
            signature = hashlib.sha256(json.dumps([sid, status, updated]).encode()).hexdigest()
            store.event('zcode', sid, event_id='zcode-snapshot-v2:' + signature, timestamp=seconds(updated),
                        state=state, attention=None)
        # Native unread markers supplement real turn events; clearing a blue dot
        # must not clear this inbox's independently acknowledged attention state.
        marker = max(seconds(unread), seconds(last_unread))
        if marker and (unread or marker >= baseline):
            store.event('zcode', sid, event_id=f'zcode-unread:{sid}:{marker}', timestamp=marker,
                        state=state, attention=True, token=f'native-unread:{marker}')
    return {'status': 'degraded' if turn_source_error else 'ok', 'sessions': len(rows),
            'turn_sessions': matched, 'note': turn_source_error or 'Real turn lifecycle with independent inbox acknowledgement'}


def _sweep_managed_directories(store):
    """agy/opencode 受管理行：项目目录被删的条目隐藏（2026-09-16 用户决定：目录缺失
    不允许跳转、条目不可见）；目录恢复存在时自动取消隐藏。"""
    with store.db() as db:
        rows = db.execute("SELECT id, project FROM sessions "
                          "WHERE provider IN ('agy','opencode') AND project != ''").fetchall()
    for row_id, project in rows:
        exists = Path(project).expanduser().is_dir()
        with store.db() as db:
            db.execute('UPDATE sessions SET hidden=? WHERE id=?', (0 if exists else 1, row_id))


def _purge_passive_opencode(store):
    """一次性迁移：opencode 由被动扫描切换为受管理模式（2026-09-16 用户决定），
    旧被动行（directory 定位）删除；受管理运行后同会话 ID 会以 managed 定位重建。"""
    if store.meta('opencode:managed-migration'):
        return
    with store.db() as db:
        removed = db.execute("DELETE FROM sessions WHERE provider='opencode' "
                             "AND json_extract(locator,'$.kind')='directory'").rowcount
    store.set_meta('opencode:managed-migration', {'removed': removed, 'at': time.time()})


def collect_agy_titles(store, home):
    """给已有受管理 agy 行补标题（来自 summaries 库）；不产生新会话与事件。

    agy hooks 载荷没有标题字段，标题只能从 conversation_summaries 反查；
    只 patch store 里已存在的 provider='agy' 行，因此非受管理会话不会因此入箱。
    """
    path = home / '.gemini/antigravity-cli/conversation_summaries.db'
    if not path.exists():
        return {'status': 'unavailable', 'reason': 'summaries database missing'}
    targets = {row['session_id']: row['id'] for row in store.rows() if row['provider'] == 'agy'}
    titled = 0
    if targets:
        connection = sqlite3.connect(str(path))
        try:
            # agy 的标题常在 preview 而 title 为空：按 title → preview 兜底。
            for conversation_id, title, preview, workspace in connection.execute(
                    'SELECT conversation_id,title,preview,workspace_uris FROM conversation_summaries'):
                if conversation_id not in targets:
                    continue
                display = str(title or '').strip() or str(preview or '').strip()
                store.patch('agy', conversation_id, title=display[:300] or None)
                titled += 1
        finally:
            connection.close()
    return {'status': 'ok', 'titled': titled,
            'note': 'Titles from summaries db; hooks carry no title field'}


def refresh(store, home=None):
    home = Path.home() if home is None else home
    health = {}
    from pi_titles import collect_pi_titles
    _purge_passive_opencode(store)
    _sweep_managed_directories(store)
    for name, collector in [('codex', collect_codex), ('claude', collect_claude), ('zcode', collect_zcode),
                            ('pi', collect_pi_titles), ('agy', collect_agy_titles)]:
        try:
            health[name] = collector(store, home)
        except (OSError, ValueError, KeyError, sqlite3.Error) as error:
            health[name] = {'status': 'unavailable', 'reason': type(error).__name__}
    store.set_meta('health', {'checked_at': time.time(), 'sources': health})
    return health
