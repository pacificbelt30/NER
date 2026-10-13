"""Compare extraction, page copying, and translated layout on synthetic PDFs.

Install requirements-benchmark.txt, then run with --output and --repeat (default 5).
Translation is fixture-controlled, so this measures PDF handling, not MT quality.
Alternate writers use identical extracted blocks / translations: their results
are implementation experiments, not intrinsic quality scores of the libraries.
"""

import argparse
from contextlib import redirect_stderr
import html
import importlib.metadata
import io
import json
from pathlib import Path
import platform
import re
import statistics
import sys
import time

import numpy as np
import pymupdf as fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.audit_pdf_layout import CODE, INLINE_CODE, JP_BODY, URL, fixture, inspect, spans
from ner.block_layout import MIN_FONT_SIZE, PreparedPage, RichPlacement, normalize_uri_links
from ner.paper_extract import PaperExtract
from ner.text_blocks import (BlockTranslation, LiteralRange, TOKEN, TranslationBlock,
                             prepare_block, restore_text, validate_tokens)


PACKAGES = ('pymupdf', 'pdfplumber', 'pdfminer.six', 'pypdf', 'pypdfium2', 'reportlab', 'numpy')


def corpus(root):
    mixed = root / 'mixed.pdf'
    regions = fixture(mixed)
    files = [mixed]
    columns = root / 'columns.pdf'
    with fitz.open() as doc:
        page = doc.new_page(width=720, height=760)
        for x, title, tail in [(380, 'Right introduction.', 'Right conclusion.'),
                               (40, 'Left introduction.', 'Left conclusion.')]:
            page.insert_textbox((x, 60, x + 280, 250),
                title + ' This paragraph contains several sentences in a separate column. '
                'The reading order should complete one column before starting the other. ' + tail, fontsize=12)
        page.insert_text((40, 360), 'Anchor', fontsize=12)
        doc.save(columns)
    files.append(columns)
    graphics = root / 'graphics.pdf'
    with fitz.open() as doc:
        page = doc.new_page(width=720, height=760)
        page.insert_text((40, 45), 'Illustrations and local translation', fontsize=20, fontname='hebo',
                         color=(0.1, 0.25, 0.55))
        page.insert_textbox((40, 75, 680, 200),
            'Local translation should keep all illustrations, image colors, and vector shapes in place. '
            'The translated paragraph must remain separate from the illustration below.', fontsize=12)
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 80, 40), False)
        pix.clear_with(100)
        page.insert_image((40, 240, 280, 360), pixmap=pix)
        page.draw_rect((380, 240, 680, 360), color=(0.2, 0.4, 0.8), fill=(0.85, 0.9, 1))
        page.draw_line((400, 330), (520, 260), color=(0, 0.2, 0.6), width=3)
        page.draw_line((520, 260), (650, 310), color=(0, 0.2, 0.6), width=3)
        page.insert_text((40, 700), 'This small footnote should not become less readable.', fontsize=6)
        doc.save(graphics)
    files.append(graphics)
    dense = root / 'dense.pdf'
    with fitz.open() as doc:
        page = doc.new_page(width=720, height=760)
        page.insert_text((40, 70), 'This stress paragraph is intentionally short.', fontsize=12)
        doc.save(dense)
    files.append(dense)
    rotated = root / 'rotated-page.pdf'
    with fitz.open() as doc:
        page = doc.new_page(width=720, height=760)
        page.insert_textbox((40, 60, 650, 150),
            'This page is rotated as a whole and must keep the translated text visible in the original area.', fontsize=12)
        page.insert_text((40, 740), 'The lower margin must remain visible after page rotation.', fontsize=10)
        page.set_rotation(90)
        doc.save(rotated)
    files.append(rotated)
    return files, regions


