#!/usr/bin/env python3
"""Read lifecycle metadata from one known Codex Desktop rollout.

Compatibility adapter for the observed 0.154.0-alpha.6.2 format, not a public API.
Never emits message bodies. Watch mode tails complete lines only.
"""
import argparse
import json
from pathlib import Path
import sys
import time


class RolloutReader:
    def __init__(self, path, expected_session, allow_ancestry=False):
        self.path = path
        self.expected_session = expected_session
        self.offset = 0
        self.identity = None
        self.session = None
        self.allow_ancestry = allow_ancestry
        self.metadata = None

    def poll(self):
        output = []
        with self.path.open('rb') as source:
            import os
            stat = os.fstat(source.fileno())
            identity = (stat.st_dev, stat.st_ino)
            if identity != self.identity or stat.st_size < self.offset:
                self.offset = 0
                self.session = None
                self.identity = identity
            source.seek(self.offset)
            while True:
                position = source.tell()
                line = source.readline()
                if not line or not line.endswith(b'\n'):
                    # A partially written line is retried next poll.
                    self.offset = position
                    break
                try:
                    record = json.loads(line)
                    payload = record.get('payload', {})
                    if record.get('type') == 'session_meta':
                        if self.allow_ancestry and self.session is None and payload.get('id') != self.expected_session:
                            continue
                        if (payload.get('id') != self.expected_session
                                or payload.get('originator') != 'Codex Desktop'):
                            raise ValueError('rollout identity or originator mismatch')
                        self.session = payload['id']
                        self.metadata = payload
                    elif record.get('type') == 'event_msg':
                        kind = payload.get('type')
                        if kind not in ('task_started', 'task_complete', 'turn_aborted'):
                            continue
                        if not self.session:
                            if self.allow_ancestry:
                                continue
                            raise ValueError('lifecycle record before verified session metadata')
                        turn = payload.get('turn_id')
                        if not isinstance(turn, str) or not turn:
                            raise ValueError('lifecycle record lacks turn_id')
                        output.append({'provider': 'codex', 'session_id': self.session,
                                       'event': kind, 'turn_id': turn,
                                       'event_id': f'{self.session}:{turn}:{kind}',
                                       'source': 'rollout-compatibility',
                                       'timestamp': record.get('timestamp')})
                except (json.JSONDecodeError, AttributeError) as error:
                    raise ValueError('unsupported or corrupt rollout record') from error
            return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', type=Path)
    parser.add_argument('--session-id', required=True)
    parser.add_argument('--watch', action='store_true')
    args = parser.parse_args()
    reader = RolloutReader(args.path, args.session_id)
    try:
        while True:
            for event in reader.poll():
                print(json.dumps(event), flush=True)
            if not args.watch:
                break
            time.sleep(1)
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError) as error:
        print(f'rollout adapter unavailable: {type(error).__name__}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
