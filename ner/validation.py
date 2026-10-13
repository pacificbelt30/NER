import math

from .errors import ConfigurationError


def number(config, key, default, *, minimum=0, integer=False, inclusive=False):
    value = config.get(key, default)
    valid = isinstance(value, int if integer else (int, float)) and not isinstance(value, bool)
    if (not valid or not math.isfinite(value)
            or (value < minimum if inclusive else value <= minimum)):
        kind = 'integer' if integer else 'number'
        relation = '>=' if inclusive else '>'
        raise ConfigurationError(f'{key} must be a finite {kind} {relation} {minimum}.')
    return value


def section(config, name):
    value = config.get(name, {})
    if not isinstance(value, dict):
        raise ConfigurationError(f'{name} must be a configuration mapping.')
    return value


def required(config, key, prefix):
    value = config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f'{prefix}.{key} must be a nonempty string.')
    return value
