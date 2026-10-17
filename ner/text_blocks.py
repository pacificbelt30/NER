"""Semantic translation units, literal protection, and bounded text chunks.

Uses the block / mask / validate / restore approach reviewed in Otranslator,
implemented independently for Python and PDF geometry.
"""

from dataclasses import dataclass
from bisect import bisect_right
import re

import regex

from .errors import TranslationError


RECIPE_VERSION = 'blocks-addresses-sentence-clause-v2'
TOKEN = re.compile(r'\[\[NER_(\d+)\]\]')
MODEL_TOKEN = re.compile(r'(?<![A-Za-z0-9_])X(\d+)(?![A-Za-z0-9_])')
LITERAL = re.compile(
    r'\[\[NER_\d+\]\]|\bX\d+\b|`[^`\n]+`|(?:https?://|ftp://|mailto:|www\.)[^\s<>`]+'
    r'|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}'
    r'|\b(?:[A-Za-z0-9-]+\.)+(?:com|org|net|edu|gov|io|ai|dev|app|jp|co|test|example|invalid)'
    r'\b(?:[/?:#][^\s<>`]*)?'
    r'|\b(?:\d{1,3}\.){3}\d{1,3}\b'
    r'|\b[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+\b'
    r'|(?:[A-Za-z]:\\|/(?:[\w.-]+/)+)[\w./\\-]*'
    r'|(?<!\w)--[a-z][\w-]*(?:=[\w.-]+)?', re.IGNORECASE,
)


@dataclass(frozen=True)
class LiteralRange:
    start: int
    end: int
    kind: str = 'code'
    href: str | None = None


@dataclass(frozen=True)
class TranslationBlock:
    id: str
    text: str
    kind: str = 'paragraph'
    context: str = ''
    protected_ranges: tuple[LiteralRange, ...] = ()


@dataclass(frozen=True)
class ProtectedSpan:
    token: str
    original: str
    kind: str
    href: str | None = None


@dataclass(frozen=True)
class PreparedBlock:
    block: TranslationBlock
    text: str
    spans: tuple[ProtectedSpan, ...]


@dataclass(frozen=True)
class TranslationChunk:
    text: str
    leading: str
    trailing: str
    tokens: tuple[str, ...]


@dataclass(frozen=True)
class BlockTranslation:
    id: str
    text: str
    canonical_text: str
    spans: tuple[ProtectedSpan, ...]
    chunk_count: int


def prepare_block(block):
    ranges = sorted(block.protected_ranges, key=lambda span: (span.start, -span.end))
    previous_end = 0
    for span in ranges:
        if not 0 <= span.start < span.end <= len(block.text) or span.start < previous_end:
            raise TranslationError('Invalid or overlapping protected PDF spans.')
        previous_end = span.end
    # Explicit PDF font/link ranges take precedence over lexical detection.
    for match in LITERAL.finditer(block.text):
        start, end = match.span()
        literal = match.group()
        if (literal.lower().startswith(('http://', 'https://', 'ftp://', 'mailto:', 'www.'))
                or re.match(r'^(?:[A-Za-z0-9-]+\.)+(?:com|org|net|edu|gov|io|ai|dev|app|jp|co|test|example|invalid)\b', literal, re.I)):
            while end > start and block.text[end - 1] in '.,;!?':
                end -= 1
            for closing, opening in ((')', '('), (']', '['), ('}', '{')):
                while end > start and block.text[end - 1] == closing and (
                        block.text[start:end].count(closing) > block.text[start:end].count(opening)):
                    end -= 1
            kind = 'url'
        elif (literal.startswith(('`', '--', '/', '\\')) or re.match(r'[A-Za-z]:\\', literal)
              or re.fullmatch(r'[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+', literal)):
            kind = 'code'
        else:
            kind = 'literal'
        overlaps = [span for span in ranges if start < span.end and span.start < end]
        if kind == 'url' and overlaps and all(start <= span.start and span.end <= end
                                              and span.href is None for span in overlaps):
            # One URL may use several PDF fonts. Protect its entire lexical range.
            ranges = [span for span in ranges if span not in overlaps]
            ranges.append(LiteralRange(start, end, kind))
        elif not overlaps:
            ranges.append(LiteralRange(start, end, kind))
    ranges.sort(key=lambda span: span.start)
    parts, spans, cursor = [], [], 0
    for index, span in enumerate(ranges):
        token = f'[[NER_{index}]]'
        original = block.text[span.start:span.end]
        href = span.href or ((original if re.match(r'^(?:https?://|ftp://|mailto:)', original, re.I)
                              else 'https://' + original) if span.kind == 'url' else None)
        parts.extend((block.text[cursor:span.start], token))
        spans.append(ProtectedSpan(token, original, span.kind, href))
        cursor = span.end
    parts.append(block.text[cursor:])
    return PreparedBlock(block, ''.join(parts), tuple(spans))


