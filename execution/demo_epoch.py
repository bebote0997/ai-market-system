"""Start of the OANDA practice demo ("epoch"): the dashboard counts demo numbers from here on, never PAPER history.

Routing to the practice account was switched on by the deploy at 2026-10-18 18:25 ET (22:25 UTC). The owner may move
the epoch with ``AI_FLOOR_DEMO_EPOCH`` (ISO 8601 WITH offset, e.g. ``2026-10-18T22:25:00Z``); an invalid or naive
value keeps the default. Routed trades carry the OANDA clientExtensions id ``aif-<run_id>``; the one-off manual test
order (``aif-test-<UTC>``) is never a demo trade. Pure; never raises.
"""
from datetime import datetime, timezone
import os

EPOCH_ENV = "AI_FLOOR_DEMO_EPOCH"
DEFAULT_DEMO_EPOCH_UTC = "2026-10-18T22:25:00Z"
DEMO_PREFIX, TEST_PREFIX = "aif-", "aif-test-"


def parse_aware(value):
    """Aware UTC datetime from an ISO 8601 string with offset (``Z`` accepted); anything else -> None."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def demo_epoch(env=None):
    """Aware UTC datetime; ``AI_FLOOR_DEMO_EPOCH`` override when valid, else the deploy time. Never raises."""
    try:
        override = parse_aware((os.environ if env is None else env).get(EPOCH_ENV))
    except Exception:  # noqa: BLE001 - a broken env mapping keeps the default
        override = None
    return override or parse_aware(DEFAULT_DEMO_EPOCH_UTC)


def is_demo_trade(client_id):
    """True only for router-routed trades (``aif-<run_id>``); the manual ``aif-test-*`` order is excluded."""
    return (isinstance(client_id, str) and client_id.startswith(DEMO_PREFIX)
            and not client_id.startswith(TEST_PREFIX) and len(client_id) > len(DEMO_PREFIX))
