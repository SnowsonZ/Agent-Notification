import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from zcode_focus import descriptor, run_helper


class ZcodeDescriptorTests(unittest.TestCase):
    def snapshot(self, **extra):
        return {'status': 'found', 'task_id': 'sess_a', 'title': 'a task',
                'workspace_path': '/fixture', 'searchable_text': 'private', **extra}

    def test_only_navigation_metadata_crosses_native_boundary(self):
        self.assertEqual(set(descriptor(self.snapshot())), {'task_id', 'title', 'workspace_path'})

    def test_archived_and_ambiguous_not_opened(self):
        for snapshot in [self.snapshot(archived=True), self.snapshot(status='ambiguous')]:
            with self.assertRaises(ValueError):
                descriptor(snapshot)

    def test_query_cannot_contain_newline_or_control_character(self):
        for title in ['', 'abc\nxyz', 'abc\txyz']:
            with self.assertRaises(ValueError):
                descriptor(self.snapshot(title=title))

    def test_native_failure_is_logged_without_cluttering_terminal_with_traces(self):
        with tempfile.TemporaryDirectory() as folder:
            helper = Path(folder) / 'helper'
            helper.write_bytes(b'fixture-binary')
            log = Path(folder) / 'run.json'
            result = subprocess.CompletedProcess([], 1, '',
                '{"trace_stage":"task_menu_open"}\n{"status":"refused","reason":"focus_changed"}\n')
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch('zcode_focus.subprocess.run', return_value=result), redirect_stdout(stdout), redirect_stderr(stderr):
                self.assertEqual(run_helper(helper, descriptor(self.snapshot()), False, log), 1)
            self.assertNotIn('trace_stage', stderr.getvalue())
            self.assertIn('focus_changed', stderr.getvalue())
            saved = json.loads(log.read_text())
            self.assertIn('task_menu_open', saved['stderr'])
            self.assertEqual(saved['exit_code'], 1)
            self.assertEqual(log.stat().st_mode & 0o777, 0o600)
