import asyncio
import json
from pathlib import Path
import shutil
from threading import Event
import time
import uuid

from fastapi.testclient import TestClient
import pymupdf
import pytest

import api_server
from ner.errors import TranslationCancelled, TranslationError


def pdf_bytes():
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_textbox(pymupdf.Rect(60, 60, 500, 150),
            'This research paper describes a practical method for translating English documents into Japanese.')
        return document.tobytes()


async def processor(workspace, config, output):
    shutil.copyfile(workspace / 'input.pdf', workspace / 'input_new.pdf')
    if output == 'comparison':
        shutil.copyfile(workspace / 'input.pdf', workspace / 'comparison.pdf')
    (workspace / 'progress.json').write_text(json.dumps({
        'stage': 'done', 'completed_blocks': 1, 'total_blocks': 1, 'page': 1, 'pages': 1,
    }))


@pytest.fixture
def api(monkeypatch, tmp_path):
    monkeypatch.setattr(api_server, 'base', str(tmp_path / 'uploads'))
    monkeypatch.setattr(api_server, 'CONFIG_PATH', str(tmp_path / 'config.yml'))
    monkeypatch.setattr(api_server, 'selected_config', lambda backend=None: {'PRIORITY': backend or 'INDEX'})
    with TestClient(api_server.app) as client:
        api_server.app.state.jobs.processor = processor
        yield client


def submit(client, *, filename='paper.v2.pdf', output='comparison', key=None, content=None):
    headers = {'Idempotency-Key': key} if key else {}
    return client.post('/api/jobs', files={'file': (filename, pdf_bytes() if content is None else content)},
                       data={'backend': 'INDEX', 'output': output}, headers=headers)


