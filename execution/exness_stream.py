"""Quote stream state. The docs say reconnect with backoff, then resubscribe and reconcile.

The delay schedule is Floor policy: the portal says "reconnect with backoff" and does not give the delays.
A dropped socket clears the last tick immediately, so the adapter opens nothing until a new tick arrives.
"""
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


class QuoteBook:
    """Last tick. ``connected`` is false the moment the socket drops, before the backoff wait."""

    def __init__(self):
        self.connected = False
        self.bid = None
        self.ask = None
        self.tick_at = None
        self.disconnects = 0

    def note_tick(self, bid, ask, at):
        self.connected = True
        self.bid = bid
        self.ask = ask
        self.tick_at = at

    def mark_disconnected(self):
        self.connected = False
        self.tick_at = None
        self.disconnects += 1

    def fresh(self, now, stale_seconds):
        if not self.connected or self.tick_at is None or not isinstance(stale_seconds, int) or stale_seconds < 1:
            return False
        age = (now - self.tick_at).total_seconds()
        return 0 <= age <= stale_seconds


def subscribe_ticks(instruments):
    """The documented ticks subscription. Instruments must already be on the account list."""
    if not isinstance(instruments, (list, tuple)) or not instruments or not all(isinstance(i, str) and i for i in instruments):
        raise ValueError("instruments required")
    return {"id": "floor-ticks", "subscribe": {"event": "ticks", "instruments": list(instruments)}}
