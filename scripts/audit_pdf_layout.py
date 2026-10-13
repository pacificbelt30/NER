"""Reproduce PDF reconstruction limits without depending on a translation model.

Run from the repository root: python scripts/audit_pdf_layout.py --output /tmp/ner-layout
The baseline uses the production extractor / renderer with identity translations.
The experimental renderer uses fixture-owned regions and known Japanese translations:
it proves rendering capabilities, NOT automatic semantic region detection.
"""

import argparse
from dataclasses import asdict, dataclass
import html
from html.parser import HTMLParser
import json
from pathlib import Path
import sys

import pymupdf as fitz
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ner.paper_extract import PaperExtract
from ner.textra_api import TextraConnection
from ner.translation import TranslationService
from ner.text_blocks import BlockTranslation, LiteralRange, TranslationBlock, prepare_block


URL = 'https://docs.example.org/translation/local-models?lang=en'
CODE = [
    'def translate_document_without_network(document_path):',
    '    local_translation_result = translator.translate(document_path)',
    '    return local_translation_result',
]
INLINE_CODE = 'translate_document_without_network'
JP_BODY = 'ローカル翻訳は文書を外部に送信せずに処理します。段組みと図の位置を保ち、読みやすい文字サイズで本文を配置します。'


@dataclass
class Region:
    page: int
    name: str
    role: str
    rect: tuple
    target: str = ''
    align: str = 'left'
    rotate: int = 0


def spans(page):
    return [span for block in page.get_text('dict', flags=fitz.TEXTFLAGS_TEXT)['blocks']
            if block['type'] == 0 for line in block['lines'] for span in line['spans']]


def region_spans(page, region):
    box = fitz.Rect(region.rect)
    return [span for span in spans(page)
            if box.contains((fitz.Rect(span['bbox']).tl + fitz.Rect(span['bbox']).br) / 2)]


