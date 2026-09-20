#!/usr/bin/env python3
"""Resolve a task descriptor and invoke the user-run native AX helper."""
import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from zcode_task_state import lookup

TRANSIENT_MARKS = ('timeout', 'focus_changed', 'lost focus')


def run_helper(helper, data, diagnose, log_path):
    command = [str(helper)] + (['--diagnose-search'] if diagnose else [])
    report = {'started_at': datetime.now(UTC).isoformat(), 'task_id': data['task_id'],
              'helper_sha256': hashlib.sha256(helper.read_bytes()).hexdigest()}
    for attempt in (1, 2):
        try:
            result = subprocess.run(command, input=json.dumps(data), text=True, timeout=90, capture_output=True, check=False)
            report.update(exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr)
        except subprocess.TimeoutExpired as error:
            def decoded(value):
                return value.decode(errors='replace') if isinstance(value, bytes) else (value or '')
            report.update(exit_code=1, stdout=decoded(error.stdout), stderr=decoded(error.stderr), timed_out=True)
        report['attempts'] = attempt
        # 焦点漂移与 Electron 渲染慢是瞬时竞态：搜索流程幂等，重跑一次即可。
        if report.get('exit_code') == 0 or not any(mark in report.get('stderr', '') for mark in TRANSIENT_MARKS):
            break
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.zcode-focus-', dir=log_path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
        os.replace(temporary, log_path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(report['stdout'], end='')
    for line in report['stderr'].splitlines():
        try:
            if 'trace_stage' in json.loads(line):
                continue
        except (ValueError, TypeError):
            pass
        print(line, file=sys.stderr)
    if report.get('timed_out'):
        print(json.dumps({'status': 'refused', 'reason': 'native helper timeout; stage log saved'}), file=sys.stderr)
    return report['exit_code']


def descriptor(snapshot):
    if snapshot.get('status') != 'found' or snapshot.get('archived') or snapshot.get('deleted'):
        raise ValueError('task missing, ambiguous, archived, or deleted')
    title = snapshot.get('title')
    if not isinstance(title, str) or not title.strip() or any(ord(ch) < 32 for ch in title):
        raise ValueError('task title is missing or unsupported')
    return {key: snapshot[key] for key in ('task_id', 'title', 'workspace_path')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('task_id')
    parser.add_argument('--describe', action='store_true', help='Resolve only; do not control Zcode')
    parser.add_argument('--diagnose-search', action='store_true', help='Inspect search controls; no query text or task selection')
    args = parser.parse_args()
    try:
        data = descriptor(lookup(Path.home() / '.zcode/v2/tasks-index.sqlite', args.task_id))
        if args.describe:
            print(json.dumps(data, ensure_ascii=False, indent=2))
            return 0
        base = Path(__file__).resolve().parents[1]  # 仓库根，或发布包的 Contents/Resources
        helper = base / 'build/zcode-focus'
        if not helper.is_file():
            raise ValueError('native helper is not built')
        # 仓库内沿用 scratch/；发布包不能往已签名的 bundle 里写，阶段日志放状态目录。
        log_dir = base / 'scratch' if (base / 'scratch').is_dir() else Path.home() / '.local/state/session-manager'
        return run_helper(helper, data, args.diagnose_search, log_dir / 'zcode-focus-latest.json')
    except (ValueError, OSError, sqlite3.Error, subprocess.TimeoutExpired) as error:
        reason = str(error) if isinstance(error, ValueError) else type(error).__name__
        print(json.dumps({'status': 'refused', 'reason': reason}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
