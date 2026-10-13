import pymupdf as fitz
import pytest

from ner.errors import PDFError
from ner.paper_extract import PaperExtract
from ner.layout_space import PageLayoutSpace


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


def test_available_space_is_used_before_shrinking(tmp_path):
    source = tmp_path / 'grow.pdf'
    source_pdf(source)
    translation = '訳文を元の文字サイズで折り返して配置します。' * 2
    with PaperExtract(source) as pdf:
        pdf.extract_textinfo()
        block = pdf.pages_info[0][0]
        original = fitz.Rect(block['bbox'])
        plan = pdf.prepare_page(0, [translation])
        placed = plan.rich[0]
        assert placed.scale == 1
        assert fitz.Rect(placed.rect).contains(original) and placed.rect[3] > original.y1
        pdf.fill_all_blocks()
        plan.write_text(pdf.doc[0])
        assert ''.join(pdf.doc[0].get_text().split()) == translation
        assert all(abs(span['size'] - 12) < 1e-4 for span in visible_spans(pdf.doc[0]))
        assert all(fitz.Rect(placed.rect).contains(span['bbox']) for span in visible_spans(pdf.doc[0]))


@pytest.mark.parametrize('kind', ['text', 'image', 'vector'])
def test_expansion_keeps_neighbor_content_and_pixels_unchanged(tmp_path, kind):
    source = tmp_path / 'obstacle.pdf'
    source_pdf(source)
    with fitz.open(source) as doc:
        page = doc[0]
        base = fitz.Rect(visible_spans(page)[0]['bbox'])
        obstacle = fitz.Rect(base.x0, base.y1 + 12, base.x1 + 30, base.y1 + 32)
        if kind == 'text':
            page.insert_text((obstacle.x0, obstacle.y0 + 12), 'Anchor', fontsize=11)
            obstacle = page.search_for('Anchor')[0]
        elif kind == 'image':
            pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 8, 8), False)
            pix.clear_with(110)
            page.insert_image(obstacle, pixmap=pix, keep_proportion=False)
        else:
            page.draw_rect(obstacle, color=(0, 0.3, 0.6), fill=(0.6, 0.8, 1), width=1)
        pixels = page.get_pixmap(clip=obstacle).samples
        doc.save(tmp_path / 'with-obstacle.pdf')
    translation = '訳文' * 45
    with PaperExtract(tmp_path / 'with-obstacle.pdf') as pdf:
        pdf.extract_textinfo()
        plan = pdf.prepare_page(0, [translation])
        box = fitz.Rect(plan.rich[0].rect)
        assert box.y1 <= obstacle.y0 - 2 + 1e-3
        assert not box.intersects(obstacle)
        pdf.fill_all_blocks()
        plan.write_text(pdf.doc[0])
        assert pdf.doc[0].get_pixmap(clip=obstacle).samples == pixels
        assert translation in ''.join(pdf.doc[0].get_text().split())


def test_diagonal_image_blocks_combined_expansion(tmp_path):
    source = tmp_path / 'diagonal.pdf'
    source_pdf(source)
    with fitz.open(source) as doc:
        base = fitz.Rect(visible_spans(doc[0])[0]['bbox'])
        obstacle = fitz.Rect(base.x1 + 12, base.y1 + 12, base.x1 + 28, base.y1 + 28)
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 8, 8), False)
        pix.clear_with(100)
        doc[0].insert_image(obstacle, pixmap=pix)
        pixels = doc[0].get_pixmap(clip=obstacle).samples
        doc.save(tmp_path / 'with-image.pdf')
    with PaperExtract(tmp_path / 'with-image.pdf') as pdf:
        pdf.extract_textinfo()
        block = pdf.pages_info[0][0]
        space = PageLayoutSpace(pdf.doc[0], pdf.pages_info[0])
        assert all(not rect.intersects(obstacle) for rect in space.candidates(block, 0))
        plan = pdf.prepare_page(0, ['訳' * 80])
        assert not fitz.Rect(plan.rich[0].rect).intersects(obstacle)
        pdf.fill_all_blocks()
        plan.write_text(pdf.doc[0])
        assert pdf.doc[0].get_pixmap(clip=obstacle).samples == pixels


