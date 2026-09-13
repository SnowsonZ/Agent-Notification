"""Real PTY regression: the checker is outside the target terminal session."""
import json
import os
from pathlib import Path
import pty
import select
import signal
import sys
import unittest
import tempfile
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from session_binding import foreground_matches, validate

CHILD = r'''
import fcntl, json, os, signal, sys
from pathlib import Path
sys.path.insert(0, sys.argv[4])
from session_binding import register, record_event
root, token, output = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
try:
    lease = (root / (token + '.lock')).open('w')
    fcntl.flock(lease, fcntl.LOCK_EX)
    register(root, 'fixture-pane', 'pi', token, os.ttyname(0), os.getpgrp())
    record_event('pi', {'event': 'session_start', 'session_id': 'fixture-session'}, {
        'SESSION_MANAGER_RUN_ID': token, 'SESSION_MANAGER_STATE': str(root)})
    os.write(output, json.dumps({'tty': os.ttyname(0), 'pgid': os.getpgrp()}).encode() + b'\n')
    os.close(output)
    while True:
        signal.pause()
except BaseException as error:
    os.write(output, json.dumps({'error': repr(error)}).encode() + b'\n')
    os._exit(1)
'''


@unittest.skipUnless(sys.platform == 'darwin', 'macOS Terminal ownership contract')
class CrossSessionForegroundTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.run_id = uuid4().hex
        read_fd, write_fd = os.pipe()
        pid, master = pty.fork()
        if pid == 0:
            os.close(read_fd)
            # Re-exec before using SQLite: macOS library state inherited across
            # fork is not safe after the parent has already used SQLite.
            os.set_inheritable(write_fd, True)
            os.execv(sys.executable, [sys.executable, '-c', CHILD, str(self.root),
                                     self.run_id, str(write_fd),
                                     str(Path(__file__).resolve().parents[1] / 'scripts')])
        os.close(write_fd)
        self.pid, self.master = pid, master
        self.addCleanup(self.cleanup_child)
        with os.fdopen(read_fd) as pipe:
            if not select.select([pipe], [], [], 3)[0]:
                self.fail('PTY child did not report its identity')
            self.target = json.loads(pipe.readline())
            self.assertNotIn('error', self.target)

    def cleanup_child(self):
        try:
            os.kill(self.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        os.waitpid(self.pid, 0)
        os.close(self.master)

    def test_live_foreground_group_is_recognized_from_another_session(self):
        self.assertTrue(foreground_matches(self.target['tty'], self.target['pgid']))

    def test_managed_binding_validates_from_another_session(self):
        binding = validate(self.root, self.run_id, 'fixture-session')
        self.assertEqual(binding['pane'], 'fixture-pane')

    def test_other_process_group_is_rejected(self):
        self.assertFalse(foreground_matches(self.target['tty'], self.target['pgid'] + 1000000))


if __name__ == '__main__':
    unittest.main()
