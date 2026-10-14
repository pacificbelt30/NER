import asyncio
import json
import sys

from fastapi.testclient import TestClient
import pymupdf as fitz
import pytest
import requests

import api_server
import launcher
from ner import Block, CombinePDF, PaperExtract, TextraConnection
from ner.errors import ConfigurationError, PDFError, TranslationError, RetryableTranslationError
from ner.http_translators import GoogleTranslator, TextraTranslator
from ner.local_translators import IndexTranslator, BergamotTranslator
from ner.translation import TranslationService
from ner.yml_load import config_load


def pdf_bytes(text='This research paper describes a practical method for translating English documents into Japanese.'):
    with fitz.open() as document:
        page = document.new_page()
        if text:
            page.insert_textbox(fitz.Rect(60, 60, 500, 150), text, fontsize=12)
        return document.tobytes()


@pytest.mark.parametrize('content', ['', '[]', '[invalid', 'null'])
def test_invalid_configuration_is_not_silently_ignored(tmp_path, content):
    path = tmp_path / 'config.yml'
    path.write_text(content)
    with pytest.raises(ConfigurationError):
        config_load(path)


def test_missing_configuration_is_reported(tmp_path):
    with pytest.raises(ConfigurationError, match='Cannot read configuration'):
        config_load(tmp_path / 'missing.yml')


@pytest.mark.parametrize('key,value', [('TIMEOUT', 0), ('TIMEOUT', float('nan')),
                                      ('MAX_TOKENS', -1), ('MAX_TOKENS', True)])
def test_invalid_inference_limits_fail_early(key, value):
    with pytest.raises(ConfigurationError):
        IndexTranslator({key: value})


class StubBackend:
    def __init__(self, config):
        self.calls = []
        self.failures = config.get('failures', [])
        self.closed = False

    def translate(self, text):
        self.calls.append(text)
        if self.failures:
            raise self.failures.pop(0)
        return '訳文 ' + text

    def close(self):
        self.closed = True


@pytest.fixture
def stub_backend(monkeypatch):
    monkeypatch.setitem(__import__('ner.translation', fromlist=['BACKENDS']).BACKENDS, 'INDEX', StubBackend)
    monkeypatch.setattr('ner.translation.time.sleep', lambda delay: None)


def test_duplicate_translation_cache_and_eviction(stub_backend):
    with TranslationService({'PRIORITY': 'INDEX', 'CACHE_SIZE': 1}) as service:
        assert service.translate('a') == service.translate('a')
        service.translate('b')
        service.translate('a')
        backend = service.backend
        assert backend.calls == ['a', 'b', 'a']
    assert backend.closed


def test_only_transient_failures_are_retried(stub_backend):
    with TranslationService({'PRIORITY': 'INDEX', 'INDEX': {
        'failures': [RetryableTranslationError('temporary')],
    }}) as service:
        assert service.translate('a') == '訳文 a'
        assert service.backend.calls == ['a', 'a']
    with TranslationService({'PRIORITY': 'INDEX', 'INDEX': {
        'failures': [TranslationError('invalid response')],
    }}) as service:
        with pytest.raises(TranslationError, match='invalid response'):
            service.translate('a')
        assert service.backend.calls == ['a']
        assert not service.cache


def test_online_failure_never_becomes_an_untranslated_success(tmp_path, stub_backend):
    path = tmp_path / 'paper.pdf'
    path.write_bytes(pdf_bytes())
    with TextraConnection(path, config={'PRIORITY': 'INDEX', 'INDEX': {
        'failures': [TranslationError('broken')],
    }}, error_policy='strict') as connection:
        connection.get_pdf_info()
        with pytest.raises(TranslationError, match='page 1, block 1'):
            connection.post_page()
        with pytest.raises(TranslationError, match='Translate every page'):
            connection.insert()
    assert not (tmp_path / 'paper_new.pdf').exists()


@pytest.mark.parametrize('content', [b'not a pdf', b''])
def test_invalid_pdf_is_rejected(tmp_path, content):
    path = tmp_path / 'bad.pdf'
    path.write_bytes(content)
    with pytest.raises(PDFError):
        PaperExtract(path)


