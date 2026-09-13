import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from capture_hook import metadata


class CaptureTests(unittest.TestCase):
    def test_body_and_tool_secrets_are_not_captured(self):
        event = metadata('claude', {'session_id': 'fixture-1', 'hook_event_name': 'Stop',
                                   'prompt': 'private', 'last_assistant_message': 'private',
                                   'tool_input': {'api_key': 'secret'}})
        self.assertEqual(event, {'provider': 'claude', 'session_id': 'fixture-1', 'event': 'Stop'})

    def test_camel_case_metadata(self):
        self.assertEqual(metadata('zcode', {'sessionId': 'fixture-1',
                                         'hookEventName': 'Stop'})['session_id'], 'fixture-1')

    def test_missing_identity_rejected(self):
        with self.assertRaises(ValueError):
            metadata('kimi', {'hook_event_name': 'Stop'})

    def test_callback_writes_metadata_without_affecting_parent_decision(self):
        script = Path(__file__).resolve().parents[1] / 'scripts/capture_hook.py'
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, str(script), 'claude', '--output-dir', directory],
                                    input='{"session_id":"fixture-1","hook_event_name":"Stop"}',
                                    capture_output=True, text=True)
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''))
            files = list(Path(directory).glob('*.json'))
            self.assertEqual(len(files), 1)
            self.assertEqual(json.loads(files[0].read_text())['event'], 'Stop')
            self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)
            bad = subprocess.run([sys.executable, str(script), 'claude', '--output-dir', directory],
                                 input='not-json-private-text', capture_output=True, text=True)
            self.assertEqual((bad.returncode, bad.stdout), (0, ''))
            self.assertNotIn('private-text', bad.stderr)
            self.assertEqual(len(list(Path(directory).glob('*.json'))), 1)


if __name__ == '__main__':
    unittest.main()
