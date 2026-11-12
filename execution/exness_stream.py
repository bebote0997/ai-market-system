"""Quote stream state. The docs say reconnect with backoff, then resubscribe and reconcile.

The delay schedule is Floor policy: the portal says "reconnect with backoff" and does not give the delays.
A dropped socket clears the last tick immediately, so the adapter opens nothing until a new tick arrives.
The quote book is written by the tick thread and read by the cycle thread.
"""
from threading import RLock
from urllib.parse import urlsplit


TICKS_PATH = "/v1/server-events/accounts/{account_id}/ws/ticks"
EVENTS_PATH = "/v1/server-events/accounts/{account_id}/ws/events"
BACKOFF_CAP_SECONDS = 30


def ticks_path(account_id):
    if not isinstance(account_id, str) or not account_id.isdigit():
        raise ValueError("account_id must be an unsigned integer string")
    return TICKS_PATH.format(account_id=account_id)


def events_path(account_id):
    if not isinstance(account_id, str) or not account_id.isdigit():
        raise ValueError("account_id must be an unsigned integer string")
    return EVENTS_PATH.format(account_id=account_id)


def ws_url(access_point, path):
    parts = urlsplit(access_point if isinstance(access_point, str) else "")
    if parts.scheme not in ("https", "wss") or not parts.hostname or parts.username or parts.password:
        raise ValueError("access_point must be an https URL without userinfo")
    scheme = "wss"
    port = f":{parts.port}" if parts.port else ""
    return f"{scheme}://{parts.hostname}{port}{path}"


def backoff_seconds(attempt):
    """1, 2, 4, ... capped at 30. ``attempt`` is the number of failed connects so far, starting at 0."""
    if type(attempt) is not int or attempt < 0:
        raise ValueError("attempt must be a non-negative int")
    return min(BACKOFF_CAP_SECONDS, 2 ** min(attempt, 5))


class _Quote:
    def __init__(self, bid, ask, at):
        self.bid = bid
        self.ask = ask
        self.tick_at = at


class QuoteBook:
    """One bid/ask per symbol. ``connected`` is false the moment the socket drops.

    A tick without a symbol stays on the unspecialized slot so a single-instrument test still
    resolves through ``quote(symbol)``. A named tick never overwrites another symbol.
    """

    def __init__(self):
        self.connected = False
        self.disconnects = 0
        self._default = None
        self._by_symbol = {}
        self._last = None
        self._lock = RLock()

    def note_tick(self, bid, ask, at, symbol=None):
        with self._lock:
            self.connected = True
            slot = _Quote(bid, ask, at)
            if symbol is None:
                self._default = slot
                self._last = None
                return
            self._by_symbol[symbol] = slot
            self._last = symbol

    def quote(self, symbol=None):
        with self._lock:
            if symbol is not None and symbol in self._by_symbol:
                return self._by_symbol[symbol]
            if symbol is not None and self._default is not None and not self._by_symbol:
                return self._default
            if symbol is None and self._last is not None:
                return self._by_symbol.get(self._last)
            return self._default

    @property
    def bid(self):
        slot = self.quote()
        return None if slot is None else slot.bid

    @property
    def ask(self):
        slot = self.quote()
        return None if slot is None else slot.ask

    @property
    def tick_at(self):
        slot = self.quote()
        return None if slot is None else slot.tick_at

    def mark_disconnected(self):
        with self._lock:
            self.connected = False
            self.disconnects += 1
            if self._default is not None:
                self._default.tick_at = None
            for slot in self._by_symbol.values():
                slot.tick_at = None

    def fresh(self, now, stale_seconds, symbol=None):
        with self._lock:
            if not self.connected or not isinstance(stale_seconds, int) or stale_seconds < 1:
                return False
            slot = self.quote(symbol)
            if slot is None or slot.tick_at is None:
                return False
            age = (now - slot.tick_at).total_seconds()
            return 0 <= age <= stale_seconds


def subscribe_ticks(instruments):
    """The documented ticks subscription. Instruments must already be on the account list."""
    if not isinstance(instruments, (list, tuple)) or not instruments or not all(isinstance(i, str) and i for i in instruments):
        raise ValueError("instruments required")
    return {"id": "floor-ticks", "subscribe": {"event": "ticks", "instruments": list(instruments)}}
