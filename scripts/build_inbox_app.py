#!/usr/bin/env python3
from pathlib import Path
import plistlib
import subprocess

root = Path(__file__).resolve().parents[1]
contents = root / 'build/SessionInbox.app/Contents'
(contents / 'MacOS').mkdir(parents=True, exist_ok=True)
subprocess.run(['xcrun', 'swiftc', '-parse-as-library', '-target', 'arm64-apple-macosx14.0',
                str(root / 'native/SessionInbox.swift'), '-o', str(contents / 'MacOS/SessionInbox')], check=True)
(contents / 'Info.plist').write_bytes(plistlib.dumps({
    'CFBundleExecutable': 'SessionInbox', 'CFBundleIdentifier': 'local.snowson.session-manager',
    'CFBundleName': 'Agent 会话', 'CFBundleDisplayName': 'Agent 会话',
    'CFBundlePackageType': 'APPL', 'CFBundleShortVersionString': '0.1.0',
    'CFBundleVersion': '1', 'LSMinimumSystemVersion': '14.0',
    'NSHighResolutionCapable': True, 'SessionManagerRoot': str(root),
    'NSAppleEventsUsageDescription': '用于定位 iTerm2 中已有的 agent 会话，不向终端输入命令。',
}))
print(contents.parent)
