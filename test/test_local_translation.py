import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import sys
from threading import Thread
from types import SimpleNamespace

import fitz
import pytest
import yaml

from ner import TextraConnection
from ner.local_translators import BergamotTranslator, IndexTranslator
from ner.text_blocks import TranslationBlock
from ner.translation import TranslationService


@pytest.fixture
def index_server():
    state = SimpleNamespace(requests=[], content='これは日本語の翻訳です。', finish_reason='stop', status=200)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            state.requests.append((self.path, payload))
            body = json.dumps({'choices': [{
                'message': {'content': state.content}, 'finish_reason': state.finish_reason,
            }]}).encode()
            self.send_response(state.status)
            if state.status == 302:
                self.send_header('Location', 'https://example.com')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.url = f'http://127.0.0.1:{server.server_port}/v1'
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_index_local_request_and_reasoning_cleanup(index_server, monkeypatch):
    monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:1')
    monkeypatch.setenv('NO_PROXY', '')
    index_server.content = '<think>internal reasoning</think> 日本語の訳文 '
    translator = IndexTranslator({'BASE_URL': index_server.url})
    assert translator.translate('This is a sentence.') == '日本語の訳文'
    path, payload = index_server.requests[0]
    assert path == '/v1/chat/completions'
    assert payload['model'] == 'IndexTeam/Index-Translate-2B'
    assert payload['messages'][0]['content'].endswith('This is a sentence.')
    assert payload['chat_template_kwargs'] == {'enable_thinking': False}


@pytest.mark.parametrize('url', [
    'https://example.com/v1', 'http://127.0.0.1.example.com/v1',
    'ftp://localhost/v1', 'http://localhost/v1?redirect=1',
    'http://localhost:invalid/v1', 'http://[::1/v1',
])
def test_index_rejects_nonlocal_endpoints(url):
    with pytest.raises(ValueError, match='loopback'):
        IndexTranslator({'BASE_URL': url})


@pytest.mark.parametrize('content,reason,status,match', [
    ('partial', 'length', 200, 'truncated'),
    ('', 'stop', 200, 'empty'),
    ('text', 'stop', 302, 'redirect'),
])
def test_index_rejects_incomplete_responses(index_server, content, reason, status, match):
    index_server.content = content
    index_server.finish_reason = reason
    index_server.status = status
    with pytest.raises(RuntimeError, match=match):
        IndexTranslator({'BASE_URL': index_server.url}).translate('English text')


def test_bergamot_uses_stdin_and_preserves_spaces(tmp_path):
    model = tmp_path / 'model config.yml'
    model.write_text('models: []')
    binary = tmp_path / 'translator-cli'
    binary.write_text(
        f'#!{sys.executable}\n'
        'import sys\n'
        'assert "--model-config-paths" in sys.argv\n'
        'assert "--cpu-threads" in sys.argv\n'
        'assert "English text" == sys.stdin.read()\n'
        'print("日本語 API の訳文")\n', encoding='utf-8',
    )
    binary.chmod(0o755)
    translator = BergamotTranslator({'MODEL_CONFIG': str(model), 'BINARY': str(binary)})
    assert translator.translate('English text') == '日本語 API の訳文'


def test_bergamot_missing_model_fails_early(tmp_path):
    with pytest.raises(FileNotFoundError, match='model configuration'):
        BergamotTranslator({'MODEL_CONFIG': str(tmp_path / 'missing.yml')})


def test_bergamot_missing_binary_fails_early(tmp_path):
    model = tmp_path / 'config.yml'
    model.write_text('models: []')
    with pytest.raises(FileNotFoundError, match='executable'):
        BergamotTranslator({'MODEL_CONFIG': str(model), 'BINARY': str(tmp_path / 'missing')})


