import sys

from fastapi.testclient import TestClient
import pytest
import yaml

import api_server
from ner.auth import AuthStore
from ner.backend_setup import backend_status


@pytest.mark.parametrize('name', ['INDEX', 'BERGAMOT', 'GOOGLE', 'TEXTRA'])
def test_missing_backend_has_direct_setup_route(name):
    result = backend_status({}, name)
    assert not result['configured']
    assert result['setup']['code'] == 'MISSING_CONFIGURATION'
    assert result['setup']['url'] == f'#settings/{name}'
    assert result['setup']['fields'] == [name]


@pytest.mark.parametrize('section,code,field', [
    ({'BASE_URL': 'ftp://private-token.example/v1'}, 'INVALID_FIELD', 'INDEX.BASE_URL'),
    ({'BASE_URL': 'http://username:private-token@example.org/v1'}, 'INVALID_FIELD', 'INDEX.BASE_URL'),
    ({'MODEL': ''}, 'INVALID_FIELD', 'INDEX.MODEL'),
    ({'MAX_TOKENS': 0}, 'INVALID_FIELD', 'INDEX.MAX_TOKENS'),
    ({'TIMEOUT': 0}, 'INVALID_FIELD', 'INDEX.TIMEOUT'),
    ({'CHUNK_CHARS': 0}, 'INVALID_FIELD', 'INDEX.CHUNK_CHARS'),
    (['private-token'], 'INVALID_SECTION', 'INDEX'),
])
def test_invalid_index_fields_are_actionable_without_exposing_values(section, code, field):
    result = backend_status({'INDEX': section}, 'INDEX')
    assert not result['configured']
    assert result['setup']['code'] == code
    assert result['setup']['fields'] == [field]
    assert 'private-token' not in str(result)


def test_common_configuration_error_identifies_shared_field():
    result = backend_status({'INDEX': {}, 'MAX_CHUNKS': 0}, 'INDEX')
    assert not result['configured']
    assert result['setup']['fields'] == ['MAX_CHUNKS']


def test_index_configuration_check_does_not_probe_network_or_claim_inference(monkeypatch):
    import requests
    monkeypatch.setattr(requests.Session, 'request', lambda *args, **kwargs: pytest.fail('Must not contact model server'))
    result = backend_status({'INDEX': {'BASE_URL': 'http://unreachable.example:8001/v1'}}, 'INDEX')
    assert result['configured']
    assert result['setup']['code'] == 'CONFIGURED'
    assert result['setup']['verification'] == 'configuration_only'
    assert '未確認' in result['setup']['message']
    assert 'unreachable' not in str(result)


def test_bergamot_differentiates_missing_model_and_executable(tmp_path):
    result = backend_status({'BERGAMOT': {'MODEL_CONFIG': str(tmp_path / 'private-token.yml')}}, 'BERGAMOT')
    assert result['setup']['code'] == 'MODEL_CONFIG_NOT_FOUND'
    assert result['setup']['fields'] == ['BERGAMOT.MODEL_CONFIG']
    assert 'private-token' not in str(result)
    model = tmp_path / 'model.yml'
    model.write_text('models: []')
    result = backend_status({'BERGAMOT': {'MODEL_CONFIG': str(model), 'WORKER_BINARY': str(tmp_path / 'private-token')}}, 'BERGAMOT')
    assert result['setup']['code'] == 'EXECUTABLE_NOT_FOUND'
    assert 'private-token' not in str(result)


def test_bergamot_configuration_check_does_not_start_worker(tmp_path, monkeypatch):
    import subprocess
    model = tmp_path / 'model.yml'
    model.write_text('models: []')
    monkeypatch.setattr(subprocess, 'Popen', lambda *args, **kwargs: pytest.fail('Must not start worker'))
    result = backend_status({'BERGAMOT': {'MODEL_CONFIG': str(model), 'BINARY': sys.executable}}, 'BERGAMOT')
    assert result['configured']
    assert result['setup']['verification'] == 'configuration_only'


def test_sample_textra_credentials_are_not_reported_as_ready():
    config = yaml.safe_load((api_server.WEB_ROOT.parent / 'config_sample.yml').read_text())
    result = backend_status(config, 'TEXTRA')
    assert not result['configured']
    assert result['setup']['code'] == 'SAMPLE_CREDENTIALS'
    assert result['setup']['fields'] == ['TEXTRA.NAME', 'TEXTRA.KEY', 'TEXTRA.SECRET']


@pytest.fixture
def api(monkeypatch, tmp_path):
    root = tmp_path / 'server'
    monkeypatch.setattr(api_server, 'base', str(root))
    monkeypatch.setattr(api_server, 'CONFIG_PATH', str(tmp_path / 'private-server-config.yml'))
    AuthStore.provision(root, 'admin', 'backend-setup-test-password')
    with TestClient(api_server.app) as client:
        response = client.post('/api/auth/login', data={'username': 'admin', 'password': 'backend-setup-test-password'})
        assert response.status_code == 200
        yield client


@pytest.mark.parametrize('contents', [None, 'not: [valid YAML', '[]', 'TEXTRA: !unknown secret-token'])
def test_unreadable_configuration_keeps_all_setup_guides_available(api, contents):
    from pathlib import Path
    if contents is not None:
        Path(api_server.CONFIG_PATH).write_text(contents)
    response = api.get('/api/capabilities')
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    body = response.json()
    assert body['configuration']['loaded'] is False
    assert len(body['backends']) == 4
    assert all(not backend['configured'] and backend['setup']['code'] == 'CONFIGURATION_UNREADABLE'
               for backend in body['backends'])
    assert 'private-server-config' not in response.text and 'secret-token' not in response.text


def test_capabilities_refresh_after_server_file_edit_and_never_expose_credentials(api):
    from pathlib import Path
    path = Path(api_server.CONFIG_PATH)
    path.write_text('INDEX:\n  BASE_URL: ftp://invalid\n')
    result = next(backend for backend in api.get('/api/capabilities').json()['backends'] if backend['id'] == 'INDEX')
    assert not result['configured']
    path.write_text(yaml.safe_dump({'INDEX': {'BASE_URL': 'http://model.example:8001/v1'},
        'TEXTRA': {'NAME': 'private-account', 'KEY': 'private-api-key', 'SECRET': 'private-api-secret',
                   'URL': 'https://private-endpoint.example/api'}}))
    response = api.get('/api/capabilities')
    assert response.json()['configuration']['loaded'] is True
    assert all(backend['configured'] for backend in response.json()['backends'] if backend['id'] in ('INDEX', 'TEXTRA'))
    assert not any(value in response.text for value in ('private-account', 'private-api-key', 'private-api-secret', 'private-endpoint'))


def test_sample_credentials_cannot_be_submitted_directly_via_api(api):
    from pathlib import Path
    config = yaml.safe_load((api_server.WEB_ROOT.parent / 'config_sample.yml').read_text())
    Path(api_server.CONFIG_PATH).write_text(yaml.safe_dump(config))
    response = api.post('/api/jobs', files={'file': ('test.pdf', b'irrelevant')}, data={'backend': 'TEXTRA'})
    assert response.status_code == 503
    assert response.json()['error']['code'] == 'BACKEND_NOT_CONFIGURED'
    assert not api_server.app.state.jobs.jobs