def extract(path, engine, details=False):
    pages = []
    if engine == 'PyMuPDF':
        with fitz.open(path) as doc:
            for page in doc:
                row = {'text': page.get_text(), 'chars': [], 'links': []}
                if details:
                    row['chars'] = [(char['c'], char['bbox'])
                        for block in page.get_text('rawdict', flags=fitz.TEXTFLAGS_TEXT)['blocks'] if block['type'] == 0
                        for line in block['lines'] for span in line['spans'] for char in span['chars']]
                    row['links'] = [link.get('uri') for link in page.get_links()]
                pages.append(row)
    elif engine == 'pdfplumber':
        import pdfplumber
        with pdfplumber.open(path) as doc:
            for page in doc.pages:
                row = {'text': page.extract_text() or '', 'chars': [], 'links': []}
                if details:
                    row['chars'] = [(char['text'], (char['x0'], char['top'], char['x1'], char['bottom']))
                                    for char in page.chars]
                    row['links'] = [link.get('uri') for link in page.hyperlinks]
                pages.append(row)
    elif engine == 'pdfminer.six':
        from pdfminer.high_level import extract_pages
        from pdfminer.layout import LAParams, LTChar, LTTextContainer
        def characters(node):
            if isinstance(node, LTChar):
                yield node
            elif hasattr(node, '__iter__'):
                for child in node:
                    yield from characters(child)
        for page in extract_pages(str(path), laparams=LAParams(detect_vertical=True)):
            text = ''.join(node.get_text() for node in page if isinstance(node, LTTextContainer))
            chars = [(char.get_text(), (char.x0, page.height - char.y1, char.x1, page.height - char.y0))
                     for char in characters(page)] if details else []
            pages.append({'text': text, 'chars': chars, 'links': None})
    elif engine == 'pypdf':
        from pypdf import PdfReader
        doc = PdfReader(path)
        for page in doc.pages:
            # The layout mode has no character bbox API. Keep rotated text enabled.
            with redirect_stderr(io.StringIO()):
                text = page.extract_text(extraction_mode='layout', layout_mode_strip_rotated=False) or ''
            links = [str(item.get_object().get('/A', {}).get('/URI'))
                     for item in page.get('/Annots', []) if '/A' in item.get_object()] if details else []
            pages.append({'text': text, 'chars': None, 'links': links})
    elif engine == 'pypdfium2':
        import pypdfium2 as pdfium
        import pypdfium2.raw as raw
        with pdfium.PdfDocument(str(path)) as doc:
            for page in doc:
                textpage = page.get_textpage()
                try:
                    text = textpage.get_text_range()
                    chars = []
                    if details:
                        height = page.get_height()
                        for index in range(textpage.count_chars()):
                            char = chr(raw.FPDFText_GetUnicode(textpage, index))
                            if char in '\r\n\x00':
                                continue
                            left, bottom, right, top = textpage.get_charbox(index, loose=True)
                            chars.append((char, (left, height - top, right, height - bottom)))
                    pages.append({'text': text, 'chars': chars, 'links': None})
                finally:
                    textpage.close()
                    page.close()
    return pages


def normalized(text):
    return re.sub(r'\s+', '', text)


def extraction_benchmark(files, repeat):
    engines = ('PyMuPDF', 'pdfplumber', 'pdfminer.six', 'pypdf', 'pypdfium2')
    checks = ('Local translation', 'translate_document_without_network', URL,
              'Detailed layout control', 'Engine', 'Status', 'Bergamot', 'Ready',
              'Rotated explanatory text', 'documentation')
    result = []
    for engine in engines:
        # Warm imports/caches; every timed repetition opens each PDF afresh.
        extract(files[0], engine)
        runs = []
        for _ in range(repeat):
            start = time.perf_counter()
            for path in files:
                extract(path, engine)
            runs.append((time.perf_counter() - start) * 1000)
        detailed = extract(files[0], engine, True)
        text = normalized(''.join(page['text'] for page in detailed))
        passed = sum(normalized(value) in text for value in checks)
        column = extract(files[1], engine, True)[0]
        positions = [column['text'].find(value) for value in
                     ('Left introduction.', 'Left conclusion.', 'Right introduction.', 'Right conclusion.')]
        correct_order = all(pos >= 0 for pos in positions) and positions == sorted(positions)
        char_boxes = column['chars']
        anchor_x = None
        if char_boxes is not None:
            chars = ''.join(item[0] for item in char_boxes)
            at = chars.find('Anchor')
            if at >= 0:
                anchor_x = abs(char_boxes[at][1][0] - 40)
        links = [uri for page in detailed if page['links'] is not None for uri in page['links']]
        table = None
        start = time.perf_counter()
        if engine == 'PyMuPDF':
            with fitz.open(files[0]) as doc:
                table = [row for found in doc[1].find_tables().tables for row in found.extract()]
        elif engine == 'pdfplumber':
            import pdfplumber
            with pdfplumber.open(files[0]) as doc:
                table = [row for found in doc.pages[1].extract_tables() for row in found]
        table_ms = (time.perf_counter() - start) * 1000 if table is not None else None
        result.append({'engine': engine, 'text_extraction_median_ms': statistics.median(runs),
                       'runs_ms': runs, 'text_probes_passed': passed, 'text_probes_total': len(checks),
                       'column_reading_order_correct': correct_order, 'column_marker_positions': positions,
                       'character_boxes_available': char_boxes is not None,
                       'anchor_left_x_error_pt': anchor_x,
                       'uri_links_extracted': len(links) if detailed[0]['links'] is not None else None,
                       'table_cells_correct': table == [['Engine', 'Status'], ['Bergamot', 'Ready']]
                           if table is not None else None, 'table_ms': table_ms, 'table': table})
    return result


