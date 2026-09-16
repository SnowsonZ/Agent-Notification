#!/usr/bin/env python3
"""Unified inbox commands used by the CLI and native menu-bar app."""
import argparse
import json
import os
from pathlib import Path
import shlex
import sqlite3
import subprocess
import sys
import tempfile
import time
from inbox_store import DEFAULT_ROOT, Store, receive
from inbox_sources import refresh
from agent_launch import iterm_select_tty, launch as launch_agent, ttys_for_command, ttys_for_open_file
from agent_launch import iterm_select_tty, launch as launch_agent, ttys_for_command, ttys_for_open_file

# 四家受管理 CLI 均原生支持按会话 ID 恢复（实测 help：pi --session、kimi --session、
# opencode --session、agy --conversation）：绑定死亡时跳转统一降级为受管理恢复。
RESUMABLE_PROVIDERS = ('pi', 'kimi', 'opencode', 'agy')
RESUME_ARGS = {'pi': ('--session',), 'kimi': ('--session',), 'opencode': ('--session',),
               'agy': ('--conversation',), 'claude': ('--resume',), 'codex': ('resume',)}


def setup_claude(root):
    directory = Path(os.environ.get('CLAUDE_CONFIG_DIR', str(Path.home() / '.claude')))
    path = directory / 'settings.json'
    if path.is_symlink():
        raise ValueError('linked Claude settings require explicit handling')
    original = path.read_text() if path.exists() else ''
    settings = json.loads(original) if original else {}
    command = shlex.join([sys.executable, str(Path(__file__).resolve()), '--root', str(root), 'hook', '--provider', 'claude'])
    count = 0
    hooks = settings.setdefault('hooks', {})
    for event in ['SessionStart', 'UserPromptSubmit', 'Stop', 'Notification', 'PermissionRequest', 'SessionEnd']:
        groups = hooks.setdefault(event, [])
        if not any(item.get('command') == command for group in groups for item in group.get('hooks', [])):
            groups.append({'hooks': [{'type': 'command', 'command': command, 'timeout': 3}]})
            count += 1
    if not count:
        return 0
    directory.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.session-manager-', dir=directory)
    try:
        with os.fdopen(fd, 'w') as file:
            json.dump(settings, file, ensure_ascii=False, indent=2)
            file.write('\n')
        if (path.read_text() if path.exists() else '') != original:
            raise ValueError('Claude settings changed during setup')
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    return count


def display_rows(store, all_rows):
    from session_binding import alive, connect
    with connect(store.root) as db:
        bindings = {row['run_id']: dict(row) for row in db.execute('SELECT * FROM bindings')}
    result = store.rows(unread_only=not all_rows)
    for row in result:
        locator = row['locator']
        available = locator.get('kind') in ('url', 'zcode', 'cli')
        if locator.get('kind') == 'managed':
            binding = bindings.get(locator.get('run_id'))
            live = bool(binding and binding['session_id'] == row['session_id'] and alive(store.root, binding['run_id']))
            # 绑定死亡但 provider 支持按 ID 恢复（agy/opencode）：跳转改为受管理恢复，仍可点。
            available = live or (row['provider'] in RESUMABLE_PROVIDERS and bool(row['session_id']))
        row['open_available'] = available
    return result


def _live_binding_for(root, provider, session_id, *, exclude=None):
    """同 provider+session_id 的其它活绑定（会话在新标签恢复/改绑后行定位滞后时使用）。"""
    from session_binding import alive, connect
    with connect(root) as db:
        rows = [dict(row) for row in db.execute(
            'SELECT * FROM bindings WHERE provider=? AND session_id=?', (provider, session_id))]
    live = [row['run_id'] for row in rows if alive(root, row['run_id']) and row['run_id'] != exclude]
    if len(live) > 1:
        raise ValueError('multiple live bindings for this session')
    return live[0] if live else None


