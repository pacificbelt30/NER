"""PDF text extraction and bounded, lossless text placement."""

import math
from dataclasses import asdict
from pathlib import Path
import re
from statistics import median

import pymupdf as fitz

from .errors import ConfigurationError, PDFError
from .io_utils import atomic_output
from .text_blocks import LiteralRange, TOKEN, TranslationBlock, prepare_block
from .block_layout import PreparedPage, prepare_rich


SEPARATOR = '\n------------------------------\n'


class PaperExtract:
    def __init__(self, path='', font=''):
        self.path = str(Path(path).expanduser())
        self.pages_info = None
        self.doc = self._open()
        self.new_doc = self.doc
        try:
            if not isinstance(font, str):
                raise ConfigurationError('FONT must be a file path or an empty string.')
            font_path = str(Path(font).expanduser()) if font else None
            if font_path and not Path(font_path).is_file():
                raise ConfigurationError(f'Font file not found: {font_path}')
            self.font = fitz.Font(fontname='japan', fontfile=font_path, language='ja')
            self.font_path = font_path
        except Exception:
            self.doc.close()
            raise

    @property
    def page_count(self):
        return self.doc.page_count

    @property
    def meta_data(self):
        return self.doc.metadata

    def _output_path(self, suffix):
        path = Path(self.path)
        return str(path.with_name(path.stem + suffix + path.suffix))

    @property
    def output_path(self):
        return self._output_path('_new')

    @property
    def output_visualize_path(self):
        return self._output_path('_vi')

    def _open(self):
        try:
            document = fitz.open(self.path)
        except (OSError, RuntimeError, ValueError) as exc:
            raise PDFError(f'Cannot open PDF: {self.path}') from exc
        if not document.is_pdf or document.needs_pass or document.page_count == 0:
            document.close()
            raise PDFError('Input must be a nonempty, unencrypted PDF.')
        return document

    def extract_textinfo(self):
        pages = []
        for page_num, page in enumerate(self.doc):
            blocks = []
            # Character geometry lets link ranges be associated with actual text,
            # rather than every span inside a paragraph rectangle.
            source = page.get_text('rawdict', flags=fitz.TEXTFLAGS_TEXT)['blocks']
            sizes = [span['size'] for block in source if block['type'] == 0
                     for line in block['lines'] for span in line['spans']]
            body_size = median(sizes) if sizes else 12
            links = [link for link in page.get_links() if link['kind'] == fitz.LINK_URI]
            context = ''
            for block_num, block in enumerate(source):
                if block['type'] != 0:
                    continue
                all_spans = [span for line in block['lines'] for span in line['spans']
                             if ''.join(char['c'] for char in span['chars']).strip()]
                all_monospace = bool(all_spans) and all(self._monospace(span) for span in all_spans)
                source_lines = [''.join(char['c'] for span in line['spans'] for char in span['chars'])
                                for line in block['lines']]
                code_syntax = any(re.search(
                    r'^\s*(?:def|class|return|import|from|function|const|let|var|#include)\b'
                    r'|\b[A-Za-z_]\w*\s*=(?!=)|^\s*[A-Za-z_]\w*\([^)]*\)\s*[;:]?$'
                    r'|^\s*(?:[$>]\s+|(?:python\d?|pip|npm|curl|git)\s+)', line) for line in source_lines)
                if all_monospace and code_syntax:
                    # Code blocks remain selectable, in their original font/position.
                    continue
                lines = block['lines']
                is_list = all(re.match(r'^\s*(?:\d+[.)]|[-•*])\s+', ''.join(
                    char['c'] for span in line['spans'] for char in span['chars'])) for line in lines)
                units = [[line] for line in lines] if is_list else [lines]
                for unit_num, unit in enumerate(units):
                    text, ranges, line_metadata = self._block_source(unit, links, code_fonts=not all_monospace)
                    block_id = f'p{page_num + 1}-b{block_num + 1}-u{unit_num + 1}'
                    font_sizes = [span['size'] for line in unit for span in line['spans']]
                    font_size = sum(font_sizes) / len(font_sizes)
                    role = ('list' if is_list else 'heading'
                            if len(unit) <= 2 and font_size >= body_size * 1.25 else 'paragraph')
                    prepared = prepare_block(TranslationBlock(block_id, text, role,
                                                               protected_ranges=tuple(ranges)))
                    prose = TOKEN.sub('', prepared.text)
                    letters = len(re.sub(r'[^a-zA-Z]', '', prose))
                    if letters < 2 or letters / max(1, len(prose.strip())) <= 0.4:
                        continue
                    bbox = fitz.Rect()
                    for line in unit:
                        bbox |= fitz.Rect(line['bbox'])
                    blocks.append({
                        'id': block_id, 'role': role, 'context': context,
                        'bbox': tuple(bbox), 'source_bbox': block['bbox'],
                        'fontsize': font_size, 'page': page_num, 'txt': text,
                        'lines': line_metadata, 'protected_ranges': [asdict(span) for span in ranges],
                    })
                    if role == 'heading':
                        context = text[:512]
            pages.append(blocks)
        self.pages_info = pages

    @staticmethod
    def _monospace(span):
        return bool(span['flags'] & fitz.TEXT_FONT_MONOSPACED
                    or re.search(r'courier|mono|consolas|menlo', span['font'], re.I))

    def _block_source(self, lines, links, *, code_fonts=True):
        parts, ranges, metadata, offset, previous = [], [], [], 0, ''
        for line in lines:
            visible = ''.join(char['c'] for span in line['spans'] for char in span['chars'])
            if parts:
                # A wrapped URL is data: retain its hyphen and join its tail exactly.
                url_tail = re.search(r'https?://\S+$', previous) and re.fullmatch(
                    r'[A-Za-z0-9/?#&=.%+_~\-]+', visible.strip())
                separator = '' if url_tail else ' '
                parts.append(separator)
                offset += len(separator)
            span_metadata = []
            for span in line['spans']:
                text = ''.join(char['c'] for char in span['chars'])
                start = offset
                parts.append(text)
                offset += len(text)
                span_metadata.append({key: value for key, value in span.items() if key != 'chars'} | {'text': text})
                # Prefer external link metadata over code-font detection. Linked labels
                # are reserved as one literal so their hyperlink can move with them.
                kinds = []
                for char in span['chars']:
                    box = fitz.Rect(char['bbox'])
                    center = (box.tl + box.br) / 2
                    link = next((link for link in links if link['from'].contains(center)), None)
                    kinds.append(('link', link['uri']) if link else
                                 ('code', None) if code_fonts and self._monospace(span) else None)
                run = 0
                while run < len(kinds):
                    end = run + 1
                    while end < len(kinds) and kinds[end] == kinds[run]:
                        end += 1
                    if kinds[run] is not None:
                        kind, href = kinds[run]
                        literal = text[run:end]
                        if href and literal.lower().startswith(('http://', 'https://')):
                            kind = 'url'
                        ranges.append(LiteralRange(start + run, start + end, kind, href))
                    run = end
            metadata.append({key: value for key, value in line.items() if key != 'spans'} | {'spans': span_metadata})
            previous = visible
        text = ''.join(parts)
        merged = []
        for span in ranges:
            previous_span = merged[-1] if merged else None
            if (previous_span and previous_span.href == span.href
                    and (span.href or previous_span.kind == span.kind)
                    and not text[previous_span.end:span.start].strip()):
                merged[-1] = LiteralRange(previous_span.start, span.end, previous_span.kind, span.href)
            else:
                merged.append(span)
        return text, merged, metadata

    def _require_extracted(self):
        if self.pages_info is None:
            raise PDFError('Extract PDF text before using page blocks.')

    def get_page_text_array(self, page=0, is_split=True):
        self._require_extracted()
        texts = [block['txt'] for block in self.pages_info[page]]
        if not is_split:
            return texts
        from nltk.tokenize import sent_tokenize
        try:
            return ['\n'.join(sent_tokenize(text)) + '\n' for text in texts]
        except LookupError as exc:
            raise PDFError('NLTK tokenizer data is missing; download punkt and punkt_tab.') from exc

    def get_all_text_array(self, is_split=True):
        self._require_extracted()
        return [text for page in range(self.page_count)
                for text in self.get_page_text_array(page, is_split)]

    def get_page_text(self, page=0):
        return ''.join(text + SEPARATOR for text in self.get_page_text_array(page, False))

    def get_all_text(self):
        return ''.join(text + SEPARATOR for text in self.get_all_text_array(False))

    def fill_all_blocks(self):
        self._require_extracted()
        for page_num, blocks in enumerate(self.pages_info):
            if not blocks:
                continue
            page = self.new_doc[page_num]
            for block in blocks:
                page.add_redact_annot(block['bbox'])
            # Preserve figures and vector graphics under the text rectangles.
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                                  graphics=fitz.PDF_REDACT_LINE_ART_NONE)

    def visualize_blocks(self):
        self._require_extracted()
        with self._open() as document:
            for page_num, blocks in enumerate(self.pages_info):
                shape = document[page_num].new_shape()
                for block in blocks:
                    shape.draw_rect(block['bbox'])
                shape.finish(color=(0, 0, 0))
                shape.commit()
            with atomic_output(self.output_visualize_path) as path:
                document.save(path)

    def calc_fontsize(self, pagerect, rect, text, font, fontsize):
        rect = fitz.Rect(rect)
        if rect.is_empty or rect.is_infinite or not rect.is_valid:
            raise PDFError('Text block has an invalid rectangle.')
        if not math.isfinite(fontsize) or fontsize <= 0:
            raise PDFError('Text block has an invalid font size.')
        maximum = max(1, math.floor(fontsize * 3 - 1))
        max_advance = max(font.char_lengths(text.replace('\n', ''), fontsize=1), default=0)
        def fits(step):
            if step / 2 * font.ascender > rect.height:
                return False
            # PyMuPDF can drop a word whose first glyph exceeds the box width.
            if step / 2 * (max_advance + 0.2) > rect.width:
                return False
            writer = fitz.TextWriter(pagerect)
            return not writer.fill_textbox(rect, text, font=font, fontsize=step / 2, warn=None)
        if fits(maximum):
            return maximum / 2
        if not fits(1):
            raise PDFError('Translation does not fit in its text block, even at the minimum font size.')
        low, high = 1, maximum - 1
        while low < high:
            middle = (low + high + 1) // 2
            if fits(middle):
                low = middle
            else:
                high = middle - 1
        return low / 2

    def prepare_page(self, page_num, texts, results=None):
        self._require_extracted()
        blocks = self.pages_info[page_num]
        if len(blocks) != len(texts):
            raise PDFError(f'Page {page_num + 1}: translation block count does not match the PDF.')
        if not blocks:
            return None
        page = self.new_doc[page_num]
        writer = fitz.TextWriter(page.rect)
        rich = []
        if results is not None and len(results) != len(blocks):
            raise PDFError(f'Page {page_num + 1}: structured translation block count does not match the PDF.')
        for index, (block, text) in enumerate(zip(blocks, texts)):
            if not isinstance(text, str) or not text.strip():
                raise PDFError(f'Page {page_num + 1}: empty or invalid translation.')
            if results is not None and results[index].spans:
                rich.append(prepare_rich(page, block, results[index], self.font_path))
                continue
            text = re.sub(r'\n(?!\n)', ' ', text)
            size = self.calc_fontsize(page.rect, block['bbox'], text, self.font, block['fontsize'])
            overflow = writer.fill_textbox(block['bbox'], text, font=self.font, fontsize=size, warn=None)
            if overflow:
                raise PDFError(f'Page {page_num + 1}: translation overflow.')
        return PreparedPage(writer, rich) if rich else writer

    def insert_txt(self, page_num, text):
        texts = text.split(SEPARATOR)
        if texts and not texts[-1].strip():
            texts.pop()
        return self.insert_txt_from_array(page_num, texts)

    def insert_txt_from_array(self, page_num, texts):
        writer = self.prepare_page(page_num, texts)
        if writer is not None:
            writer.write_text(self.new_doc[page_num], color=(0, 0, 0), opacity=0.9)
        return True

    def save(self):
        with atomic_output(self.output_path) as path:
            self.new_doc.save(path, garbage=4)

    def close(self):
        if not self.doc.is_closed:
            self.doc.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
