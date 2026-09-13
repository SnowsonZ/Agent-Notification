import fcntl
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from session_binding import register, record_event, validate, alive, install_kimi_hooks, KIMI_EVENTS


class BindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.run_id = self.start_run()

    def start_run(self):
        token = uuid4().hex
        lease = (self.root / (token + '.lock')).open('w')
        fcntl.flock(lease, fcntl.LOCK_EX)
        self.addCleanup(lease.close)
        self.lease = lease
        register(self.root, 'pane-1', 'pi', token, '/dev/tty-fixture', 123)
        return token

    def event(self, sid, event='session_start', token=None):
        record_event('pi', {'session_id': sid, 'event': event}, {
            'SESSION_MANAGER_RUN_ID': token or self.run_id,
            'SESSION_MANAGER_STATE': str(self.root)})

    @patch('session_binding.foreground_matches', return_value=True)
    def test_live_binding(self, _):
        self.event('session-a')
        self.assertEqual(validate(self.root, self.run_id, 'session-a')['pane'], 'pane-1')

    @patch('session_binding.foreground_matches', return_value=True)
    def test_new_session_invalidates_old_and_late_shutdown_does_not_clear_new(self, _):
        self.event('session-a')
        self.event('session-b')
        self.event('session-a', 'session_shutdown')
        with self.assertRaises(ValueError):
            validate(self.root, self.run_id, 'session-a')
        self.assertEqual(validate(self.root, self.run_id, 'session-b')['session_id'], 'session-b')

    @patch('session_binding.foreground_matches', return_value=True)
    def test_pane_reuse_rejects_old_run_and_late_events(self, _):
        self.event('session-a')
        new = self.start_run()
        self.event('session-new', token=new)
        self.event('session-late-old')
        with self.assertRaises(ValueError):
            validate(self.root, self.run_id, 'session-late-old')
        self.assertEqual(validate(self.root, new, 'session-new')['run_id'], new)

    @patch('session_binding.foreground_matches', return_value=True)
    def test_released_lock_rejects_stale_record(self, _):
        self.event('session-a')
        self.lease.close()
        self.assertFalse(alive(self.root, self.run_id))
        with self.assertRaises(ValueError):
            validate(self.root, self.run_id, 'session-a')

    @patch('session_binding.foreground_matches', return_value=False)
    def test_suspended_or_background_agent_cannot_claim_pane(self, _):
        self.event('session-a')
        with self.assertRaises(ValueError):
            validate(self.root, self.run_id, 'session-a')

    @patch('session_binding.foreground_matches', return_value=True)
    def test_shutdown_clears_binding(self, _):
        self.event('session-a')
        self.event('session-a', 'session_shutdown')
        with self.assertRaises(ValueError):
            validate(self.root, self.run_id, 'session-a')

    def test_unmanaged_hook_is_noop(self):
        record_event('kimi', {}, {})

    def test_kimi_install_preserves_existing_config_and_is_idempotent(self):
        import tomllib
        path = self.root / 'config.toml'
        original = '# Keep this comment\ndefault_model = "my-model"\n'
        path.write_text(original)
        self.assertEqual(install_kimi_hooks(self.root), len(KIMI_EVENTS))
        content = path.read_text()
        self.assertTrue(content.startswith(original))
        self.assertEqual(tomllib.loads(content)['default_model'], 'my-model')
        self.assertEqual(install_kimi_hooks(self.root), 0)
        self.assertEqual(path.read_text(), content)


if __name__ == '__main__':
    unittest.main()
