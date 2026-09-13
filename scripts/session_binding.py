#!/usr/bin/env python3
"""Managed CLI lifetime and session-to-pane bindings; no terminal UI control."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import shlex
import sqlite3
import subprocess
import sys
import tomllib
import tempfile
from uuid import uuid4

DEFAULT_ROOT = Path.home() / '.local/state/session-manager'
TOKEN = re.compile(r'[a-f0-9]{32}\Z')
KIMI_EVENTS = ('SessionStart', 'SessionEnd', 'UserPromptSubmit', 'Stop', 'StopFailure',
               'PermissionRequest', 'PermissionResult', 'Interrupt')


def kimi_command():
    return shlex.join([sys.executable, str(Path(__file__).resolve()), 'event', '--provider', 'kimi'])


def install_kimi_hooks(home):
    path = home / 'config.toml'
    if path.is_symlink():
        raise ValueError('refusing to replace a linked config')
    original = path.read_text() if path.exists() else ''
    settings = tomllib.loads(original)
    command = kimi_command()
    existing = {hook['event'] for hook in settings.get('hooks', []) if hook.get('command') == command}
    additions = ''
    for event_name in KIMI_EVENTS:
        if event_name not in existing:
            additions += f'\n[[hooks]]\nevent = "{event_name}"\ncommand = {json.dumps(command)}\ntimeout = 3\n'
    if not additions:
        return 0
    content = original.rstrip() + '\n' + additions
    tomllib.loads(content)
    home.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.session-manager-', dir=home)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(content)
        if (path.read_text() if path.exists() else '') != original:
            raise ValueError('config changed during installation')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return len(set(KIMI_EVENTS) - existing)


def connect(root):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(root / 'bindings.sqlite', timeout=3)
    db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE IF NOT EXISTS bindings ('
               'pane TEXT PRIMARY KEY, run_id TEXT UNIQUE, provider TEXT, session_id TEXT, '
               'tty TEXT, pgid INTEGER)')
    columns = {row['name'] for row in db.execute('PRAGMA table_info(bindings)')}
    for name, kind in [('tty', 'TEXT'), ('pgid', 'INTEGER')]:
        if name not in columns:
            db.execute(f'ALTER TABLE bindings ADD COLUMN {name} {kind}')
    return db


def alive(root, run_id):
    if not TOKEN.fullmatch(run_id):
        return False
    try:
        with (root / (run_id + '.lock')).open('rb') as lease:
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(lease, fcntl.LOCK_UN)
    except OSError:
        pass
    return False


def register(root, pane, provider, run_id, tty=None, pgid=None):
    with connect(root) as db:
        db.execute('INSERT OR REPLACE INTO bindings VALUES (?,?,?,NULL,?,?)',
                   (pane, run_id, provider, tty, pgid))


def foreground_matches(tty, pgid):
    if (not isinstance(tty, str) or not re.fullmatch(r'/dev/tty[A-Za-z0-9]+', tty)
            or not isinstance(pgid, int) or pgid <= 0):
        return False
    try:
        # macOS tcgetpgrp rejects another session's controlling TTY (ENOTTY).
        # ps exposes its foreground group without reading terminal contents.
        result = subprocess.run(['/bin/ps', '-t', tty, '-o', 'pgid=,tpgid='],
                                capture_output=True, text=True, timeout=2)
        if result.returncode:
            return False
        groups = [tuple(map(int, line.split())) for line in result.stdout.splitlines() if line.strip()]
        if not groups or any(len(pair) != 2 for pair in groups):
            return False
        return ({pair[1] for pair in groups} == {pgid}
                and any(pair[0] == pgid for pair in groups))
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


def record_event(provider, payload, env=None):
    env = os.environ if env is None else env
    run_id = env.get('SESSION_MANAGER_RUN_ID', '')
    if not run_id:
        return  # Unmanaged sessions are deliberately unaffected.
    root = Path(env['SESSION_MANAGER_STATE'])
    event = payload.get('event') or payload.get('hook_event_name') or payload.get('type')
    sid = payload.get('session_id')
    if not isinstance(sid, str) or not sid or len(sid) > 256:
        raise ValueError('missing session identity')
    if not alive(root, run_id):
        raise ValueError('run lease expired')
    starts = {'pi': {'session_start'}, 'kimi': {'SessionStart'}}
    ends = {'pi': {'session_shutdown'}, 'kimi': {'SessionEnd'}}
    with connect(root) as db:
        prior = db.execute('SELECT session_id FROM bindings WHERE run_id=? AND provider=?', (run_id, provider)).fetchone()
        if prior is None:
            return
        if event in starts.get(provider, set()):
            db.execute('UPDATE bindings SET session_id=? WHERE run_id=? AND provider=?',
                       (sid, run_id, provider))
        elif event in ends.get(provider, set()):
            if prior['session_id'] != sid:
                return
            db.execute('UPDATE bindings SET session_id=NULL '
                       'WHERE run_id=? AND provider=? AND session_id=?', (run_id, provider, sid))
        elif prior['session_id'] != sid:
            return
    from inbox_store import receive
    if event in starts.get(provider, set()) and prior['session_id'] and prior['session_id'] != sid:
        receive(root, provider, prior['session_id'], 'SessionEnd', run_id=run_id)
    receive(root, provider, sid, event, run_id=run_id,
            title=payload.get('session_title') or payload.get('title'),
            project=payload.get('cwd'), idle=payload.get('idle', True))


def validate(root, run_id, session_id):
    if not TOKEN.fullmatch(run_id) or not session_id:
        raise ValueError('invalid binding identity')
    with connect(root) as db:
        row = db.execute('SELECT * FROM bindings WHERE run_id=?', (run_id,)).fetchone()
    if row is None or row['session_id'] != session_id or not alive(root, run_id):
        raise ValueError('binding expired, superseded, or session changed')
    if not foreground_matches(row['tty'], row['pgid']):
        raise ValueError('foreground terminal ownership could not be verified: '
                         'process group changed, target exited, or metadata unavailable')
    return dict(row)


def managed_run(root, pane, provider, command):
    if not os.isatty(0) or os.tcgetpgrp(0) != os.getpgrp():
        raise ValueError('launch from the foreground of an interactive terminal')
    run_id = uuid4().hex
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / (run_id + '.lock')
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as lease:
        fcntl.flock(lease, fcntl.LOCK_EX)
        register(root, pane, provider, run_id, os.ttyname(0), os.getpgrp())
        env = dict(os.environ, SESSION_MANAGER_RUN_ID=run_id,
                   SESSION_MANAGER_STATE=str(root), SESSION_MANAGER_PYTHON=sys.executable,
                   SESSION_MANAGER_BINDING_SCRIPT=str(Path(__file__).resolve()))
        print(json.dumps({'managed_run_id': run_id, 'pane': pane, 'provider': provider}), flush=True)
        process = None
        try:
            process = subprocess.Popen(command, env=env)
            while True:
                try:
                    return process.wait()
                except KeyboardInterrupt:
                    # Foreground child receives the same terminal SIGINT.
                    continue
        finally:
            with connect(root) as db:
                last = db.execute('SELECT session_id FROM bindings WHERE run_id=?', (run_id,)).fetchone()
                db.execute('DELETE FROM bindings WHERE run_id=?', (run_id,))
            if last and last['session_id']:
                from inbox_store import receive
                receive(root, provider, last['session_id'], 'SessionEnd', run_id=run_id)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, default=DEFAULT_ROOT)
    sub = parser.add_subparsers(dest='action', required=True)
    run = sub.add_parser('run')
    run.add_argument('provider', choices=['pi', 'kimi'])
    run.add_argument('args', nargs=argparse.REMAINDER)
    sub.add_parser('list')
    event = sub.add_parser('event')
    event.add_argument('--provider', choices=['pi', 'kimi'])
    sub.add_parser('kimi-hook-config')
    sub.add_parser('install-kimi-hooks')
    args = parser.parse_args()
    root = args.state_dir.expanduser().resolve()
    try:
        if args.action == 'install-kimi-hooks':
            home = Path(os.environ.get('KIMI_CODE_HOME', str(Path.home() / '.kimi-code')))
            print(json.dumps({'hooks_added': install_kimi_hooks(home)}))
            return 0
        if args.action == 'kimi-hook-config':
            command = kimi_command()
            for event_name in KIMI_EVENTS:
                print(f'[[hooks]]\nevent = "{event_name}"\ncommand = {json.dumps(command)}\ntimeout = 3\n')
            return 0
        if args.action == 'event':
            raw = sys.stdin.buffer.read(65537)
            if len(raw) > 65536:
                raise ValueError('event too large')
            payload = json.loads(raw)
            record_event(args.provider or payload['provider'], payload)
            return 0
        if args.action == 'list':
            with connect(root) as db:
                rows = [dict(row) for row in db.execute('SELECT * FROM bindings')]
            print(json.dumps([{**row, 'lease_alive': alive(root, row['run_id'])}
                              for row in rows], indent=2))
            return 0
        pane = os.environ.get('ITERM_SESSION_ID', '')
        if not pane:
            parser.error('Run this launcher inside iTerm2 (ITERM_SESSION_ID is missing)')
        executable = shutil.which(args.provider)
        if not executable:
            parser.error('Agent executable is missing')
        command = [executable]
        if args.provider == 'pi':
            command += ['-e', str(Path(__file__).with_name('pi_capture.ts'))]
        else:
            home = Path(os.environ.get('KIMI_CODE_HOME', str(Path.home() / '.kimi-code')))
            config = home / 'config.toml'
            settings = tomllib.loads(config.read_text()) if config.exists() else {}
            expected = [sys.executable, str(Path(__file__).resolve()), 'event', '--provider', 'kimi']
            registered = False
            for hook in settings.get('hooks', []):
                try:
                    if hook.get('event') == 'SessionStart' and shlex.split(hook.get('command', '')) == expected:
                        registered = True
                except ValueError:
                    continue
            if not registered:
                parser.error('Kimi binding hook is not configured. Generate it with kimi-hook-config; '
                             'add it to the active Kimi config before managed launch.')
        command += args.args[1:] if args.args[:1] == ['--'] else args.args
        return managed_run(root, pane, args.provider, command)
    except (OSError, ValueError, KeyError, sqlite3.Error) as error:
        print('binding unavailable: ' + type(error).__name__, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
