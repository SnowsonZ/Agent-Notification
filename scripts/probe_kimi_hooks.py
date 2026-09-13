#!/usr/bin/env python3
"""Run the installed Kimi CLI against a local fixture, with an isolated home.

Exercises the real hook runtime without using or copying account credentials.
The response is a protocol fixture, NOT a real Kimi model quality test.
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--failure', action='store_true', help='Return HTTP 400 instead of OK')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    (root / 'scratch').mkdir(exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix='kimi-probe-', dir=root / 'scratch'))
    home, events = run / 'home', run / 'events'
    home.mkdir(mode=0o700)
    (run / 'empty-skills').mkdir()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length', '0')))
            requests.append(self.path)
            if args.failure:
                body = json.dumps({'error': {'message': 'fixture-request-rejected',
                                            'type': 'invalid_request_error'}}).encode()
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            for delta, finish in [({'role': 'assistant', 'content': 'OK'}, None), ({}, 'stop')]:
                chunk = {'id': 'fixture', 'object': 'chat.completion.chunk',
                         'created': 1, 'model': 'fixture',
                         'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}
                self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    command = shlex.join([sys.executable, str(root / 'scripts/capture_hook.py'),
                         'kimi', '--output-dir', str(events)])
    config = f'''default_model = "fixture"
telemetry = false
builtin_product_skills = false
[providers.fixture]
type = "openai"
base_url = "http://127.0.0.1:{server.server_port}/v1"
api_key = "local-fixture-not-a-real-key"
[models.fixture]
provider = "fixture"
model = "fixture"
max_context_size = 32000
max_output_size = 128
'''
    for event in ['SessionStart', 'TurnStarted', 'UserPromptSubmit', 'Stop', 'StopFailure', 'SessionEnd']:
        config += f'\n[[hooks]]\nevent = "{event}"\ncommand = {json.dumps(command)}\ntimeout = 3\n'
    (home / 'config.toml').write_text(config)
    env = dict(os.environ, KIMI_CODE_HOME=str(home), KIMI_DISABLE_TELEMETRY='1',
               KIMI_CODE_NO_AUTO_UPDATE='1', KIMI_CODE_MODEL_CATALOG_REFRESH_ON_START='0')
    # Do not let ambient model overrides redirect this fixture to a real endpoint.
    env = {key: value for key, value in env.items() if not key.startswith('KIMI_MODEL_')}
    status = 'completed'
    try:
        process = subprocess.run([shutil.which('kimi') or 'kimi', '--skills-dir',
                                  str(run / 'empty-skills'), '-p', 'Reply only OK.'],
                                 cwd=run, env=env, capture_output=True, text=True, timeout=45)
        exit_code = process.returncode
        (run / 'process.log').write_text(process.stdout + '\n' + process.stderr)
    except subprocess.TimeoutExpired:
        status, exit_code = 'timed_out', None
    finally:
        server.shutdown()
        server.server_close()
    captured = sorted((json.loads(p.read_text()) for p in events.glob('*.json')),
                      key=lambda event: event['received_at'])
    report = {'fixture_mode': 'failure' if args.failure else 'success',
              'status': status, 'exit_code': exit_code, 'requests': requests,
              'events': captured, 'run_directory': str(run)}
    (run / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    expected = 'StopFailure' if args.failure else 'Stop'
    return 0 if any(event['event'] == expected for event in captured) else 1


if __name__ == '__main__':
    sys.exit(main())
