import json

import pymupdf as fitz
import pytest

from ner.block_layout import PreparedPage
from ner.combine_pdfs import CombinePDF
from ner.errors import ConfigurationError, TranslationCancelled, TranslationError
from ner.paper_extract import PaperExtract
from ner.pdf_warnings import REPORT_PREFIX
from ner.textra_api import TextraConnection
from ner.translation import BACKENDS


class Backend:
    def __init__(self, config):
        self.mode = config.get('mode', 'ok')
        self.calls = []

    def translate_chunk(self, text, **kwargs):
        self.calls.append(text)
        if 'Fail this' in text:
            raise TranslationError('backend private-token=must-not-leak')
        if self.mode == 'corrupt' and 'X0' in text:
            return '保護対象が消えた不正な訳文'
        if self.mode == 'oversize':
            return '長い訳文' * 6000
        return '正常に翻訳された文章です。'

    def close(self):
        pass


@pytest.fixture
def backend(monkeypatch):
    monkeypatch.setitem(BACKENDS, 'INDEX', Backend)


def make_pdf(path, texts=('Translate this first paragraph.', 'Fail this second paragraph.'), pages=1):
    with fitz.open() as doc:
        for _ in range(pages):
            page = doc.new_page(width=600, height=500)
            for index, text in enumerate(texts):
                page.insert_text((40, 80 + index * 120), text, fontsize=12)
        doc.save(path)


def report(doc):
    name = next(name for name in doc.embfile_names() if name.startswith(REPORT_PREFIX))
    return json.loads(doc.embfile_get(name))


def test_translation_failure_keeps_original_and_writes_visible_report(tmp_path, backend):
    source = tmp_path / 'mixed.pdf'
    make_pdf(source)
    before = source.read_bytes()
    with TextraConnection(source, config={'PRIORITY': 'INDEX'}) as job:
        job.get_pdf_info()
        failed = job.pe.pages_info[0][1]
        with fitz.open(source) as original:
            preserved = original[0].get_text('rawdict', clip=failed['bbox'])
        job.post_page()
        output = job.insert()
        assert job.result_summary['outcome'] == 'partial'
        assert job.result_summary['translated_blocks'] == job.result_summary['retained_blocks'] == 1
    with fitz.open(output) as doc:
        assert doc.page_count > 1
        assert doc[0].get_text('rawdict', clip=failed['bbox']) == preserved
        assert '正常に翻訳された文章です。' in ''.join(doc[0].get_text().split())
        assert len(list(doc[0].annots())) == 2
        assert '翻訳エラーレポート' in ''.join(doc[1].get_text().split())
        info = report(doc)
        assert info['summary']['report_pages'] == doc.page_count - 1
        assert info['warnings'][0]['translation'] is None
        assert 'must-not-leak' not in str(info)
    assert source.read_bytes() == before


def test_invalid_literal_translation_is_never_embedded(tmp_path, backend):
    source = tmp_path / 'protected.pdf'
    make_pdf(source, ('Read https://example.org/help now.',))
    with TextraConnection(source, config={'PRIORITY': 'INDEX', 'INDEX': {'mode': 'corrupt'}}) as job:
        job.get_pdf_info()
        job.post_page()
        output = job.insert()
        assert job.result_summary['outcome'] == 'untranslated'
    with fitz.open(output) as doc:
        text = ''.join(page.get_text() for page in doc)
        assert 'Read https://example.org/help now.' in text
        assert '保護対象が消えた不正な訳文' not in text
        assert report(doc)['warnings'][0]['code'] == 'PROTECTED_CONTENT_INVALID'


def test_layout_failure_appends_full_translation_and_keeps_source(tmp_path, backend):
    source = tmp_path / 'oversize.pdf'
    make_pdf(source, ('Translate this original paragraph.',))
    with TextraConnection(source, config={'PRIORITY': 'INDEX', 'INDEX': {'mode': 'oversize'}}) as job:
        job.get_pdf_info()
        job.pe.pages_info[0][0]['fixed_layout'] = True
        job.post_page()
        translated = job.page_array[0][0]
        output = job.insert()
        assert job.result_summary['retained_blocks'] == 1
    with fitz.open(output) as doc:
        assert 'Translate this original paragraph.' in doc[0].get_text()
        info = report(doc)
        assert info['warnings'][0]['translation'] == translated
        assert info['warnings'][0]['code'] == 'LAYOUT_FAILED'
        appendix = ''.join(''.join(page.get_text(clip=(25, 45, 570, 805)).split())
                           for page in list(doc)[1:])
        assert appendix.count('長い訳文') == 6000


