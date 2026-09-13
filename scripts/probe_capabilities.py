#!/usr/bin/env python3
"""Read installed application code and metadata; never read user conversations.

This is static evidence, not a runtime integration test. No configuration is
changed, no model is called, and no application is launched.
"""
import json
import pathlib
import plistlib
import shutil
import struct
import subprocess


def app_metadata(name):
    root = pathlib.Path('/Applications') / (name + '.app')
    info = root / 'Contents/Info.plist'
    if not info.exists():
        return root, {'installed_at_expected_path': False}
    data = plistlib.loads(info.read_bytes())
    return root, {
        'installed_at_expected_path': True,
        'version': data.get('CFBundleShortVersionString'),
        'bundle_id': data.get('CFBundleIdentifier'),
        'schemes': [scheme for item in data.get('CFBundleURLTypes', [])
                    for scheme in item.get('CFBundleURLSchemes', [])],
    }


def javascript_entries(archive):
    with archive.open('rb') as source:
        _, header_size, _, json_size = struct.unpack('<4I', source.read(16))
        header = json.loads(source.read(json_size))

        def walk(files, prefix=''):
            for name, value in files.items():
                path = prefix + name
                if 'files' in value:
                    yield from walk(value['files'], path + '/')
                elif (path.endswith('.js') and not value.get('unpacked')
                      and 'offset' in value and value['size'] < 12_000_000):
                    yield path, value

        for path, value in walk(header['files']):
            source.seek(8 + header_size + int(value['offset']))
            yield path, source.read(value['size']).decode('utf-8', errors='replace')


def probe():
    result = {'evidence_level': 'static-local-inspection',
              'runtime_delivery_verified': False, 'runtime_focus_verified': False}
    for name in ('Claude', 'ZCode', 'iTerm', 'Codex', 'ChatGPT'):
        root, metadata = app_metadata(name)
        result[name] = metadata
        archive = root / 'Contents/Resources/app.asar'
        if name not in ('Claude', 'ZCode') or not archive.exists():
            continue
        findings = []
        for path, code in javascript_entries(archive):
            if name == 'Claude' and 'code/continue' in code:
                findings.append({'file': path,
                                 'continue_route_present': '"/continue"' in code,
                                 'session_query_present': 'searchParams.get("session")' in code,
                                 'existing_session_lookup_present': 't.sessionId===e' in code,
                                 'epitaxy_route_present': '"/epitaxy/"' in code})
            if name == 'ZCode' and path == 'out/main/index.js':
                findings.append({'file': path,
                                 'workspace_link_present': 'zcode://workspace/open?path=' in code,
                                 'exact_session_link_verified': False})
        metadata['findings'] = findings

    pi = shutil.which('pi')
    result['Pi'] = {'on_path': bool(pi)}
    if pi:
        for parent in pathlib.Path(pi).resolve().parents:
            package = parent / 'package.json'
            docs = parent / 'docs/extensions.md'
            if package.exists() and docs.exists():
                source = docs.read_text()
                result['Pi'].update({
                    'version': json.loads(package.read_text()).get('version'),
                    'evidence_path': str(docs),
                    'documented_events': {key: key in source for key in (
                        'session_start', 'session_info_changed', 'agent_settled',
                        'ui_prompt_start', 'ui_prompt_end')},
                })
                break

    kimi = shutil.which('kimi')
    result['Kimi'] = {'on_path': bool(kimi)}
    if kimi:
        version = subprocess.run([kimi, '--version'], capture_output=True,
                                 text=True, timeout=10, check=True).stdout.strip()
        binary = pathlib.Path(kimi).read_bytes()
        result['Kimi'].update({
            'version': version,
            'binary_strings_only': {key: key.encode() in binary for key in (
                'SessionHeartbeat', 'TurnStarted', 'PermissionRequest', 'StopFailure')},
            'hook_payload_runtime_verified': False,
        })
    setting = subprocess.run(['defaults', 'read', 'com.googlecode.iterm2',
                              'EnableAPIServer'], capture_output=True, text=True)
    result['iTerm']['api_preference'] = (
        setting.stdout.strip() if setting.returncode == 0 else 'not-explicitly-set')
    return result


if __name__ == '__main__':
    print(json.dumps(probe(), ensure_ascii=False, indent=2))
