"""Shared, private backend settings layered over the deployment's YAML file."""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path

from .errors import ConfigurationError
from .io_utils import atomic_output
from .file_locks import exclusive_lock
from .translation import TranslationService
from .yml_load import config_load
from .backend_setup import require_real_credentials

FIELDS = {
    'INDEX': {'BASE_URL': 'text', 'MODEL': 'text', 'API_KEY': 'secret', 'MAX_TOKENS': 'integer',
              'TIMEOUT': 'number', 'CHUNK_CHARS': 'integer'},
    'GOOGLE': {'URL': 'text', 'TIMEOUT': 'number', 'REQUEST_INTERVAL': 'nonnegative', 'CHUNK_CHARS': 'integer'},
    'TEXTRA': {'URL': 'text', 'NAME': 'text', 'KEY': 'secret', 'SECRET': 'secret',
               'TIMEOUT': 'number', 'REQUEST_INTERVAL': 'nonnegative', 'CHUNK_CHARS': 'integer'},
    'BERGAMOT': {'BINARY': 'text', 'WORKER_BINARY': 'text', 'MODEL_CONFIG': 'text',
                 'NUM_WORKERS': 'integer', 'TIMEOUT': 'number', 'CHUNK_CHARS': 'integer'},
}
DEFAULTS = {
    'INDEX': {'MODEL': 'IndexTeam/Index-Translate-2B', 'MAX_TOKENS': 4096, 'TIMEOUT': 300, 'CHUNK_CHARS': 2000},
    'GOOGLE': {'URL': 'https://translate.googleapis.com/translate_a/single', 'TIMEOUT': 60,
               'REQUEST_INTERVAL': 0, 'CHUNK_CHARS': 2000},
    'TEXTRA': {'URL': 'https://mt-auto-minhon-mlt.ucri.jgn-x.jp/api/mt/generalNT_en_ja/',
               'TIMEOUT': 60, 'REQUEST_INTERVAL': 3, 'CHUNK_CHARS': 2000},
    'BERGAMOT': {'BINARY': 'translator-cli', 'NUM_WORKERS': 1, 'TIMEOUT': 300, 'CHUNK_CHARS': 700},
}


class ConfigurationConflict(ConfigurationError):
    pass


class ConfigurationStore:
    def __init__(self, config_path=None, storage_path=None):
        self.base = Path(config_path or os.environ.get('NER_CONFIG_PATH', 'config.yml')).expanduser()
        self.root = Path(storage_path or os.environ.get('NER_STORAGE_PATH', 'pdfstore')).expanduser()
        self.path = self.root / 'backend-settings.json'

    def _overrides(self):
        if not self.path.exists():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(value, dict):
                raise ValueError()
            if any(key not in FIELDS and key != 'PRIORITY' for key in value):
                raise ValueError()
            if any(key in FIELDS and not isinstance(item, dict) for key, item in value.items()):
                raise ValueError()
            return value
        except (OSError, ValueError) as exc:
            raise ConfigurationError('Saved backend settings could not be read.') from exc

    def load(self):
        overrides = self._overrides()
        if self.base.exists():
            base = config_load(self.base)
        elif overrides:
            base = {}
        else:
            raise ConfigurationError('No translation settings have been saved.')
        merged = dict(base)
        for name, values in overrides.items():
            if name in FIELDS:
                merged[name] = {**(base[name] if isinstance(base.get(name), dict) else {}), **values}
            elif name == 'PRIORITY':
                merged[name] = values
        return merged

    def revision(self):
        digest = hashlib.sha256()
        for path in (self.base, self.path):
            content = path.read_bytes() if path.exists() else b''
            digest.update(len(content).to_bytes(8, 'big'))
            digest.update(content)
        return digest.hexdigest()

    def view(self):
        before = self.revision()
        # Missing initial YAML is supported: the UI can create the first settings.
        data = self.load() if self.base.exists() or self.path.exists() else {}
        backends = {}
        for name, fields in FIELDS.items():
            values = {**DEFAULTS[name], **(data[name] if isinstance(data.get(name), dict) else {})}
            backends[name] = {'values': {key: value for key, value in values.items()
                                        if key in fields and fields[key] != 'secret'},
                              'secrets_set': {key: bool(values.get(key)) for key in fields if fields[key] == 'secret'}}
        if before != self.revision():
            raise ConfigurationConflict('Settings changed while they were being read; reload.')
        return {'revision': before, 'default_backend': data.get('PRIORITY'), 'backends': backends}

    @contextmanager
    def _lock(self):
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        with exclusive_lock(self.root / '.backend-settings.lock'):
            yield

    def save(self, name, values, *, revision, make_default=False):
        if name not in FIELDS or not isinstance(values, dict) or set(values) - FIELDS[name].keys():
            raise ConfigurationError('Unknown backend setting.')
        for key, value in values.items():
            kind = FIELDS[name][key]
            if value is None and kind == 'secret':
                continue
            if kind in ('text', 'secret'):
                if not isinstance(value, str) or len(value) > 4096 or any(ord(char) < 32 for char in value):
                    raise ConfigurationError(f'{name}.{key} must be text without control characters.')
            elif (not isinstance(value, (int, float)) or isinstance(value, bool)
                  or kind == 'integer' and not isinstance(value, int)):
                raise ConfigurationError(f'{name}.{key} must be numeric.')
        with self._lock():
            if revision != self.revision():
                raise ConfigurationConflict('Settings have changed. Reload before saving.')
            current = self.load() if self.base.exists() or self.path.exists() else {}
            previous = current.get(name, {})
            section = {**(previous if isinstance(previous, dict) else {}),
                       **{key: value for key, value in values.items() if value is not None}}
            candidate = {**current, name: section, 'PRIORITY': name}
            require_real_credentials(candidate, name)
            with TranslationService(candidate):
                pass
            # Save only supplied fields, preserving deployment defaults and other backends.
            overrides = self._overrides()
            overrides[name] = {**overrides.get(name, {}),
                               **{key: value for key, value in values.items() if value is not None}}
            if make_default:
                overrides['PRIORITY'] = name
            with atomic_output(self.path) as path:
                path.write_text(json.dumps(overrides, ensure_ascii=False, allow_nan=False), encoding='utf-8')
            return self.view()


def effective_config(config_path=None, storage_path=None):
    return ConfigurationStore(config_path, storage_path).load()
