"""Persist reproducible rendering inputs and explicit human review decisions.

Rejected model output stays in this private sidecar, never in the automatic PDF.
Reviews replay all blocks from the original, so previous layout expansion is not
painted over and no further translation requests are needed.
"""

from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re

from .errors import PDFError
from .io_utils import atomic_output
from .pdf_warnings import PDFWarning
from .text_blocks import (BlockTranslation, LiteralRange, ProtectedSpan, TOKEN,
                          TranslationBlock, prepare_block)

MANIFEST = 'review-manifest.json'
DRAFT = 'review-draft.json'
MAX_EDIT = 20000


def write_json(destination, value):
    with atomic_output(destination) as path:
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


def save_manifest(job, workspace):
    data = {
        'schema': 1,
        'source_sha256': hashlib.sha256(Path(job.pe.path).read_bytes()).hexdigest(),
        'pages': job.pe.pages_info, 'tables': job.pe.table_regions,
        'has_selectable_text': job.pe.has_selectable_text,
        'font': job.pe.font_path or '',
        'translations': job.page_array,
        'results': [[asdict(item) if item else None for item in page] for page in job.block_results],
        'warnings': [asdict(warning) for warning in job.warnings],
        'candidates': job.rejected_candidates,
        'decisions': [], 'applied': {},
    }
    write_json(Path(workspace) / MANIFEST, data)


def load_manifest(workspace):
    data = json.loads((Path(workspace) / MANIFEST).read_text(encoding='utf-8'))
    if data['schema'] != 1:
        raise ValueError('Unsupported review manifest.')
    return data


def review_items(data):
    """One review unit per failed block; document/page failures remain informational."""
    blocks = {block['id']: block for page in data['pages'] for block in page}
    items = {}
    decisions = {item['id']: item for item in data.get('decisions', [])}
    for index, warning in enumerate(data['warnings']):
        block_id = warning['block_id']
        key = block_id if block_id in blocks else f'note-{index}'
        if key in items:
            continue
        block = blocks.get(block_id)
        rejected = data.get('candidates', {}).get(block_id)
        candidate = rejected['text'] if rejected else warning.get('translation')
        complete = rejected.get('complete', False) if rejected else candidate is not None
        literals = prepare_block(TranslationBlock(block_id, block['txt'],
            protected_ranges=tuple(LiteralRange(**span) for span in block['protected_ranges']))).spans if block else ()
        items[key] = {
            'id': key, **PDFWarning(**warning).view(), 'bbox': warning['bbox'],
            'source': block['txt'] if block else warning['source'],
            'candidate': candidate, 'candidate_complete': complete,
            'candidate_validated': bool(candidate is not None and not rejected),
            'can_adopt': bool(block and complete and candidate and len(candidate) <= MAX_EDIT
                              and not TOKEN.search(candidate)),
            'editable': block is not None,
            'literals': [{'text': span.original, 'kind': span.kind, 'href': span.href} for span in literals],
            'decision': decisions.get(key, {'id': key, 'action': 'pending', 'text': None}),
            'applied': data.get('applied', {}).get(key),
        }
    return list(items.values())


def load_review(workspace):
    data = load_manifest(workspace)
    draft_path = Path(workspace) / DRAFT
    draft = json.loads(draft_path.read_text(encoding='utf-8')) if draft_path.exists() else {
        'revision': 0, 'decisions': data.get('decisions', []),
    }
    applied_decisions = {item['id']: item for item in data.get('decisions', [])}
    data['applied'] = {item['id']: data.get('applied', {}).get(item['id'])
                       for item in draft['decisions'] if applied_decisions.get(item['id']) == item}
    data['decisions'] = draft['decisions']
    return {'available': True, 'revision': draft['revision'], 'items': review_items(data)}