def test_blank_pdf_translation_reports_missing_text(tmp_path, stub_backend):
    path = tmp_path / 'blank.pdf'
    path.write_bytes(pdf_bytes(''))
    with TextraConnection(path, config={'PRIORITY': 'INDEX'}, error_policy='strict') as connection:
        connection.get_pdf_info()
        with pytest.raises(PDFError, match='No translatable text'):
            connection.post_page()


def test_long_html_translation_places_all_text(tmp_path):
    path = tmp_path / 'paper.pdf'
    path.write_bytes(pdf_bytes())
    with PaperExtract(path) as pdf:
        pdf.extract_textinfo()
        translation = '非常に長い日本語の翻訳をすべて表示します。' * 12
        prepared = pdf.prepare_page(0, [translation])
        pdf.fill_all_blocks()
        prepared.write_text(pdf.doc[0])
        assert translation == pdf.doc[0].get_text().replace('\n', '').replace(' ', '')


def test_impossible_text_box_fails_instead_of_looping(tmp_path):
    path = tmp_path / 'paper.pdf'
    path.write_bytes(pdf_bytes())
    with PaperExtract(path) as pdf:
        with pytest.raises(PDFError, match='minimum font size'):
            pdf.calc_fontsize(pdf.doc[0].rect, fitz.Rect(1, 1, 5, 1.1), '日本語', pdf.font, 12)


def test_narrow_text_box_cannot_silently_drop_glyphs(tmp_path):
    path = tmp_path / 'paper.pdf'
    path.write_bytes(pdf_bytes())
    with PaperExtract(path) as pdf:
        with pytest.raises(PDFError, match='minimum font size'):
            pdf.calc_fontsize(pdf.doc[0].rect, fitz.Rect(1, 1, 1.4, 20), '日本語', pdf.font, 12)


def make_worker(tmp_path, body):
    model = tmp_path / 'config.yml'
    model.write_text('models: []')
    binary = tmp_path / 'worker'
    binary.write_text(f'#!{sys.executable}\n' + body)
    binary.chmod(0o755)
    return {'MODEL_CONFIG': str(model), 'WORKER_BINARY': str(binary)}


def test_worker_reuses_process_and_preserves_utf8_and_newlines(tmp_path):
    config = make_worker(tmp_path,
        'import sys\n'
        'while header := sys.stdin.buffer.readline():\n'
        '    text = sys.stdin.buffer.read(int(header)).decode("utf-8")\n'
        '    data = ("訳文 " + text).encode("utf-8")\n'
        '    sys.stdout.buffer.write(b"OK " + str(len(data)).encode() + b"\\n" + data)\n'
        '    sys.stdout.buffer.flush()\n')
    worker = BergamotTranslator(config)
    try:
        assert worker.translate('first\n第二行') == '訳文 first\n第二行'
        process = worker.process
        assert worker.translate('last') == '訳文 last'
        assert worker.process is process
    finally:
        worker.close()
    assert process.poll() is not None


def test_worker_timeout_terminates_process(tmp_path):
    config = make_worker(tmp_path, 'import time\ntime.sleep(60)\n')
    worker = BergamotTranslator({**config, 'TIMEOUT': 0.05})
    with pytest.raises(TranslationError, match='timed out'):
        worker.translate('text')
    assert worker.process is None


@pytest.mark.parametrize('body', ['import sys\nsys.exit(1)\n', 'print("broken", flush=True)\n'])
def test_worker_bad_response_fails_and_closes(tmp_path, body):
    worker = BergamotTranslator(make_worker(tmp_path, body))
    with pytest.raises(TranslationError, match='invalid response'):
        worker.translate('text')
    assert worker.process is None


def test_translation_count_mismatch_preserves_previous_output(tmp_path, stub_backend):
    source = tmp_path / 'paper.pdf'
    source.write_bytes(pdf_bytes())
    output = tmp_path / 'paper_new.pdf'
    output.write_bytes(b'previous output')
    with TextraConnection(source, config={'PRIORITY': 'INDEX'}) as connection:
        connection.get_pdf_info()
        connection.page_array = [[]]
        with pytest.raises(PDFError, match='block count'):
            connection.insert()
        assert 'This research paper' in connection.pe.doc[0].get_text()
    assert output.read_bytes() == b'previous output'


