import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from iterm_probe import choose_session


class TerminalIdentityTests(unittest.TestCase):
    def test_exact_observed_id(self):
        self.assertEqual(choose_session('id-a', {'id-a', 'id-b'}), 'id-a')

    def test_prefix_only_removed_if_suffix_is_live(self):
        self.assertEqual(choose_session('w0t0p0:id-a', {'id-a'}), 'id-a')
        with self.assertRaises(ValueError):
            choose_session('w0t0p0:closed', {'id-a'})

    def test_conflicting_candidates_do_not_guess(self):
        with self.assertRaises(ValueError):
            choose_session('prefix:id-a', {'prefix:id-a', 'id-a'})


if __name__ == '__main__':
    unittest.main()
