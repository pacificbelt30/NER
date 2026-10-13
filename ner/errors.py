"""Errors exposed by the CLI and API without hiding their original cause."""


class ConfigurationError(ValueError):
    pass


class PDFError(ValueError):
    pass


class TranslationError(RuntimeError):
    pass


class RetryableTranslationError(TranslationError):
    pass


class TranslationCancelled(TranslationError):
    pass
