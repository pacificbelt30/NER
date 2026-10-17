"""Verify a built Docker image using isolated containers, network and storage.

Default: exercise INDEX against a separate mock HTTP model container (no weights).
Pass --model /path/to/bergamot/config.yml with a Bergamot image for real inference.
All resources created here are removed on exit; existing app data is not used.
"""

import argparse
import hashlib
import json
from pathlib import Path
import secrets
import subprocess
import tempfile
import time

import pymupdf
import requests
import yaml


MOCK_MODEL = r'''
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        assert self.path == '/v1/chat/completions'
        data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        assert data['model'] == 'IndexTeam/Index-Translate-2B'
        body = json.dumps({'choices': [{'message': {'content': '文書を日本語へ翻訳します。'},
                                       'finish_reason': 'stop'}]}).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
HTTPServer(('0.0.0.0', 8001), Handler).serve_forever()
'''


def docker(*args, input=None, timeout=120):
    result = subprocess.run(['docker', *args], input=input, text=True,
                            capture_output=True, timeout=timeout)
    if result.returncode:
        # Commands contain no passwords; account input is passed through stdin.
        raise RuntimeError(f'docker {args[0]} failed: {result.stderr}')
    return result.stdout.strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='ner:local')
    parser.add_argument('--model', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    name = 'ner-smoke-' + secrets.token_hex(5)
    app, model = name + '-app', name + '-model'
    volume, network = name + '-data', name + '-network'
    password = secrets.token_urlsafe(24)
    report = {'image': args.image, 'backend': 'BERGAMOT' if args.model else 'INDEX'}
    with tempfile.TemporaryDirectory(prefix='ner-docker-') as directory:
        config_path = Path(directory) / 'config.yml'
        config = ({'PRIORITY': 'BERGAMOT', 'BERGAMOT': {
            'WORKER_BINARY': '/usr/local/bin/ner-bergamot-worker',
            'MODEL_CONFIG': '/models/' + args.model.name,
        }} if args.model else {'PRIORITY': 'INDEX', 'INDEX': {
            'BASE_URL': 'http://index:8001/v1',
        }})
        config_path.write_text(yaml.safe_dump(config))
        # The container is non-root; allow reading only this test configuration.
        Path(directory).chmod(0o755)
        config_path.chmod(0o644)
        try:
            docker('volume', 'create', volume)
            docker('network', 'create', network)
            subnet = json.loads(docker('network', 'inspect', network))[0]['IPAM']['Config'][0]['Subnet']
            docker('run', '--rm', '-i', '--mount', f'type=volume,source={volume},target=/data',
                   args.image, 'python', '-m', 'ner.auth', 'setup',
                   input=f'admin\n{password}\n{password}\n')
            if not args.model:
                docker('run', '-d', '--name', model, '--network', network,
                       '--network-alias', 'index', args.image, 'python', '-u', '-c', MOCK_MODEL)
            extra = (['--mount', f'type=bind,source={args.model.resolve().parent},target=/models,readonly']
                     if args.model else [])
            docker('run', '-d', '--name', app, '--init', '--network', network,
                   '-p', '127.0.0.1::8000', '-e', 'NO_PROXY=localhost,127.0.0.1,index',
                   '-e', f'FORWARDED_ALLOW_IPS={subnet}',
                   '--mount', f'type=volume,source={volume},target=/data',
                   '--mount', f'type=bind,source={config_path},target=/app/config.yml,readonly',
                   *extra, args.image)
            base = 'http://' + docker('port', app, '8000/tcp').splitlines()[0]
            client = requests.Session()
            client.trust_env = False

            def ready():
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    try:
                        response = client.get(base + '/api/auth/session', timeout=2)
                        if response.ok:
                            return
                    except requests.RequestException:
                        pass
                    time.sleep(0.2)
                raise AssertionError('Container did not become ready.')

            ready()
            assert client.get(base + '/api/jobs', timeout=10).status_code == 401
            # A remote public Host and forwarded HTTPS must agree with Origin.
            public = {'Host': 'ner.example.test:8443', 'Origin': 'https://ner.example.test:8443',
                      'X-Forwarded-Proto': 'https'}
            response = client.post(base + '/api/auth/login', headers=public,
                                   data={'username': 'admin', 'password': password}, timeout=10)
            assert response.ok, response.text
            assert 'Secure' in response.headers['Set-Cookie']
            client.headers['Authorization'] = 'Bearer ' + response.json()['access_token']
            assert client.get(base + '/', timeout=10).status_code == 200
            assert client.get(base + '/assets/app.js', timeout=10).status_code == 200
            assert client.put(base + '/api/settings', json={'retention_days': 7},
                              headers=public, timeout=10).ok
            assert client.put(base + '/api/settings', json={'retention_days': 8},
                              headers={'Origin': 'https://unrelated.example'}, timeout=10).status_code == 403
            with pymupdf.open() as document:
                page = document.new_page()
                page.insert_text((50, 80), 'This document will be translated into Japanese.', fontsize=12)
                source = document.tobytes()
            response = client.post(base + '/api/jobs', headers=public,
                files={'file': ('docker.pdf', source, 'application/pdf')},
                data={'backend': report['backend'], 'output': 'comparison'}, timeout=30)
            assert response.status_code == 202, response.text
            job_id = response.json()['id']
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                response = client.get(base + f'/api/jobs/{job_id}', timeout=10)
                response.raise_for_status()
                result = response.json()
                if result['status'] in ('succeeded', 'failed', 'cancelled'):
                    break
                time.sleep(0.1)
            assert result['status'] == 'succeeded', result
            assert result['result_summary']['outcome'] == 'complete', result
            digests = {}
            for kind, relative in result['downloads'].items():
                response = client.get(base + relative, timeout=30)
                response.raise_for_status()
                with pymupdf.open(stream=response.content, filetype='pdf') as document:
                    assert len(document) == (2 if kind == 'comparison' else 1)
                    if kind == 'translated':
                        text = ''.join(page.get_text() for page in document)
                        assert any('\u3040' <= char <= '\u9fff' for char in text), text
                (args.output / f'{kind}.pdf').write_bytes(response.content)
                digests[kind] = hashlib.sha256(response.content).hexdigest()
            assert digests['original'] == hashlib.sha256(source).hexdigest()
            assert client.get(base + f'/api/jobs/{job_id}/preview/translated', timeout=30).content.startswith(b'\x89PNG')
            assert docker('exec', app, 'id', '-u') == '10001'
            # Recreate rather than merely restart: all state must be in the volume.
            inspect = json.loads(docker('inspect', app))[0]
            docker('stop', app)
            docker('rm', app)
            docker('run', '-d', '--name', app, '--init', '--network', network,
                   '-p', base.removeprefix('http://') + ':8000',
                   '-e', 'NO_PROXY=localhost,127.0.0.1,index',
                   '--mount', f'type=volume,source={volume},target=/data',
                   '--mount', f'type=bind,source={config_path},target=/app/config.yml,readonly',
                   *extra, args.image)
            ready()
            assert client.get(base + '/api/auth/session', timeout=10).json()['authenticated']
            assert client.get(base + '/api/settings', timeout=10).json() == {'retention_days': 7}
            history = client.get(base + '/api/jobs', timeout=10).json()
            assert history['total'] == 1, history
            for kind, relative in result['downloads'].items():
                response = client.get(base + relative, timeout=30)
                response.raise_for_status()
                assert hashlib.sha256(response.content).hexdigest() == digests[kind]
            docker('exec', app, 'python', '-c', inspect['Config']['Healthcheck']['Test'][-1])
            report.update(nonroot=True, account_setup=True, unauthenticated_blocked=True,
                forwarded_https_and_remote_origin=True, cross_origin_rejected=True,
                translation_and_pdf_downloads=True, preview=True, persistent_session=True,
                persistent_history_and_settings=True, recreated_container_downloads_unchanged=True,
                healthcheck=True)
        finally:
            # Cleanup is restricted to the randomly named resources from this run.
            for command in [('logs', app), ('rm', '-f', app, model),
                            ('network', 'rm', network), ('volume', 'rm', volume)]:
                result = subprocess.run(['docker', *command], capture_output=True, text=True, timeout=120)
                if command[0] == 'logs':
                    (args.output / 'server.log').write_text(result.stdout + result.stderr)
    (args.output / 'results.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
