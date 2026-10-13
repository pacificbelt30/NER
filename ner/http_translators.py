"""HTTP transport and online backends, separate from PDF manipulation."""

import time

import requests
from requests_oauthlib import OAuth1

from .errors import TranslationError, RetryableTranslationError
from .validation import number, required


class HTTPTranslator:
    def __init__(self, config, *, local=False, interval=0):
        self.timeout = number(config, 'TIMEOUT', 300 if local else 60)
        self.interval = number(config, 'REQUEST_INTERVAL', interval, inclusive=True)
        self.session = requests.Session()
        if local:
            self.session.trust_env = False
        self.last_request = None

    def request_json(self, method, url, **kwargs):
        if self.last_request is not None:
            remaining = self.interval - (time.monotonic() - self.last_request)
            if remaining > 0:
                time.sleep(remaining)
        self.last_request = time.monotonic()
        try:
            response = self.session.request(method, url, timeout=self.timeout,
                                            allow_redirects=False, **kwargs)
        except (requests.Timeout, requests.ConnectionError) as exc:
            raise RetryableTranslationError('Translation server could not be reached.') from exc
        except requests.RequestException as exc:
            raise TranslationError('Translation request failed.') from exc
        try:
            if response.status_code in (429, 500, 502, 503, 504):
                raise RetryableTranslationError(f'Translation server returned HTTP {response.status_code}.')
            if 300 <= response.status_code < 400:
                raise TranslationError('Translation server returned a redirect.')
            if response.status_code >= 400:
                raise TranslationError(f'Translation server returned HTTP {response.status_code}.')
            try:
                return response.json()
            except ValueError as exc:
                raise TranslationError('Translation server returned invalid JSON.') from exc
        finally:
            response.close()

    def close(self):
        self.session.close()


def checked_text(value, backend):
    if not isinstance(value, str) or not value.strip():
        raise TranslationError(f'{backend} returned an empty or invalid translation.')
    return value.strip()


class GoogleTranslator(HTTPTranslator):
    def __init__(self, config):
        self.url = required(config, 'URL', 'GOOGLE')
        super().__init__(config)

    def translate(self, text):
        if not text.strip():
            return ''
        result = self.request_json('GET', self.url, params={
            'client': 'gtx', 'sl': 'en', 'tl': 'ja', 'dt': 't', 'q': text,
        })
        try:
            translated = ''.join(segment[0] for segment in result[0] if segment[0] is not None)
        except (KeyError, IndexError, TypeError) as exc:
            raise TranslationError('Google returned an invalid translation response.') from exc
        return checked_text(translated, 'Google')


class TextraTranslator(HTTPTranslator):
    def __init__(self, config):
        self.url = required(config, 'URL', 'TEXTRA')
        self.params = {
            'key': required(config, 'KEY', 'TEXTRA'),
            'name': required(config, 'NAME', 'TEXTRA'),
            'type': 'json',
        }
        self.auth = OAuth1(self.params['key'], required(config, 'SECRET', 'TEXTRA'))
        super().__init__(config, interval=3)

    def translate(self, text):
        if not text.strip():
            return ''
        payload = self.request_json('POST', self.url,
                                    data={**self.params, 'text': text}, auth=self.auth)
        try:
            result = payload['resultset']
            code = int(result['code'])
            if code != 0 and code != 200:
                error = RetryableTranslationError if code in (429, 500, 502, 503, 504) else TranslationError
                raise error(f'TexTra returned error code {code}.')
            translated = result['result']['text']
        except (KeyError, TypeError, ValueError) as exc:
            raise TranslationError('TexTra returned an invalid translation response.') from exc
        return checked_text(translated, 'TexTra')
