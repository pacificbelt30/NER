from dataclasses import replace
import re

import pymupdf as fitz
import pytest

import api_server
from ner.errors import ConfigurationError, PDFError, TranslationCancelled, TranslationError
from ner.paper_extract import PaperExtract
from ner.textra_api import TextraConnection
from ner.text_blocks import (LiteralRange, TranslationBlock, canonical_text, model_text,
                             prepare_block, restore_text, split_block, validate_tokens)
from ner.translation import BACKENDS, TranslationService


class RecordingBackend:
    def __init__(self, config):
        self.calls = []
        self.replies = config.get('replies', [])
        self.closed = False

    def translate_chunk(self, text, **kwargs):
        self.calls.append((text, kwargs))
        return self.replies.pop(0) if self.replies else text

    def close(self):
        self.closed = True


@pytest.fixture
def recorded(monkeypatch):
    monkeypatch.setitem(BACKENDS, 'INDEX', RecordingBackend)


@pytest.mark.parametrize('text,literal', [
    ('Read https://example.org/local-models?lang=en.', 'https://example.org/local-models?lang=en'),
    ('Read https://example.org/path(foo).', 'https://example.org/path(foo)'),
    ('Read (https://example.org/path).', 'https://example.org/path'),
    ('Run `python -m package --help` safely.', '`python -m package --help`'),
    ('Open /home/user/document.pdf now.', '/home/user/document.pdf'),
    ('Contact support@example.org today.', 'support@example.org'),
    ('Connect to 127.0.0.1 with --offline.', '127.0.0.1'),
    ('Describe X0 and [[NER_0]] accurately.', '[[NER_0]]'),
    ('Call translate_document_without_network safely.', 'translate_document_without_network'),
])
def test_protected_literals_round_trip_exact_bytes(text, literal):
    prepared = prepare_block(TranslationBlock('example', text))
    assert literal in [span.original for span in prepared.spans]
    canonical = canonical_text(model_text(prepared.text))
    validate_tokens(canonical, tuple(span.token for span in prepared.spans), prepared.text)
    assert restore_text(canonical, prepared.spans) == text


def test_pdf_ranges_take_precedence_and_preserve_inline_spaces():
    block = TranslationBlock('example', 'Run python  -m package at https://example.org.',
                             protected_ranges=(LiteralRange(4, 22),))
    prepared = prepare_block(block)
    assert prepared.spans[0].original == 'python  -m package'
    assert prepared.spans[1].kind == 'url'
    assert restore_text(prepared.text, prepared.spans) == block.text


@pytest.mark.parametrize('ranges', [
    (LiteralRange(-1, 1),), (LiteralRange(0, 100),),
    (LiteralRange(0, 3), LiteralRange(2, 5)),
])
def test_invalid_pdf_literal_ranges_fail(ranges):
    with pytest.raises(TranslationError, match='Invalid or overlapping'):
        prepare_block(TranslationBlock('a', 'abcde', protected_ranges=ranges))


@pytest.mark.parametrize('target', ['[[NER_0]]', '[[NER_0]] [[NER_0]] [[NER_1]]',
                                    '[[NER_0]] [[NER_1]] [[NER_2]]',
                                    '[[NER_0]].html [[NER_1]]'])
def test_missing_duplicate_unknown_and_extended_tokens_are_rejected(target):
    with pytest.raises(TranslationError, match='Protected token mismatch'):
        validate_tokens(target, ('[[NER_0]]', '[[NER_1]]'), 'Run [[NER_0]] at [[NER_1]].')


def test_token_order_can_follow_japanese_grammar():
    source = 'Run [[NER_0]] at [[NER_1]].'
    validate_tokens('[[NER_1]]で[[NER_0]]を実行。', ('[[NER_0]]', '[[NER_1]]'), source)


