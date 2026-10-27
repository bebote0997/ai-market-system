"""Peek-only readers of the OANDA demo caches for the "Floor" page: they NEVER fetch (no broker / network call).

``execution.oanda_readonly`` keeps module-level TTL caches (``_TRADES``: ``since -> (fetched_at, result)``;
``_VIEWS``: ``environment -> (fetched_at, AccountView)``) that the other dashboard pages fill through
``cached_demo_trades`` / ``cached_account_view`` (which refresh, i.e. GET OANDA, when the TTL expired). The Floor only
reads what is already in those caches: the module is looked up in ``sys.modules`` (never imported here, so nothing is
loaded or configured by the Floor), the stored entry is returned as is, and an absent module / entry is None ("sin
datos"; ``read_attempted`` tells a cold cache from a stored failed read). Plug into ``execution.demo_dashboard.demo_snapshot(now, trades_reader=..., account_reader=...)``.

The ``ui.demo_refresher`` producer keeps those caches warm with the same keys while the demo is shown; without it (no
credentials, producer stopped / backing off) a peeked entry can be arbitrarily old, so the trades carry their
observation time (``observed_at``) for the Floor to mark them "(dato antiguo)" like the NAV.
"""
import sys
from datetime import datetime, timedelta, timezone

READONLY_MODULE = "execution.oanda_readonly"


def stored_entry(cache_name, key):
    """``(holder, (fetched_at, value))`` of ``<READONLY_MODULE>.<cache_name>._cache[key]`` or ``(None, None)`` when
    absent / unreadable; never refreshes, never raises."""
    try:
        module = sys.modules.get(READONLY_MODULE)
        holder = getattr(module, cache_name, None)
        cache = getattr(holder, "_cache", None)
        entry = cache.get(key) if isinstance(cache, dict) else None
        if not (isinstance(entry, tuple) and len(entry) == 2):
            return None, None
    except Exception:  # noqa: BLE001 - display only: unreadable cache = no data
        return None, None
    return holder, entry


def cached_observation(cache_name, key):
    """``(observed_at, value)`` of ``<READONLY_MODULE>.<cache_name>._cache[key]`` or ``(None, None)``; never refreshes,
    never raises. ``observed_at``: aware UTC time the entry was stored, from the cache's own clock (``(fetched_at,
    value)`` with ``fetched_at`` on ``_clock``); None when that clock is unusable."""
    holder, entry = stored_entry(cache_name, key)
    if entry is None:
        return None, None
    try:
        age = float(holder._clock()) - float(entry[0])
        observed = datetime.now(timezone.utc) - timedelta(seconds=max(0.0, age))
    except Exception:  # noqa: BLE001 - unknown observation time, never "now"
        observed = None
    return observed, entry[1]


def cached_entry(cache_name, key):
    """The stored value of ``<READONLY_MODULE>.<cache_name>._cache[key]`` or None; never refreshes, never raises."""
    return cached_observation(cache_name, key)[1]


def read_attempted(cache_name, key):
    """True when the cache holds an entry for ``key``, even a stored None: ``CachedDemoTrades`` caches None after a
    failed read (auth / connection error), which is "sin datos", not a cold cache still loading. Never raises."""
    return stored_entry(cache_name, key)[1] is not None


def peek_demo_trades(since):
    """Last stored demo trades read for ``since`` (``{"closed", "open", "as_of"}`` / ``{"error", ...}``) or None, plus
    ``observed_at``: the read's own ``as_of`` when it is an aware datetime, else the cache store time (or None)."""
    stored_at, value = cached_observation("_TRADES", since)
    if not isinstance(value, dict):
        return value
    as_of = value.get("as_of")
    aware = isinstance(as_of, datetime) and as_of.tzinfo is not None
    return {**value, "observed_at": as_of if aware else stored_at}


def peek_account_view(environment):
    """Last stored AccountView for ``environment`` (``stale``/``as_of`` kept as stored) or None."""
    return cached_entry("_VIEWS", environment)
