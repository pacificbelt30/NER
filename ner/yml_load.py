from pathlib import Path

import yaml

from .errors import ConfigurationError


def config_load(path):
    path = Path(path).expanduser()
    try:
        with path.open(encoding='utf-8') as handle:
            config = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError, UnicodeError) as exc:
        raise ConfigurationError(f'Cannot read configuration: {path}') from exc
    if not isinstance(config, dict):
        raise ConfigurationError(f'Configuration must be a mapping: {path}')
    return config

