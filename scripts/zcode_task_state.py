#!/usr/bin/env python3
"""Read one exact Zcode task's metadata from its local SQLite index.

This is a version-dependent compatibility adapter, not a public API. It does
not read searchable_text, prompts, credentials, or alter the source database.
"""
import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path
from urllib.parse import quote


def lookup(database, task_id):
    connection = sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        connection.execute('PRAGMA query_only=ON')
        rows = connection.execute(
            'SELECT task_id, task_status, unread_at, updated_at, workspace_path, archived, deleted, title '
            'FROM tasks WHERE task_id=? LIMIT 2', (task_id,)).fetchall()
    finally:
        connection.close()
    if not rows:
        return {'status': 'not_found'}
    if len(rows) != 1:
        return {'status': 'ambiguous'}
    row = rows[0]
    result = {'status': 'found', 'provider': 'zcode', 'source': 'task-index-compatibility',
              'task_id': row[0], 'reported_status': row[1], 'unread_at': row[2],
              'updated_at': row[3], 'archived': bool(row[5]), 'deleted': bool(row[6]),
              'exact_session_navigation': False, 'workspace_path': row[4], 'title': row[7]}
    # Keep workspace navigation explicitly separate from exact task navigation.
    workspace = row[4]
    if isinstance(workspace, str) and Path(workspace).is_absolute() and not (row[5] or row[6]):
        result['workspace_url'] = 'zcode://workspace/open?path=' + quote(workspace, safe='')
    return result


def verify_selection(snapshot, copied_path):
    """Verify a UI-copied task path against the exact indexed task identity.

    This verifies a selection, not availability of an external navigation API.
    """
    if snapshot.get('status') != 'found' or snapshot.get('deleted') or snapshot.get('archived'):
        return False
    task_id = snapshot.get('task_id', '')
    workspace = snapshot.get('workspace_path')
    if not re.fullmatch(r'sess_[a-zA-Z0-9-]+', task_id) or not isinstance(workspace, str):
        return False
    if not Path(workspace).is_absolute() or not Path(copied_path).is_absolute():
        return False
    # Zcode's Copy Task Path can return a logical legacy path absent on disk.
    # Compare the UI value with the index-derived identity, not file existence.
    expected = Path(workspace) / (task_id + '.zcode-session')
    return Path(copied_path) == expected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('task_id')
    parser.add_argument('--copied-task-path', help='Task path copied through the Zcode UI')
    parser.add_argument('--database', type=Path,
                        default=Path.home() / '.zcode/v2/tasks-index.sqlite')
    args = parser.parse_args()
    try:
        result = lookup(args.database, args.task_id)
        if args.copied_task_path:
            result['selection_identity_matches'] = verify_selection(result, args.copied_task_path)
    except sqlite3.Error:
        print('Zcode task index unavailable or schema unsupported', file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status'] == 'found' and result.get('selection_identity_matches', True) else 1


if __name__ == '__main__':
    sys.exit(main())
