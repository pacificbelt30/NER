"""PDF translation workflow; TextraConnection remains the public compatibility name."""

from .errors import PDFError, TranslationCancelled, TranslationError
from .paper_extract import PaperExtract
from .translation import TranslationService
from .yml_load import config_load


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

    def _post_block(self, txt, sleeptime=None):
        # Retain the old entry point; backend configuration now controls pacing.
        return self.service.translate(txt)

    def get_translation_service_func(self):
        return {self.priority: self._post_block}

    def print_pdf_info(self):
        count = sum(map(len, self.pe.pages_info))
        print('Num of Blocks = Total requests:', count)
        return count

    def get_pdf_info(self):
        self.pe.extract_textinfo()

    def post_page(self, on_progress=None):
        self.page_array = None
        self.pe._require_extracted()
        if not any(self.pe.pages_info):
            raise PDFError('No translatable text found. Scanned PDFs need OCR before translation.')
        total = sum(map(len, self.pe.pages_info))
        completed = 0
        if on_progress:
            on_progress(completed, total, 0, self.pe.page_count)
        translated_pages = []
        for page_num in range(self.pe.page_count):
            translated_blocks = []
            for block_num, text in enumerate(self.pe.get_page_text_array(page_num, is_split=False)):
                try:
                    translated_blocks.append(self.service.translate(text))
                except TranslationError as exc:
                    raise TranslationError(f'{self.priority} translation failed on page {page_num + 1}, '
                                           f'block {block_num + 1}: {exc}') from exc
                completed += 1
                if on_progress:
                    on_progress(completed, total, page_num + 1, self.pe.page_count)
            translated_pages.append(translated_blocks)
        self.page_array = translated_pages

    def insert(self):
        if self.page_array is None or len(self.page_array) != self.pe.page_count:
            raise TranslationError('Translate every page before writing the PDF.')
        prepared = [self.pe.prepare_page(index, texts) for index, texts in enumerate(self.page_array)]
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