def test_pdf_save_failure_is_atomic_and_cleans_temporary_file(tmp_path, monkeypatch):
    source = tmp_path / 'paper.pdf'
    source.write_bytes(pdf_bytes())
    output = tmp_path / 'paper_new.pdf'
    output.write_bytes(b'previous complete output')
    def partial_save(document, path, **kwargs):
        path.write_bytes(b'partial output')
        raise OSError('disk full')
    with PaperExtract(source) as document:
        monkeypatch.setattr(fitz.Document, 'save', partial_save)
        with pytest.raises(OSError, match='disk full'):
            document.save()
    assert output.read_bytes() == b'previous complete output'
    assert sorted(path.name for path in tmp_path.iterdir()) == ['paper.pdf', 'paper_new.pdf']


def mock_http_response(backend, monkeypatch, payload, status=200):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(payload).encode()
    response._content_consumed = True
    calls = []
    def request(*args, **kwargs):
        calls.append(kwargs)
        return response
    monkeypatch.setattr(backend.session, 'request', request)
    return calls


def test_google_preserves_spaces_and_does_not_sleep_before_first_request(monkeypatch):
    backend = GoogleTranslator({'URL': 'https://example.com'})
    calls = mock_http_response(backend, monkeypatch, [[['日本語 API の訳文', 'source']]])
    monkeypatch.setattr('ner.http_translators.time.sleep', lambda delay: pytest.fail('Unexpected initial sleep'))
    try:
        assert backend.translate('English text') == '日本語 API の訳文'
        assert calls[0]['timeout'] == 60
    finally:
        backend.close()


@pytest.mark.parametrize('status,error', [(429, RetryableTranslationError), (503, RetryableTranslationError),
                                         (401, TranslationError)])
def test_http_failures_are_classified(monkeypatch, status, error):
    backend = GoogleTranslator({'URL': 'https://example.com'})
    mock_http_response(backend, monkeypatch, {}, status)
    try:
        with pytest.raises(error, match=f'HTTP {status}'):
            backend.translate('English text')
    finally:
        backend.close()


def test_malformed_google_response_is_reported(monkeypatch):
    backend = GoogleTranslator({'URL': 'https://example.com'})
    mock_http_response(backend, monkeypatch, {'wrong': 'shape'})
    try:
        with pytest.raises(TranslationError, match='invalid translation response'):
            backend.translate('English text')
    finally:
        backend.close()


def test_textra_failure_payload_is_not_used_as_translation(monkeypatch):
    backend = TextraTranslator({'URL': 'https://example.com', 'NAME': 'test', 'KEY': 'test', 'SECRET': 'test'})
    mock_http_response(backend, monkeypatch, {'resultset': {'code': 400, 'result': {'text': 'wrong'}}})
    try:
        with pytest.raises(TranslationError, match='error code 400'):
            backend.translate('English text')
    finally:
        backend.close()


@pytest.mark.parametrize('payload', [
    {'choices': []}, {'choices': [{'finish_reason': 'stop', 'message': {'content': 42}}]},
    {'choices': [{'finish_reason': 'content_filter', 'message': {'content': 'partial'}}]},
    {'choices': [{'finish_reason': 'stop', 'message': {'content': '<think>unfinished'}}]},
])
def test_index_rejects_malformed_or_unfinished_outputs(monkeypatch, payload):
    backend = IndexTranslator({})
    mock_http_response(backend, monkeypatch, payload)
    try:
        with pytest.raises(TranslationError):
            backend.translate('English text')
    finally:
        backend.close()


