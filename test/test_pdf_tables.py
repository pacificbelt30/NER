from dataclasses import replace
from pathlib import Path

import pymupdf as fitz
import pytest

from ner.errors import PDFError
from ner.paper_extract import PaperExtract
from ner.textra_api import TextraConnection
from ner.translation import BACKENDS
from scripts.evaluate_pdf_tables import DeterministicBackend, borderless_fixture, evaluate, fixture


@pytest.mark.parametrize('borderless', [False, True])
def test_cells_translate_independently_and_preserve_other_cells(tmp_path, monkeypatch, borderless):
    monkeypatch.setitem(BACKENDS, 'INDEX', DeterministicBackend)
    path = tmp_path / 'table.pdf'
    cells = (borderless_fixture if borderless else fixture)(path)
    original = path.read_bytes()
    with TextraConnection(path, config={'PRIORITY': 'INDEX'}) as connection:
        connection.get_pdf_info()
        blocks = connection.pe.pages_info[0]
        assert len(blocks) == sum(cell['translate'] for cell in cells)
        assert all(block['role'] == 'table_cell' for block in blocks)
        assert len({block['id'] for block in blocks}) == len(blocks)
        assert all(fitz.Rect(block['table_cell']['bbox']).contains(block['bbox']) for block in blocks)
        assert all(block['context'] == '' for block in blocks)
        connection.post_page()
        assert all(not any(word in call for word in ('Engine', 'Bergamot', 'Offline', '42', 'https://'))
                   for call in connection.service.backend.calls)
        output = connection.insert()
    metrics = evaluate(path, Path(output), cells, borderless=borderless)
    assert metrics['translated_cells_correct'] == metrics['translated_cells_total']
    assert metrics['skipped_cells_exact'] == metrics['skipped_cells_total']
    assert metrics['grid_and_background_probes_exact'] == metrics['grid_and_background_probes_total']
    if not borderless:
        assert metrics['uri_on_visible_address']
    assert path.read_bytes() == original
    with fitz.open(output) as doc:
        assert all(span['size'] >= 8 - 0.01 for block in doc[0].get_text('dict')['blocks']
                   if block['type'] == 0 for line in block['lines'] for span in line['spans'])


def test_table_overflow_fails_before_redaction_or_replacing_output(tmp_path, monkeypatch):
    monkeypatch.setitem(BACKENDS, 'INDEX', DeterministicBackend)
    source = tmp_path / 'table.pdf'
    fixture(source)
    output = tmp_path / 'table_new.pdf'
    output.write_bytes(b'previous artifact')
    with TextraConnection(source, config={'PRIORITY': 'INDEX'}) as connection:
        connection.get_pdf_info()
        before = connection.pe.doc[0].get_text('rawdict')
        connection.post_page()
        oversized = '長い翻訳文' * 500
        connection.page_array[0][0] = oversized
        connection.block_results[0][0] = replace(connection.block_results[0][0], text=oversized,
                                                canonical_text=oversized)
        with pytest.raises(PDFError, match='does not fit'):
            connection.insert()
        assert connection.pe.doc[0].get_text('rawdict') == before
    assert output.read_bytes() == b'previous artifact'


def test_merged_cell_is_one_translation_unit(tmp_path):
    source = tmp_path / 'table.pdf'
    fixture(source)
    with PaperExtract(source) as pdf:
        pdf.extract_textinfo()
        merged = next(block for block in pdf.pages_info[0] if block['txt'].startswith('Keep the original'))
        assert merged['table_cell']['bbox'] == (40, 220, 450, 270)
        multiline = next(block for block in pdf.pages_info[0] if block['txt'].startswith('Use a local'))
        assert len(multiline['lines']) == 2


def test_unstructured_table_placement_also_enforces_readable_floor(tmp_path):
    source = tmp_path / 'table.pdf'
    fixture(source)
    with PaperExtract(source) as pdf:
        pdf.extract_textinfo()
        texts = [block['txt'] for block in pdf.pages_info[0]]
        texts[0] = '長い文章' * 500
        with pytest.raises(PDFError, match='does not fit'):
            pdf.prepare_page(0, texts)


def test_tightly_spaced_two_column_prose_is_not_a_borderless_table(tmp_path):
    source = tmp_path / 'columns.pdf'
    with fitz.open() as doc:
        page = doc.new_page()
        for x in (40, 320):
            page.insert_text((x, 50), ['These short lines belong', 'to a prose paragraph.',
                                     'Keep this paragraph together.'], fontsize=11)
        doc.save(source)
    with PaperExtract(source) as pdf:
        pdf.extract_textinfo()
        assert len(pdf.pages_info[0]) == 2
        assert all(not block['table_cell'] for block in pdf.pages_info[0])


def test_table_detection_failure_is_explicit(tmp_path, monkeypatch):
    source = tmp_path / 'table.pdf'
    fixture(source)
    def fail(*args, **kwargs):
        raise RuntimeError('broken geometry')
    monkeypatch.setattr(fitz.Page, 'find_tables', fail)
    with PaperExtract(source) as pdf:
        with pytest.raises(PDFError, match='table detection failed'):
            pdf.extract_textinfo()


def test_uncertain_two_row_borderless_labels_are_not_merged(tmp_path):
    source = tmp_path / 'labels.pdf'
    with fitz.open() as doc:
        page = doc.new_page()
        for x, y, text in [(40, 50, 'Engine'), (250, 50, 'Status'),
                           (40, 90, 'Bergamot'), (250, 90, 'Ready')]:
            page.insert_text((x, y), text)
        doc.save(source)
    with PaperExtract(source) as pdf:
        pdf.extract_textinfo()
        assert pdf.pages_info == [[]]
        assert len(pdf.selection_decisions) == 4


@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
def test_table_cell_plans_respect_page_rotation(tmp_path, monkeypatch, rotation):
    monkeypatch.setitem(BACKENDS, 'INDEX', DeterministicBackend)
    source = tmp_path / 'table.pdf'
    cells = fixture(source)
    if rotation:
        with fitz.open(source) as doc:
            doc[0].set_rotation(rotation)
            doc.save(tmp_path / 'rotated.pdf')
        source = tmp_path / 'rotated.pdf'
    with TextraConnection(source, config={'PRIORITY': 'INDEX'}) as connection:
        connection.get_pdf_info()
        connection.post_page()
        output = connection.insert()
    metrics = evaluate(source, Path(output), cells)
    assert metrics['translated_cells_correct'] == 5
    assert metrics['skipped_cells_exact'] == 6
    with fitz.open(output) as doc:
        assert doc[0].rotation == rotation
