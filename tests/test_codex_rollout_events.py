import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from codex_rollout_events import RolloutReader


class RolloutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'rollout.jsonl'
        self.path.write_text(json.dumps({'type': 'session_meta', 'payload': {
            'id': 'fixture', 'originator': 'Codex Desktop'}}) + '\n')
        self.reader = RolloutReader(self.path, 'fixture')

    def event(self, kind='task_complete'):
        return json.dumps({'type': 'event_msg', 'payload': {
            'type': kind, 'turn_id': 'turn-1', 'last_agent_message': 'private text'}})

    def test_partial_line_waits_without_duplicates_or_message_leak(self):
        with self.path.open('a') as stream:
            stream.write(self.event())
        self.assertEqual(self.reader.poll(), [])
        with self.path.open('a') as stream:
            stream.write('\n')
        events = self.reader.poll()
        self.assertEqual(len(events), 1)
        self.assertNotIn('private text', json.dumps(events))
        self.assertEqual(self.reader.poll(), [])

    def test_wrong_session_is_rejected(self):
        with self.assertRaises(ValueError):
            RolloutReader(self.path, 'other').poll()

    def test_replace_file_rechecks_identity(self):
        self.reader.poll()
        replacement = self.path.with_suffix('.new')
        replacement.write_text(json.dumps({'type': 'session_meta', 'payload': {
            'id': 'different', 'originator': 'Codex Desktop'}}) + '\n')
        replacement.replace(self.path)
        with self.assertRaises(ValueError):
            self.reader.poll()

    def test_missing_turn_identity_is_not_guessed(self):
        with self.path.open('a') as stream:
            stream.write(json.dumps({'type': 'event_msg', 'payload': {'type': 'task_complete'}}) + '\n')
        with self.assertRaises(ValueError):
            self.reader.poll()


if __name__ == '__main__':
    unittest.main()
