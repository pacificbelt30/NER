import hashlib
import io
import json
from pathlib import Path
import stat
import threading
import time

from fastapi.testclient import TestClient
import pytest
import requests

import api_server
import launcher
from ner.auth import AuthStore
from ner.configuration import ConfigurationConflict, ConfigurationStore, effective_config
from ner.download_jobs import DownloadManager
from ner.errors import ConfigurationError
from ner.local_translators import IndexTranslator
from ner.model_downloads import (CATALOG, DownloadCancelled, download_index, download_resource, plan_index)


def test_private_overlay_keeps_readonly_base_and_other_backends(tmp_path):
    base = tmp_path / 'base.yml'
    original = b'PRIORITY: BERGAMOT\nFONT: ""\nGOOGLE:\n  URL: https://example.org/translate\nINDEX:\n  MAX_TOKENS: 8192\n'
    base.write_bytes(original)
    base.chmod(0o444)
    store = ConfigurationStore(base, tmp_path / 'data')
    saved = store.save('INDEX', {'BASE_URL': 'http://model:8001/v1', 'API_KEY': 'private-bearer'},
                       revision=store.revision(), make_default=True)
    assert base.read_bytes() == original
    assert store.path.stat().st_mode & 0o777 == 0o600
    assert 'private-bearer' not in str(saved)
    assert saved['backends']['INDEX']['secrets_set']['API_KEY']
    data = store.load()
    assert data['GOOGLE']['URL'] == 'https://example.org/translate'
    assert data['INDEX']['MAX_TOKENS'] == 8192 and data['PRIORITY'] == 'INDEX'
    store.save('INDEX', {'MODEL': 'changed-model', 'API_KEY': None}, revision=saved['revision'])
    assert store.load()['INDEX']['API_KEY'] == 'private-bearer'
    store.save('INDEX', {'API_KEY': ''}, revision=store.revision())
    assert store.load()['INDEX']['API_KEY'] == ''


def test_first_setup_without_yaml_and_revision_conflicts(tmp_path):
    store = ConfigurationStore(tmp_path / 'absent.yml', tmp_path / 'data')
    revision = store.view()['revision']
    saved = store.save('INDEX', {'BASE_URL': 'http://model:8001/v1'}, revision=revision)
    assert store.load()['INDEX']['BASE_URL'] == 'http://model:8001/v1'
    with pytest.raises(ConfigurationConflict):
        store.save('INDEX', {'MODEL': 'lost-update'}, revision=revision)
    assert saved['revision'] != revision


@pytest.mark.parametrize('values', [{'UNKNOWN': 'x'}, {'BASE_URL': 'ftp://bad'}, {'API_KEY': 'bad\r\nheader'},
                                   {'MAX_TOKENS': True}, {'TIMEOUT': 0}, {'MAX_TOKENS': 1.5}])
def test_invalid_settings_never_replace_saved_configuration(tmp_path, values):
    store = ConfigurationStore(tmp_path / 'base.yml', tmp_path / 'data')
    store.save('INDEX', {'BASE_URL': 'http://model:8001/v1'}, revision=store.revision())
    before = store.path.read_bytes()
    with pytest.raises((ConfigurationError, ValueError)):
        store.save('INDEX', values, revision=store.revision())
    assert store.path.read_bytes() == before


def test_index_optional_bearer_auth_is_not_sent_without_configuration():
    with_key = IndexTranslator({'API_KEY': 'test-bearer'})
    without = IndexTranslator({})
    try:
        assert with_key.session.headers['Authorization'] == 'Bearer test-bearer'
        assert 'Authorization' not in without.session.headers
    finally:
        with_key.close(); without.close()


def resource(content, name='model.gguf'):
    return {'name': name, 'size': len(content), 'algorithm': 'sha256',
            'digest': hashlib.sha256(content).hexdigest(), 'url': 'https://fixture/' + name}


class Response:
    def __init__(self, content=b'', *, data=None, status=200, headers=None, fail=False):
        self.content, self.data, self.status_code, self.headers, self.fail = content, data, status, headers or {}, fail
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def raise_for_status(self):
        if self.status_code >= 400: raise requests.HTTPError('private signed URL must not leak')
    def json(self): return self.data
    def iter_content(self, *args):
        yield self.content
        if self.fail: raise requests.ConnectionError('connection interrupted')