def test_pdf_info_needs_no_translation_configuration(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / 'paper.pdf'
    source.write_bytes(pdf_bytes())
    assert launcher.main(['info', str(source)]) == 0
    assert launcher.main(['info', str(tmp_path / 'missing.pdf')]) == 1


def test_combine_handles_multiple_dots_and_closes_inputs(tmp_path):
    source = tmp_path / 'paper.v2.pdf'
    source.write_bytes(pdf_bytes('original page'))
    translated = tmp_path / 'paper.v2_new.pdf'
    translated.write_bytes(pdf_bytes('translated page'))
    output = launcher.combine(str(source))
    with fitz.open(output) as document:
        assert document.page_count == 2
        assert 'original page' in document[0].get_text()
        assert 'translated page' in document[1].get_text()
    source.unlink()
    translated.unlink()


def test_combine_rejects_overwriting_input(tmp_path):
    with pytest.raises(PDFError, match='differ'):
        CombinePDF(tmp_path / 'a.pdf', tmp_path / 'b.pdf', tmp_path / 'a.pdf')


def test_text_translation_keeps_unterminated_last_block(tmp_path, monkeypatch, stub_backend):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'config.yml').write_text('PRIORITY: INDEX\n')
    text = tmp_path / 'text.txt'
    text.write_text('first\n------------------------------\nlast')
    with Block(text) as block:
        assert '訳文 last' in block.translate()
        block.save(tmp_path / 'translated.txt')
    assert '訳文 last' in (tmp_path / 'translated.txt').read_text()


@pytest.fixture
def api(monkeypatch, tmp_path):
    monkeypatch.setattr(api_server, 'base', str(tmp_path / 'uploads'))
    with TestClient(api_server.app) as client:
        yield client


def test_api_real_process_returns_pdf_and_cleans_workspace(api, tmp_path):
    data = pdf_bytes()
    response = api.post('/return_origin_file', files={'file': ('../paper.pdf', data, 'application/pdf')})
    assert response.status_code == 200
    assert response.content == data
    assert list((tmp_path / 'uploads').iterdir()) == []
    assert not (tmp_path / 'paper.pdf').exists()


@pytest.mark.parametrize('filename,content,status', [
    ('bad.txt', b'hello', 415), ('empty.pdf', b'', 400), ('broken.pdf', b'broken', 400),
])
def test_api_rejects_invalid_uploads_and_cleans_up(api, tmp_path, filename, content, status):
    response = api.post('/return_origin_file', files={'file': (filename, content)})
    assert response.status_code == status
    assert not (tmp_path / 'uploads').exists() or not list((tmp_path / 'uploads').iterdir())


def test_api_upload_limit_and_unimplemented_direction(api, monkeypatch):
    monkeypatch.setattr(api_server, 'MAX_UPLOAD_BYTES', 8)
    assert api.post('/return_origin_file', files={'file': ('large.pdf', b'123456789')}).status_code == 413
    assert api.post('/return_translated_file_jaen', files={'file': ('paper.pdf', b'data')}).status_code == 501


def test_api_backend_failure_is_502_and_does_not_leave_files(api, tmp_path, monkeypatch):
    async def fail(*args):
        raise TranslationError('temporary translation failure')
    monkeypatch.setattr(api_server, 'run_pdf_job', fail)
    response = api.post('/return_translated_file', files={'file': ('paper.pdf', pdf_bytes())})
    assert response.status_code == 502
    assert not list((tmp_path / 'uploads').iterdir())


def test_api_same_filename_requests_are_isolated_and_nonblocking(api, tmp_path, monkeypatch):
    paths = []
    started = asyncio.Event()
    async def slow_job(filename, translate):
        paths.append(filename)
        if len(paths) == 2:
            started.set()
        await asyncio.wait_for(started.wait(), 2)
        return filename
    monkeypatch.setattr(api_server, 'run_pdf_job', slow_job)
    import httpx
    async def requests():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api_server.app),
                                    base_url='http://test') as client:
            return await asyncio.gather(*[
                client.post('/return_origin_file', files={'file': ('paper.pdf', pdf_bytes())})
                for _ in range(2)
            ])
    responses = asyncio.run(requests())
    assert all(response.status_code == 200 for response in responses)
    assert len(set(paths)) == 2
    assert not list((tmp_path / 'uploads').iterdir())
