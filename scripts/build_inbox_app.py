#!/usr/bin/env python3
"""Build Agent Notification.app.

Default: developer bundle — Info.plist carries SessionManagerRoot and the app runs the
repository's scripts and venv in place. --standalone: release bundle — scripts, CLI,
zcode-focus and pure-Python deps are copied into Contents/Resources so the app runs
without a checkout (needs a python3 on the machine; see bin/session-manager).
"""
from pathlib import Path
import argparse
import plistlib
import shutil
import subprocess
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--standalone', action='store_true', help='bundle scripts and deps into the app')
args = parser.parse_args()

root = Path(__file__).resolve().parents[1]
contents = root / 'build/Agent Notification.app/Contents'
running = subprocess.run(['/bin/ps', '-axo', 'comm='], capture_output=True, text=True, check=True).stdout.splitlines()
if str(contents / 'MacOS/Agent Notification') in [line.strip() for line in running]:
    raise SystemExit('Quit Agent Notification before rebuilding its executable.')
(contents / 'MacOS').mkdir(parents=True, exist_ok=True)
# 两种构建共用同一目录：先清掉上一次可能留下的自包含资源，保证开发包不带旧脚本。
for stale in ('scripts', 'bin', 'build', 'pylib'):
    shutil.rmtree(contents / 'Resources' / stale, ignore_errors=True)
(contents / 'Resources').mkdir(parents=True, exist_ok=True)
if args.standalone:
    resources = contents / 'Resources'
    shutil.copytree(root / 'scripts', resources / 'scripts',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'probe_*.py'))
    (resources / 'bin').mkdir()
    shutil.copy2(root / 'bin/session-manager', resources / 'bin/session-manager')
    (resources / 'build').mkdir()
    helper = root / 'build/zcode-focus'
    if not helper.is_file():
        subprocess.run(['xcrun', 'swiftc', str(root / 'native/ZcodeFocus.swift'), '-o', str(helper)], check=True)
    shutil.copy2(helper, resources / 'build/zcode-focus')
    # 纯 Python wheel，按系统 python3（3.9）解析；--target 不碰当前环境。
    subprocess.run([sys.executable, '-m', 'pip', 'install', '--quiet', '--target', str(resources / 'pylib'),
                    '--no-compile', '--only-binary=:all:', '--platform', 'any', '--python-version', '3.9',
                    '--implementation', 'py', '-r', str(root / 'requirements-standalone.txt')], check=True)
    shutil.rmtree(resources / 'pylib/bin', ignore_errors=True)
for icon in sorted((root / 'native/agent-icons').iterdir()):
    shutil.copy2(icon, contents / 'Resources' / icon.name)
generator = root / 'build/generate-app-icon'
subprocess.run(['xcrun', 'swiftc', str(root / 'native/GenerateAppIcon.swift'), '-o', str(generator)], check=True)
iconset = root / 'build/AppIcon.iconset'
iconset_dark = root / 'build/AppIconDark.iconset'
subprocess.run([str(generator), str(iconset), 'light'], check=True)
subprocess.run([str(generator), str(iconset_dark), 'dark'], check=True)
subprocess.run(['iconutil', '-c', 'icns', str(iconset), '-o', str(contents / 'Resources/AppIcon.icns')], check=True)
subprocess.run(['iconutil', '-c', 'icns', str(iconset_dark), '-o', str(contents / 'Resources/AppIconDark.icns')], check=True)
subprocess.run(['xcrun', 'swiftc', '-parse-as-library', '-target', 'arm64-apple-macosx14.0',
                str(root / 'native/InboxPolicy.swift'), str(root / 'native/SessionInbox.swift'), '-o', str(contents / 'MacOS/Agent Notification')], check=True)
info = {
    'CFBundleExecutable': 'Agent Notification', 'CFBundleIdentifier': 'local.session-manager.inbox',
    'CFBundleName': 'Agent Notification', 'CFBundleDisplayName': 'Agent Notification',
    'CFBundlePackageType': 'APPL', 'CFBundleShortVersionString': '0.5.4',
    'CFBundleVersion': '15', 'LSMinimumSystemVersion': '14.0', 'CFBundleIconFile': 'AppIcon.icns',
    'NSHighResolutionCapable': True,
    'NSAppleEventsUsageDescription': '用于定位 iTerm2 中已有的 agent 会话，不向终端输入命令。',
}
if not args.standalone:
    info['SessionManagerRoot'] = str(root)  # 开发包：运行仓库内的脚本与 venv
(contents / 'Info.plist').write_bytes(plistlib.dumps(info))
subprocess.run(['codesign', '--force', '--sign', '-', str(contents.parent)], check=True)
subprocess.run(['codesign', '--verify', '--deep', '--strict', str(contents.parent)], check=True)
# Refresh this bundle's metadata after in-place builds (name and icon changes).
contents.parent.touch()
subprocess.run(['/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister',
                '-f', str(contents.parent)], check=True)
print(contents.parent)