def translations(extractor, dense=False):
    results = []
    for page in extractor.pages_info:
        rows = []
        for block in page:
            prepared = prepare_block(TranslationBlock(block['id'], block['txt'], block['role'], block['context'],
                tuple(LiteralRange(**span) for span in block['protected_ranges'])))
            tokens = [span.token for span in prepared.spans]
            text = block['txt']
            if dense:
                target = '非常に長い翻訳です。' * 100
            elif tokens:
                target = '文書の設定方法を確認します。' + ' '.join(tokens) + 'を参照してください。'
            elif text.startswith('Local translation'):
                target = 'ローカル翻訳'
            elif text.startswith('Detailed layout'):
                target = '文書翻訳のレイアウト制御'
            elif block['role'] == 'heading':
                target = '文書の図と翻訳'
            elif text.strip() == 'Anchor':
                target = '基準位置'
            elif text.startswith('This small footnote'):
                target = '脚注の文字を読みやすく保ちます。'
            elif 'Engine' in text or 'Bergamot' in text:
                target = text.replace('Engine', 'エンジン').replace('Status', '状態').replace('Ready', '準備完了')
            elif re.match(r'^\d+[.)]', text):
                target = text.split('.')[0] + '. 配置を保持します。'
            elif text.startswith('Rotated'):
                target = '回転した説明文の方向を維持します。'
            elif text.startswith('The lower margin'):
                target = '回転後も下端の文章を表示します。'
            else:
                target = JP_BODY
            validate_tokens(target, tuple(tokens), prepared.text)
            rows.append(BlockTranslation(block['id'], restore_text(target, prepared.spans), target,
                                          prepared.spans, 1))
        results.append(rows)
    return results


def all_rich(extractor, results):
    plans = []
    for page_num, blocks in enumerate(extractor.pages_info):
        page = extractor.doc[page_num]
        page_plans = []
        for block, result in zip(blocks, results[page_num]):
            if result.spans:
                from ner.block_layout import prepare_rich
                page_plans.append(prepare_rich(page, block, result, extractor.font_path))
                continue
            size = block['fontsize']
            direction = tuple(round(v) for v in block['lines'][0]['dir'])
            rotation = {(1, 0): 0, (0, -1): 90, (-1, 0): 180, (0, 1): 270}[direction]
            first = block['lines'][0]['spans'][0]
            align = 'center' if block['role'] == 'heading' else 'left'
            css = (f'body {{margin:0; font-family:sans-serif; font-size:{size}pt; '
                   f'line-height:1.1; color:#{first["color"]:06x};}} p {{margin:0; text-align:{align};}}')
            plan = RichPlacement(block['bbox'], '<p>' + html.escape(result.text) + '</p>', css,
                                 min(size, 8) / size, rotation)
            with fitz.open() as scratch:
                test = scratch.new_page(width=page.cropbox.width, height=page.cropbox.height)
                spare, _ = test.insert_htmlbox(plan.rect, plan.text, css=css, scale_low=plan.scale_low,
                                               rotate=rotation)
            if spare < 0:
                raise ValueError(f'{block["id"]}: overflow above readable font floor')
            page_plans.append(plan)
        plans.append(PreparedPage(fitz.TextWriter(page.rect), page_plans))
    return plans


def reportlab_overlay(source, destination, blocks, results, root):
    from pypdf import PdfReader, PdfWriter
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, KeepInFrame
    font = root / 'benchmark-font.ttf'
    if not font.exists():
        font.write_bytes(fitz.Font('japan').buffer)
    pdfmetrics.registerFont(TTFont('NERBenchmark', str(font)))
    reader, writer = PdfReader(source), PdfWriter()
    for index, source_page in enumerate(reader.pages):
        stream = io.BytesIO()
        width, height = float(source_page.mediabox.width), float(source_page.mediabox.height)
        overlay = canvas.Canvas(stream, pagesize=(width, height))
        for block, result in zip(blocks[index], results[index]):
            x0, y0, x1, y1 = block['bbox']
            overlay.setFillColorRGB(1, 1, 1)
            overlay.rect(x0, height - y1, x1 - x0, y1 - y0, fill=1, stroke=0)
            overlay.setFillColorRGB(0, 0, 0)
            paragraph = Paragraph(html.escape(result.text), ParagraphStyle('p', fontName='NERBenchmark',
                                   fontSize=block['fontsize'], leading=block['fontsize'] * 1.1))
            frame = KeepInFrame(x1 - x0, y1 - y0, [paragraph], mode='shrink', hAlign='LEFT', vAlign='TOP')
            drawn_w, drawn_h = frame.wrapOn(overlay, x1 - x0, y1 - y0)
            frame.drawOn(overlay, x0, height - y0 - drawn_h)
        overlay.showPage()
        overlay.save()
        stream.seek(0)
        source_page.merge_page(PdfReader(stream).pages[0])
        writer.add_page(source_page)
    with destination.open('wb') as output:
        writer.write(output)