@pytest.mark.parametrize('text,limit', [
    ('First sentence. Second sentence. Third sentence.', 20),
    ('alpha beta gamma delta epsilon zeta', 12),
    ('A long condition; but another condition, however the final condition.', 30),
    ('e\u0301' * 20, 5), ('🇯🇵' * 10, 3), ('👨‍👩‍👧‍👦' * 4, 9),
    ('  first sentence.\n\n second sentence.  ', 18),
    ('Read https://example.org/very-long-address?keep=exact then continue.', 18),
])
def test_chunks_rejoin_source_and_never_split_tokens_or_graphemes(text, limit):
    prepared = prepare_block(TranslationBlock('a', text))
    chunks = split_block(prepared, limit)
    assert ''.join(chunk.leading + chunk.text + chunk.trailing for chunk in chunks) == prepared.text
    assert all(len(chunk.text) <= limit for chunk in chunks)
    for chunk in chunks:
        if 'NER' in chunk.text:
            assert '[[NER_0]]' in chunk.text
        if '👨' in chunk.text:
            assert re.fullmatch(r'(?:👨‍👩‍👧‍👦)+', chunk.text)
        if '🇯' in chunk.text:
            assert re.fullmatch(r'(?:🇯🇵)+', chunk.text)
        if '\u0301' in chunk.text:
            assert re.fullmatch(r'(?:e\u0301)+', chunk.text)


def test_sentence_boundary_preferred_over_more_words():
    chunks = split_block(prepare_block(TranslationBlock('a', 'One sentence. Words that follow it.')), 25)
    assert chunks[0].text == 'One sentence.'


@pytest.mark.parametrize('text,limit,max_chunks', [('👨‍👩‍👧‍👦', 3, 128),
                                                 ('https://example.org', 5, 128),
                                                 ('one two three four five six', 5, 2)])
def test_unrepresentable_or_excessively_split_input_fails(text, limit, max_chunks):
    with pytest.raises(TranslationError):
        split_block(prepare_block(TranslationBlock('a', text)), limit, max_chunks)


def test_block_cache_distinguishes_protected_originals_and_context(recorded):
    first = TranslationBlock('a', 'Read https://one.example/', context='Installation')
    with TranslationService({'PRIORITY': 'INDEX'}) as service:
        result = service.translate_block(first)
        second = service.translate_block(replace(first, id='b'))
        assert second.id == 'b' and second.text == first.text
        assert result.spans and service.backend.calls[0][1]['protected_tokens'] == ('X0',)
        service.translate_block(replace(first, text='Read https://two.example/'))
        service.translate_block(replace(first, context='Troubleshooting'))
        assert len(service.backend.calls) == 3


def test_long_block_reports_progress_and_keeps_heading_context(recorded):
    block = TranslationBlock('p1-b2', 'First complete sentence. Second complete sentence. Third complete sentence.',
                             context='Installation')
    progress = []
    with TranslationService({'PRIORITY': 'INDEX', 'INDEX': {'CHUNK_CHARS': 30}}) as service:
        result = service.translate_block(block, lambda *event: progress.append(event))
        assert result.text == block.text
        assert result.chunk_count == 3
        assert progress == [(0, 3), (1, 3), (2, 3), (3, 3)]
        assert all(kwargs['context'] == 'Installation' for _, kwargs in service.backend.calls)


def test_invalid_last_chunk_never_populates_block_cache(recorded):
    with TranslationService({'PRIORITY': 'INDEX', 'INDEX': {'CHUNK_CHARS': 20,
                             'replies': ['最初の文。', '保護IDが消えた訳文']}}) as service:
        with pytest.raises(TranslationError, match=r'chunk 2/2.*Protected token mismatch'):
            service.translate_block(TranslationBlock('a', 'First sentence. Read https://example.org/'))
        assert not service.cache


def test_cancel_between_chunks_prevents_next_request_and_cache(recorded):
    def progress(done, total):
        if done == 1:
            raise TranslationCancelled('Stopped')
    with TranslationService({'PRIORITY': 'INDEX', 'INDEX': {'CHUNK_CHARS': 20}}) as service:
        with pytest.raises(TranslationCancelled):
            service.translate_block(TranslationBlock('a', 'First sentence. Second sentence.'), progress)
        assert len(service.backend.calls) == 1 and not service.cache


