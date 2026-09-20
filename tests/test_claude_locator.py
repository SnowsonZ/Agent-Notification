import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from claude_locator import resolve


class LocatorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def record(self, account, number, **extra):
        sid = f'local_00000000-0000-0000-0000-{number:012d}'
        path = self.root / account / (sid + '.json')
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({'sessionId': sid, 'cliSessionId': f'cli-{number}',
                                    'title': 'same title', 'cwd': '/same/path', **extra}))
        return sid

    def test_same_title_and_directory_resolve_by_runtime_id(self):
        first = self.record('account-a', 1)
        self.record('account-a', 2)
        result = resolve(self.root, 'cli-1')
        self.assertEqual(result['desktop_session_id'], first)
        self.assertTrue(result['url'].endswith(first))

    def test_duplicate_runtime_id_across_accounts_never_picks_latest(self):
        self.record('account-a', 1)
        self.record('account-b', 2, cliSessionId='cli-1')
        self.assertEqual(resolve(self.root, 'cli-1')['status'], 'ambiguous')

    def test_archived_is_not_sent_to_continue_route(self):
        self.record('account-a', 1, isArchived=True)
        self.assertEqual(resolve(self.root, 'cli-1')['status'], 'archived')

    def test_corrupt_metadata_does_not_produce_false_unique_match(self):
        self.record('account-a', 1)
        (self.root / 'local_corrupt.json').write_text('{')
        self.assertEqual(resolve(self.root, 'cli-1')['status'], 'incomplete_index')

    def test_missing_record_never_falls_back_to_last(self):
        self.record('account-a', 1)
        self.assertEqual(resolve(self.root, 'unknown')['status'], 'not_found')


if __name__ == '__main__':
    unittest.main()
