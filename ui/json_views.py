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
    value = ordered(value)
    if not isinstance(value, (list, dict)):
        return escape(str(value), quote=True)
    fields = value if isinstance(value, dict) else (value[0] if value and isinstance(value[0], dict) else {})
    first = ' · '.join(f'{key}: {fields[key]}' for key in KEY_FIELDS if key in fields
                       and not isinstance(fields[key], (dict, list)))
    pretty = json.dumps(value, indent=2, ensure_ascii=False, default=str)
    return (f'<div class="json-key-fields">{escape(first, quote=True)}</div>'
            '<details class="json-details"><summary>Formatted JSON</summary>'
            f'<pre>{escape(pretty, quote=True)}</pre></details>')
