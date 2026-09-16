"""Explicit JSON/UTC serialization for operational records. No pickle."""
from dataclasses import fields
from datetime import datetime, timezone
import json
import math
import numbers

from execution.contracts import PaperAccount, PaperOrder, PaperFill, PaperPosition, ClosedTrade

PAPER_TYPES = {c.__name__: c for c in (PaperAccount, PaperOrder, PaperFill, PaperPosition, ClosedTrade)}
TIME_FIELDS = {"as_of", "opened_at", "last_processed_at", "fill_timestamp", "exited_at"}
PUBLIC_METADATA_KEYS = {"provider", "model", "version", "model_version", "prompt_version", "schema_version", "deterministic"}


def public_metadata(value):
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items()
            if key in PUBLIC_METADATA_KEYS and (isinstance(item, (str, int, float, bool)) or item is None)
            and (not isinstance(item, str) or len(item) <= 120)}


def utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone-aware datetime required")
    return value.astimezone(timezone.utc).isoformat()


def parse_utc(value):
    if not isinstance(value, str):
        raise ValueError("UTC timestamp string required")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("naive timestamp rejected")
    return parsed.astimezone(timezone.utc)


def safe_json(value):
    def sanitize(item):
        if item is None or isinstance(item, (str, bool)):
            return item
        if isinstance(item, numbers.Integral):
            return int(item)
        if isinstance(item, numbers.Real):
            if not math.isfinite(float(item)):
                raise ValueError("nonfinite JSON number")
            return float(item)
        if isinstance(item, datetime):
            return utc(item)
        if isinstance(item, (tuple, list)):
            return [sanitize(v) for v in item]
        if isinstance(item, dict):
            return {str(k): sanitize(v) for k, v in item.items() if not any(s in str(k).lower() for s in ("secret", "password", "token", "api_key", "credential"))}
        raise TypeError(f"unsupported JSON value: {type(item).__name__}")
    return json.dumps(sanitize(value), ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def paper_encode(obj):
    name = type(obj).__name__
    if name not in PAPER_TYPES:
        raise TypeError("unsupported paper type")
    payload = {}
    for f in fields(obj):
        if name == "PaperAccount" and f.name in {"open_positions", "closed_trades"}:
            continue
        value = getattr(obj, f.name)
        payload[f.name] = utc(value) if f.name in TIME_FIELDS and value is not None else value
    return safe_json(payload)


def paper_decode(name, payload):
    cls = PAPER_TYPES[name]
    data = json.loads(payload)
    allowed = {f.name for f in fields(cls)}
    if not isinstance(data, dict) or set(data) - allowed:
        raise ValueError("invalid paper payload")
    for key in TIME_FIELDS & data.keys():
        if data[key] is not None:
            data[key] = parse_utc(data[key])
    return cls(**data)
