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
    index = home / '.codex/session_index.jsonl'
    if index.exists():
        for line in index.open():
            try:
                record = json.loads(line)
                titles[record['id']] = str(record['thread_name'])[:300]
            except (ValueError, KeyError, TypeError):
                continue
    errors, changed = 0, 0
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
                    store.patch('codex', prior['sid'], title=titles[prior['sid']])
                continue
            with path.open() as file:
                first = json.loads(file.readline())
            meta = first.get('payload', {})
            if first.get('type') != 'session_meta' or meta.get('originator') != 'Codex Desktop':
                continue
            sid = meta['id']
            try:
                sid = str(UUID(path.stem[-36:]))
            except ValueError:
                pass
            reader = RolloutReader(path, sid, allow_ancestry=True)
            if prior and prior.get('sid') == sid:
                reader.offset, reader.identity, reader.session = prior['offset'], tuple(prior['identity']), prior.get('validated_session', sid)
            batch = reader.poll()
            project = reader.metadata.get('cwd', '') if reader.metadata else None
            store.patch('codex', sid, title=titles.get(sid), project=project,
                        locator={'kind': 'url', 'url': 'codex://threads/' + quote(sid, safe='')})
            for event in batch:
                stamp = seconds(event['timestamp'])
                state = {'task_started': 'running', 'task_complete': 'idle', 'turn_aborted': 'interrupted'}[event['event']]
                # Import old history without flooding the new inbox.
                attention = event['event'] == 'task_complete' and stamp >= baseline
                store.event('codex', sid, event_id=event['event_id'], timestamp=stamp,
                            state=state, attention=attention, token=event['turn_id'])
            store.set_meta(key, {'signature': signature, 'sid': sid, 'offset': reader.offset, 'identity': reader.identity,
                                 'validated_session': reader.session})
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
            store.patch('claude', sid, locator={'kind': 'unavailable', 'reason': 'desktop identity ambiguous or incomplete'})
            continue
        data = records[0]
        desktop = data.get('sessionId', '')
        if not desktop.startswith('local_'):
            continue
        store.patch('claude', sid, title=str(data.get('title') or 'Claude · ' + sid[:12])[:300],
                    project=data.get('cwd', ''), hidden=bool(data.get('isArchived')),
                    locator={'kind': 'url', 'url': 'claude://code/continue?session=' + quote(desktop, safe='')})
        if data.get('error'):
            stamp = seconds(data.get('errorAt') or data.get('lastActivityAt'))
            store.event('claude', sid, event_id=f'claude-error:{sid}:{stamp}', timestamp=stamp,
                        state='failed', attention=stamp >= store.meta('started_at'))
    return {'status': 'degraded' if errors else 'ok', 'sessions': len(grouped), 'errors': errors,
            'note': 'Live status requires hooks in sessions started after setup'}


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
                    hidden=bool(archived or deleted), locator={'kind': 'zcode', 'task_id': sid})
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
            if completed is not None and turn_status in ('completed', 'error', 'cancelled'):
                end = seconds(completed)
                final_state = {'completed': 'idle', 'error': 'failed', 'cancelled': 'interrupted'}[turn_status]
                attention = (turn_status != 'cancelled') if end >= baseline else None
                store.event('zcode', sid, event_id=f'zcode-turn-end:{sid}:{turn_id}:{turn_status}:{completed}',
                            timestamp=end, state=final_state, attention=attention, token=f'{turn_id}:{turn_status}')
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


def refresh(store, home=None):
    home = Path.home() if home is None else home
    health = {}
    for name, collector in [('codex', collect_codex), ('claude', collect_claude), ('zcode', collect_zcode)]:
        try:
            health[name] = collector(store, home)
        except (OSError, ValueError, KeyError, sqlite3.Error) as error:
            health[name] = {'status': 'unavailable', 'reason': type(error).__name__}
    store.set_meta('health', {'checked_at': time.time(), 'sources': health})
    return health