def preservation(source, output):
    import pypdfium2 as pdfium
    with fitz.open(source) as before, fitz.open(output) as after:
        page_sizes = [tuple(page.rect) for page in before] == [tuple(page.rect) for page in after]
        originals = [page.get_text() for page in before]
        visible = '\n'.join(page.get_text() for page in after)
        hidden_originals = sum('Offline translation' in page.get_text() for page in after)
        japanese = bool(re.search('[\u3040-\u30ff\u4e00-\u9fff]', visible))
    # Use the same independent PDFium renderer for preservation comparisons.
    with pdfium.PdfDocument(str(source)) as left, pdfium.PdfDocument(str(output)) as right:
        image_errors = []
        for index in range(len(left)):
            page_l, page_r = left[index], right[index]
            image_l, image_r = page_l.render(scale=1), page_r.render(scale=1)
            array_l, array_r = np.asarray(image_l.to_pil()), np.asarray(image_r.to_pil())
            image_errors.append(float(np.mean(array_l != array_r)) * 100 if array_l.shape == array_r.shape else 100)
            image_l.close(); image_r.close(); page_l.close(); page_r.close()
    return {'page_boxes_preserved': page_sizes, 'japanese_text_extractable': japanese,
            'unremoved_original_paragraph_pages': hidden_originals,
            'pdfium_changed_channel_percent': image_errors}


def layout_benchmark(files, regions, root):
    rows = []
    for engine in ('NER current', 'PyMuPDF HTML experiment', 'ReportLab + pypdf overlay'):
        cases = []
        for source in files:
            destination = root / (engine.replace(' ', '_').replace('+', 'and') + '-' + source.name)
            started = time.perf_counter()
            try:
                placements = []
                with PaperExtract(source) as extractor:
                    extractor.extract_textinfo()
                    results = translations(extractor, dense=source.stem == 'dense')
                    if engine == 'ReportLab + pypdf overlay':
                        reportlab_overlay(source, destination, extractor.pages_info, results, root)
                    else:
                        if engine == 'NER current':
                            plans = [extractor.prepare_page(index, [row.text for row in page], results=page)
                                     for index, page in enumerate(results)]
                            placements = [{
                                'id': block['id'], 'page': index + 1, 'role': block['role'],
                                'source_rect': block['bbox'], 'placed_rect': plan.rect,
                                'source_font_size': block['fontsize'],
                                'placed_font_size': plan.font_size * plan.scale,
                                'expanded': any(abs(a - b) > 1e-4 for a, b in zip(block['bbox'], plan.rect)),
                            } for index, page_plan in enumerate(plans) if page_plan is not None
                               for block, plan in zip(extractor.pages_info[index], page_plan.rich)]
                        else:
                            plans = all_rich(extractor, results)
                        extractor.fill_all_blocks()
                        for page_number, plan in enumerate(plans):
                            if plan is not None:
                                plan.write_text(extractor.doc[page_number], color=(0, 0, 0), opacity=0.9)
                        extractor.doc.save(destination, garbage=4, deflate=True)
                    source_blocks = extractor.pages_info
                metrics = preservation(source, destination)
                if engine == 'NER current':
                    metrics['block_placements'] = placements
                    metrics['expanded_blocks'] = sum(row['expanded'] for row in placements)
                    metrics['source_size_blocks'] = sum(abs(row['placed_font_size'] - max(
                        row['source_font_size'], MIN_FONT_SIZE)) < 1e-4 for row in placements)
                with fitz.open(destination) as doc:
                    sizes = [span['size'] for page in doc for span in spans(page)]
                    metrics['minimum_font_size'] = min(sizes)
                    floor = MIN_FONT_SIZE if engine == 'NER current' else 8
                    metrics['font_size_floor_policy'] = floor
                    violations = 0
                    for index, blocks in enumerate(source_blocks):
                        for block in blocks:
                            box = fitz.Rect(block['bbox'])
                            inside = [span for span in spans(doc[index]) if box.contains(
                                (fitz.Rect(span['bbox']).tl + fitz.Rect(span['bbox']).br) / 2)]
                            if any(span['size'] < min(floor, block['fontsize']) - 0.1 for span in inside):
                                violations += 1
                    metrics['font_floor_violating_blocks'] = violations
                if source.stem == 'mixed':
                    metrics['mixed_layout'] = inspect(source, destination, regions)
                    with fitz.open(destination) as doc:
                        joined = normalized(''.join(page.get_text() for page in doc))
                        metrics['mixed_layout']['url_characters_present_ignoring_wrap'] = normalized(URL) in joined
                        metrics['mixed_layout']['uri_targets_unchanged'] = all(
                            link.get('uri') == URL for page in doc for link in page.get_links())
                        # This fixture consists only of single-word labels: the
                        # selection policy now leaves them in their source cells.
                        # Phrase-cell translation is measured separately by
                        # scripts/evaluate_pdf_tables.py, not inferred from these.
                        cells = [('Engine', (40, 230, 230, 270)), ('Status', (230, 230, 420, 270)),
                                 ('Bergamot', (40, 270, 230, 310)), ('Ready', (230, 270, 420, 310))]
                        metrics['original_table_cells_correct'] = sum(
                            target == doc[1].get_textbox(fitz.Rect(box)).strip() for target, box in cells)
                        metrics['original_table_cells_total'] = 4
                if source.stem == 'graphics':
                    # Fixed graphic regions contain no translated text.
                    with fitz.open(source) as before, fitz.open(destination) as after:
                        metrics['image_pixels_exact'] = before[0].get_pixmap(clip=(40, 240, 280, 360)).samples == after[0].get_pixmap(clip=(40, 240, 280, 360)).samples
                        metrics['vector_pixels_exact'] = before[0].get_pixmap(clip=(380, 240, 680, 360)).samples == after[0].get_pixmap(clip=(380, 240, 680, 360)).samples
                cases.append({'case': source.stem, 'status': 'written', 'metrics': metrics,
                              'elapsed_ms': (time.perf_counter() - started) * 1000,
                              'bytes': destination.stat().st_size})
            except Exception as exc:
                destination.unlink(missing_ok=True)
                cases.append({'case': source.stem, 'status': 'rejected', 'error': f'{type(exc).__name__}: {exc}',
                              'elapsed_ms': (time.perf_counter() - started) * 1000})
        rows.append({'engine': engine, 'cases': cases})
    return rows