class Session:
    def __init__(self, callback): self.callback = callback
    def get(self, url, **kwargs): return self.callback(url, kwargs)
    def __enter__(self): return self
    def __exit__(self, *args): pass


def test_interrupted_download_resumes_with_verified_range(tmp_path):
    content = b'verified model content'
    item = resource(content)
    seen = []
    def first(url, kwargs):
        seen.append(kwargs)
        return Response(content[:8], fail=True)
    with pytest.raises(requests.ConnectionError):
        download_resource(Session(first), item, tmp_path, lambda *args: None)
    assert not (tmp_path / item['name']).exists()
    def second(url, kwargs):
        assert kwargs['headers']['Range'] == 'bytes=8-'
        return Response(content[8:], status=206, headers={'Content-Range': f'bytes 8-{len(content)-1}/{len(content)}'})
    output = download_resource(Session(second), item, tmp_path, lambda *args: None)
    assert output.read_bytes() == content
    assert output.stat().st_mode & stat.S_IROTH
    assert not list(tmp_path.glob('*.part'))


def test_range_ignored_restarts_cleanly_and_bad_checksum_keeps_previous_model(tmp_path):
    item = resource(b'new model content')
    (tmp_path / item['name']).write_bytes(b'previous verified model')
    partial = tmp_path / ('.' + item['name'] + '.' + item['digest'][:12] + '.part')
    partial.write_bytes(b'new ')
    with pytest.raises(ValueError, match='checksum'):
        download_resource(Session(lambda *args: Response(b'corrupt modelxxxx')), item, tmp_path, lambda *args: None)
    assert (tmp_path / item['name']).read_bytes() == b'previous verified model'
    output = download_resource(Session(lambda *args: Response(b'new model content')), item, tmp_path, lambda *args: None)
    assert output.read_bytes() == b'new model content'


def test_invalid_range_and_cancellation_keep_partial_data(tmp_path):
    content = b'complete model'
    item = resource(content)
    partial = tmp_path / ('.' + item['name'] + '.' + item['digest'][:12] + '.part')
    partial.write_bytes(b'comp')
    with pytest.raises(ValueError, match='resume'):
        download_resource(Session(lambda *args: Response(content[4:], status=206,
            headers={'Content-Range': f'bytes 0-{len(content)-1}/{len(content)}'})), item, tmp_path, lambda *args: None)
    assert partial.read_bytes() == b'comp'
    event = threading.Event(); event.set()
    with pytest.raises(DownloadCancelled):
        download_resource(Session(lambda *args: pytest.fail('must not request')), item, tmp_path, lambda *args: None, event)
    assert partial.read_bytes() == b'comp'


def index_fixture():
    files = {CATALOG['gguf-q4']['filename']: b'model weights fixture', 'README.md': b'official model card'}
    siblings = []
    for name, value in files.items():
        entry = {'rfilename': name, 'size': len(value), 'blobId': hashlib.sha1(f'blob {len(value)}\0'.encode() + value).hexdigest()}
        if name.endswith('.gguf'): entry['lfs'] = {'sha256': hashlib.sha256(value).hexdigest()}
        siblings.append(entry)
    metadata = {'sha': CATALOG['gguf-q4']['revision'], 'siblings': siblings}
    def callback(url, kwargs):
        if '/api/models/' in url: return Response(data=metadata)
        return Response(files[url.rsplit('/', 1)[1]])
    return metadata, callback


def test_index_manifest_progress_and_repeated_download_reuses_verified_files(tmp_path, monkeypatch):
    metadata, callback = index_fixture()
    calls = []
    def request(url, kwargs): calls.append(url); return callback(url, kwargs)
    monkeypatch.setattr('ner.model_downloads.requests.Session', lambda: Session(request))
    progress = []
    result = download_index(tmp_path, on_progress=progress.append)
    assert Path(result['model_path']).read_bytes() == b'model weights fixture'
    manifest = json.loads((tmp_path / 'ner-model.json').read_text())
    assert manifest['revision'] == CATALOG['gguf-q4']['revision']
    assert progress[-1]['stage'] == 'done' and progress[-1]['downloaded_bytes'] == progress[-1]['total_bytes']
    calls.clear()
    download_index(tmp_path)
    assert len(calls) == 1 and '/api/models/' in calls[0]


