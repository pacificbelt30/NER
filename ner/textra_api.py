"""PDF translation workflow; TextraConnection remains the public compatibility name."""

import pymupdf as fitz

from .errors import ConfigurationError, PDFError, ProtectedContentError, TranslationCancelled, TranslationError
from .paper_extract import PaperExtract
from .translation import TranslationService
from .yml_load import config_load
from .text_blocks import LiteralRange, TranslationBlock
from .pdf_warnings import PDFWarning, append_warning_report


class TextraConnection:
    def __init__(self, path, priority='GOOGLE', *, config=None, error_policy=None, render_only=False):
        self.warnings = []
        self.result_summary = None
        startup_error = None
        try:
            self.config = config_load('./config.yml') if config is None else config
        except (ConfigurationError, OSError) as exc:
            if error_policy == 'strict':
                raise
            self.config, startup_error = {}, exc
        if not isinstance(self.config, dict):
            raise ConfigurationError('Translation configuration must be a mapping.')
        self.error_policy = error_policy or self.config.get('ERROR_POLICY', 'preserve')
        if self.error_policy not in ('preserve', 'strict'):
            raise ConfigurationError('ERROR_POLICY must be preserve or strict.')
        self.service = None
        self.priority = self.config.get('PRIORITY', priority)
        if startup_error is None and not render_only:
            try:
                self.service = TranslationService(self.config, priority)
            except (ConfigurationError, TranslationError, OSError) as exc:
                if self.error_policy == 'strict':
                    raise
                startup_error = exc
        if self.service is not None:
            self.priority = self.service.name
        if startup_error is not None:
            self.warnings.append(PDFWarning('BACKEND_NOT_CONFIGURED', 'configuration'))
        self.local_translator = (self.service.backend
                                 if self.service and self.priority in ('INDEX', 'BERGAMOT') else None)
        if self.service and self.priority == 'TEXTRA':
            self.consumer = self.service.backend.auth
            self.params = self.service.backend.params
        try:
            try:
                self.pe = PaperExtract(path=path, font=self.config.get('FONT', ''))
            except (ConfigurationError, RuntimeError, ValueError) as exc:
                if self.error_policy == 'strict' or not self.config.get('FONT'):
                    raise
                self.pe = PaperExtract(path=path)
                self.warnings.append(PDFWarning('FONT_FALLBACK', 'configuration'))
        except Exception:
            if self.service:
                self.service.close()
            raise
        self.page_array = None
        self.block_results = None
        self.rejected_candidates = {}

    def _post_block(self, txt, sleeptime=None):
        # Retain the old entry point; backend configuration now controls pacing.
        if self.service is None:
            raise TranslationError('Translation backend is unavailable.')
        return self.service.translate_block(TranslationBlock('legacy', txt)).text

    def get_translation_service_func(self):
        return {self.priority: self._post_block}

    def print_pdf_info(self):
        count = sum(map(len, self.pe.pages_info))
        print('Num of Blocks = Total requests:', count)
        return count

    def get_pdf_info(self):
        try:
            self.pe.extract_textinfo()
        except (PDFError, RuntimeError, ValueError):
            if self.error_policy == 'strict':
                raise
            self.pe.pages_info = [[] for _ in range(self.pe.page_count)]
            self.pe.table_regions = [[] for _ in range(self.pe.page_count)]
            self.pe.has_selectable_text = True
            self.warnings.append(PDFWarning('EXTRACTION_FAILED', 'extraction'))

    def post_page(self, on_progress=None, on_chunk_progress=None):
        self.page_array = None
        self.block_results = None
        self.rejected_candidates = {}
        self.pe._require_extracted()
        if not any(self.pe.pages_info) and not self.pe.has_selectable_text:
            if self.error_policy == 'strict':
                raise PDFError('No translatable text found. Scanned PDFs need OCR before translation.')
            self.warnings.append(PDFWarning('OCR_REQUIRED', 'extraction'))
        elif not any(self.pe.pages_info) and self.pe.has_selectable_text:
            self.warnings = [warning for warning in self.warnings if warning.stage != 'configuration']
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
                    if self.service is None:
                        translated_blocks.append(None)
                        page_results.append(None)
                        completed += 1
                        if on_progress:
                            on_progress(completed, total, page_num + 1, self.pe.page_count)
                        continue
                    result = self.service.translate_block(TranslationBlock(
                        block['id'], block['txt'], block['role'], block['context'],
                        tuple(LiteralRange(**span) for span in block['protected_ranges'])),
                        on_progress=chunk_progress)
                    translated_blocks.append(result.text)
                    page_results.append(result)
                except TranslationCancelled:
                    raise
                except TranslationError as exc:
                    if self.error_policy == 'strict':
                        raise TranslationError(
                            f'{self.priority} translation failed on page {page_num + 1}, '
                            f'block {block_num + 1}: {exc}'
                        ) from exc
                    code = ('PROTECTED_CONTENT_INVALID' if 'Protected token mismatch' in str(exc)
                            else 'TRANSLATION_FAILED')
                    if isinstance(exc, ProtectedContentError):
                        self.rejected_candidates[block['id']] = {
                            'text': exc.candidate, 'complete': exc.complete,
                        }
                    self.warnings.append(PDFWarning(code, 'translation', page_num + 1, block['id'],
                                                    block.get('text_bbox', block['bbox']), block['txt']))
                    translated_blocks.append(None)
                    page_results.append(None)
                completed += 1
                if on_progress:
                    on_progress(completed, total, page_num + 1, self.pe.page_count)
            translated_pages.append(translated_blocks)
            results.append(page_results)
        self.page_array = translated_pages
        self.block_results = results

    def insert(self, check_cancel=None):
        if self.page_array is None or len(self.page_array) != self.pe.page_count:
            raise TranslationError('Translate every page before writing the PDF.')
        self.warnings = [warning for warning in self.warnings if warning.stage not in ('layout', 'rendering')]
        def reset_output():
            if self.pe.new_doc is not self.pe.doc:
                self.pe.new_doc.close()
            self.pe.new_doc = fitz.open(stream=self.pe.doc.tobytes(), filetype='pdf')
        reset_output()
        failed_ids = {block['id'] for page, texts in zip(self.pe.pages_info, self.page_array)
                      for block, text in zip(page, texts) if text is None}
        failed_pages = set()
        def page_error(index):
            failed_pages.add(index)
            text = '\n\n'.join(value for value in self.page_array[index] if value is not None)
            self.warnings.append(PDFWarning('PAGE_RENDER_FAILED', 'rendering', index + 1,
                                            translation=text or None))
        def layout_error(block, exc, text):
            failed_ids.add(block['id'])
            self.warnings.append(PDFWarning('LAYOUT_FAILED', 'layout', block['page'] + 1,
                                            block['id'], block.get('text_bbox', block['bbox']),
                                            block['txt'], text))
        prepared = []
        for index, texts in enumerate(self.page_array):
            if check_cancel:
                check_cancel()
            blocks = self.pe.pages_info[index]
            results = self.block_results[index] if self.block_results is not None else None
            # A broken caller contract is not a recoverable PDF rendering error.
            if len(texts) != len(blocks) or (results is not None and len(results) != len(blocks)):
                raise PDFError(f'Page {index + 1}: translation block count does not match the PDF.')
            try:
                prepared.append(self.pe.prepare_page(index, texts, results=results,
                    on_error=layout_error if self.error_policy == 'preserve' else None))
            except TranslationCancelled:
                raise
            except (PDFError, RuntimeError, ValueError, OSError):
                if self.error_policy == 'strict':
                    raise
                page_error(index)
                prepared.append(None)
        # Rendering failures can happen after redaction. Rebuild from the untouched
        # source and replay valid plans, keeping each failed page entirely original.
        # Each restart excludes another page, so recovery is bounded by page count.
        while True:
            restart = False
            for index, writer in enumerate(prepared):
                if check_cancel:
                    check_cancel()
                if index in failed_pages or writer is None or not writer.rich:
                    continue
                try:
                    self.pe.fill_page_blocks(index, failed_ids)
                    writer.write_text(self.pe.new_doc[index], color=(0, 0, 0), opacity=0.9)
                except TranslationCancelled:
                    raise
                except (PDFError, RuntimeError, ValueError, OSError):
                    if self.error_policy == 'strict':
                        raise
                    page_error(index)
                    reset_output()
                    restart = True
                    break
            if not restart:
                break
        total = sum(map(len, self.pe.pages_info))
        translated = sum(block['id'] not in failed_ids and index not in failed_pages
                         for index, page in enumerate(self.pe.pages_info) for block in page)
        outcome = ('complete' if not self.warnings else 'partial' if 0 < translated < total
                   else 'untranslated' if translated == 0 and (total or any(
                       warning.code in ('OCR_REQUIRED', 'EXTRACTION_FAILED') for warning in self.warnings))
                   else 'complete_with_warnings')
        self.result_summary = {'outcome': outcome, 'total_blocks': total, 'translated_blocks': translated,
                               'retained_blocks': total - translated, 'warning_count': len(self.warnings),
                               'source_pages': self.pe.page_count, 'report_pages': 0}
        if check_cancel:
            check_cancel()
        self.result_summary['report_pages'] = append_warning_report(
            self.pe.new_doc, self.warnings, self.result_summary, check_cancel)
        if check_cancel:
            check_cancel()
        self.pe.save()
        return self.pe.output_path

    def close(self):
        try:
            self.pe.close()
        finally:
            if self.service:
                self.service.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
