import builtins

import pymupdf as fitz
import pytest

from ner.errors import PDFError
from ner.paper_extract import PaperExtract
from ner.text_blocks import LiteralRange, split_sentences


@pytest.mark.parametrize('text,expected', [
    ('First sentence. Second sentence!', ['First sentence.', 'Second sentence!']),
    ('"Ready?" Yes.\nNext line.', ['"Ready?"', 'Yes.', 'Next line.']),
    ('Dr. A. Smith uses e.g. 3.14 in Fig. 2. Results follow.',
     ['Dr. A. Smith uses e.g. 3.14 in Fig. 2.', 'Results follow.']),
    ('The U.S. team agrees with Smith et al. on this. Done.',
     ['The U.S. team agrees with Smith et al. on this.', 'Done.']),
    ('Read https://example.org/a.b?q=3.14. Next sentence.',
     ['Read https://example.org/a.b?q=3.14.', 'Next sentence.']),
    ('Use `first. second` and [[NER_0]]. Done.',
     ['Use `first. second` and [[NER_0]].', 'Done.']),
    ('最初の文。次の文！最後です？', ['最初の文。', '次の文！', '最後です？']),
    ('   \n', []),
    ('  A heading without punctuation  ', ['A heading without punctuation']),
    ('Wait... Really?! Yes.', ['Wait...', 'Really?!', 'Yes.']),
])
def test_model_free_sentence_boundaries(text, expected):
    assert split_sentences(text) == expected


def test_pdf_literal_ranges_are_not_split():
    text = 'Run obj. method now. Then stop.'
    assert split_sentences(text, [LiteralRange(4, 15)]) == [
        'Run obj. method now.', 'Then stop.']


def test_extraction_helpers_work_without_nltk_and_preserve_page_blocks(tmp_path, monkeypatch):
    original_import = builtins.__import__

    def without_nltk(name, *args, **kwargs):
        if name == 'nltk' or name.startswith('nltk.'):
            raise AssertionError('Sentence extraction must not import NLTK')
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', without_nltk)
    path = tmp_path / 'pages.pdf'
    with fitz.open() as document:
        for text in ('First sentence. Second sentence.', 'Last page. Another sentence.'):
            document.new_page().insert_text((60, 60), text)
        document.save(path)
    with PaperExtract(path) as pdf:
        with pytest.raises(PDFError, match='Extract PDF text'):
            pdf.get_page_text_array()
        pdf.extract_textinfo()
        assert pdf.get_page_text_array() == ['First sentence.\nSecond sentence.\n']
        assert pdf.get_all_text_array() == [
            'First sentence.\nSecond sentence.\n', 'Last page.\nAnother sentence.\n']
        raw = pdf.get_all_text_array(False)
        assert raw == [block['txt'] for page in pdf.pages_info for block in page]
        pdf.pages_info[0] = [{'txt': '  Raw text.  More text.\n\n'}, {'txt': ''}]
        assert pdf.get_page_text_array(is_split=False) == ['  Raw text.  More text.\n\n', '']
        assert pdf.get_page_text_array() == ['Raw text.\nMore text.\n', '\n']
        pdf.pages_info[0] = [{'txt': 'Run obj. method now. Then stop.',
                              'protected_ranges': [{'start': 4, 'end': 15, 'kind': 'code'}]}]
        assert pdf.get_page_text_array() == ['Run obj. method now.\nThen stop.\n']
        pdf.pages_info[0] = []
        assert pdf.get_page_text_array() == []