def test_unsafe_metadata_paths_and_wrong_revision_are_rejected():
    metadata, callback = index_fixture()
    metadata['sha'] = 'wrong'
    with pytest.raises(ValueError, match='revision'):
        plan_index(Session(callback), 'gguf-q4')
    metadata['sha'] = CATALOG['hf']['revision']
    metadata['siblings'] = [{'rfilename': '../config.json', 'size': 1, 'blobId': '0' * 40}]
    with pytest.raises(ValueError, match='path'):
        plan_index(Session(callback), 'hf')


@pytest.fixture
def api(monkeypatch, tmp_path):
    root = tmp_path / 'server'
    monkeypatch.setattr(api_server, 'base', str(root))
    monkeypatch.setattr(api_server, 'CONFIG_PATH', str(tmp_path / 'absent.yml'))
    AuthStore.provision(root, 'admin', 'management-test-password')
    with TestClient(api_server.app) as client:
        assert client.post('/api/auth/login', data={'username': 'admin', 'password': 'management-test-password'}).status_code == 200
        yield client


def test_api_saves_settings_masks_secrets_and_applies_shared_config(api):
    original = api.get('/api/backend-settings').json()
    response = api.put('/api/backend-settings/INDEX', json={'revision': original['revision'],
        'values': {'BASE_URL': 'http://model:8001/v1', 'API_KEY': 'management-private-key'}, 'make_default': True})
    assert response.status_code == 200, response.text
    assert 'management-private-key' not in response.text
    assert response.json()['backends']['INDEX']['secrets_set']['API_KEY']
    assert api_server.selected_config('INDEX')['INDEX']['API_KEY'] == 'management-private-key'
    cap = api.get('/api/capabilities').json()
    assert next(item for item in cap['backends'] if item['id'] == 'INDEX')['configured']
    assert api.put('/api/backend-settings/INDEX', json={'revision': original['revision'], 'values': {'MODEL': 'stale'}}).status_code == 409
    revision = api.get('/api/backend-settings').json()['revision']
    assert api.put('/api/backend-settings/INDEX', json={'revision': revision, 'values': {'MAX_TOKENS': True}}).status_code == 422
    api.post('/api/auth/logout')
    assert api.get('/api/backend-settings').status_code == 401
    assert api.get('/api/model-downloads').status_code == 401


def test_api_background_download_cancel_retry_persistence_and_private_errors(api):
    manager = api_server.app.state.downloads
    entered = threading.Event()
    def wait(destination, variant, *, on_progress, cancel_event):
        entered.set()
        while not cancel_event.wait(.01):
            on_progress({'stage': 'downloading', 'downloaded_bytes': 4, 'total_bytes': 10, 'file': 'model.gguf'})
        raise DownloadCancelled()
    manager.downloader = wait
    first = api.post('/api/model-downloads', json={'variant': 'gguf-q4'}).json()
    assert entered.wait(2)
    assert api.post('/api/model-downloads', json={'variant': 'gguf-q4'}).json()['id'] == first['id']
    assert api.post('/api/model-downloads', json={'variant': 'gguf-q8'}).status_code == 409
    assert api.post(f"/api/model-downloads/{first['id']}/cancel").status_code == 200
    for _ in range(100):
        cancelled = api.get(f"/api/model-downloads/{first['id']}").json()
        if cancelled['status'] == 'cancelled': break
        time.sleep(.01)
    assert cancelled['status'] == 'cancelled'
    def fail(*args, **kwargs): raise requests.ConnectionError('signed URL secret-token-must-not-leak')
    manager.downloader = fail
    second = api.post('/api/model-downloads', json={'variant': 'gguf-q4'}).json()
    for _ in range(100):
        failed = api.get(f"/api/model-downloads/{second['id']}").json()
        if failed['status'] == 'failed': break
        time.sleep(.01)
    assert failed['status'] == 'failed'
    assert 'secret-token' not in str(failed)
    restarted = DownloadManager(manager.root)
    restarted.start()
    assert restarted.get(first['id'])['status'] == 'cancelled'
    assert restarted.get(second['id'])['status'] == 'failed'


