from datetime import timedelta
import json
import stat
import sys
import time

from fastapi.testclient import TestClient
import pytest

import api_server
from ner.auth import AuthStore, COOKIE_NAME, main


@pytest.fixture
def account(monkeypatch, tmp_path):
    root = tmp_path / 'store'
    AuthStore.provision(root, 'admin', 'test-only-password')
    monkeypatch.setattr(api_server, 'base', str(root))
    return root


def login(client, username='admin', password='test-only-password'):
    return client.post('/api/auth/login', data={'username': username, 'password': password})


@pytest.mark.parametrize('path', ['/api/jobs', '/api/capabilities', '/api/settings',
    '/api/jobs/fake/files/original', '/api/jobs/fake/preview/translated', '/openapi.json'])
def test_private_endpoints_require_login(account, path):
    with TestClient(api_server.app) as client:
        response = client.get(path)
        assert response.status_code == 401
        assert response.json()['error']['code'] == 'LOGIN_REQUIRED'
        assert response.headers['cache-control'] == 'no-store'


@pytest.mark.parametrize('path', ['/api/jobs', '/api/settings', '/return_origin_file',
    '/return_translated_file', '/return_translated_file_jaen'])
def test_writes_and_legacy_api_are_protected_before_body_validation(account, path):
    with TestClient(api_server.app) as client:
        response = client.request('PUT' if path == '/api/settings' else 'POST', path)
        assert response.status_code == 401
        assert not (account / 'jobs').exists()


def test_login_redirect_and_public_assets(account):
    with TestClient(api_server.app) as client:
        assert client.get('/', follow_redirects=False).status_code == 303
        assert client.get('/docs', follow_redirects=False).headers['location'].startswith('/login?next=')
        assert client.get('/login').status_code == 200
        assert client.get('/assets/login.js').status_code == 200
        assert client.get('/api/auth/session').json() == {
            'configured': True, 'authenticated': False, 'username': None}


def test_login_cookie_bearer_and_logout_revoke_session(account):
    with TestClient(api_server.app) as client:
        response = login(client)
        assert response.status_code == 200
        assert 'httponly' in response.headers['set-cookie'].lower()
        assert 'samesite=strict' in response.headers['set-cookie'].lower()
        assert 'max-age=43200' in response.headers['set-cookie'].lower()
        token = response.json()['access_token']
        assert client.cookies.get(COOKIE_NAME) == token
        assert client.get('/api/jobs').status_code == 200
        assert client.get('/api/auth/session').json()['username'] == 'admin'
        client.cookies.clear()
        bearer = {'Authorization': f'Bearer {token}'}
        assert client.get('/api/jobs', headers=bearer).status_code == 200
        assert client.post('/api/auth/logout', headers=bearer).status_code == 200
        assert client.get('/api/jobs', headers=bearer).status_code == 401
        assert client.get('/api/auth/session', headers=bearer).json()['authenticated'] is False


def test_login_survives_restart_and_password_reset_invalidates_old_tokens(account):
    with TestClient(api_server.app) as client:
        token = login(client).json()['access_token']
    with TestClient(api_server.app) as client:
        assert client.get('/api/jobs', headers={'Authorization': f'Bearer {token}'}).status_code == 200
    AuthStore.provision(account, 'admin', 'new-test-password')
    with TestClient(api_server.app) as client:
        assert client.get('/api/jobs', headers={'Authorization': f'Bearer {token}'}).status_code == 401
        assert login(client).status_code == 401
        assert login(client, password='new-test-password').status_code == 200


def test_invalid_credentials_do_not_identify_the_wrong_field_or_set_cookie(account):
    with TestClient(api_server.app) as client:
        wrong_password = login(client, password='wrong')
        wrong_user = login(client, username='unknown')
        assert wrong_password.status_code == wrong_user.status_code == 401
        assert wrong_password.json() == wrong_user.json()
        assert 'set-cookie' not in wrong_password.headers
        assert 'wrong' not in wrong_password.text
        assert client.get('/api/jobs').status_code == 401


