import pytest

import api_server


def test_server_startup_defaults_and_environment(monkeypatch):
    calls = []
    monkeypatch.setattr(api_server.uvicorn, 'run', lambda *args, **kwargs: calls.append(kwargs))
    for name in ('NER_HOST', 'NER_PORT', 'FORWARDED_ALLOW_IPS'):
        monkeypatch.delenv(name, raising=False)
    api_server.main([])
    assert calls[-1] == dict(host='0.0.0.0', port=8000, reload=False, workers=1,
                             forwarded_allow_ips='127.0.0.1')
    monkeypatch.setenv('NER_HOST', '192.0.2.1')
    monkeypatch.setenv('NER_PORT', '8080')
    monkeypatch.setenv('FORWARDED_ALLOW_IPS', '192.0.2.2')
    api_server.main([])
    assert calls[-1]['host'] == '192.0.2.1'
    assert calls[-1]['port'] == 8080
    assert calls[-1]['forwarded_allow_ips'] == '192.0.2.2'
    api_server.main(['--host', '::', '--port', '9000', '--reload'])
    assert calls[-1]['host'] == '::'
    assert calls[-1]['port'] == 9000
    assert calls[-1]['reload'] is True


@pytest.mark.parametrize('value', ['0', '65536', 'invalid'])
def test_invalid_environment_port_fails_before_starting(monkeypatch, value):
    monkeypatch.setenv('NER_PORT', value)
    with pytest.raises(SystemExit) as exc:
        api_server.main([])
    assert exc.value.code == 2
