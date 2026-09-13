#!/usr/bin/env python3
"""Diagnostic hook receiver. Explicit output dir; no config auto-installation.

Stores only selected metadata, never complete input, prompts, or tool arguments.
Always leaves the parent agent's hook decision unchanged (empty stdout, exit 0).
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import sqlite3
from uuid import uuid4
from session_binding import record_event

FIELDS = {
    'session_id': ('session_id', 'sessionId'),
    'event': ('hook_event_name', 'hookEventName', 'type'),
    'turn_id': ('turn_id', 'turnId'),
}


def metadata(provider, payload):
    if not isinstance(payload, dict):
        raise ValueError('expected object')
    result = {'provider': provider}
    for target, aliases in FIELDS.items():
        value = next((payload[key] for key in aliases if key in payload), None)
        if value is None and target == 'turn_id':
            continue
        if not isinstance(value, str) or not value or len(value) > 256:
            raise ValueError('missing or invalid event metadata')
        result[target] = value
    return result


def save(directory, event):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    event = {**event, 'received_at': datetime.now(timezone.utc).isoformat()}
    # One file per callback avoids concurrent append interleaving.
    name = uuid4().hex
    temporary = directory / (name + '.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as output:
        json.dump(event, output, ensure_ascii=False)
        output.write('\n')
    temporary.rename(directory / (name + '.json'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('provider', choices=['claude', 'codex', 'zcode', 'kimi', 'pi'])
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args()
    try:
        raw = sys.stdin.buffer.read(1_048_577)
        if len(raw) > 1_048_576:
            raise ValueError('input too large')
        event = metadata(args.provider, json.loads(raw))
        record_event(args.provider, event)
        save(args.output_dir, event)
    except (ValueError, OSError, KeyError, sqlite3.Error) as error:
        # Never print the payload or exception text: either can contain secrets.
        print('session-manager capture failed: ' + type(error).__name__, file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