def fixture(destination):
    regions = []
    with fitz.open() as document:
        page = document.new_page(width=720, height=760)
        page.insert_text((40, 44), 'Local translation', fontname='hebo', fontsize=22,
                         color=(0.1, 0.25, 0.55))
        regions.append(Region(0, 'short_heading', 'heading', (40, 18, 680, 50),
                              'ローカル翻訳', 'center'))
        # Intentionally write the right column first: stream order != reading order.
        for name, box in [('right_column', (380, 75, 680, 210)),
                          ('left_column', (40, 75, 340, 210))]:
            page.insert_textbox(box, 'Offline translation keeps private documents on your computer. '
                                'The original columns and illustrations should remain readable. '
                                'A paragraph must not collide with the neighboring column.', fontsize=12)
            regions.append(Region(0, name, 'paragraph', box, JP_BODY))
        page.insert_text((40, 250), 'Read the documentation before configuring the local translation server.',
                         fontsize=10)
        page.insert_text((40, 264), URL, fontsize=10, color=(0, 0.3, 0.7))
        page.insert_text((40, 278), 'This link must stay clickable and its address must remain unchanged.',
                         fontsize=10)
        regions.append(Region(0, 'url_paragraph', 'mixed', (40, 236, 680, 284)))
        url_box = page.search_for(URL)[0]
        page.insert_link({'kind': fitz.LINK_URI, 'from': url_box, 'uri': URL})
        # Code is deliberately long enough to pass the current alphabetic filter.
        page.insert_text((40, 340), CODE, fontname='cour', fontsize=10)
        regions.append(Region(0, 'code_block', 'code', (40, 325, 680, 380)))
        prefix = 'Call '
        page.insert_text((40, 420), prefix, fontsize=10)
        code_x = 40 + fitz.get_text_length(prefix, fontsize=10)
        page.insert_text((code_x, 420), INLINE_CODE, fontname='cour', fontsize=10)
        suffix_x = code_x + fitz.get_text_length(INLINE_CODE, fontname='cour', fontsize=10)
        page.insert_text((suffix_x, 420), ' to translate private documents safely.', fontsize=10)
        regions.append(Region(0, 'inline_code', 'mixed', (40, 406, 680, 424)))
        # Two URL lines: the trailing hyphen is data, not prose hyphenation.
        page.insert_text((40, 475), ['See the full address for offline translation configuration:',
                                   'https://docs.example.org/translation/local-', 'models?lang=en'], fontsize=10)
        regions.append(Region(0, 'wrapped_url', 'protected', (40, 460, 680, 510)))
        # Paragraph over a nonwhite vector background.
        page.draw_rect((40, 555, 680, 635), color=None, fill=(0.88, 0.94, 1))
        page.insert_textbox((50, 565, 670, 630),
                            'The colored background should remain visible after the original text is removed. '
                            'Preserving the drawing object alone is not sufficient.', fontsize=12)
        regions.append(Region(0, 'colored_background', 'paragraph', (50, 565, 670, 630), JP_BODY))

        page = document.new_page(width=720, height=760)
        title = 'Detailed layout control for local document translation'
        page.insert_textbox((40, 35, 680, 90), title, fontsize=20, fontname='hebo',
                            align=fitz.TEXT_ALIGN_CENTER, color=(0.1, 0.25, 0.55))
        regions.append(Region(1, 'styled_heading', 'heading', (40, 35, 680, 90),
                              '文書翻訳のレイアウト制御', 'center'))
        page.insert_text((40, 140), ['1. Keep links clickable', '2. Preserve code indentation',
                                    '3. Respect column boundaries'], fontsize=12)
        regions.append(Region(1, 'short_list', 'protected', (40, 124, 360, 180)))
        for x in (40, 230, 420):
            page.draw_line((x, 230), (x, 310))
        for y in (230, 270, 310):
            page.draw_line((40, y), (420, y))
        for x, y, text in [(50, 253, 'Engine'), (240, 253, 'Status'),
                           (50, 293, 'Bergamot'), (240, 293, 'Ready')]:
            page.insert_text((x, y), text, fontsize=12)
        regions.append(Region(1, 'table', 'protected', (40, 230, 420, 310)))
        page.insert_text((650, 660), 'Rotated explanatory text should keep its original writing direction.',
                         fontsize=10, rotate=90)
        regions.append(Region(1, 'rotated', 'paragraph', (620, 310, 665, 680),
                              '回転した文章も元の方向を保って配置します。', rotate=90))
        rich = ('<p>Read the <a href="' + URL + '">documentation</a> and call '
                '<code>' + INLINE_CODE + '</code> to translate private documents.</p>')
        box = (40, 385, 560, 510)
        spare, _ = page.insert_htmlbox(box, rich, css='body {font-size:12pt; margin:0;} '
                                      'p {margin:0;} code {font-family:monospace;}', scale_low=1)
        assert spare >= 0
        regions.append(Region(1, 'rich_flow', 'flow', box))
        document.save(destination)
    return regions


def baseline(source):
    with PaperExtract(source) as extractor:
        extractor.extract_textinfo()
        extracted = extractor.pages_info
        identity_results = []
        for page in extractor.pages_info:
            results = []
            for block in page:
                prepared = prepare_block(TranslationBlock(block['id'], block['txt'], block['role'],
                    block['context'], tuple(LiteralRange(**span) for span in block['protected_ranges'])))
                results.append(BlockTranslation(block['id'], block['txt'], prepared.text, prepared.spans, 1))
            identity_results.append(results)
        writers = [extractor.prepare_page(i, extractor.get_page_text_array(i, False), identity_results[i])
                   for i in range(extractor.page_count)]
        extractor.fill_all_blocks()
        for index, writer in enumerate(writers):
            if writer is not None:
                writer.write_text(extractor.doc[index], color=(0, 0, 0), opacity=0.9)
        extractor.save()
        return Path(extractor.output_path), extracted


def protected(span):
    return ('Courier' in span['font'] or 'mono' in span['font'].lower()
            or 'https://' in span['text'])