def copy_benchmark(files, root):
    rows = []
    for engine in ('PyMuPDF', 'pypdf', 'pypdfium2'):
        started = time.perf_counter()
        cases = []
        for source in files:
            output = root / ('copy-' + engine + '-' + source.name)
            if engine == 'PyMuPDF':
                with fitz.open(source) as doc:
                    doc.save(output)
            elif engine == 'pypdf':
                from pypdf import PdfReader, PdfWriter
                writer = PdfWriter()
                writer.append(PdfReader(source))
                with output.open('wb') as stream:
                    writer.write(stream)
            else:
                import pypdfium2 as pdfium
                with pdfium.PdfDocument(str(source)) as original, pdfium.PdfDocument.new() as copied:
                    copied.import_pages(original)
                    copied.save(str(output))
            metrics = preservation(source, output)
            with fitz.open(source) as original, fitz.open(output) as copied:
                metrics['uri_link_count_preserved'] = sum(len(p.get_links()) for p in original) == sum(len(p.get_links()) for p in copied)
            cases.append({'case': source.stem, 'metrics': metrics})
        rows.append({'engine': engine, 'elapsed_ms': (time.perf_counter() - started) * 1000, 'cases': cases})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repeat', type=int, default=5)
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error('--repeat must be positive')
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    files, regions = corpus(root)
    report = {'versions': {name: importlib.metadata.version(name) for name in PACKAGES},
              'platform': platform.platform(), 'python': platform.python_version(),
              'scope': '5 synthetic documents / 6 pages. Fixture-controlled translations, not an MT evaluation. '
                       'Text-only median includes fresh document opens, excludes imports; geometry/tables measured separately. '
                       'Writer wall time is one run including verification, not a fair engine speed ranking. '
                       'Copying unchanged pages is not translation reconstruction.',
              'repeat': args.repeat, 'extraction': extraction_benchmark(files, args.repeat),
              'copying': copy_benchmark(files, root), 'layout': layout_benchmark(files, regions, root)}
    (root / 'results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'extraction': report['extraction'],
                     'layout_status': {row['engine']: [(case['case'], case['status']) for case in row['cases']]
                                       for row in report['layout']}, 'report': str(root / 'results.json')},
                    ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