def split_block(prepared, limit, max_chunks=128):
    """Prefer sentence, clause, word, then extended grapheme boundaries."""
    text = prepared.text
    if type(limit) is not int or limit < 1 or type(max_chunks) is not int or max_chunks < 1:
        raise TranslationError('Chunk limits must be positive integers.')
    # Match tokens as a unit before graphemes; no split can bisect either.
    units = list(regex.finditer(r'\[\[NER_\d+\]\]|\X', text))
    ends = [unit.end() for unit in units]
    safe = set(ends)
    sentences = {match.end() for match in re.finditer(r'[.!?。！？][\"\')\]]*\s+|\n+', text)} & safe
    clauses = {match.end() for match in re.finditer(r'[;:]\s+|,\s+(?=(?:but|however|and|or)\s)', text, re.I)} & safe
    words = {match.end() for match in re.finditer(r'\s+', text)} & safe
    boundaries_by_priority = [sorted(values) for values in (sentences, clauses, words, safe)]
    chunks, start = [], 0
    while start < len(text):
        edge = min(start + limit, len(text))
        # Do not cut a sentence just because its separator space exceeds the budget.
        while edge < len(text) and text[edge].isspace():
            edge += 1
        if edge == len(text):
            end = edge
        else:
            end = None
            for boundaries in boundaries_by_priority:
                at = bisect_right(boundaries, edge) - 1
                if at >= 0 and boundaries[at] > start:
                    end = boundaries[at]
                    break
            if end is None:
                raise TranslationError('A protected token or Unicode grapheme exceeds CHUNK_CHARS.')
        source = text[start:end]
        leading = source[:len(source) - len(source.lstrip())]
        trailing = source[len(source.rstrip()):] if source.strip() else ''
        chunk = source.strip()
        if chunk:
            if len(chunk) > limit:
                raise TranslationError('Translation chunk exceeds CHUNK_CHARS.')
            chunks.append(TranslationChunk(chunk, leading, trailing,
                                           tuple(match.group() for match in TOKEN.finditer(chunk))))
        elif chunks:
            last = chunks[-1]
            chunks[-1] = TranslationChunk(last.text, last.leading, last.trailing + source, last.tokens)
        if len(chunks) > max_chunks:
            raise TranslationError('Translation block exceeds MAX_CHUNKS.')
        start = end
    return tuple(chunks)


def model_text(text):
    return TOKEN.sub(lambda match: f'X{match[1]}', text)


def canonical_text(text):
    return MODEL_TOKEN.sub(lambda match: f'[[NER_{match[1]}]]', text)


def validate_tokens(text, expected, source):
    actual = [match.group() for match in TOKEN.finditer(text)]
    if len(actual) != len(expected) or set(actual) != set(expected) or len(set(actual)) != len(actual):
        raise TranslationError('Protected token mismatch: missing, duplicated, or unknown literal ID.')
    # Reordering is legal in Japanese. Mutating a URL/identifier around its ID is not.
    for token in expected:
        def boundaries(value):
            at = value.index(token)
            left = re.search(r'[A-Za-z0-9_]+$', value[:at])
            right = re.match(r'(?:[A-Za-z0-9_]+|[/#?&=._%+-][A-Za-z0-9_/?#&=.%+\-]+)',
                             value[at + len(token):])
            return (left.group() if left else '', right.group() if right else '')
        before, after = boundaries(source), boundaries(text)
        if any(value and value != before[index] for index, value in enumerate(after)):
            raise TranslationError('Protected token mismatch: a literal ID was modified or extended.')


def restore_text(text, spans):
    originals = {span.token: span.original for span in spans}
    return TOKEN.sub(lambda match: originals[match.group()], text)


def split_sentences(text, protected_ranges=()):
    """Split extracted English/Japanese prose without downloading tokenizer data.

    This conservative heuristic keeps common abbreviations and initials together;
    it is not a statistical tokenizer. PDF translation itself uses whole blocks.
    """
    prepared = prepare_block(TranslationBlock('', text, protected_ranges=tuple(protected_ranges)))
    source = prepared.text
    sentences, start = [], 0
    abbreviations = {'mr', 'mrs', 'ms', 'dr', 'prof', 'jr', 'sr', 'st',
                     'vs', 'etc', 'fig', 'figs', 'eq', 'eqs', 'no', 'vol', 'al', 'e.g', 'i.e'}
    for match in re.finditer(r'[.!?]+[\"\'”’\)\]]*(?=\s|$)|[。！？]+[\"\'”’\)\]]*', source):
        if match.group().startswith('.') and not match.group().startswith('..'):
            word_start = match.start()
            while word_start > start and (source[word_start - 1].isalnum() or source[word_start - 1] in '._'):
                word_start -= 1
            word = source[word_start:match.start()]
            if (word.lower() in abbreviations or re.fullmatch(r'(?:[A-Za-z]\.)*[A-Za-z]', word)):
                continue
        sentence = source[start:match.end()].strip()
        if sentence:
            sentences.append(restore_text(sentence, prepared.spans))
        start = match.end()
    remainder = source[start:].strip()
    if remainder:
        sentences.append(restore_text(remainder, prepared.spans))
    return sentences