@pytest.mark.parametrize('config', [{'MAX_CHUNKS': 0}, {'MAX_CHUNKS': True},
                                    {'INDEX': {'CHUNK_CHARS': float('nan')}},
                                    {'INDEX': {'CHUNK_CHARS': -1}}])
def test_invalid_chunk_configuration_fails_early(recorded, config):
    with pytest.raises(ConfigurationError):
        TranslationService({'PRIORITY': 'INDEX', **config})


def make_protected_pdf(path):
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((40, 45), 'Installation', fontsize=22, fontname='hebo')
        page.insert_text((40, 100), 'Call ', fontsize=10)
        page.insert_text((60, 100), 'translate_document', fontsize=10, fontname='cour')
        page.insert_text((168, 100), ' to process private documents.', fontsize=10)
        page.insert_text((40, 150), ['Read the documentation before configuring your server.',
                                   'https://example.org/translation/local-models?lang=en'], fontsize=10)
        box = page.search_for('https://example.org/translation/local-models?lang=en')[0]
        page.insert_link({'kind': fitz.LINK_URI, 'uri': 'https://example.org/translation/local-models?lang=en',
                          'from': box})
        page.insert_text((40, 220), ['def translate_document(document_path):',
                                   '    return translator.translate(document_path)'], fontsize=10, fontname='cour')
        page.insert_text((40, 280), ['1. Keep links clickable', '2. Preserve code indentation'], fontsize=10)
        doc.save(path)


def test_pdf_preserves_code_and_places_literal_links_in_translated_flow(tmp_path, recorded):
    path = tmp_path / 'source.pdf'
    make_protected_pdf(path)
    with fitz.open(path) as source:
        before_code = source[0].get_text('rawdict', clip=fitz.Rect(35, 200, 500, 250))
    with TextraConnection(path, config={'PRIORITY': 'INDEX'}) as connection:
        connection.get_pdf_info()
        blocks = connection.pe.pages_info[0]
        assert any(block['txt'] == 'Installation' and block['role'] == 'heading' for block in blocks)
        assert len([block for block in blocks if block['role'] == 'list']) == 2
        assert not any('def translate_document' in block['txt'] for block in blocks)
        assert next(block for block in blocks if 'Call ' in block['txt'])['context'] == 'Installation'
        assert all(block['lines'][0]['spans'] for block in blocks)
        connection.post_page()
        result = connection.insert()
        calls = connection.service.backend.calls
        assert any('X0' in text for text, _ in calls)
        assert not any('https://' in text or 'def translate_document' in text for text, _ in calls)
    with fitz.open(result) as output:
        page = output[0]
        assert page.get_text('rawdict', clip=fitz.Rect(35, 200, 500, 250)) == before_code
        assert 'translate_document' in page.get_text()
        links = page.get_links()
        assert len(links) == 1
        assert links[0]['uri'] == 'https://example.org/translation/local-models?lang=en'
        assert 'https://' in page.get_textbox(links[0]['from'])


def test_protected_output_error_leaves_original_and_existing_output_untouched(tmp_path, recorded):
    source = tmp_path / 'source.pdf'
    make_protected_pdf(source)
    output = tmp_path / 'source_new.pdf'
    output.write_bytes(b'existing artifact')
    source_bytes = source.read_bytes()
    with TextraConnection(source, config={'PRIORITY': 'INDEX', 'INDEX': {
        'replies': ['導入', '関数名が消えた訳文']}}) as connection:
        connection.get_pdf_info()
        with pytest.raises(TranslationError, match='Protected token mismatch'):
            connection.post_page()
        assert connection.page_array is None and connection.block_results is None
        with pytest.raises(TranslationError, match='Translate every page'):
            connection.insert()
    assert source.read_bytes() == source_bytes and output.read_bytes() == b'existing artifact'




