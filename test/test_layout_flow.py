from pathlib import Path

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
