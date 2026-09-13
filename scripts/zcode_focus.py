#!/usr/bin/env python3
"""Resolve a task descriptor and invoke the user-run native AX helper."""
import argparse
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import hashlib
import os
import tempfile
from datetime import datetime, timezone
from zcode_task_state import lookup


def run_helper(helper, data, diagnose, log_path):
    command = [str(helper)] + (['--diagnose-search'] if diagnose else [])
    report = {'started_at': datetime.now(timezone.utc).isoformat(), 'task_id': data['task_id'],
              'helper_sha256': hashlib.sha256(helper.read_bytes()).hexdigest()}
    try:
        result = subprocess.run(command, input=json.dumps(data), text=True, timeout=90, capture_output=True)
        report.update(exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr)
    except subprocess.TimeoutExpired as error:
        def decoded(value):
            return value.decode(errors='replace') if isinstance(value, bytes) else (value or '')
        report.update(exit_code=1, stdout=decoded(error.stdout), stderr=decoded(error.stderr), timed_out=True)
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
        helper = Path(__file__).resolve().parents[1] / 'build/zcode-focus'
        if not helper.is_file():
            raise ValueError('native helper is not built')
        return run_helper(helper, data, args.diagnose_search,
                          helper.parent.parent / 'scratch/zcode-focus-latest.json')
    except (ValueError, OSError, sqlite3.Error, subprocess.TimeoutExpired) as error:
        reason = str(error) if isinstance(error, ValueError) else type(error).__name__
        print(json.dumps({'status': 'refused', 'reason': reason}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