def normalize_links(page):
    """Story can emit empty and adjacent link fragments for Japanese glyph runs."""
    original = page.get_links()
    merged = []
    for link in sorted(original, key=lambda item: (item.get('uri', ''), item['from'].y0,
                                                  item['from'].x0)):
        box = link['from']
        if box.is_empty:
            continue
        previous = merged[-1] if merged else None
        if (previous and previous.get('uri') == link.get('uri')
                and abs(previous['from'].y0 - box.y0) < 1
                and abs(previous['from'].y1 - box.y1) < 1
                and box.x0 <= previous['from'].x1 + 1):
            previous['from'] |= box
        else:
            merged.append({'kind': link['kind'], 'from': fitz.Rect(box), 'uri': link['uri']})
    for link in original:
        page.delete_link(link)
    for link in merged:
        page.insert_link(link)


def experimental(source, destination, regions):
    """Fixture-driven placement; leaves protected spans at their original coordinates."""
    placements = []
    with fitz.open(source) as document:
        for index in range(len(document)):
            page = document[index]
            plans = []
            for region in [region for region in regions if region.page == index]:
                originals = region_spans(page, region)
                if region.role in ('code', 'protected'):
                    continue
                if region.role == 'flow':
                    # Translation is supplied by the fixture, with protected bytes intact.
                    content = ('<p><a href="' + html.escape(URL, quote=True) + '">ドキュメント</a>を読み、'
                               '<code>' + html.escape(INLINE_CODE) + '</code>を呼び出して文書を翻訳します。</p>')
                    plans.append((region, region.rect, content, 12, '#000000', originals))
                elif region.role == 'mixed':
                    # Absolute mode: reserve each URL/code span, translate surrounding slots.
                    for span in originals:
                        if protected(span):
                            continue
                        target = ('実行' if span['text'] == 'Call ' else
                                  'で文書を翻訳します。' if span['text'].startswith(' to ') else
                                  '設定方法を確認してください。')
                        plans.append((region, span['bbox'], html.escape(target), 10, '#000000', [span]))
                else:
                    color = f"#{originals[0]['color']:06x}"
                    size = 20 if region.role == 'heading' else 12
                    plans.append((region, region.rect, html.escape(region.target), size, color, originals))
            # Validate every placement on disposable pages before touching original content.
            for region, box, content, size, color, originals in plans:
                css = (f'body {{margin:0; font-size:{size}pt; color:{color}; line-height:1.3;}} '
                       f'p {{margin:0; text-align:{region.align};}} '
                       'code {font-family:monospace; font-size:10pt;}')
                if region.role != 'flow':
                    content = '<p>' + content + '</p>'
                minimum_scale = 8 / size  # An explicit 8pt floor, unlike baseline's 0.5pt.
                with fitz.open() as scratch:
                    check = scratch.new_page(width=page.rect.width, height=page.rect.height)
                    spare, scale = check.insert_htmlbox(box, content, css=css,
                                                         scale_low=minimum_scale, rotate=region.rotate)
                if spare < 0:
                    raise ValueError(f'{region.name}: cannot fit above the readability floor')
                placements.append({'page': index, 'name': region.name, 'bbox': box,
                                   'font_size': size, 'scale': scale, 'rotate': region.rotate,
                                   'align': region.align, 'html': content, 'css': css})
                for span in originals:
                    # Slightly inset rectangles avoid touching adjacent protected glyphs.
                    box_span = fitz.Rect(span['bbox']) + (0.1, 0.1, -0.1, -0.1)
                    page.add_redact_annot(box_span, fill=False)
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                                  graphics=fitz.PDF_REDACT_LINE_ART_NONE)
            for plan in [plan for plan in placements if plan['page'] == index]:
                spare, _ = page.insert_htmlbox(plan['bbox'], plan['html'], css=plan['css'],
                                              scale_low=8 / plan['font_size'], rotate=plan['rotate'])
                assert spare >= 0
            # Read new annotations after reloading; then normalize Japanese link fragments.
            page = document.reload_page(page)
            normalize_links(page)
        document.save(destination, garbage=4)
    return placements


def character_records(page, clip=None):
    raw = page.get_text('rawdict', flags=fitz.TEXTFLAGS_TEXT, clip=clip)
    return [(char['c'], tuple(round(v, 2) for v in char['bbox']))
            for block in raw['blocks'] if block['type'] == 0
            for line in block['lines'] for span in line['spans'] for char in span['chars']]