def open_session(store, key, revision=None):
    row = store.get(key)
    locator = row['locator']
    scripts = Path(__file__).resolve().parent
    if locator.get('kind') == 'managed':
        def probe(run_id):
            return subprocess.run([sys.executable, str(scripts / 'iterm_probe.py'),
                '--state-dir', str(store.root), '--run-id', run_id,
                '--agent-session-id', row['session_id'], '--activate'],
                capture_output=True, text=True, timeout=100)
        result = probe(locator['run_id'])
        if result.returncode != 0:
            # 绑定死亡：先找同会话的其它活绑定（改绑/恢复后行定位滞后的情形）。
            try:
                alternate = _live_binding_for(store.root, row['provider'], row['session_id'],
                                              exclude=locator.get('run_id'))
            except ValueError as error:
                print(error, file=sys.stderr)
                return 1
            if alternate:
                result = probe(alternate)
        if result.returncode != 0 and row['provider'] in RESUMABLE_PROVIDERS:
            # 仍无活绑定：经包装器在新标签受管理恢复目标会话（注册新绑定，
            # 下一回合事件把条目改挂新绑定）；恢复前不自动确认。
            directory = row['project']
            if not directory or not Path(directory).expanduser().is_dir():
                print('session directory is missing', file=sys.stderr)
                return result.returncode
            try:
                launch_agent(row['provider'], directory,
                             args=RESUME_ARGS[row['provider']] + (row['session_id'],))
            except ValueError as error:
                print(error, file=sys.stderr)
                return 1
            acknowledged = store.acknowledge(key, row['revision'] if revision is None else revision)
            print(json.dumps({'opened': True, 'acknowledged': acknowledged,
                              'new_activity_preserved': not acknowledged, 'navigation': 'managed_resume'}))
            return 0
        if result.returncode != 0:
            print(result.stderr or result.stdout or 'Open failed', file=sys.stderr, end='\n')
            return result.returncode
        acknowledged = store.acknowledge(key, row['revision'] if revision is None else revision)
        print(json.dumps({'opened': True, 'acknowledged': acknowledged, 'new_activity_preserved': not acknowledged,
                          'navigation': 'verified_adapter'}))
        return 0
    elif locator.get('kind') == 'zcode':
        command = [sys.executable, str(scripts / 'zcode_focus.py'), row['session_id']]
    elif locator.get('kind') == 'cli':
        # claude/codex CLI 直启会话。与受管理 provider 同语义的"已打开则聚焦"：
        # 1) 命令行匹配（经包装器恢复的会话，命令行携带会话 ID）；
        # 2) 会话文件 fd 匹配（运行中的 claude/codex 持有转写/rollout 文件）；
        # 都没有才在新标签按会话 ID 恢复；目录缺失仍拒绝。
        args = RESUME_ARGS[row['provider']] + (row['session_id'],)
        ttys = []
        try:
            ttys = ttys_for_command(args)
            session_file = locator.get('file', '')
            if session_file:
                ttys += ttys_for_open_file(session_file)
        except OSError as error:
            print(f'process lookup failed: {error}', file=sys.stderr)
            return 1
        seen = []
        for tty in ttys:
            if tty in seen:
                continue
            seen.append(tty)
            try:
                focused = iterm_select_tty(tty)
            except ValueError as error:
                print(error, file=sys.stderr)
                return 1
            if focused:
                acknowledged = store.acknowledge(key, row['revision'] if revision is None else revision)
                print(json.dumps({'opened': True, 'acknowledged': acknowledged,
                                  'new_activity_preserved': not acknowledged, 'navigation': 'iterm_focus'}))
                return 0
        directory = row['project'] or locator.get('cwd', '')
        if not directory or not Path(directory).expanduser().is_dir():
            print('session directory is missing', file=sys.stderr)
            return 1
        try:
            launch_agent(row['provider'], directory, args=args)
        except ValueError as error:
            print(error, file=sys.stderr)
            return 1
        acknowledged = store.acknowledge(key, row['revision'] if revision is None else revision)
        print(json.dumps({'opened': True, 'acknowledged': acknowledged,
                          'new_activity_preserved': not acknowledged, 'navigation': 'cli_resume'}))
        return 0
    elif locator.get('kind') == 'url':
        url = locator.get('url', '')
        if not url.startswith(('codex://threads/', 'claude://code/continue?session=')):
            raise ValueError('unsupported session URL')
        command = ['/usr/bin/open', url]
    else:
        raise ValueError('no verified opener for this session')
    result = subprocess.run(command, capture_output=True, text=True, timeout=100)
    if result.returncode != 0:
        print(result.stderr or result.stdout or 'Open failed', file=sys.stderr, end='\n')
        return result.returncode
    # Use the UI/notification's revision, not a fresh revision after navigating.
    acknowledged = store.acknowledge(key, row['revision'] if revision is None else revision)
    print(json.dumps({'opened': True, 'acknowledged': acknowledged, 'new_activity_preserved': not acknowledged,
                      'navigation': 'os_dispatch' if locator['kind'] == 'url' else 'verified_adapter'}))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    sub = parser.add_subparsers(dest='action', required=True)
    rows = sub.add_parser('rows')
    rows.add_argument('--all', action='store_true')
    rows.add_argument('--refresh', action='store_true')
    sub.add_parser('sync')
    watch = sub.add_parser('watch')
    watch.add_argument('--interval', type=float, default=3)
    ack = sub.add_parser('ack')
    ack.add_argument('id')
    ack.add_argument('--revision', type=int, required=True)
    op = sub.add_parser('open')
    op.add_argument('id')
    op.add_argument('--revision', type=int)
    sub.add_parser('setup')
    hook = sub.add_parser('hook')
    hook.add_argument('--provider', choices=['claude'], required=True)
    sub.add_parser('agents')
    launcher = sub.add_parser('launch')
    launcher.add_argument('--agent', required=True)
    launcher.add_argument('--dir', required=True)
    daily = sub.add_parser('daily-report')
    daily.add_argument('--date', help='YYYY-MM-DD, defaults to today')
    daily.add_argument('--overview', action='store_true',
                       help='heatmap totals + top projects instead of one day')
    daily.add_argument('--days', type=int, default=182)
    daily.add_argument('--top', type=int, default=5)
    daily.add_argument('--refresh', action='store_true',
                       help='rescan sources for a past day even if a finalized report is cached')
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    try:
        store = Store(root)
        if args.action == 'hook':
            raw = sys.stdin.buffer.read(1_048_577)
            if len(raw) > 1_048_576:
                raise ValueError('oversized hook')
            payload = json.loads(raw)
            sid = payload.get('session_id')
            if not isinstance(sid, str) or not sid:
                raise ValueError('missing session ID')
            event = payload.get('hook_event_name')
            if event == 'Notification':
                if payload.get('notification_type') == 'permission_prompt':
                    event = 'PermissionRequest'
                else:
                    return 0
            receive(root, 'claude', sid, event, project=payload.get('cwd'),
                    transcript=payload.get('transcript_path'))
            return 0
        if args.action == 'setup':
            from session_binding import install_kimi_hooks
            count = setup_claude(root)
            kimi = install_kimi_hooks(Path(os.environ.get('KIMI_CODE_HOME', str(Path.home() / '.kimi-code'))))
            print(json.dumps({'claude_hooks_added': count, 'kimi_hooks_added': kimi}))
            return 0
        if args.action == 'agents':
            from agent_launch import installed_agents
            print(json.dumps({'agents': installed_agents()}, ensure_ascii=False))
            return 0
        if args.action == 'launch':
            from agent_launch import launch
            launch(args.agent, args.dir)
            print(json.dumps({'launched': True, 'agent': args.agent}))
            return 0
        if args.action == 'daily-report':
            from daily_report import generate_day, generate_overview
            if args.overview:
                payload = generate_overview(store, Path.home(),
                                            days=max(7, args.days), top=max(1, min(args.top, 10)))
            else:
                payload = generate_day(store, Path.home(), args.date, refresh=args.refresh)
            print(json.dumps(payload, ensure_ascii=False))
            return 0
        if args.action in ('sync', 'watch'):
            while True:
                print(json.dumps(refresh(store)), flush=True)
                if args.action == 'sync':
                    break
                time.sleep(max(args.interval, 1))
            return 0
        if args.action == 'rows':
            if args.refresh:
                refresh(store)
            print(json.dumps({'sessions': display_rows(store, args.all), 'health': store.meta('health', {})}, ensure_ascii=False))
            return 0
        if args.action == 'ack':
            if not store.acknowledge(args.id, args.revision):
                raise ValueError('new activity arrived; refresh before acknowledging')
            print('{"acknowledged":true}')
            return 0
        return open_session(store, args.id, args.revision)
    except KeyboardInterrupt:
        return 0
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error, subprocess.TimeoutExpired) as error:
        if args.action == 'hook':
            print('inbox hook unavailable: ' + type(error).__name__, file=sys.stderr)
            return 0
        print(json.dumps({'status': 'error', 'reason': str(error)}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
