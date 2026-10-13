"""Backend selection, bounded duplicate cache, and transient-error retries."""

from collections import OrderedDict
from dataclasses import replace
import time

from .errors import ConfigurationError, RetryableTranslationError, TranslationError
from .http_translators import GoogleTranslator, TextraTranslator, checked_text
from .local_translators import IndexTranslator, BergamotTranslator
from .validation import number, section
from .text_blocks import (RECIPE_VERSION, TOKEN, BlockTranslation, TranslationBlock, canonical_text,
                          model_text, prepare_block, restore_text, split_block, validate_tokens)


BACKENDS = {
    'GOOGLE': GoogleTranslator,
    'TEXTRA': TextraTranslator,
    'INDEX': IndexTranslator,
    'BERGAMOT': BergamotTranslator,
}


class TranslationService:
    def __init__(self, config, priority='GOOGLE'):
        self.name = config.get('PRIORITY', priority)
        if not isinstance(self.name, str) or self.name not in BACKENDS:
            raise ConfigurationError(f'Unknown translation service: {self.name}')
        self.retries = number(config, 'RETRIES', 1, integer=True, inclusive=True)
        self.cache_size = number(config, 'CACHE_SIZE', 1024, integer=True, inclusive=True)
        backend_config = section(config, self.name)
        self.chunk_chars = number(backend_config, 'CHUNK_CHARS',
                                  700 if self.name == 'BERGAMOT' else 2000, integer=True)
        self.max_chunks = number(config, 'MAX_CHUNKS', 128, integer=True)
        self.backend = BACKENDS[self.name](backend_config)
        self.cache = OrderedDict()

    def _cache(self, key, result):
        if self.cache_size:
            self.cache[key] = result
            self.cache.move_to_end(key)
            if len(self.cache) > self.cache_size:
                self.cache.popitem(last=False)

    def _request(self, text, *, protected_tokens=(), context='', kind='paragraph'):
        for attempt in range(self.retries + 1):
            try:
                if hasattr(self.backend, 'translate_chunk'):
                    value = self.backend.translate_chunk(text, protected_tokens=protected_tokens,
                                                         context=context, kind=kind)
                else:
                    value = self.backend.translate(text)
                return checked_text(value, self.name)
            except RetryableTranslationError:
                if attempt == self.retries:
                    raise
                time.sleep(min(0.5 * 2 ** attempt, 4))

    def translate(self, text):
        if not text.strip():
            return ''
        if text in self.cache:
            self.cache.move_to_end(text)
            return self.cache[text]
        translated = self._request(text)
        self._cache(text, translated)
        return translated

    def translate_block(self, block, on_progress=None):
        prepared = prepare_block(block)
        context = TOKEN.sub('[protected literal]', prepare_block(
            TranslationBlock('context', block.context)).text)
        key = ('block', RECIPE_VERSION, self.name, self.chunk_chars, self.max_chunks,
               block.kind, block.context, prepared.text,
               tuple((span.original, span.kind, span.href) for span in prepared.spans))
        if key in self.cache:
            cached = self.cache[key]
            self.cache.move_to_end(key)
            if on_progress:
                on_progress(0, cached.chunk_count)
                on_progress(cached.chunk_count, cached.chunk_count)
            return replace(cached, id=block.id)
        chunks = split_block(prepared, self.chunk_chars, self.max_chunks)
        if on_progress:
            on_progress(0, len(chunks))
        parts = []
        for index, chunk in enumerate(chunks):
            aliases = tuple(model_text(token) for token in chunk.tokens)
            try:
                translated = self._request(model_text(chunk.text), protected_tokens=aliases,
                                           context=context, kind=block.kind)
                canonical = canonical_text(translated) if prepared.spans else translated
                validate_tokens(canonical, chunk.tokens, chunk.text)
            except TranslationError as exc:
                raise TranslationError(f'Block {block.id}, chunk {index + 1}/{len(chunks)}: {exc}') from exc
            parts.extend((chunk.leading, canonical, chunk.trailing))
            if on_progress:
                on_progress(index + 1, len(chunks))
        canonical = ''.join(parts)
        validate_tokens(canonical, tuple(span.token for span in prepared.spans), prepared.text)
        result = BlockTranslation(block.id, restore_text(canonical, prepared.spans), canonical,
                                  prepared.spans, len(chunks))
        # No incomplete block or unvalidated chunk is stored as a successful result.
        self._cache(key, result)
        return result

    def close(self):
        self.cache.clear()
        self.backend.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
