"""Peek-only readers of the OANDA demo caches for the "Floor" page: they NEVER fetch (no broker / network call).

``execution.oanda_readonly`` keeps module-level TTL caches (``_TRADES``: ``since -> (fetched_at, result)``;
``_VIEWS``: ``environment -> (fetched_at, AccountView)``) that the other dashboard pages fill through
``cached_demo_trades`` / ``cached_account_view`` (which refresh, i.e. GET OANDA, when the TTL expired). The Floor only
reads what is already in those caches: the module is looked up in ``sys.modules`` (never imported here, so nothing is
loaded or configured by the Floor), the stored entry is returned as is, and an absent module / entry is None ("sin
datos"). Plug into ``execution.demo_dashboard.demo_snapshot(now, trades_reader=..., account_reader=...)``.
"""
import sys

READONLY_MODULE = "execution.oanda_readonly"


def cached_entry(cache_name, key):
    """The stored value of ``<READONLY_MODULE>.<cache_name>._cache[key]`` or None; never refreshes, never raises."""
    try:
        module = sys.modules.get(READONLY_MODULE)
        cache = getattr(getattr(module, cache_name, None), "_cache", None)
        entry = cache.get(key) if isinstance(cache, dict) else None
        return entry[1] if isinstance(entry, tuple) and len(entry) == 2 else None
    except Exception:  # noqa: BLE001 - display only: unreadable cache = no data
        return None


def peek_demo_trades(since):
    """Last stored demo trades read for ``since`` (``{"closed", "open", "as_of"}`` / ``{"error", ...}``) or None."""
    return cached_entry("_TRADES", since)


def peek_account_view(environment):
    """Last stored AccountView for ``environment`` (``stale``/``as_of`` kept as stored) or None."""
    return cached_entry("_VIEWS", environment)
