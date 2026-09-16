import subprocess
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from agent_launch import AGENTS, cli_installed, command_for, installed_agents, iterm_applescript, launch


def which_only(*names):
    def fake(name, *args, **kwargs):
        return '/usr/local/bin/' + name if name in names else None
    return fake


class AgentLaunchTests(unittest.TestCase):
    def test_agents_catalog_lists_only_clis(self):
        self.assertEqual({spec['id'] for spec in AGENTS},
                         {'claude', 'codex', 'pi', 'kimi', 'agy', 'opencode'})

    def test_managed_agents_use_absolute_launcher(self):
        repo = Path('/repo')
        self.assertEqual(command_for('pi', repo), '/repo/bin/session-manager pi')
        self.assertEqual(command_for('kimi', repo), '/repo/bin/session-manager kimi')
        self.assertEqual(command_for('opencode', repo), '/repo/bin/session-manager opencode')
        self.assertEqual(command_for('agy', repo), '/repo/bin/session-manager agy')

    def test_unmanaged_agents_run_bare_command(self):
        self.assertEqual(command_for('claude'), 'claude')
        self.assertEqual(command_for('codex'), 'codex')

    def test_unknown_agent_is_rejected(self):
        with self.assertRaises(ValueError):
            command_for('zcode')

    def test_installed_agents_reports_detection(self):
        with patch('agent_launch.iterm_available', return_value=True), \
             patch('agent_launch.cli_installed', side_effect=lambda aid: aid in ('claude', 'pi')):
            rows = {row['id']: row for row in installed_agents()}
        self.assertTrue(rows['claude']['installed'])
        self.assertFalse(rows['codex']['installed'])
        self.assertTrue(rows['pi']['installed'])
        self.assertFalse(rows['kimi']['installed'])
        self.assertTrue(rows['claude']['iterm'])

    def test_cli_installed_checks_path_then_well_known_locations(self):
        with patch('agent_launch.shutil.which', return_value=None):
            self.assertTrue(cli_installed('kimi', exists=lambda p: p.endswith('.kimi-code/bin/kimi')))
            self.assertFalse(cli_installed('kimi', exists=lambda p: False))
        with patch('agent_launch.shutil.which', return_value='/usr/local/bin/claude'):
            self.assertTrue(cli_installed('claude', exists=lambda p: False))

    def test_applescript_changes_into_directory_and_runs_command(self):
        script = iterm_applescript('/Users/x/My Project', 'claude')
        self.assertIn("cd '/Users/x/My Project' && claude", script)
        self.assertIn('create tab with default profile', script)
        self.assertIn('write text "', script)

    def test_applescript_escapes_shell_metacharacters(self):
        script = iterm_applescript("/tmp/we'ird \\dir \"x\"", 'claude')
        # shlex 先用 '"'"' 处理单引号，AppleScript 再把反斜杠与双引号转义。
        self.assertIn("cd '/tmp/we'\\\"'\\\"'ird \\\\dir \\\"x\\\"' && claude", script)
        # 原始未转义的反斜杠不得出现：AppleScript 字符串里必须是双反斜杠。
        self.assertNotIn('ird \\dir', script.replace('ird \\\\dir', ''))

    def test_launch_rejects_unknown_or_missing_inputs(self):
        with self.assertRaises(ValueError):
            launch('zcode', '/tmp')
        with patch('agent_launch.shutil.which', return_value=None), \
             patch('agent_launch.iterm_available', return_value=False):
            with self.assertRaises(ValueError):
                launch('claude', '/tmp')
        with patch('agent_launch.shutil.which', return_value=None), \
             patch('agent_launch.iterm_available', return_value=True), \
             patch('agent_launch.cli_installed', return_value=True):
            with self.assertRaises(ValueError):
                launch('claude', '/nonexistent-directory-xyz')

    def test_launch_runs_osascript_with_built_script(self):
        seen = {}

        def fake_run(command, capture_output, text, timeout):
            seen['command'] = command
            seen['timeout'] = timeout
            return subprocess.CompletedProcess([], 0, '', '')

        with patch('agent_launch.shutil.which', which_only('claude')), \
             patch('agent_launch.iterm_available', return_value=True), \
             patch('agent_launch.subprocess.run', side_effect=fake_run):
            launch('claude', '/tmp')
        self.assertEqual(seen['command'][:2], ['osascript', '-e'])
        self.assertIn('cd /tmp && claude', seen['command'][2])

    def test_launch_reports_osascript_failure(self):
        with patch('agent_launch.shutil.which', which_only('claude')), \
             patch('agent_launch.iterm_available', return_value=True), \
             patch('agent_launch.subprocess.run',
                   return_value=subprocess.CompletedProcess([], 1, '', 'user declined')):
            with self.assertRaisesRegex(ValueError, 'user declined'):
                launch('claude', '/tmp')


if __name__ == '__main__':
    unittest.main()