def test_unexpected_write_failure_rolls_back_page_and_continues(tmp_path, backend, monkeypatch):
    source = tmp_path / 'render.pdf'
    make_pdf(source, ('Translate this paragraph safely.',), pages=2)
    actual = PreparedPage.write_text
    def fail_first(self, page, **kwargs):
        if page.number == 0:
            page.insert_text((10, 20), 'PARTIAL WRITE MUST DISAPPEAR')
            raise RuntimeError('unexpected render failure')
        return actual(self, page, **kwargs)
    monkeypatch.setattr(PreparedPage, 'write_text', fail_first)
    with TextraConnection(source, config={'PRIORITY': 'INDEX'}) as job:
        job.get_pdf_info()
        job.post_page()
        output = job.insert()
        assert job.result_summary['outcome'] == 'partial'
    with fitz.open(output) as doc:
        assert 'Translate this paragraph safely.' in doc[0].get_text()
        assert 'PARTIAL WRITE' not in doc[0].get_text()
        assert '正常に翻訳された文章です。' in ''.join(doc[1].get_text().split())
        assert report(doc)['warnings'][0]['code'] == 'PAGE_RENDER_FAILED'


def test_page_preparation_failure_preserves_page_and_continues(tmp_path, backend, monkeypatch):
    source = tmp_path / 'prepare.pdf'
    make_pdf(source, ('Translate this paragraph safely.',), pages=2)
    actual = PaperExtract.prepare_page
    def fail_first(self, page_num, *args, **kwargs):
        if page_num == 0:
            raise RuntimeError('page obstacle inspection failed')
        return actual(self, page_num, *args, **kwargs)
    monkeypatch.setattr(PaperExtract, 'prepare_page', fail_first)
    with TextraConnection(source, config={'PRIORITY': 'INDEX'}) as job:
        job.get_pdf_info()
        job.post_page()
        output = job.insert()
        assert job.result_summary['outcome'] == 'partial'
    with fitz.open(output) as doc:
        assert 'Translate this paragraph safely.' in doc[0].get_text()
        assert '正常に翻訳された文章です。' in ''.join(doc[1].get_text().split())
        assert report(doc)['warnings'][0]['code'] == 'PAGE_RENDER_FAILED'


def test_backend_initialization_failure_still_produces_pdf(tmp_path, monkeypatch):
    source = tmp_path / 'no-model.pdf'
    make_pdf(source, ('Translate this original paragraph.',))
    class Broken:
        def __init__(self, config):
            raise ConfigurationError('model not available secret=private')
    monkeypatch.setitem(BACKENDS, 'INDEX', Broken)
    with TextraConnection(source, config={'PRIORITY': 'INDEX'}) as job:
        job.get_pdf_info()
        job.post_page()
        output = job.insert()
        assert job.result_summary['outcome'] == 'untranslated'
    with fitz.open(output) as doc:
        assert 'Translate this original paragraph.' in doc[0].get_text()
        assert report(doc)['warnings'][0]['code'] == 'BACKEND_NOT_CONFIGURED'


def test_scanned_or_blank_pdf_gets_ocr_notice(tmp_path, backend):
    source = tmp_path / 'scan.pdf'
    with fitz.open() as doc:
        page = doc.new_page()
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 16, 16), False)
        pix.clear_with(90)
        page.insert_image((40, 40, 400, 400), pixmap=pix)
        doc.save(source)
    with TextraConnection(source, config={'PRIORITY': 'INDEX'}) as job:
        job.get_pdf_info()
        job.post_page()
        assert not job.service.backend.calls
        output = job.insert()
    with fitz.open(output) as doc, fitz.open(source) as original:
        assert doc[0].get_pixmap(clip=(40, 40, 400, 400), annots=False).samples == original[0].get_pixmap(clip=(40, 40, 400, 400)).samples
        assert report(doc)['warnings'][0]['code'] == 'OCR_REQUIRED'


def test_warning_appendix_can_be_added_to_comparison_pdf(tmp_path, backend):
    source = tmp_path / 'input.pdf'
    make_pdf(source)
    with TextraConnection(source, config={'PRIORITY': 'INDEX'}) as job:
        job.get_pdf_info()
        job.post_page()
        translated = job.insert()
    combined = CombinePDF(source, translated, tmp_path / 'comparison.pdf').combine()
    with fitz.open(combined) as doc, fitz.open(translated) as translated_doc:
        assert doc.page_count == translated_doc.page_count + 1
        assert '翻訳エラーレポート' in ''.join(doc[2].get_text().split())


