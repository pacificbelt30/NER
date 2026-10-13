"""Reproducible cell placement / preservation audit, optionally against a checkout.

Generate one fixture and translate it with deterministic replies or real Bergamot.
--code-root points to a previous checkout for a before/after comparison; no files
in that checkout are edited. Geometry/text/pixel probes use the same fixture.
"""

import argparse
import json
from pathlib import Path
import sys
import time

import pymupdf as fitz


URL = 'https://example.org/help'
PHRASES = {
    'Local translation': 'ローカル翻訳',
    'Ready for use': '使用できます',
    'Use a local model. Keep files on this computer.': 'ローカルモデルを使い、ファイルをこのコンピューターに保存します。',
    'Read X0 now.': 'X0を今読んでください。',
    'Keep the original table layout.': '元の表のレイアウトを維持します。',
    'Use a local model.': 'ローカルモデルを使います。',
    'Keep files on this computer.': 'ファイルをこのコンピューターに保存します。',
}


def fixture(path):
    """3-column, 4-row ruled table: shading, wrapped cell, merged cell, URI."""
    cells = [
        ((40, 50, 240, 95), 'Engine'), ((240, 50, 450, 95), 'Status'),
        ((450, 50, 610, 95), 'Count'),
        ((40, 95, 240, 145), 'Local translation'), ((240, 95, 450, 145), 'Ready for use'),
        ((450, 95, 610, 145), '42'),
        ((40, 145, 240, 220), 'Use a local model.\nKeep files on this computer.'),
        ((240, 145, 450, 220), 'Read ' + URL + ' now.'),
        ((450, 145, 610, 220), 'Bergamot'),
        ((40, 220, 450, 270), 'Keep the original table layout.'),
        ((450, 220, 610, 270), 'www.example.org'),
    ]
    with fitz.open() as doc:
        page = doc.new_page(width=650, height=330)
        page.draw_rect((40, 50, 610, 95), fill=(0.82, 0.9, 1), color=None)
        page.draw_rect((40, 145, 610, 220), fill=(0.94, 0.94, 0.86), color=None)
        for y in (50, 95, 145, 220, 270):
            page.draw_line((40, y), (610, y), color=(0.1, 0.25, 0.45), width=0.7)
        for x in (40, 450, 610):
            page.draw_line((x, 50), (x, 270), color=(0.1, 0.25, 0.45), width=0.7)
        page.draw_line((240, 50), (240, 220), color=(0.1, 0.25, 0.45), width=0.7)
        for rect, text in cells:
            box = fitz.Rect(rect) + (8, 8, -8, -8)
            assert page.insert_textbox(box, text, fontsize=11, color=(0.1, 0.2, 0.35)) >= 0
        page.insert_link({'kind': fitz.LINK_URI, 'from': page.search_for(URL)[0], 'uri': URL})
        doc.save(path)
    return [{'rect': rect, 'source': text, 'translate': text.replace('\n', ' ') in PHRASES
             or text.startswith('Read ')} for rect, text in cells]


def borderless_fixture(path):
    rows = [('Local translation', 'Ready for use'),
            ('Keep the original table layout.', 'Ready for use'),
            ('Use a local model.', 'Keep files on this computer.'),
            ('Offline', '42')]
    cells = []
    with fitz.open() as doc:
        page = doc.new_page(width=620, height=260)
        for row_num, row in enumerate(rows):
            for col_num, text in enumerate(row):
                x = (40, 300)[col_num]
                baseline = 60 + row_num * 40
                page.insert_text((x, baseline), text, fontsize=11)
                cells.append({'rect': (x - 2, baseline - 14, x + 258, baseline + 26),
                              'source': text, 'translate': text in PHRASES})
        doc.save(path)
    return cells


class DeterministicBackend:
    def __init__(self, config):
        self.calls = []

    def translate_chunk(self, text, **kwargs):
        self.calls.append(text)
        # Replace phrases wherever they occur to exercise the former row grouping
        # too. Preserve leftover source words and literal aliases verbatim.
        for source, target in PHRASES.items():
            text = text.replace(source, target)
        return text

    def close(self):
        pass


