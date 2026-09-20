#!/usr/bin/env python3
"""Resolve a Claude runtime ID to an existing Desktop session URL, read-only.

The URL has been tested on Claude 1.52386.3. Local metadata is not a stable
vendor API; ambiguous matches are deliberately not resolved automatically.
"""
import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import quote

DESKTOP_ID = re.compile(r'local_[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z')
DEFAULT_ROOT = Path.home() / 'Library/Application Support/Claude/claude-code-sessions'


def resolve(root, identifier):
    if not root.is_dir():
        return {'status': 'source_missing'}
    matches = []
    unreadable = 0
    # Only Desktop metadata records. Do not scan transcript files or settings.
    for path in sorted(root.rglob('local_*.json')):
        try:
            if path.stat().st_size > 4_000_000:
                unreadable += 1
                continue
            record = json.loads(path.read_text())
            if not isinstance(record, dict):
                unreadable += 1
                continue
        except (OSError, ValueError):
            unreadable += 1
            continue
        desktop_id = record.get('sessionId')
        if identifier not in (desktop_id, record.get('cliSessionId')):
            continue
        if not isinstance(desktop_id, str) or not DESKTOP_ID.fullmatch(desktop_id):
            return {'status': 'unsupported_desktop_id'}
        matches.append((desktop_id, record.get('isArchived') is True))
    # Fail closed when a corrupt record could conceal an account collision.
    if unreadable:
        return {'status': 'incomplete_index', 'unreadable_records': unreadable}
    if not matches:
        return {'status': 'not_found'}
    if len(matches) != 1:
        return {'status': 'ambiguous', 'matches': len(matches)}
    desktop_id, archived = matches[0]
    if archived:
        return {'status': 'archived', 'desktop_session_id': desktop_id}
    return {'status': 'resolved', 'desktop_session_id': desktop_id,
            'url': 'claude://code/continue?session=' + quote(desktop_id, safe=''),
            'navigation_verified_here': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('session_id', help='Exact Desktop ID or hook/CLI session ID')
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT,
                        help='Desktop metadata directory; narrow to one account if ambiguous')
    args = parser.parse_args()
    result = resolve(args.root, args.session_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status'] == 'resolved' else 1


if __name__ == '__main__':
    sys.exit(main())