def test_pending_original_redactions_are_not_applied(tmp_path, backend):
    source = tmp_path / 'pending.pdf'
    make_pdf(source, ('Translate this paragraph safely.',))
    with fitz.open(source) as doc:
        doc[0].add_redact_annot(doc[0].search_for('paragraph')[0])
        doc.save(tmp_path / 'annotated.pdf')
    with TextraConnection(tmp_path / 'annotated.pdf', config={'PRIORITY': 'INDEX'}) as job:
        job.get_pdf_info()
        job.post_page()
        output = job.insert()
    with fitz.open(output) as doc:
        assert 'Translate this paragraph safely.' in doc[0].get_text()


def test_cancel_during_pdf_output_is_not_recovery(tmp_path, backend):
    source = tmp_path / 'cancel.pdf'
    make_pdf(source, ('Translate this paragraph safely.',))
    with TextraConnection(source, config={'PRIORITY': 'INDEX'}) as job:
        job.get_pdf_info()
        job.post_page()
        def stop():
            raise TranslationCancelled('User stopped')
        with pytest.raises(TranslationCancelled):
            job.insert(check_cancel=stop)
    assert not (tmp_path / 'cancel_new.pdf').exists()


def test_save_failure_leaves_existing_output_intact(tmp_path, backend, monkeypatch):
    source = tmp_path / 'save.pdf'
    make_pdf(source)
    target = tmp_path / 'save_new.pdf'
    target.write_bytes(b'existing result')
    def fail(doc, *args, **kwargs):
        raise OSError('disk full')
    monkeypatch.setattr(fitz.Document, 'save', fail)
    with TextraConnection(source, config={'PRIORITY': 'INDEX'}) as job:
        job.get_pdf_info()
        job.post_page()
        with pytest.raises(OSError):
            job.insert()
    assert target.read_bytes() == b'existing result'


def test_invalid_custom_font_falls_back_with_notice(tmp_path, backend):
    source = tmp_path / 'font.pdf'
    make_pdf(source, ('Translate this paragraph safely.',))
    with TextraConnection(source, config={'PRIORITY': 'INDEX', 'FONT': str(tmp_path / 'missing.ttf')}) as job:
        job.get_pdf_info()
        job.post_page()
        output = job.insert()
        assert job.result_summary['outcome'] == 'complete_with_warnings'
        assert job.result_summary['translated_blocks'] == 1
    with fitz.open(output) as doc:
        assert report(doc)['warnings'][0]['code'] == 'FONT_FALLBACK'


def test_extraction_failure_still_preserves_original_pdf(tmp_path, backend, monkeypatch):
    source = tmp_path / 'extract.pdf'
    make_pdf(source)
    def fail(self):
        raise RuntimeError('private extraction details')
    monkeypatch.setattr(PaperExtract, 'extract_textinfo', fail)
    with TextraConnection(source, config={'PRIORITY': 'INDEX'}) as job:
        job.get_pdf_info()
        job.post_page()
        output = job.insert()
        assert job.result_summary['outcome'] == 'untranslated'
    with fitz.open(output) as doc, fitz.open(source) as original:
        assert doc[0].get_text('rawdict') == original[0].get_text('rawdict')
        assert report(doc)['warnings'][0]['code'] == 'EXTRACTION_FAILED'


def test_no_eligible_text_does_not_need_backend_or_add_appendix(tmp_path, monkeypatch):
    source = tmp_path / 'literal.pdf'
    make_pdf(source, ('https://example.org/help', '12345'))
    class Broken:
        def __init__(self, config):
            raise ConfigurationError('missing backend')
    monkeypatch.setitem(BACKENDS, 'INDEX', Broken)
    with TextraConnection(source, config={'PRIORITY': 'INDEX'}) as job:
        job.get_pdf_info()
        job.post_page()
        output = job.insert()
        assert job.result_summary['outcome'] == 'complete'
    with fitz.open(output) as doc, fitz.open(source) as original:
        assert len(doc) == 1 and not doc.embfile_names()
        assert doc[0].get_text('rawdict') == original[0].get_text('rawdict')


def test_existing_report_attachment_is_not_overwritten(tmp_path, backend):
    source = tmp_path / 'attachment.pdf'
    make_pdf(source)
    with fitz.open(source) as doc:
        doc.embfile_add(REPORT_PREFIX + '.json', b'original attached data')
        payload = doc.tobytes()
    source.write_bytes(payload)
    with TextraConnection(source, config={'PRIORITY': 'INDEX'}) as job:
        job.get_pdf_info()
        job.post_page()
        output = job.insert()
    with fitz.open(output) as doc:
        assert doc.embfile_get(REPORT_PREFIX + '.json') == b'original attached data'
        assert json.loads(doc.embfile_get(REPORT_PREFIX + '-2.json'))['schema'] == 1

