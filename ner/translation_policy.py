"""Conservative English prose eligibility for automatic PDF translation.

This policy only selects PDF content. An explicit TranslationService request
can still translate a word. Decisions use protected source text, never lengths.
"""

from dataclasses import dataclass
import re

import regex

from .text_blocks import TOKEN


@dataclass(frozen=True)
class TranslationDecision:
    translate: bool
    reason: str


def translation_decision(prepared):
    source = prepared.block.text.strip()
    text = re.sub(r'^\s*(?:\d+[.)]|[-•*])\s+', '', prepared.text).strip()
    prose = TOKEN.sub(' ', text).strip()
    if not source:
        return TranslationDecision(False, 'empty')
    # Bare addresses / filenames are data too, even without PDF link metadata.
    if re.fullmatch(r'(?:https?://|ftp://|mailto:|www\.)\S+|'
                    r'[\w.-]+\.[A-Za-z]{2,}(?:[/?:#]\S*)?', source, re.I):
        return TranslationDecision(False, 'address')
    if not prose or not regex.search(r'\p{L}', prose):
        return TranslationDecision(False, 'literal_or_numeric')
    if re.search(r'^\s*(?:def |class |import |from \S+ import |return |'
                 r'(?:const|let|var) \w+\s*=|#include|[$>]\s+)|'
                 r'^\s*[A-Za-z_]\w*\s*=(?!=)|'
                 r'^\s*[\w.]+\([^)]*\)\s*[;:]?\s*$', source):
        return TranslationDecision(False, 'code')
    words = regex.findall(r"\p{Latin}+(?:['’\-]\p{Latin}+)*", prose)
    latin = sum(len(regex.findall(r'\p{Latin}', word)) for word in words)
    letters = len(regex.findall(r'\p{L}', prose))
    if not words or latin / max(1, letters) < 0.6:
        return TranslationDecision(False, 'other_language')
    # A prose verb plus a protected object ("Read <URL>") is an instruction,
    # while a standalone word / number / acronym stays at its source position.
    if len(words) == 1 and not prepared.spans:
        return TranslationDecision(False, 'single_word')
    if all(re.fullmatch(r'[A-Z]{1,5}', word) for word in words):
        return TranslationDecision(False, 'acronyms')
    return TranslationDecision(True, 'prose')
