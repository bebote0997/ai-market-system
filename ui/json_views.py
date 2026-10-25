"""Escaped, formatted JSON with contract identities and outcomes first."""
from html import escape
import json

KEY_FIELDS = ('run_id', 'setup_id', 'symbol', 'as_of', 'timestamp', 'status', 'final_status',
              'setup_status', 'decision', 'reason', 'side', 'entry', 'stop', 'target', 'risk_reward')


def structured(value):
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, (dict, list)) else value
        except (ValueError, TypeError):
            return value
    return value


def ordered(value):
    value = structured(value)
    if isinstance(value, dict):
        keys = [k for k in KEY_FIELDS if k in value] + [k for k in value if k not in KEY_FIELDS]
        return {key: ordered(value[key]) for key in keys}
    if isinstance(value, (list, tuple)):
        return [ordered(item) for item in value]
    return value


def details(value):
    from ui.i18n_es import readable
    return escape(readable(value), quote=True)
