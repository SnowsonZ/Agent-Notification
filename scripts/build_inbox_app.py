#!/usr/bin/env python3
from pathlib import Path
import plistlib
import shutil
import subprocess

root = Path(__file__).resolve().parents[1]
contents = root / 'build/SessionInbox.app/Contents'
running = subprocess.run(['/bin/ps', '-axo', 'comm='], capture_output=True, text=True, check=True).stdout.splitlines()
if str(contents / 'MacOS/SessionInbox') in [line.strip() for line in running]:
    raise SystemExit('Quit Agent Notification before rebuilding its executable.')
(contents / 'MacOS').mkdir(parents=True, exist_ok=True)
(contents / 'Resources').mkdir(parents=True, exist_ok=True)
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
                str(root / 'native/InboxPolicy.swift'), str(root / 'native/SessionInbox.swift'), '-o', str(contents / 'MacOS/SessionInbox')], check=True)
(contents / 'Info.plist').write_bytes(plistlib.dumps({
    'CFBundleExecutable': 'SessionInbox', 'CFBundleIdentifier': 'local.session-manager.inbox',
    'CFBundleName': 'Agent Notification', 'CFBundleDisplayName': 'Agent Notification',
    'CFBundlePackageType': 'APPL', 'CFBundleShortVersionString': '0.5.0',
    'CFBundleVersion': '11', 'LSMinimumSystemVersion': '14.0', 'CFBundleIconFile': 'AppIcon.icns',
    'NSHighResolutionCapable': True, 'SessionManagerRoot': str(root),
    'NSAppleEventsUsageDescription': '用于定位 iTerm2 中已有的 agent 会话，不向终端输入命令。',
}))
subprocess.run(['codesign', '--force', '--sign', '-', str(contents.parent)], check=True)
subprocess.run(['codesign', '--verify', '--deep', '--strict', str(contents.parent)], check=True)
# Refresh this bundle's metadata after in-place builds (name and icon changes).
contents.parent.touch()
subprocess.run(['/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister',
                '-f', str(contents.parent)], check=True)
print(contents.parent)
