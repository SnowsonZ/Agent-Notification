"""Detect installed agent CLIs and launch them in an iTerm2 tab.

Only CLI agents are covered; desktop apps (Zcode, Claude Desktop, Codex
Desktop) are deliberately out of scope. Managed agents (pi/kimi) must run
through bin/session-manager so their bindings register with the inbox, and
that launcher only works inside an iTerm2 tab. Unmanaged additions (agy,
opencode) launch-only: their sessions do not enter the inbox yet.
"""
import shlex
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

AGENTS = [
    {'id': 'claude', 'name': 'Claude', 'managed': False},
    {'id': 'codex', 'name': 'Codex', 'managed': False},
    {'id': 'pi', 'name': 'Pi', 'managed': True},
    {'id': 'kimi', 'name': 'Kimi', 'managed': True},
    # Gemini CLI 已于 2026-06-18 对消费级用户停服，Google 官方继任者是 Antigravity CLI（命令 agy）。
    {'id': 'agy', 'name': 'Antigravity CLI', 'managed': False},
    {'id': 'opencode', 'name': 'OpenCode', 'managed': False},
]

# GUI App 的 PATH 不含用户 shell 的安装目录（/opt/homebrew/bin 等），
# which 找不到不等于没安装；已知安装位置作为第二通道。
WELL_KNOWN = {
    'claude': ('~/.local/bin/claude', '/opt/homebrew/bin/claude', '/usr/local/bin/claude'),
    'codex': ('/opt/homebrew/bin/codex', '/usr/local/bin/codex', '~/.local/bin/codex'),
    'pi': ('/opt/homebrew/bin/pi', '/usr/local/bin/pi'),
    'kimi': ('~/.kimi-code/bin/kimi', '/opt/homebrew/bin/kimi', '/usr/local/bin/kimi'),
    'agy': ('~/.local/bin/agy', '/opt/homebrew/bin/agy', '/usr/local/bin/agy'),
    'opencode': ('/opt/homebrew/bin/opencode', '/usr/local/bin/opencode',
                 '~/.opencode/bin/opencode', '~/.local/bin/opencode'),
}


def iterm_available():
    for parent in (Path('/Applications'), Path.home() / 'Applications'):
        if (parent / 'iTerm.app').is_dir():
            return True
    return False


def command_for(agent_id, repo=REPO):
    if agent_id not in {spec['id'] for spec in AGENTS}:
        raise ValueError('unknown agent: ' + str(agent_id))
    if agent_id in ('pi', 'kimi'):
        return shlex.quote(str(repo / 'bin/session-manager')) + ' ' + agent_id
    return agent_id


def cli_installed(agent_id, which=None, exists=None):
    which = which or shutil.which

    def default_exists(path):
        return Path(path).expanduser().is_file()

    exists = exists or default_exists
    if which(agent_id):
        return True
    return any(exists(path) for path in WELL_KNOWN.get(agent_id, ()))


def installed_agents(repo=REPO):
    iterm = iterm_available()
    result = []
    for spec in AGENTS:
        result.append({'id': spec['id'], 'name': spec['name'], 'managed': spec['managed'],
                       'installed': cli_installed(spec['id']), 'iterm': iterm})
    return result


def iterm_applescript(directory, command):
    shell = 'cd ' + shlex.quote(directory) + ' && ' + command
    escaped = shell.replace('\\', '\\\\').replace('"', '\\"')
    return (
        'tell application "iTerm"\n'
        '    activate\n'
        '    if (count of windows) = 0 then\n'
        '        create window with default profile\n'
        '        tell current session of current window to write text "%s"\n'
        '    else\n'
        '        tell current window\n'
        '            create tab with default profile\n'
        '            tell current session to write text "%s"\n'
        '        end tell\n'
        '    end if\n'
        'end tell' % (escaped, escaped)
    )


def launch(agent_id, directory, repo=REPO):
    if not any(spec['id'] == agent_id for spec in AGENTS):
        raise ValueError('unknown agent: ' + str(agent_id))
    if not cli_installed(agent_id):
        raise ValueError(agent_id + ' is not installed')
    if not iterm_available():
        raise ValueError('iTerm2 is not installed')
    directory = Path(directory).expanduser()
    if not directory.is_dir():
        raise ValueError('directory missing: ' + str(directory))
    script = iterm_applescript(str(directory), command_for(agent_id, repo))
    result = subprocess.run(['osascript', '-e', script], capture_output=True, text=True, timeout=15)
    if result.returncode != 0:
        reason = result.stderr.strip() or 'unknown AppleScript failure'
        raise ValueError('iTerm launch failed: ' + reason)
