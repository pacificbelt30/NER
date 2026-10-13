"""PDF translation workflow; TextraConnection remains the public compatibility name."""

from .errors import PDFError, TranslationCancelled, TranslationError
from .paper_extract import PaperExtract
from .translation import TranslationService
from .yml_load import config_load
from .text_blocks import LiteralRange, TranslationBlock


class TextraConnection:
    def __init__(self, path, priority='GOOGLE', *, config=None):
        self.config = config_load('./config.yml') if config is None else config
        self.service = TranslationService(self.config, priority)
        self.priority = self.service.name
        self.local_translator = (self.service.backend
                                 if self.priority in ('INDEX', 'BERGAMOT') else None)
        if self.priority == 'TEXTRA':
            self.consumer = self.service.backend.auth
            self.params = self.service.backend.params
        try:
            self.pe = PaperExtract(path=path, font=self.config.get('FONT', ''))
        except Exception:
            self.service.close()
            raise
        self.page_array = None
        self.block_results = None

    def _post_block(self, txt, sleeptime=None):
        # Retain the old entry point; backend configuration now controls pacing.
        return self.service.translate_block(TranslationBlock('legacy', txt)).text

    def get_translation_service_func(self):
        return {self.priority: self._post_block}

    def print_pdf_info(self):
        count = sum(map(len, self.pe.pages_info))
        print('Num of Blocks = Total requests:', count)
        return count

    def get_pdf_info(self):
        self.pe.extract_textinfo()

    def post_page(self, on_progress=None, on_chunk_progress=None):
        self.page_array = None
        self.block_results = None
        self.pe._require_extracted()
        if not any(self.pe.pages_info):
            raise PDFError('No translatable text found. Scanned PDFs need OCR before translation.')
        total = sum(map(len, self.pe.pages_info))
        completed = 0
        if on_progress:
            on_progress(completed, total, 0, self.pe.page_count)
        translated_pages, results = [], []
        for page_num in range(self.pe.page_count):
            translated_blocks, page_results = [], []
            for block_num, block in enumerate(self.pe.pages_info[page_num]):
                def chunk_progress(chunk, chunks):
                    # This callback also checks cancellation between chunks of one block.
                    if on_progress and (chunk == 0 or on_chunk_progress is None):
                        on_progress(completed, total, page_num + 1, self.pe.page_count)
                    if on_chunk_progress:
                        on_chunk_progress(block['id'], chunk, chunks)
                try:
                    result = self.service.translate_block(TranslationBlock(
                        block['id'], block['txt'], block['role'], block['context'],
                        tuple(LiteralRange(**span) for span in block['protected_ranges'])),
                        on_progress=chunk_progress)
                    translated_blocks.append(result.text)
                    page_results.append(result)
                except TranslationCancelled:
                    raise
                except TranslationError as exc:
                    raise TranslationError(
                        f'{self.priority} translation failed on page {page_num + 1}, '
                        f'block {block_num + 1}: {exc}'
                    ) from exc
                completed += 1
                if on_progress:
                    on_progress(completed, total, page_num + 1, self.pe.page_count)
            translated_pages.append(translated_blocks)
            results.append(page_results)
        self.page_array = translated_pages
        self.block_results = results

    def insert(self):
        if self.page_array is None or len(self.page_array) != self.pe.page_count:
            raise TranslationError('Translate every page before writing the PDF.')
        # Validate and fit all translations before removing any original text.
        prepared = [self.pe.prepare_page(index, texts, results=(self.block_results[index]
                    if self.block_results is not None else None))
                    for index, texts in enumerate(self.page_array)]
        self.pe.fill_all_blocks()
        for index, writer in enumerate(prepared):
            if writer is not None:
                writer.write_text(self.pe.new_doc[index], color=(0, 0, 0), opacity=0.9)
        self.pe.save()
        return self.pe.output_path

    def close(self):
        try:
            self.pe.close()
        finally:
            self.service.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