def test_download_restart_marks_active_jobs_interrupted(tmp_path):
    root = tmp_path / 'model-downloads'; root.mkdir()
    job = {'id': 'a' * 32, 'variant': 'gguf-q4', 'status': 'running'}
    (root / (job['id'] + '.json')).write_text(json.dumps(job))
    manager = DownloadManager(tmp_path)
    manager.start()
    assert manager.get(job['id'])['error']['code'] == 'SERVER_INTERRUPTED'


def test_cli_download_and_config_share_logic_and_hide_secrets(tmp_path, monkeypatch, capsys):
    base, root = tmp_path / 'missing.yml', tmp_path / 'data'
    common = ['--config', str(base), '--storage', str(root)]
    monkeypatch.setattr('sys.stdin', io.StringIO('cli-private-key\n'))
    assert launcher.main([*common, 'config', 'set', 'index', '--base-url', 'http://model:8001/v1', '--api-key-stdin', '--default']) == 0
    assert 'cli-private-key' not in capsys.readouterr().out
    assert effective_config(base, root)['INDEX']['API_KEY'] == 'cli-private-key'
    assert launcher.main(['config', 'show', 'index', *common]) == 0
    assert 'cli-private-key' not in capsys.readouterr().out
    called = []
    monkeypatch.setattr(launcher, 'download_index', lambda destination, variant, **kwargs: called.append((destination, variant)) or {'directory': str(destination)})
    assert launcher.main(['dl', 'index', '--format', 'gguf-q8', *common]) == 0
    assert called[0] == (root / 'models' / 'index' / 'gguf-q8', 'gguf-q8')


def test_api_downloads_real_fixture_bytes_verifies_and_restores_completed_job(api, monkeypatch):
    metadata, callback = index_fixture()
    monkeypatch.setattr('ner.model_downloads.requests.Session', lambda: Session(callback))
    created = api.post('/api/model-downloads', json={'variant': 'gguf-q4'})
    assert created.status_code == 202
    job_id = created.json()['id']
    for _ in range(100):
        job = api.get(f'/api/model-downloads/{job_id}').json()
        if job['status'] not in ('queued', 'running'):
            break
        time.sleep(.01)
    assert job['status'] == 'succeeded', job
    assert Path(job['result']['model_path']).read_bytes() == b'model weights fixture'
    assert job['downloaded_bytes'] == job['total_bytes']
    restored = DownloadManager(api_server.app.state.downloads.root)
    restored.start()
    assert restored.get(job_id)['result'] == job['result']


def test_overrides_changed_directly_on_disk_are_detected_as_a_conflict(tmp_path):
    base = tmp_path / 'base.yml'
    base.write_text('INDEX:\n  BASE_URL: http://model:8001/v1\n')
    store = ConfigurationStore(base, tmp_path / 'data')
    old = store.view()['revision']
    base.write_text('INDEX:\n  BASE_URL: http://new-model:8001/v1\n')
    with pytest.raises(ConfigurationConflict):
        store.save('INDEX', {'MODEL': 'stale'}, revision=old)
    assert store.load()['INDEX']['BASE_URL'] == 'http://new-model:8001/v1'


def test_download_status_write_failure_finishes_job_without_leaking_task_or_event(api, monkeypatch):
    manager = api_server.app.state.downloads
    save = manager._save
    writes = [0]
    def fail_later(job):
        writes[0] += 1
        if writes[0] > 1: raise PermissionError('private-storage-path')
        return save(job)
    monkeypatch.setattr(manager, '_save', fail_later)
    response = api.post('/api/model-downloads', json={'variant': 'gguf-q4'})
    assert response.status_code == 202
    job_id = response.json()['id']
    for _ in range(100):
        job = api.get(f'/api/model-downloads/{job_id}').json()
        if job['status'] == 'failed': break
        time.sleep(.01)
    assert job['error']['code'] == 'DOWNLOAD_STORAGE_FAILED'
    assert 'private-storage-path' not in str(job)
    assert job_id not in manager.events


def test_same_model_destination_cannot_be_written_by_cli_and_server_concurrently(tmp_path):
    from ner.file_locks import exclusive_lock
    from ner.model_downloads import DownloadBusy
    with exclusive_lock(tmp_path / '.download.lock'):
        with pytest.raises(DownloadBusy):
            download_index(tmp_path)
