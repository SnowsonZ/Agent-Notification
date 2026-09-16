"""Detect installed agent CLIs and launch them in an iTerm2 tab.

Only CLI agents are covered; desktop apps (Zcode, Claude Desktop, Codex
Desktop) are deliberately out of scope. Managed agents (pi/kimi/opencode/agy)
must run through bin/session-manager so their bindings register with the
inbox; that launcher only works inside an iTerm2 tab. Unmanaged agents
(claude, codex, agy) launch as bare commands and rely on their own passive
collectors (claude/codex) or stay launch-only (agy).
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
    {'id': 'agy', 'name': 'Antigravity CLI', 'managed': True},
    {'id': 'opencode', 'name': 'OpenCode', 'managed': True},
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
    if agent_id in ('pi', 'kimi', 'opencode', 'agy'):
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


def normalize_tty(tty):
    """ps 的 tt 列是 's002' 短格式，iTerm2 的 tty 属性是 '/dev/ttys002'，统一到后者。"""
    name = tty.strip().removeprefix('/dev/')
    if not (name.startswith('ttys') and name[4:].isdigit()):
        raise ValueError('unexpected tty name: ' + tty)
    return name


def ttys_for_command(tokens):
    """命令行同时包含全部 token 的进程的控制 TTY（归一化 ttysNNN，去重保序）。"""
    result = subprocess.run(['ps', '-axo', 'tt=,command='], capture_output=True, text=True, timeout=10)
    if result.returncode != 0:
        raise OSError('ps failed')
    ttys = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) < 2 or parts[0] in ('??', '-'):
            continue
        tty, command = parts
        if all(token in command for token in tokens):
            normalized = tty if tty.startswith('ttys') else 'tty' + tty
            if normalized not in ttys:
                ttys.append(normalized)
    return ttys


def ttys_for_open_file(path):
    """当前持有指定会话文件的进程的 TTY 列表。claude/codex 运行中会持续持有
    转写/rollout 的 fd，即使命令行里没有会话 ID（用户自行启动的会话）也能定位。"""
    result = subprocess.run(['lsof', '-F', 'p', str(path)], capture_output=True, text=True, timeout=10)
    if result.returncode != 0:
        return []
    ttys = []
    for line in result.stdout.splitlines():
        if not line.startswith('p'):
            continue
        probe = subprocess.run(['ps', '-o', 'tt=', '-p', line[1:]], capture_output=True, text=True, timeout=5)
        tty = probe.stdout.strip()
        if not tty or tty in ('??', '-'):
            continue
        normalized = tty if tty.startswith('ttys') else 'tty' + tty
        if normalized not in ttys:
            ttys.append(normalized)
    return ttys


def iterm_select_tty(tty):
    """在 iTerm2 里选中指定 TTY 的标签页并置前；返回是否找到。只按 TTY 精确匹配，
    不做标题或序号匹配。tty 形如 ttysNNN 或 /dev/ttysNNN。"""
    name = normalize_tty(tty)
    script = (
        'tell application "iTerm"\n'
        '    repeat with w in windows\n'
        '        repeat with t in tabs of w\n'
        '            repeat with s in sessions of t\n'
        '                if tty of s is "/dev/' + name + '" then\n'
        '                    select w\n'
        '                    select t\n'
        '                    select s\n'
        '                    activate\n'
        '                    return "focused"\n'
        '                end if\n'
        '            end repeat\n'
        '        end repeat\n'
        '    end repeat\n'
        '    return "missing"\n'
        'end tell'
    ).replace('\n', chr(10))
    result = subprocess.run(['osascript', '-e', script], capture_output=True, text=True, timeout=15)
    if result.returncode != 0:
        reason = result.stderr.strip() or 'unknown AppleScript failure'
        raise ValueError('iTerm focus failed: ' + reason)
    return result.stdout.strip() == 'focused'


def launch(agent_id, directory, repo=REPO, args=()):
    if not any(spec['id'] == agent_id for spec in AGENTS):
        raise ValueError('unknown agent: ' + str(agent_id))
    if not cli_installed(agent_id):
        raise ValueError(agent_id + ' is not installed')
    if not iterm_available():
        raise ValueError('iTerm2 is not installed')
    directory = Path(directory).expanduser()
    if not directory.is_dir():
        raise ValueError('directory missing: ' + str(directory))
    command = command_for(agent_id, repo)
    if args:
        command += ' ' + ' '.join(shlex.quote(str(arg)) for arg in args)
    script = iterm_applescript(str(directory), command)
    result = subprocess.run(['osascript', '-e', script], capture_output=True, text=True, timeout=15)
    if result.returncode != 0:
        reason = result.stderr.strip() or 'unknown AppleScript failure'
        raise ValueError('iTerm launch failed: ' + reason)
