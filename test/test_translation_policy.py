import pymupdf as fitz
import pytest

from ner.paper_extract import PaperExtract
from ner.text_blocks import TranslationBlock, prepare_block
from ner.translation_policy import translation_decision
from ner.textra_api import TextraConnection
from ner.translation import BACKENDS


@pytest.mark.parametrize('text, expected', [
    ('Installation', False), ('Internationalization', False), ('Ready!', False),
    ('Bergamot', False), ('CPU GPU', False), ('state-of-the-art', False),
    ('1. Ready', False), ('• Ready', False), ('128 MB', False), ('3.14', False),
    ('https://example.org/a?x=1', False), ('www.example.org', False),
    ('example.org/path', False), ('mailto:help@example.org', False),
    ('help@example.org', False), ('report.pdf', False), ('--offline', False),
    ('translate_document', False), ('return x + y', False), ('value = 42', False),
    ('translator.translate(path)', False), ('APIを使って翻訳します', False),
    ('Getting started', True), ('Go now.', True), ('1. Keep links clickable', True),
    ('Read https://example.org/a?x=1', True), ('Run `translate_document(path)`.', True),
    ('Read www.example.org now.', True), ('Visit example.org/path now.', True),
    ('Ordinary English prose with numbers 123.', True),
])
def test_semantic_selection(text, expected):
    assert translation_decision(prepare_block(TranslationBlock('a', text))).translate == expected


@pytest.mark.parametrize('address', ['www.example.org/path', 'example.org/path',
                                      'ftp://example.org/file', 'mailto:help@example.org'])
def test_addresses_inside_prose_are_protected(address):
    prepared = prepare_block(TranslationBlock('a', 'Read ' + address + ' now.'))
    assert len(prepared.spans) == 1
    assert prepared.spans[0].original == address
    assert address not in prepared.text


def test_only_skipped_content_saves_unchanged_without_model_calls(tmp_path, monkeypatch):
    class NoRequests:
        def __init__(self, config):
            pass
        def translate(self, text):
            pytest.fail('Skipped content must not reach the translator')
        def close(self):
            pass
    monkeypatch.setitem(BACKENDS, 'INDEX', NoRequests)
    path = tmp_path / 'words.pdf'
    with fitz.open() as doc:
        page = doc.new_page()
        for y, text in enumerate(['Installation', 'Ready', 'https://example.org/path', '123']):
            page.insert_text((40, 50 + y * 50), text)
        page.insert_link({'kind': fitz.LINK_URI, 'from': page.search_for('https://example.org/path')[0],
                          'uri': 'https://example.org/path'})
        before = page.get_text('rawdict')
        pixels = page.get_pixmap().samples
        doc.save(path)
    with TextraConnection(path, config={'PRIORITY': 'INDEX'}) as connection:
        connection.get_pdf_info()
        assert connection.pe.pages_info == [[]]
        connection.post_page()
        output = connection.insert()
    with fitz.open(output) as doc:
        assert doc[0].get_text('rawdict') == before
        assert doc[0].get_pixmap().samples == pixels
        assert doc[0].get_links()[0]['uri'] == 'https://example.org/path'