def inspect(source, output, regions):
    with fitz.open(source) as original, fitz.open(output) as result:
        text = '\n'.join(page.get_text() for page in result)
        protected_positions = {}
        for name in ('code_block', 'inline_code', 'wrapped_url'):
            region = next(region for region in regions if region.name == name)
            before = character_records(original[region.page], region.rect)
            if name == 'inline_code':
                protected_spans = [s for s in region_spans(original[region.page], region) if protected(s)]
                before = character_records(original[region.page], protected_spans[0]['bbox'])
                after = character_records(result[region.page], protected_spans[0]['bbox'])
            else:
                after = character_records(result[region.page], region.rect)
            protected_positions[name] = bool(before) and before == after
        links = [link for page in result for link in page.get_links()]
        link_over_text = []
        for page in result:
            for link in page.get_links():
                link_over_text.append(bool(page.get_textbox(link['from']).strip()))
        sizes = [s['size'] for page in result for s in spans(page)]
        bg_before = original[0].get_pixmap(clip=(51, 566, 52, 567)).samples[:3]
        bg_after = result[0].get_pixmap(clip=(51, 566, 52, 567)).samples[:3]
        rotated = next(region for region in regions if region.name == 'rotated')
        directions = [line['dir'] for block in result[1].get_text('dict', clip=rotated.rect)['blocks']
                      if block['type'] == 0 for line in block['lines']]
        column_bounds = {}
        for region in [region for region in regions if region.name.endswith('_column')]:
            box = fitz.Rect(region.rect)
            column_bounds[region.name] = all(
                box.x0 - 0.01 <= span['bbox'][0] and span['bbox'][2] <= box.x1 + 0.01
                for span in region_spans(result[region.page], region))
        heading_spans = region_spans(result[1], next(r for r in regions if r.name == 'styled_heading'))
        heading_box = fitz.Rect()
        for span in heading_spans:
            heading_box |= fitz.Rect(span['bbox'])
        short_heading = next(region for region in regions if region.name == 'short_heading')
        flow = next(region for region in regions if region.name == 'rich_flow')
        return {
            'url_bytes_present': URL in text,
            'inline_code_bytes_present': INLINE_CODE in text,
            'code_lines_present': [line in text for line in CODE],
            'protected_characters_same_coordinates': protected_positions,
            'links_count': len(links), 'link_uris': [link.get('uri') for link in links],
            'all_links_overlap_visible_text': bool(links) and all(link_over_text),
            'background_pixel_before': list(bg_before), 'background_pixel_after': list(bg_after),
            'background_preserved': bg_before == bg_after,
            'rotated_line_directions': directions,
            'column_horizontal_bounds_respected': column_bounds,
            'short_heading_translated': 'ローカル翻訳' in ''.join(
                s['text'] for s in region_spans(result[0], short_heading)),
            'flow_inline_code_bytes_present': INLINE_CODE in result[1].get_textbox(fitz.Rect(flow.rect)),
            'styled_heading_colored': bool(heading_spans) and all(s['color'] != 0 for s in heading_spans),
            'styled_heading_center_x': (heading_box.x0 + heading_box.x1) / 2,
            'short_list_line_count': len(result[1].get_textbox(fitz.Rect(40, 124, 360, 180)).splitlines()),
            'short_list_same_line_structure': (original[1].get_textbox(fitz.Rect(40, 124, 360, 180))
                                               == result[1].get_textbox(fitz.Rect(40, 124, 360, 180))),
            'minimum_font_size': min(sizes),
            'styled_heading_spans': heading_spans,
        }


def constraint_probes(source):
    with fitz.open(source) as document, fitz.open() as scratch:
        page = scratch.new_page(width=720, height=760)
        prefix = next(s for s in spans(document[0]) if s['text'] == 'Call ')
        spare, scale = page.insert_htmlbox(prefix['bbox'], '<p>呼び出し</p>',
                                          css='body {margin:0; font-size:10pt; line-height:1.3;} '
                                              'p {margin:0;}', scale_low=0.8)
        return {'full_japanese_prefix_fits_original_slot_above_8pt': spare >= 0,
                'prefix_slot_width': fitz.Rect(prefix['bbox']).width,
                'minimum_allowed_scale': 0.8, 'returned_scale': scale}


