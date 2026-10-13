import pymupdf as fitz
import pytest

from ner.errors import PDFError
from ner.paper_extract import PaperExtract


def source_pdf(path, *, fontsize=12):
    with fitz.open() as doc:
        page = doc.new_page(width=600, height=500)
        page.insert_text((40, 60), 'This paragraph contains ordinary English prose.', fontsize=fontsize)
        doc.save(path)


def visible_spans(page):
    return [span for block in page.get_text('dict')['blocks'] if block['type'] == 0
            for line in block['lines'] for span in line['spans']]


def test_one_point_floor_preserves_full_dense_translation(tmp_path):
    source = tmp_path / 'dense.pdf'
    source_pdf(source)
    translation = '訳文' * 500
    with PaperExtract(source) as pdf:
        pdf.extract_textinfo()
        pdf.pages_info[0][0].update(bbox=(40, 40, 140, 70), fixed_layout=True)
        plan = pdf.prepare_page(0, [translation])
        pdf.fill_all_blocks()
        plan.write_text(pdf.doc[0])
        output = pdf.doc[0]
        assert ''.join(output.get_text().split()) == translation
        sizes = [span['size'] for span in visible_spans(output)]
        assert min(sizes) >= 1 - 1e-4 and max(sizes) < 2


def test_narrow_box_rejects_unbreakable_word_without_truncating(tmp_path):
    source = tmp_path / 'narrow.pdf'
    source_pdf(source)
    with PaperExtract(source) as pdf:
        pdf.extract_textinfo()
        pdf.pages_info[0][0].update(bbox=(40, 40, 50, 70), fixed_layout=True)
        before = pdf.doc[0].get_text('rawdict')
        with pytest.raises(PDFError, match='minimum font size'):
            pdf.prepare_page(0, ['W' * 200])
        assert pdf.doc[0].get_text('rawdict') == before


def test_plain_prose_uses_html_and_preserves_block_style(tmp_path, monkeypatch):
    source = tmp_path / 'styled.pdf'
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((40, 60), 'The styled paragraph preserves its block appearance.',
                         fontname='hebi', fontsize=12, color=(0.1, 0.3, 0.6))
        doc.save(source)
    def obsolete_writer(*args, **kwargs):
        pytest.fail('PDF paragraphs must use the shared HTML path')
    monkeypatch.setattr(fitz, 'TextWriter', obsolete_writer)
    with PaperExtract(source) as pdf:
        pdf.extract_textinfo()
        original = visible_spans(pdf.doc[0])[0]
        plan = pdf.prepare_page(0, ['Translated prose keeps its style.'])
        pdf.fill_all_blocks()
        plan.write_text(pdf.doc[0])
        spans = visible_spans(pdf.doc[0])
        assert ''.join(pdf.doc[0].get_text().split()) == 'Translatedprosekeepsitsstyle.'
        assert all(span['color'] == original['color'] for span in spans)
        assert all(span['flags'] & fitz.TEXT_FONT_BOLD for span in spans)
        assert all(span['flags'] & fitz.TEXT_FONT_ITALIC for span in spans)
        assert all(span['size'] <= original['size'] + 1e-4 for span in spans)


@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
def test_plain_prose_keeps_its_writing_direction(tmp_path, rotation):
    source = tmp_path / 'rotated.pdf'
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((300, 300), 'This paragraph explains document translation.', rotate=rotation)
        direction = page.get_text('dict')['blocks'][0]['lines'][0]['dir']
        doc.save(source)
    with PaperExtract(source) as pdf:
        pdf.extract_textinfo()
        plan = pdf.prepare_page(0, ['文章を翻訳します。'])
        pdf.fill_all_blocks()
        plan.write_text(pdf.doc[0])
        output_lines = [line for block in pdf.doc[0].get_text('dict')['blocks']
                        if block['type'] == 0 for line in block['lines']]
        assert ''.join(pdf.doc[0].get_text().split()) == '文章を翻訳します。'
        assert all(all(abs(a - b) < 1e-4 for a, b in zip(line['dir'], direction)) for line in output_lines)


def test_centered_heading_keeps_its_center(tmp_path):
    source = tmp_path / 'heading.pdf'
    with fitz.open() as doc:
        page = doc.new_page(width=600, height=500)
        page.insert_textbox((40, 40, 560, 95), 'Centered source heading', fontsize=22,
                            fontname='hebo', align=fitz.TEXT_ALIGN_CENTER)
        for y in (150, 200, 250):
            page.insert_text((40, y), 'Ordinary paragraphs establish the body font size.', fontsize=12)
        doc.save(source)
    with PaperExtract(source) as pdf:
        pdf.extract_textinfo()
        plan = pdf.prepare_page(0, ['中央の見出し', '本文です。', '本文です。', '本文です。'])
        pdf.fill_all_blocks()
        plan.write_text(pdf.doc[0])
        title = pdf.doc[0].search_for('中央の見出し')[0]
        assert abs((title.x0 + title.x1) / 2 - 300) < 1
