"""Derive a short Pi display title without storing conversation bodies."""
import json
from pathlib import Path


def compact(text, limit=80):
    return ' '.join(text.split())[:limit] if isinstance(text, str) else ''


def read_title(path, expected_id):
    with path.open('rb') as file:
        prefix = file.read(262144)
        file.seek(0, 2)
        size = file.tell()
        offset = max(0, size - 65536)
        file.seek(offset)
        tail = file.read()
    lines = prefix.split(b'\n')
    header = json.loads(lines[0])
    if header.get('type') != 'session' or header.get('id') != expected_id:
        raise ValueError('Pi session identity mismatch')
    def records(chunks):
        for line in chunks:
            try:
                record = json.loads(line)
                if isinstance(record, dict):
                    yield record
            except ValueError:
                continue
    first_prompt = ''
    explicit = ''
    for record in records(lines[1:-1]):
        if record.get('type') == 'session_info':
            explicit = compact(record.get('name'))
        message = record.get('message', {})
        if not first_prompt and record.get('type') == 'message' and message.get('role') == 'user':
            content = message.get('content', '')
            text = content if isinstance(content, str) else ' '.join(
                block.get('text', '') for block in content if isinstance(block, dict) and block.get('type') == 'text')
            first_prompt = compact(text)
    if offset:
        for record in records(tail.split(b'\n')[1:-1]):
            if record.get('type') == 'session_info':
                explicit = compact(record.get('name'))
    return explicit or first_prompt or ('Pi · ' + Path(header.get('cwd') or '.').name)


def collect_pi_titles(store, home):
    rows = [row for row in store.rows() if row['provider'] == 'pi']
    directory = home / '.pi/agent/sessions'
    files = list(directory.rglob('*.jsonl')) if directory.exists() else []
    updated, errors = 0, 0
    for row in rows:
        if row['title'] and not row['title'].startswith(('pi · ', 'Pi · ')) and row['title'] != 'Pi 会话':
            continue  # Live extension names win over bounded historical reads.
        sid = row['session_id']
        registered = store.meta('pi-file:' + sid)
        matches = [Path(registered)] if isinstance(registered, str) else [p for p in files if sid in p.name]
        if len(matches) != 1:
            if row['title'].startswith('pi · '):
                project = Path(row['project']).name if row['project'] else ''
                store.patch('pi', sid, title='Pi · ' + project if project else 'Pi 会话')
            continue
        try:
            title = read_title(matches[0], sid)
            store.patch('pi', sid, title=title)
            updated += 1
        except (OSError, ValueError, AttributeError, TypeError):
            errors += 1
    return {'status': 'degraded' if errors else 'ok', 'titles_updated': updated, 'errors': errors}