def characters(page, rect):
    box = fitz.Rect(rect)
    return [(char['c'], tuple(round(v, 3) for v in char['bbox']))
            for block in page.get_text('rawdict', flags=fitz.TEXTFLAGS_TEXT)['blocks']
            if block['type'] == 0 for line in block['lines'] for span in line['spans']
            for char in span['chars']
            if box.contains((fitz.Rect(char['bbox']).tl + fitz.Rect(char['bbox']).br) / 2)]


def evaluate(source, output, cells, translations=None, borderless=False):
    correct, preserved, probes = 0, 0, []
    with fitz.open(source) as before, fitz.open(output) as after:
        for cell in cells:
            chars = characters(after[0], cell['rect'])
            visible = ''.join(char[0] for char in chars)
            if cell['translate']:
                key = cell['source'].replace('\n', ' ')
                target = (translations[key] if translations is not None else PHRASES.get(
                    key, PHRASES['Read X0 now.'].replace('X0', URL)))
                passed = ''.join(target.split()) == ''.join(visible.split())
                correct += bool(passed)
            else:
                passed = chars == characters(before[0], cell['rect'])
                preserved += bool(passed)
            probes.append({'source': cell['source'], 'translated': cell['translate'],
                           'visible': visible, 'passed': passed})
        # Cell strokes and shaded blank areas have to remain pixel-identical.
        regions = [(x - 0.5, 52, x + 0.5, 218) for x in (40, 240, 450, 610)]
        regions += [(42, y - 0.5, 608, y + 0.5) for y in (50, 95, 145, 220, 270)]
        regions += [(42, 89, 608, 92), (42, 211, 608, 217)]
        if borderless:
            regions = [(40, 231, 610, 240)]
        unchanged = [before[0].get_pixmap(clip=region, matrix=fitz.Matrix(2, 2)).samples
                     == after[0].get_pixmap(clip=region, matrix=fitz.Matrix(2, 2)).samples
                     for region in regions]
        link_boxes = [link['from'] for link in after[0].get_links() if link.get('uri') == URL]
        linked_chars = ''.join(''.join(c for c, _ in characters(after[0], box)) for box in link_boxes)
        link_ok = (bool(link_boxes) and ''.join(URL.split()) in ''.join(linked_chars.split())
                   if not borderless else None)
        after[0].get_pixmap(matrix=fitz.Matrix(1.5, 1.5)).save(output.with_suffix('.png'))
        before[0].get_pixmap(matrix=fitz.Matrix(1.5, 1.5)).save(source.with_suffix('.png'))
    return {'translated_cells_correct': correct, 'translated_cells_total': sum(c['translate'] for c in cells),
            'skipped_cells_exact': preserved, 'skipped_cells_total': sum(not c['translate'] for c in cells),
            'grid_and_background_probes_exact': sum(unchanged),
            'grid_and_background_probes_total': len(unchanged), 'uri_on_visible_address': link_ok,
            'cells': probes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--code-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--worker', type=Path)
    parser.add_argument('--model', type=Path)
    parser.add_argument('--borderless', action='store_true')
    args = parser.parse_args()
    if bool(args.worker) != bool(args.model):
        parser.error('--worker and --model must be provided together')
    sys.path.insert(0, str(args.code_root.resolve()))
    from ner.textra_api import TextraConnection
    from ner.translation import BACKENDS
    if args.worker:
        config = {'PRIORITY': 'BERGAMOT', 'BERGAMOT': {
            'WORKER_BINARY': str(args.worker.resolve()), 'MODEL_CONFIG': str(args.model.resolve())}}
    else:
        BACKENDS['INDEX'] = DeterministicBackend
        config = {'PRIORITY': 'INDEX'}
    args.output.mkdir(parents=True, exist_ok=True)
    source = args.output / 'table.pdf'
    cells = borderless_fixture(source) if args.borderless else fixture(source)
    started = time.monotonic()
    with TextraConnection(source, config=config) as connection:
        connection.get_pdf_info()
        blocks = connection.pe.pages_info
        connection.post_page()
        output = Path(connection.insert())
        targets = ({block['txt']: result.text for page, results in zip(blocks, connection.block_results)
                    for block, result in zip(page, results)} if args.worker else None)
        report = {'backend': config['PRIORITY'], 'seconds': time.monotonic() - started,
                  'execution': 'native_worker' if args.worker else 'deterministic_fixture_no_model',
                  'selected_blocks': sum(map(len, blocks)),
                  'metrics': evaluate(source, output, cells, targets, args.borderless)}
    (args.output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