def html_translation_probes(worker, model_config, destination):
    """Requires an experimental native worker compiled with options.HTML = true."""
    original_config = model_config.resolve()
    config = yaml.safe_load(original_config.read_text())
    for key in ('models', 'vocabs'):
        config[key] = [str((original_config.parent / name).resolve()) for name in config[key]]
    config['shortlist'][0] = str((original_config.parent / config['shortlist'][0]).resolve())
    # Without soft alignments, native HTML restoration aborts; HTML=true alone is insufficient.
    config['alignment'] = 'soft'
    html_config = destination / 'config-html.yml'
    html_config.write_text(yaml.safe_dump(config))
    cases = [
        ('inline_code', 'Read the documentation and call <code>' + INLINE_CODE
         + '</code> to translate private documents.', INLINE_CODE),
        ('visible_url', 'Read <a href="' + URL + '"><code>' + URL
         + '</code></a> before configuring the local server.', URL),
        ('plain_anchor', 'Read <a href="' + URL
         + '">documentation</a> before configuring the local server.', URL),
        ('numbered_placeholder', 'Read __NER_TOKEN_0__ and call __NER_TOKEN_1__ '
         'to translate private documents.', '__NER_TOKEN_1__'),
    ]
    results = []
    with TranslationService({'PRIORITY': 'BERGAMOT', 'BERGAMOT': {
        'WORKER_BINARY': str(worker.resolve()), 'MODEL_CONFIG': str(html_config)}}) as translator:
        for name, source, token in cases:
            target = translator.translate(source)
            markup = MarkupProbe()
            markup.feed(target)
            results.append({'name': name, 'source': source, 'target': target,
                            'token_preserved': token in target,
                            'anchor_labels': markup.anchors,
                            'all_anchors_have_visible_labels': (all(label.strip() for label in markup.anchors)
                                                                if markup.anchors else None)})
    assert all(case['token_preserved'] for case in results[:3])
    return {'prerequisites': 'ResponseOptions.HTML=true AND model alignment=soft',
            'scope': 'Four examples; token retention is tested, translation fluency and '
                     'semantic token placement are not guaranteed.', 'cases': results}


class MarkupProbe(HTMLParser):
    def __init__(self):
        super().__init__()
        self.anchors = []
        self.active = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.active.append(len(self.anchors))
            self.anchors.append('')

    def handle_endtag(self, tag):
        if tag == 'a' and self.active:
            self.active.pop()

    def handle_data(self, data):
        for index in self.active:
            self.anchors[index] += data