def finished(client, job_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = client.get(f'/api/jobs/{job_id}')
        assert response.status_code == 200
        body = response.json()
        if body['status'] in ('succeeded', 'failed', 'cancelled'):
            return body
        time.sleep(0.01)
    pytest.fail('Job did not finish.')


def test_ui_assets_and_api_schema_are_served(api):
    assert api.get('/').status_code == 200
    assert 'PDFを、' in api.get('/').text
    assert api.get('/assets/app.js').status_code == 200
    assert api.get('/assets/style.css').status_code == 200
    schema = api.get('/openapi.json').json()
    assert '/api/jobs/{job_id}/retry' in schema['paths']
    assert 'JobView' in schema['components']['schemas']


def test_job_acceptance_progress_and_repeatable_downloads(api):
    response = submit(api)
    assert response.status_code == 202
    job = response.json()
    assert response.headers['location'] == f"/api/jobs/{job['id']}"
    result = finished(api, job['id'])
    assert result['status'] == 'succeeded'
    assert result['progress']['completed_blocks'] == 1
    assert result['expires_at']
    for kind in ('original', 'translated', 'comparison'):
        download = api.get(result['downloads'][kind])
        assert download.status_code == 200
        assert download.headers['content-type'].startswith('application/pdf')
        assert api.get(result['downloads'][kind]).content == download.content
    assert 'paper.v2_new.pdf' in api.get(result['downloads']['translated']).headers['content-disposition']
    assert 'inline' in api.get(result['downloads']['original'] + '?inline=true').headers['content-disposition']


def test_translated_only_has_no_comparison_link(api):
    job = submit(api, output='translated').json()
    result = finished(api, job['id'])
    assert 'comparison' not in result['downloads']
    assert api.get(f"/api/jobs/{job['id']}/files/comparison").status_code == 404


def test_idempotent_upload_and_receipt_recovery(api):
    key = str(uuid.uuid4())
    first = submit(api, key=key).json()
    second = submit(api, key=key).json()
    assert first['id'] == second['id']
    recovered = api.get(f'/api/jobs/by-key/{key}').json()
    assert recovered['id'] == first['id']
    assert len(api_server.app.state.jobs.jobs) == 1
    assert submit(api, filename='other.pdf', key=key).status_code == 409


@pytest.mark.parametrize('filename,content,status,code', [
    ('text.txt', b'text', 415, 'UNSUPPORTED_FILE'),
    ('empty.pdf', b'', 400, 'EMPTY_FILE'),
])
def test_structured_upload_errors_leave_no_files(api, filename, content, status, code):
    response = submit(api, filename=filename, content=content)
    assert response.status_code == status
    assert response.json()['error']['code'] == code
    assert not list(api_server.app.state.jobs.root.iterdir())


def test_api_validates_backend_and_upload_limit(api, monkeypatch):
    response = api.post('/api/jobs', files={'file': ('paper.pdf', pdf_bytes())}, data={'backend': 'UNKNOWN'})
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'INVALID_REQUEST'
    monkeypatch.setattr(api_server, 'MAX_UPLOAD_BYTES', 8)
    response = submit(api)
    assert response.status_code == 413
    assert response.json()['error']['code'] == 'FILE_TOO_LARGE'


def test_missing_config_is_actionable_and_does_not_leak_secrets(api, monkeypatch):
    # Undo the fixture's replacement for this endpoint only.
    def unavailable(*args):
        raise api_server.api_error(503, 'BACKEND_NOT_CONFIGURED', '翻訳方式の準備が完了していません。', True)
    monkeypatch.setattr(api_server, 'selected_config', unavailable)
    response = submit(api)
    assert response.status_code == 503
    assert response.json()['error']['retryable']


def test_queue_position_running_cancel_and_queued_cancel(api):
    gate = Event()
    async def blocked(workspace, config, output):
        while not gate.is_set():
            if (workspace / 'cancel').exists():
                raise TranslationCancelled('stopped')
            await asyncio.sleep(0.01)
        await processor(workspace, config, output)
    api_server.app.state.jobs.processor = blocked
    first = submit(api).json()
    second = submit(api).json()
    second_state = api.get(f"/api/jobs/{second['id']}").json()
    assert second_state['status'] == 'queued'
    assert second_state['queue_position'] == 1
    assert api.get(f"/api/jobs/{first['id']}/files/translated").status_code == 409
    cancelled = api.post(f"/api/jobs/{second['id']}/cancel").json()
    assert cancelled['status'] == 'cancelled'
    api.post(f"/api/jobs/{first['id']}/cancel")
    assert finished(api, first['id'])['status'] == 'cancelled'
    gate.set()


def test_failure_retry_reuses_pdf_and_allows_engine_change(api):
    async def broken(*args):
        raise TranslationError('private internal path /tmp/secret-engine')
    api_server.app.state.jobs.processor = broken
    first = submit(api).json()
    failed = finished(api, first['id'])
    assert failed['error']['code'] == 'TRANSLATION_FAILED'
    assert '/tmp/secret' not in json.dumps(failed)
    api_server.app.state.jobs.processor = processor
    retry = api.post(f"/api/jobs/{first['id']}/retry", data={'backend': 'BERGAMOT', 'output': 'translated'})
    assert retry.status_code == 202
    next_job = finished(api, retry.json()['id'])
    assert next_job['status'] == 'succeeded'
    assert next_job['backend'] == 'BERGAMOT'
    assert next_job['output'] == 'translated'
    assert next_job['id'] != first['id']
    assert api.post(f"/api/jobs/{next_job['id']}/retry").status_code == 409


def test_capacity_and_expiration(api):
    gate = Event()
    async def blocked(workspace, config, output):
        while not gate.is_set():
            await asyncio.sleep(0.01)
        await processor(workspace, config, output)
    manager = api_server.app.state.jobs
    manager.capacity = 1
    manager.processor = blocked
    first = submit(api).json()
    assert submit(api).status_code == 429
    gate.set()
    finished(api, first['id'])
    manager.jobs[first['id']]['expires_at'] = time.time() - 1
    response = api.get(f"/api/jobs/{first['id']}")
    assert response.status_code == 410
    assert response.json()['error']['code'] == 'JOB_EXPIRED'
    assert not (manager.root / first['id']).exists()


def test_completed_job_survives_server_restart(api):
    job = submit(api).json()
    finished(api, job['id'])
    root = api_server.app.state.jobs.root
    # Restart the manager using its on-disk metadata, with the same HTTP app.
    manager = api_server.JobManager(root, processor)
    manager.start()
    api_server.app.state.jobs = manager
    assert api.get(f"/api/jobs/{job['id']}").json()['status'] == 'succeeded'
    assert api.get(f"/api/jobs/{job['id']}/files/translated").status_code == 200


@pytest.mark.parametrize('failed_blocks', [1, 2])
def test_recovered_pdf_is_downloadable_and_warnings_survive_restart(api, monkeypatch, failed_blocks):
    from ner.translation import BACKENDS

    class MixedBackend:
        def __init__(self, config):
            self.calls = 0

        def translate_chunk(self, text, **kwargs):
            self.calls += 1
            if self.calls <= failed_blocks:
                raise TranslationError('private-token=do-not-expose')
            return '翻訳に成功した本文です。'

        def close(self):
            pass

    monkeypatch.setitem(BACKENDS, 'INDEX', MixedBackend)
    async def recover(workspace, config, output):
        return api_server.translation_job_worker(workspace, config, output)
    api_server.app.state.jobs.processor = recover
    with pymupdf.open() as source:
        page = source.new_page()
        page.insert_text((40, 70), 'Translate the first paragraph.')
        page.insert_text((40, 170), 'Translate the second paragraph.')
        payload = source.tobytes()
    job = submit(api, content=payload).json()
    result = finished(api, job['id'])
    assert result['status'] == 'succeeded'
    assert result['result_summary']['outcome'] == ('partial' if failed_blocks == 1 else 'untranslated')
    assert result['result_summary']['translated_blocks'] == 2 - failed_blocks
    assert len(result['warnings']) == failed_blocks
    assert 'private-token' not in str(result)
    for kind in ('translated', 'comparison'):
        download = api.get(result['downloads'][kind])
        assert download.status_code == 200
        with pymupdf.open(stream=download.content, filetype='pdf') as doc:
            assert len(doc) == (2 if kind == 'translated' else 3)
            assert '翻訳エラーレポート' in ''.join(doc[-1].get_text().split())
    preview = api.get(f"/api/jobs/{job['id']}/preview/translated?page=1")
    assert preview.status_code == 200 and preview.content.startswith(b'\x89PNG')
    manager = api_server.JobManager(api_server.app.state.jobs.root, recover)
    manager.start()
    api_server.app.state.jobs = manager
    restored = api.get(f"/api/jobs/{job['id']}").json()
    assert restored['warnings'] == result['warnings']
    assert restored['result_summary'] == result['result_summary']
    assert api.get(restored['downloads']['translated']).status_code == 200


def test_comparison_failure_keeps_translated_pdf_available(api, monkeypatch):
    from ner.translation import BACKENDS
    class Backend:
        def __init__(self, config):
            pass
        def translate_chunk(self, text, **kwargs):
            return '正常に翻訳された文章です。'
        def close(self):
            pass
    monkeypatch.setitem(BACKENDS, 'INDEX', Backend)
    def fail(self):
        raise OSError('private path /secret')
    monkeypatch.setattr(api_server.CombinePDF, 'combine', fail)
    async def recover(workspace, config, output):
        return api_server.translation_job_worker(workspace, config, output)
    api_server.app.state.jobs.processor = recover
    result = finished(api, submit(api).json()['id'])
    assert result['status'] == 'succeeded'
    assert result['result_summary']['outcome'] == 'complete_with_warnings'
    assert result['warnings'][0]['code'] == 'COMPARISON_FAILED'
    assert 'secret' not in str(result)
    assert 'comparison' not in result['downloads']
    assert api.get(result['downloads']['translated']).status_code == 200


def test_interrupted_job_is_recoverable_after_restart(api):
    root = api_server.app.state.jobs.root
    job_id = uuid.uuid4().hex
    workspace = root / job_id
    workspace.mkdir(parents=True)
    (workspace / 'input.pdf').write_bytes(pdf_bytes())
    (workspace / 'job.json').write_text(json.dumps({
        'id': job_id, 'filename': 'paper.pdf', 'backend': 'INDEX', 'output': 'comparison',
        'status': 'running', 'created_at': time.time(), 'updated_at': time.time(),
        'expires_at': None, 'error': None,
    }))
    manager = api_server.JobManager(root, processor)
    manager.start()
    api_server.app.state.jobs = manager
    recovered = api.get(f'/api/jobs/{job_id}').json()
    assert recovered['status'] == 'failed'
    assert recovered['error']['code'] == 'SERVER_INTERRUPTED'
    assert api.post(f'/api/jobs/{job_id}/retry').status_code == 202


def test_capabilities_do_not_expose_backend_keys(api, tmp_path):
    (tmp_path / 'config.yml').write_text('PRIORITY: TEXTRA\nTEXTRA:\n  NAME: test\n  KEY: secret-test-key\n  SECRET: secret-test-value\n  URL: https://example.com\n')
    response = api.get('/api/capabilities')
    assert response.status_code == 200
    assert 'secret-test' not in response.text
    textra = next(b for b in response.json()['backends'] if b['id'] == 'TEXTRA')
    assert textra['configured'] and not textra['local']


def test_unknown_job_and_file_kind_are_structured(api):
    assert api.get('/api/jobs/unknown').json()['error']['code'] == 'JOB_NOT_FOUND'
    job = submit(api).json()
    assert api.get(f"/api/jobs/{job['id']}/files/other").status_code == 422


def test_page_preview_is_rendered_cached_and_bounded(api):
    job = submit(api).json()
    finished(api, job['id'])
    for kind in ('original', 'translated'):
        response = api.get(f"/api/jobs/{job['id']}/preview/{kind}?page=1")
        assert response.status_code == 200
        assert response.content.startswith(b'\x89PNG')
        assert len(response.content) < 2 * 1024 * 1024
        assert api.get(f"/api/jobs/{job['id']}/preview/{kind}?page=1").content == response.content
    assert api.get(f"/api/jobs/{job['id']}/preview/original?page=2").status_code == 404
    assert api.get(f"/api/jobs/{job['id']}/preview/original?page=0").status_code == 422


def test_startup_removes_unaccepted_partial_upload(api):
    root = api_server.app.state.jobs.root
    workspace = root / uuid.uuid4().hex
    workspace.mkdir(parents=True)
    (workspace / 'input.pdf').write_bytes(b'incomplete')
    manager = api_server.JobManager(root, processor)
    manager.start()
    assert not workspace.exists()


def test_layout_error_allows_retry_with_another_engine():
    from ner.errors import PDFError
    from ner.jobs import public_error
    error = public_error(PDFError('Translation does not fit in its text block.'))
    assert error['code'] == 'LAYOUT_FAILED'
    assert error['retryable']
