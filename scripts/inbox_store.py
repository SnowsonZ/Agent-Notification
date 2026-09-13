"""Local inbox state. Source events are idempotent; acknowledgements use CAS."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import time

DEFAULT_ROOT = Path.home() / '.local/state/session-manager'


def identity(provider, session_id):
    return hashlib.sha256(json.dumps([provider, session_id]).encode()).hexdigest()[:24]


class Store:
    def __init__(self, root=DEFAULT_ROOT):
        self.root = root
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = root / 'inbox.sqlite'
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS sessions (
                  id TEXT PRIMARY KEY, provider TEXT, session_id TEXT, title TEXT,
                  project TEXT DEFAULT '', state TEXT DEFAULT 'unknown', unread INTEGER DEFAULT 0,
                  event_at REAL DEFAULT 0, revision INTEGER DEFAULT 0, attention_token TEXT,
                  locator TEXT DEFAULT '{}', hidden INTEGER DEFAULT 0);
                CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT);
            ''')
            db.execute('INSERT OR IGNORE INTO metadata VALUES (?,?)', ('started_at', json.dumps(time.time())))
            if 'activity_at' not in {row['name'] for row in db.execute('PRAGMA table_info(sessions)')}:
                db.execute('ALTER TABLE sessions ADD COLUMN activity_at REAL DEFAULT 0')

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.path, timeout=3)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def meta(self, key, default=None):
        with self.db() as db:
            row = db.execute('SELECT value FROM metadata WHERE key=?', (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set_meta(self, key, value):
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)', (key, json.dumps(value)))

    def ensure(self, provider, sid):
        key = identity(provider, sid)
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO sessions (id,provider,session_id,title) VALUES (?,?,?,?)',
                       (key, provider, sid, provider + ' · ' + sid[:12]))
        return key

    def patch(self, provider, sid, *, title=None, project=None, locator=None, hidden=None, activity_at=None):
        key = self.ensure(provider, sid)
        fields = {k: v for k, v in [('title', title), ('project', project),
                  ('locator', json.dumps(locator) if locator is not None else None),
                  ('hidden', int(hidden) if hidden is not None else None)] if v is not None}
        if fields:
            with self.db() as db:
                db.execute('UPDATE sessions SET ' + ','.join(k + '=?' for k in fields) + ' WHERE id=?',
                           [*fields.values(), key])
        if isinstance(activity_at, (int, float)):
            with self.db() as db:
                db.execute('UPDATE sessions SET activity_at=MAX(activity_at,?) WHERE id=?', (activity_at, key))
        return key

    def event(self, provider, sid, *, event_id, timestamp, state, attention=None, token=None):
        key = self.ensure(provider, sid)
        with self.db() as db:
            if not db.execute('INSERT OR IGNORE INTO events VALUES (?)', (event_id,)).rowcount:
                return False
            row = db.execute('SELECT * FROM sessions WHERE id=?', (key,)).fetchone()
            if timestamp < row['event_at']:
                return False
            unread = row['unread']
            attention_token = row['attention_token']
            if attention is False:
                unread = 0
            elif attention is True and (token or event_id) != attention_token:
                unread = 1
                attention_token = token or event_id
            db.execute('UPDATE sessions SET state=?,unread=?,event_at=?,activity_at=MAX(activity_at,?),revision=revision+1,attention_token=? WHERE id=?',
                       (state, unread, timestamp, timestamp, attention_token, key))
        return True

    def acknowledge(self, key, revision):
        with self.db() as db:
            return bool(db.execute('UPDATE sessions SET unread=0,revision=revision+1 WHERE id=? AND revision=?',
                                   (key, revision)).rowcount)

    def rows(self, unread_only=False):
        order = ("CASE state WHEN 'waiting' THEN 0 WHEN 'failed' THEN 1 ELSE 2 END, " if unread_only else '')
        order += 'MAX(activity_at,event_at) DESC, id'
        with self.db() as db:
            rows = db.execute('SELECT * FROM sessions WHERE hidden=0 ' + ('AND unread=1 ' if unread_only else '') +
                              'ORDER BY ' + order).fetchall()
        return [{**dict(row), 'locator': json.loads(row['locator']), 'unread': bool(row['unread'])} for row in rows]

    def get(self, key):
        with self.db() as db:
            row = db.execute('SELECT * FROM sessions WHERE id=? AND hidden=0', (key,)).fetchone()
        if row is None:
            raise ValueError('session missing or archived')
        return {**dict(row), 'locator': json.loads(row['locator'])}


EVENTS = {
    'SessionStart': ('idle', False), 'session_start': ('idle', False),
    'UserPromptSubmit': ('running', False), 'TurnStarted': ('running', False), 'agent_start': ('running', False),
    'Stop': ('idle', True), 'agent_settled': ('idle', True),
    'StopFailure': ('failed', True), 'PermissionRequest': ('waiting', True), 'ui_prompt_start': ('waiting', True),
    'PermissionResult': ('running', False), 'ui_prompt_end': ('running', False),
    'Interrupt': ('interrupted', False), 'SessionEnd': ('closed', None), 'session_shutdown': ('closed', None),
}


def receive(root, provider, sid, event, *, run_id=None, title=None, project=None, idle=True):
    from uuid import uuid4
    store = Store(root)
    title = title[:300] if isinstance(title, str) and title else None
    project = project[:2048] if isinstance(project, str) else None
    if provider == 'claude':
        key = store.ensure(provider, sid)
        with store.db() as db:
            db.execute("UPDATE sessions SET hidden=1 WHERE id=? AND locator='{}'", (key,))
    locator = {'kind': 'managed', 'run_id': run_id, 'session_id': sid} if run_id else None
    store.patch(provider, sid, title=title, project=project, locator=locator,
                activity_at=time.time() if event == 'session_info_changed' else None)
    if event not in EVENTS or (event == 'agent_settled' and not idle):
        return
    state, attention = EVENTS[event]
    store.event(provider, sid, event_id=uuid4().hex, timestamp=time.time(), state=state, attention=attention)