def test_single_line_break_does_not_merge_english_words(tmp_path):
    path = tmp_path / 'source.pdf'
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_textbox((40, 40, 500, 120), 'Original English paragraph for translation.', fontsize=12)
        doc.save(path)
    with PaperExtract(path) as pdf:
        pdf.extract_textinfo()
        writer = pdf.prepare_page(0, ['two\nwords'])
        pdf.fill_all_blocks()
        writer.write_text(pdf.doc[0])
        assert 'two words' in pdf.doc[0].get_text()


def test_monospace_prose_is_not_misclassified_as_code(tmp_path):
    path = tmp_path / 'prose.pdf'
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((40, 50), 'This is ordinary English prose in a typewriter font.', fontname='cour')
        doc.save(path)
    with PaperExtract(path) as pdf:
        pdf.extract_textinfo()
        assert len(pdf.pages_info[0]) == 1
        assert not pdf.pages_info[0][0]['protected_ranges']


@pytest.mark.parametrize('obstacle', [None, 'vector', 'image'])
def test_protected_inline_growth_only_uses_free_space(tmp_path, recorded, obstacle):
    path = tmp_path / 'inline.pdf'
    with fitz.open() as doc:
        page = doc.new_page(width=720, height=760)
        page.insert_text((40, 420), 'Call ', fontsize=10)
        page.insert_text((60, 420), 'translate_document_without_network', fontname='cour', fontsize=10)
        page.insert_text((264, 420), ' to translate private documents safely.', fontsize=10)
        right = page.get_text('dict')['blocks'][0]['bbox'][2]
        if obstacle == 'vector':
            page.draw_line((right + 4, 405), (right + 4, 425))
        elif obstacle == 'image':
            pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 8, 20), False)
            pix.clear_with(120)
            page.insert_image((right + 4, 405, right + 12, 425), pixmap=pix)
        doc.save(path)
    config = {'PRIORITY': 'INDEX', 'INDEX': {'replies': [
        'プライベートドキュメントを安全に翻訳するためにX0に電話して。']}}
    with TextraConnection(path, config=config) as connection:
        connection.get_pdf_info()
        connection.post_page()
        if obstacle:
            with pytest.raises(PDFError, match='does not fit'):
                connection.insert()
            assert 'Call ' in connection.pe.doc[0].get_text()
            assert not (tmp_path / 'inline_new.pdf').exists()
        else:
            plan = connection.pe.prepare_page(0, connection.page_array[0], connection.block_results[0])
            assert right < plan.rich[0].rect[2] <= right + 36
            assert connection.insert()


def test_one_url_using_multiple_pdf_fonts_stays_one_literal():
    text = 'Read https://example.org/local-models now.'
    start = text.index('local')
    block = TranslationBlock('a', text, protected_ranges=(LiteralRange(start, start + 5),))
    prepared = prepare_block(block)
    assert len(prepared.spans) == 1
    assert prepared.spans[0].original == 'https://example.org/local-models'


def test_heading_context_does_not_leak_protected_literals_into_model_input(recorded):
    with TranslationService({'PRIORITY': 'INDEX'}) as service:
        service.translate_block(TranslationBlock('a', 'Read the documentation.',
                                context='Install https://example.org/private with --offline'))
        context = service.backend.calls[0][1]['context']
        assert 'https://' not in context and '--offline' not in context
        assert '[protected literal]' in context


def test_pdf_chunk_cancellation_keeps_its_exception_type(tmp_path, recorded):
    source = tmp_path / 'source.pdf'
    make_protected_pdf(source)
    def stop(block_id, completed, total):
        if completed == 1:
            raise TranslationCancelled('Stopped between chunks')
    with TextraConnection(source, config={'PRIORITY': 'INDEX'}) as connection:
        connection.get_pdf_info()
        with pytest.raises(TranslationCancelled):
            connection.post_page(on_chunk_progress=stop)
        assert connection.page_array is None and connection.block_results is None