def test_expanded_translation_plans_do_not_collide(tmp_path):
    source = tmp_path / 'reservations.pdf'
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((40, 60), 'This paragraph explains safe translation.', fontsize=12)
        page.insert_text((500, 90), 'This paragraph is written in the opposite direction.',
                         fontsize=12, rotate=180)
        doc.save(source)
    with PaperExtract(source) as pdf:
        pdf.extract_textinfo()
        plan = pdf.prepare_page(0, ['甲' * 70, '乙' * 70])
        first, second = [fitz.Rect(placement.rect) for placement in plan.rich]
        assert not first.intersects(second)
        pdf.fill_all_blocks()
        plan.write_text(pdf.doc[0])
        assert ''.join(pdf.doc[0].get_text().split()) == '甲' * 70 + '乙' * 70


def test_growth_respects_inferred_column_gutter(tmp_path):
    source = tmp_path / 'columns.pdf'
    with fitz.open() as doc:
        page = doc.new_page(width=600, height=500)
        text = ('This column contains several sentences about translation. '
                'Keep this paragraph separate from the next column.')
        for x in (40, 300):
            assert page.insert_textbox((x, 40, x + 200, 200), text, fontsize=12) >= 0
        doc.save(source)
    with PaperExtract(source) as pdf:
        pdf.extract_textinfo()
        left, right = pdf.pages_info[0]
        gutter = (left['bbox'][2] + right['bbox'][0]) / 2
        plan = pdf.prepare_page(0, ['甲' * 150, '乙' * 150])
        assert plan.rich[0].rect[2] <= gutter - 2 + 1e-3
        assert plan.rich[1].rect[0] >= gutter
        assert not fitz.Rect(plan.rich[0].rect).intersects(plan.rich[1].rect)


@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
def test_free_space_expansion_follows_writing_direction(tmp_path, rotation):
    source = tmp_path / 'writing.pdf'
    with fitz.open() as doc:
        page = doc.new_page(width=700, height=700)
        page.insert_text((350, 350), 'This paragraph explains safe translation.', rotate=rotation)
        doc.save(source)
    with PaperExtract(source) as pdf:
        pdf.extract_textinfo()
        original = fitz.Rect(pdf.pages_info[0][0]['bbox'])
        plan = pdf.prepare_page(0, ['訳文' * 25])
        frame = fitz.Rect(plan.rich[0].rect)
        assert frame.contains(original)
        assert frame != original and plan.rich[0].scale == 1
        pdf.fill_all_blocks()
        plan.write_text(pdf.doc[0])
        assert ''.join(pdf.doc[0].get_text().split()) == '訳文' * 25


@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
def test_growth_stays_inside_rotated_crop_box(tmp_path, rotation):
    source = tmp_path / 'crop.pdf'
    source_pdf(source)
    with fitz.open(source) as doc:
        doc[0].set_cropbox((20, 30, 570, 450))
        doc[0].set_rotation(rotation)
        doc.save(tmp_path / 'cropped.pdf')
    with PaperExtract(tmp_path / 'cropped.pdf') as pdf:
        pdf.extract_textinfo()
        bounds = pdf.doc[0].rect * pdf.doc[0].derotation_matrix
        plan = pdf.prepare_page(0, ['訳文' * 40])
        assert bounds.contains(plan.rich[0].rect)
        pdf.fill_all_blocks()
        plan.write_text(pdf.doc[0])
        assert ''.join(pdf.doc[0].get_text().split()) == '訳文' * 40
        assert pdf.doc[0].rotation == rotation


def test_centered_heading_expands_without_moving_its_center(tmp_path):
    source = tmp_path / 'center-grow.pdf'
    with fitz.open() as doc:
        page = doc.new_page(width=600, height=500)
        assert page.insert_textbox((40, 40, 560, 95), 'Centered source heading', fontsize=22,
                                   fontname='hebo', align=fitz.TEXT_ALIGN_CENTER) >= 0
        for y in (150, 200, 250):
            page.insert_text((40, y), 'Ordinary paragraphs establish the body font size.', fontsize=12)
        doc.save(source)
    with PaperExtract(source) as pdf:
        pdf.extract_textinfo()
        original = fitz.Rect(pdf.pages_info[0][0]['bbox'])
        title = '中央の見出しを元の文字サイズで折り返して配置します。'
        plan = pdf.prepare_page(0, [title, '本文です。', '本文です。', '本文です。'])
        frame = fitz.Rect(plan.rich[0].rect)
        assert frame.width > original.width and plan.rich[0].scale == 1
        assert abs((frame.x0 + frame.x1) / 2 - 300) < 1
        pdf.fill_all_blocks()
        plan.write_text(pdf.doc[0])
        headings = [span for span in visible_spans(pdf.doc[0]) if span['size'] > 20]
        assert headings
        assert all(abs((span['bbox'][0] + span['bbox'][2]) / 2 - 300) < 1 for span in headings)
