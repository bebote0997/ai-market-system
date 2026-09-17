"""Parse HTTP Retry-After without logging response headers or request data."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


def retry_after_seconds(headers, now=None):
    value = headers.get("Retry-After") if headers is not None else None
    if not isinstance(value, str):
        return None
    value = value.strip()
    if value.isdigit():
        return int(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if when.tzinfo is None:
        return None
    now = now or datetime.now(timezone.utc)
    return max(0, int((when - now).total_seconds() + 0.999))