def test_bergamot_reports_engine_errors(tmp_path):
    model = tmp_path / 'config.yml'
    model.write_text('models: []')
    binary = tmp_path / 'translator-cli'
    binary.write_text(
        f'#!{sys.executable}\n'
        'import sys\n'
        'print("Incompatible model vocabulary", file=sys.stderr)\n'
        'sys.exit(2)\n',
    )
    binary.chmod(0o755)
    translator = BergamotTranslator({'MODEL_CONFIG': str(model), 'BINARY': str(binary)})
    with pytest.raises(RuntimeError, match='Incompatible model vocabulary'):
        translator.translate('English text')


def test_empty_local_input_does_not_call_engine(index_server, tmp_path):
    assert IndexTranslator({'BASE_URL': index_server.url}).translate('  ') == ''
    assert not index_server.requests


def make_pdf(path):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_textbox(
        fitz.Rect(60, 60, 500, 180),
        'This research paper explains a practical method for translating English documents into Japanese.',
        fontsize=12,
    )
    doc.save(path)
    doc.close()


def test_pdf_translation_uses_index_without_textra_or_nltk(tmp_path, monkeypatch, index_server):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'config.yml').write_text(yaml.safe_dump({
        'PRIORITY': 'INDEX', 'INDEX': {'BASE_URL': index_server.url},
    }))
    source = tmp_path / 'paper.pdf'
    make_pdf(source)
    connection = TextraConnection(str(source))
    connection.get_pdf_info()
    assert connection.print_pdf_info() == 1
    connection.post_page()
    connection.insert()
    with fitz.open(tmp_path / 'paper_new.pdf') as output:
        assert output.page_count == 1
        assert 'これは日本語の翻訳です。' in output[0].get_text().replace('\n', '')
    with fitz.open(source) as original:
        assert 'This research paper' in original[0].get_text()
    assert len(index_server.requests) == 1


def test_local_failure_does_not_create_successful_pdf(tmp_path, monkeypatch, index_server):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'config.yml').write_text(yaml.safe_dump({
        'PRIORITY': 'INDEX', 'INDEX': {'BASE_URL': index_server.url},
    }))
    source = tmp_path / 'paper.pdf'
    make_pdf(source)
    index_server.finish_reason = 'length'
    connection = TextraConnection(str(source), error_policy='strict')
    connection.get_pdf_info()
    with pytest.raises(RuntimeError, match='INDEX translation failed on page 1'):
        connection.post_page()
    assert not (tmp_path / 'paper_new.pdf').exists()


def test_google_initialization_does_not_require_textra(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'config.yml').write_text(yaml.safe_dump({
        'PRIORITY': 'GOOGLE', 'GOOGLE': {'URL': 'https://example.com'},
    }))
    source = tmp_path / 'paper.pdf'
    make_pdf(source)
    connection = TextraConnection(str(source))
    assert connection.local_translator is None
    assert not hasattr(connection, 'consumer')


def test_textra_initialization_remains_available(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'config.yml').write_text(yaml.safe_dump({
        'PRIORITY': 'TEXTRA', 'TEXTRA': {
            'NAME': 'test', 'KEY': 'test-key', 'SECRET': 'test-secret', 'URL': 'https://example.com',
        },
    }))
    source = tmp_path / 'paper.pdf'
    make_pdf(source)
    connection = TextraConnection(str(source))
    assert connection.params['name'] == 'test'
    assert connection.consumer is not None


def test_index_block_prompt_protects_literals_and_passes_heading_context(index_server):
    index_server.content = '設定方法はX0をご覧ください。'
    with TranslationService({'PRIORITY': 'INDEX', 'INDEX': {'BASE_URL': index_server.url}}) as service:
        result = service.translate_block(TranslationBlock('p1-b2', 'Read https://example.org/setup.',
                                                         context='Installation'))
    assert result.text == '設定方法はhttps://example.org/setupをご覧ください。'
    prompt = index_server.requests[0][1]['messages'][0]['content']
    assert 'X0' in prompt and 'Installation' in prompt and 'https://example.org/setup' not in prompt
