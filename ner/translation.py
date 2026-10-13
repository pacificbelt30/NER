"""Backend selection, bounded duplicate cache, and transient-error retries."""

from collections import OrderedDict
import time

from .errors import ConfigurationError, RetryableTranslationError, TranslationError
from .http_translators import GoogleTranslator, TextraTranslator, checked_text
from .local_translators import IndexTranslator, BergamotTranslator
from .validation import number, section


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
        self.backend = BACKENDS[self.name](backend_config)
        self.cache = OrderedDict()

    def _cache(self, key, result):
        if self.cache_size:
            self.cache[key] = result
            self.cache.move_to_end(key)
            if len(self.cache) > self.cache_size:
                self.cache.popitem(last=False)

    def _request(self, text):
        for attempt in range(self.retries + 1):
            try:
                return checked_text(self.backend.translate(text), self.name)
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


    def close(self):
        self.cache.clear()
        self.backend.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