def validate_decisions(data, decisions):
    items = {item['id']: item for item in review_items(data)}
    seen = set()
    for decision in decisions:
        key, action = decision['id'], decision['action']
        if key in seen or key not in items or not items[key]['editable']:
            raise ValueError('確認する箇所が不正または重複しています。画面を更新してください。')
        seen.add(key)
        if action == 'adopt' and not items[key]['can_adopt']:
            raise ValueError('この候補は不完全か長すぎます。「手入力で修正」で全文を確認・修正してください。')
        text = decision.get('text')
        if action == 'edit' and (not isinstance(text, str) or not text.strip() or len(text) > MAX_EDIT
                                 or TOKEN.search(text) or re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', text)):
            raise ValueError('修正文は空欄にせず、内部の保護記号や制御文字を除き、2万文字以内で入力してください。')
        if action != 'edit' and text is not None:
            raise ValueError('修正文は「手入力で修正」を選んだ箇所だけに指定してください。')


def edited_result(block, text):
    # Preserve explicit PDF link destinations for unchanged labels. Changed URL
    # text is detected afresh, so its clickable target follows the corrected URL.
    original = prepare_block(TranslationBlock(block['id'], block['txt'],
        protected_ranges=tuple(LiteralRange(**span) for span in block['protected_ranges'])))
    natural = prepare_block(TranslationBlock(block['id'], text))
    ranges = []
    for span in original.spans:
        if not span.href or text.count(span.original) != 1:
            continue
        if any(span.original in literal.original and span.original != literal.original
               for literal in natural.spans):
            continue  # A URL extended by the reviewer needs its new destination.
        start = text.index(span.original)
        if not any(start < old.end and old.start < start + len(span.original) for old in ranges):
            ranges.append(LiteralRange(start, start + len(span.original), span.kind, span.href))
    prepared = prepare_block(TranslationBlock(block['id'], text, protected_ranges=tuple(ranges)))
    return BlockTranslation(block['id'], text, prepared.text, prepared.spans, 0)


def render_review(workspace, check_cancel=None):
    from .textra_api import TextraConnection
    workspace = Path(workspace)
    data = load_manifest(workspace)
    draft = json.loads((workspace / DRAFT).read_text(encoding='utf-8'))
    decisions = draft['decisions']
    validate_decisions(data, decisions)
    if hashlib.sha256((workspace / 'input.pdf').read_bytes()).hexdigest() != data['source_sha256']:
        raise PDFError('Review source PDF does not match the saved rendering data.')
    items = {item['id']: item for item in review_items(data)}
    with TextraConnection(workspace / 'input.pdf', config={'FONT': data['font']}, render_only=True) as job:
        job.pe.pages_info = data['pages']
        job.pe.table_regions = data['tables']
        job.pe.has_selectable_text = data['has_selectable_text']
        job.page_array = deepcopy(data['translations'])
        job.block_results = [[BlockTranslation(**(item | {
            'spans': tuple(ProtectedSpan(**span) for span in item['spans'])})) if item else None
            for item in page] for page in data['results']]
        job.warnings = [PDFWarning(**warning) for warning in data['warnings']] + job.warnings
        selected = {decision['id']: decision for decision in decisions if decision['action'] != 'pending'}
        for page_index, blocks in enumerate(job.pe.pages_info):
            for block_index, block in enumerate(blocks):
                decision = selected.get(block['id'])
                if decision is None:
                    continue
                job.warnings = [warning for warning in job.warnings if warning.block_id != block['id']]
                text = (None if decision['action'] == 'original' else items[block['id']]['candidate']
                        if decision['action'] == 'adopt' else decision['text'])
                job.page_array[page_index][block_index] = text
                job.block_results[page_index][block_index] = edited_result(block, text) if text is not None else None
        output = job.insert(check_cancel)
        failed_blocks = {warning.block_id for warning in job.warnings}
        failed_pages = {warning.page for warning in job.warnings if warning.code == 'PAGE_RENDER_FAILED'}
        data['decisions'] = decisions
        data['applied'] = {key: key not in failed_blocks and items[key]['page'] not in failed_pages
                           for key in selected}
        # Include new rendering failures in subsequent reviews without losing the
        # original rejected candidates or previous decisions.
        known = {(warning['code'], warning['block_id'], warning['page']) for warning in data['warnings']}
        data['warnings'].extend(asdict(warning) for warning in job.warnings
                               if (warning.code, warning.block_id, warning.page) not in known)
        write_json(workspace / MANIFEST, data)
        audit = {'schema': 1, 'decisions': decisions, 'applied': data['applied']}
        name, suffix = 'ner-human-review.json', 2
        names = set(job.pe.new_doc.embfile_names())
        while name in names:
            name = f'ner-human-review-{suffix}.json'
            suffix += 1
        job.pe.new_doc.embfile_add(name, json.dumps(audit, ensure_ascii=False).encode(),
                                  desc='Explicit human review decisions and placement results')
        job.pe.save()
        return output, {'result_summary': job.result_summary,
                        'warnings': [warning.view() for warning in job.warnings]}
