"""Start of the OANDA practice demo ("epoch"): the dashboard counts demo numbers from here on, never PAPER history.

Routing to the practice account was switched on by the deploy at 2026-10-18 18:25 ET (22:25 UTC). The owner may move
the epoch with ``AI_FLOOR_DEMO_EPOCH`` (ISO 8601 WITH offset, e.g. ``2026-10-18T22:25:00Z``); an invalid or naive
value keeps the default. Routed trades carry the OANDA clientExtensions id ``aif-<run_id>``; the one-off manual test
order (``aif-test-<UTC>``) is never a demo trade. The demo's starting equity (DEC-DEMO-GATE-1 baseline of the PAPER
5% drawdown gate while routing is ON) is the practice NAV at the epoch; ``AI_FLOOR_DEMO_START_EQUITY`` (positive
decimal) overrides it, an invalid value keeps the default. Pure; never raises.
"""
from datetime import datetime, timezone
from decimal import Decimal
import os
from zoneinfo import ZoneInfo

EPOCH_ENV = "AI_FLOOR_DEMO_EPOCH"
DEFAULT_DEMO_EPOCH_UTC = "2026-10-18T22:25:00Z"
DEMO_PREFIX, TEST_PREFIX = "aif-", "aif-test-"
START_EQUITY_ENV = "AI_FLOOR_DEMO_START_EQUITY"
DEFAULT_DEMO_START_EQUITY = Decimal("99999.977")  # OANDA practice NAV at the epoch (2026-10-18 18:25 ET)
NEW_YORK = ZoneInfo("America/New_York")


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


def demo_day_from_epoch(epoch, now):
    """"Día N" of the demo, the ONE rule for screens, alerts, daily summary and email: day 1 = the New York calendar
    date of ``epoch``, every NY date after it adds one (weekends count). Uncapped int (> 30 = complete); None when
    either timestamp is unknown / naive or ``now`` is before the epoch."""
    if not isinstance(epoch, datetime) or not isinstance(now, datetime):
        return None
    if epoch.tzinfo is None or now.tzinfo is None or now < epoch:
        return None
    return (now.astimezone(NEW_YORK).date() - epoch.astimezone(NEW_YORK).date()).days + 1


def demo_start_equity(env=None):
    """Positive Decimal; ``AI_FLOOR_DEMO_START_EQUITY`` override when valid, else the NAV at the epoch. Never raises."""
    try:
        raw = (os.environ if env is None else env).get(START_EQUITY_ENV)
        value = Decimal(raw.strip()) if isinstance(raw, str) and raw.strip() else None
    except Exception:  # noqa: BLE001 - malformed value or broken env mapping keeps the default
        value = None
    return value if value is not None and value.is_finite() and value > 0 else DEFAULT_DEMO_START_EQUITY


def is_demo_trade(client_id):
    """True only for router-routed trades (``aif-<run_id>``); the manual ``aif-test-*`` order is excluded."""
    return (isinstance(client_id, str) and client_id.startswith(DEMO_PREFIX)
            and not client_id.startswith(TEST_PREFIX) and len(client_id) > len(DEMO_PREFIX))
