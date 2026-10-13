"""PDF text extraction and bounded, lossless text placement."""

import math
from pathlib import Path
import re

import pymupdf as fitz

from .errors import ConfigurationError, PDFError
from .io_utils import atomic_output


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
            for block in page.get_text('dict', flags=fitz.TEXTFLAGS_TEXT)['blocks']:
                if block['type'] != 0:
                    continue
                sizes, lines = [], []
                for line in block['lines']:
                    spans = line['spans']
                    lines.append(re.sub(r'- $', '', ''.join(span['text'] for span in spans) + ' '))
                    sizes.extend(span['size'] for span in spans)
                text = ''.join(lines)
                letters = len(re.sub(r'[^a-zA-Z]', '', text))
                if letters <= 40 or not text or letters / len(text) <= 0.6:
                    continue
                blocks.append({'bbox': block['bbox'], 'fontsize': sum(sizes) / len(sizes),
                               'page': page_num, 'txt': text})
            pages.append(blocks)
        self.pages_info = pages



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

    def prepare_page(self, page_num, texts):
        self._require_extracted()
        blocks = self.pages_info[page_num]
        if len(blocks) != len(texts):
            raise PDFError(f'Page {page_num + 1}: translation block count does not match the PDF.')
        if not blocks:
            return None
        page = self.new_doc[page_num]
        writer = fitz.TextWriter(page.rect)
        for block, text in zip(blocks, texts):
            if not isinstance(text, str) or not text.strip():
                raise PDFError(f'Page {page_num + 1}: empty or invalid translation.')
            text = re.sub(r'\n(?!\n)', ' ', text)
            size = self.calc_fontsize(page.rect, block['bbox'], text, self.font, block['fontsize'])
            overflow = writer.fill_textbox(block['bbox'], text, font=self.font, fontsize=size, warn=None)
            if overflow:
                raise PDFError(f'Page {page_num + 1}: translation overflow.')
        return writer

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
