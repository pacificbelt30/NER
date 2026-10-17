"""Errors exposed by the CLI and API without hiding their original cause."""


class ConfigurationError(ValueError):
    pass


class PDFError(ValueError):
    pass


class TranslationError(RuntimeError):
    pass


class ProtectedContentError(TranslationError):
    """A rejected candidate, retained only for explicit human review."""

    def __init__(self, message, candidate, *, complete):
        super().__init__(message)
        self.candidate = candidate
        self.complete = complete


class RetryableTranslationError(TranslationError):
    pass


class TranslationCancelled(TranslationError):
    pass
