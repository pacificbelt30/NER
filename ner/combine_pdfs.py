"""Combine original and translated pages while closing every document."""

from pathlib import Path

import pymupdf as fitz

from .errors import PDFError
from .io_utils import atomic_output
from .paper_extract import PaperExtract
from .pdf_warnings import has_warning_appendix


class CombinePDF:
    def __init__(self, doc1, doc2, output_path):
        self.doc1_path = str(Path(doc1).expanduser())
        self.doc2_path = str(Path(doc2).expanduser())
        self.output_path = str(Path(output_path).expanduser())
        if Path(self.output_path).resolve() in {
            Path(self.doc1_path).resolve(), Path(self.doc2_path).resolve(),
        }:
            raise PDFError('Combined output must differ from the input PDFs.')

    def combine(self, insert_first_page=False):
        with (PaperExtract(self.doc1_path) as first,
              PaperExtract(self.doc2_path) as second, fitz.open() as combined):
            if first.page_count != second.page_count and not has_warning_appendix(second.doc, first.page_count):
                raise PDFError('Original and translated PDFs must have the same number of pages.')
            if insert_first_page:
                combined.insert_pdf(first.doc, from_page=0, to_page=0, final=0)
            for page in range(first.page_count):
                final = int(page == first.page_count - 1 and first.page_count == second.page_count)
                combined.insert_pdf(first.doc, from_page=page, to_page=page, final=final)
                combined.insert_pdf(second.doc, from_page=page, to_page=page, final=final)
            if second.page_count > first.page_count:
                combined.insert_pdf(second.doc, from_page=first.page_count, final=1)
            with atomic_output(self.output_path) as path:
                combined.save(path)
        return self.output_path