def render_comparison(paths, destination):
    with fitz.open() as comparison:
        for page_number in range(2):
            page = comparison.new_page(width=2160, height=805)
            for column, (label, path) in enumerate(paths):
                page.insert_text((column * 720 + 20, 25), label, fontsize=18)
                with fitz.open(path) as document:
                    page.show_pdf_page(fitz.Rect(column * 720, 40, (column + 1) * 720, 800),
                                       document, page_number)
                    # show_pdf_page copies content only; copy the fixture's URI links too.
                    for link in document[page_number].get_links():
                        box = link['from'] + (column * 720, 40, column * 720, 40)
                        page.insert_link({'kind': fitz.LINK_URI, 'from': box, 'uri': link['uri']})
            page.get_pixmap().save(destination / f'comparison-page-{page_number + 1}.png')
        comparison.save(destination / 'comparison.pdf', garbage=4, deflate=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--bergamot-worker', type=Path)
    parser.add_argument('--bergamot-model', type=Path)
    parser.add_argument('--bergamot-html-worker', type=Path,
                        help='Optional experimental worker compiled with ResponseOptions.HTML=true.')
    args = parser.parse_args()
    if bool(args.bergamot_worker) != bool(args.bergamot_model):
        parser.error('Supply both --bergamot-worker and --bergamot-model.')
    if args.bergamot_html_worker and not args.bergamot_model:
        parser.error('--bergamot-html-worker requires --bergamot-model.')
    args.output.mkdir(parents=True, exist_ok=True)
    source = args.output / 'source.pdf'
    regions = fixture(source)
    baseline_path, extracted = baseline(source)
    prototype_path = args.output / 'prototype.pdf'
    placements = experimental(source, prototype_path, regions)
    report = {'pymupdf_version': fitz.__version__,
              'scope': 'Synthetic 2-page fixture; identity baseline and known Japanese prototype. '
                       'Region roles and translations are fixture supplied; automatic detection is not tested.',
              'regions': [asdict(region) for region in regions],
              'extracted_production_blocks': extracted,
              'baseline_identity': inspect(source, baseline_path, regions),
              'prototype': inspect(source, prototype_path, regions),
              'prototype_placements': placements, 'constraint_probes': constraint_probes(source)}
    with PaperExtract(source) as extractor:
        short = extractor.calc_fontsize(fitz.Rect(0, 0, 100, 100), (0, 0, 100, 20),
                                        '非常に長い翻訳です。' * 100, extractor.font, 12)
        report['stress_font_size_for_1000_japanese_characters'] = short
    if args.bergamot_worker:
        with TextraConnection(source, config={'PRIORITY': 'BERGAMOT', 'BERGAMOT': {
            'WORKER_BINARY': str(args.bergamot_worker.resolve()),
            'MODEL_CONFIG': str(args.bergamot_model.resolve())}, 'FONT': ''}) as connection:
            connection.get_pdf_info()
            connection.post_page()
            connection.insert()
            actual_path = args.output / 'bergamot.pdf'
            Path(connection.pe.output_path).replace(actual_path)
            report['bergamot_plain'] = inspect(source, actual_path, regions)
            report['bergamot_translations'] = connection.page_array
        # Preserve the identity output after the production path was reused.
        baseline_path, _ = baseline(source)
    if args.bergamot_html_worker:
        report['bergamot_html'] = html_translation_probes(args.bergamot_html_worker,
                                                        args.bergamot_model, args.output)
    prototype = report['prototype']
    assert all(prototype['protected_characters_same_coordinates'].values())
    assert prototype['url_bytes_present'] and prototype['inline_code_bytes_present']
    assert prototype['flow_inline_code_bytes_present']
    assert all(prototype['code_lines_present'])
    assert prototype['links_count'] == 2 and prototype['all_links_overlap_visible_text']
    assert prototype['background_preserved']
    assert prototype['minimum_font_size'] >= 8 - 0.01
    assert prototype['short_heading_translated'] and prototype['styled_heading_colored']
    assert abs(prototype['styled_heading_center_x'] - 360) < 1
    assert prototype['short_list_line_count'] == 3
    assert prototype['short_list_same_line_structure']
    assert all(prototype['column_horizontal_bounds_respected'].values())
    assert prototype['rotated_line_directions']
    assert all(abs(direction[0]) < 0.01 and direction[1] < -0.99
               for direction in prototype['rotated_line_directions'])
    render_comparison([('SOURCE', source), ('CURRENT (identity)', baseline_path),
                       ('PROTOTYPE (fixture translations)', prototype_path)], args.output)
    (args.output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    summary = {'scope': report['scope'], 'constraint_probes': report['constraint_probes'],
               'stress_font_size_for_1000_japanese_characters':
               report['stress_font_size_for_1000_japanese_characters']}
    for name in ('baseline_identity', 'prototype', 'bergamot_plain'):
        if name in report:
            summary[name] = {key: value for key, value in report[name].items()
                             if key not in ('styled_heading_spans', 'rotated_line_directions', 'link_uris')}
            summary[name]['rotated_direction_preserved'] = bool(report[name]['rotated_line_directions']) and all(
                abs(direction[0]) < 0.01 and direction[1] < -0.99
                for direction in report[name]['rotated_line_directions'])
    if 'bergamot_html' in report:
        summary['bergamot_html'] = report['bergamot_html']
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f'Artifacts: {args.output.resolve()}')


if __name__ == '__main__':
    main()
