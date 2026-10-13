"""Exercise real local Bergamot block translation and PDF placement.

python scripts/verify_block_translation.py --output /tmp/ner-blocks \
    --worker /path/to/ner-bergamot-worker --model /path/to/model/config.yml
All generated PDFs/reports are written beneath --output, never to model sources.
"""

import argparse
import json
from pathlib import Path
import sys
import time

import pymupdf as fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.audit_pdf_layout import fixture, inspect
from ner import TextraConnection


def run_document(source, config):
    events = []
    started = time.monotonic()
    with TextraConnection(source, config=config) as connection:
        connection.get_pdf_info()
        connection.post_page(on_chunk_progress=lambda *event: events.append(event))
        output = Path(connection.insert())
        summary = {'seconds': time.monotonic() - started, 'progress': events,
                   'blocks': connection.pe.pages_info, 'translations': connection.page_array,
                   'chunk_counts': [result.chunk_count for page in connection.block_results for result in page]}
    return output, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--worker', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    config = {'PRIORITY': 'BERGAMOT', 'BERGAMOT': {
        'WORKER_BINARY': str(args.worker.resolve()), 'MODEL_CONFIG': str(args.model.resolve())}}
    source = root / 'source.pdf'
    regions = fixture(source)
    output, report = run_document(source, config)
    report['metrics'] = inspect(source, output, regions)
    metrics = report['metrics']
    assert metrics['url_bytes_present'] and metrics['inline_code_bytes_present']
    assert metrics['flow_inline_code_bytes_present'] and all(metrics['code_lines_present'])
    assert metrics['protected_characters_same_coordinates']['code_block']
    assert metrics['links_count'] >= 2 and metrics['all_links_overlap_visible_text']
    (root / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    with fitz.open(output) as document:
        for index, page in enumerate(document):
            page.get_pixmap().save(root / f'translated-page-{index + 1}.png')

    long_source = root / 'long.pdf'
    text = ('Offline translation keeps private documents on the computer. '
            'The document should preserve technical details and the original paragraph boundaries. ') * 20
    text += ('Read https://example.org/local-models?lang=en and call '
             '`translate_document_without_network` before sending a request.')
    with fitz.open() as document:
        page = document.new_page(width=720, height=960)
        assert page.insert_textbox((40, 40, 680, 900), text, fontsize=10) >= 0
        document.save(long_source)
    long_output, long_report = run_document(long_source, config)
    with fitz.open(long_output) as document:
        visible = document[0].get_text()
        assert 'https://example.org/local-models?lang=en' in visible
        assert 'translate_document_without_network' in visible
        assert document[0].get_links()
    assert len(long_report['chunk_counts']) == 1 and long_report['chunk_counts'][0] > 1
    long_report['source_characters'] = len(text)
    (root / 'long-report.json').write_text(json.dumps(long_report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'fixture': 'protected bytes, code coordinates, and clickable links verified',
                      'long_source_characters': len(text), 'long_chunk_counts': long_report['chunk_counts'],
                      'artifacts': str(root)}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