def test_tampered_and_expired_tokens_are_rejected(account):
    with TestClient(api_server.app) as client:
        token = login(client).json()['access_token']
        client.cookies.clear()
        assert client.get('/api/jobs', headers={'Authorization': f'Bearer {token}invalid'}).status_code == 401
        auth = api_server.app.state.auth
        session = auth.login('admin', 'test-only-password')
        import asyncio
        user = asyncio.run(auth.manager.get_current_user(session))
        expired = auth.manager.create_access_token(data={'sub': user['session_id']}, expires=timedelta(seconds=-1))
        assert client.get('/api/jobs', headers={'Authorization': f'Bearer {expired}'}).status_code == 401
        with auth.database() as db:
            db.execute('UPDATE sessions SET expires = ?', (time.time() - 1,))
        assert client.get('/api/jobs', headers={'Authorization': f'Bearer {token}'}).status_code == 401


def test_login_is_rate_limited(account):
    with TestClient(api_server.app) as client:
        for _ in range(5):
            assert login(client, password='wrong').status_code == 401
        response = login(client)
        assert response.status_code == 429
        assert response.headers['retry-after'] == '60'


def test_cross_origin_cookie_mutations_are_rejected(account):
    with TestClient(api_server.app) as client:
        assert login(client).status_code == 200
        for path in ('/api/auth/login', '/api/auth/logout', '/api/jobs'):
            response = client.post(path, headers={'Origin': 'https://unrelated.example'})
            assert response.status_code == 403
        assert client.put('/api/settings', json={'retention_days': 7},
                          headers={'Sec-Fetch-Site': 'cross-site'}).status_code == 403
        assert client.get('/api/settings').json()['retention_days'] is None
        assert client.put('/api/settings', json={'retention_days': 7},
                          headers={'Origin': 'http://testserver'}).status_code == 200


def test_unconfigured_auth_does_not_expose_the_app(monkeypatch, tmp_path):
    monkeypatch.setattr(api_server, 'base', str(tmp_path / 'missing'))
    with TestClient(api_server.app) as client:
        assert client.get('/api/jobs').status_code == 503
        assert client.get('/api/auth/session').json()['configured'] is False
        assert login(client).status_code == 503
        assert client.get('/login').status_code == 200


def test_setup_stores_only_hash_and_private_files(account):
    data = json.loads((account / 'auth.json').read_text())
    assert data['password_hash'].startswith('$argon2id$')
    assert 'test-only-password' not in str(data)
    assert stat.S_IMODE((account / 'auth.json').stat().st_mode) == 0o600


def test_secure_cookie_configuration(account, monkeypatch):
    monkeypatch.setenv('NER_COOKIE_SECURE', '1')
    with TestClient(api_server.app, base_url='https://testserver') as client:
        response = login(client)
        assert 'secure' in response.headers['set-cookie'].lower()
        assert client.get('/api/jobs').status_code == 200


@pytest.mark.parametrize('confirmation', ['setup-password', 'does-not-match'])
def test_setup_command_confirms_password_and_creates_account(tmp_path, monkeypatch, confirmation):
    root = tmp_path / 'configured'
    monkeypatch.setattr(sys, 'argv', ['ner.auth', 'setup', '--storage', str(root)])
    monkeypatch.setattr('builtins.input', lambda prompt: 'owner')
    passwords = iter(['setup-password', confirmation])
    monkeypatch.setattr('ner.auth.getpass.getpass', lambda prompt: next(passwords))
    if confirmation != 'setup-password':
        with pytest.raises(SystemExit) as error:
            main()
        assert error.value.code == 2
        assert not (root / 'auth.json').exists()
    else:
        main()
        account = AuthStore(root)
        assert account.login('owner', 'setup-password')
