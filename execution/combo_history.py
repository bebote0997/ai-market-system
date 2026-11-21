"""Read-only fill of session-combo history from Exness deals.

GET ``/v1/history/accounts/{id}/deals`` only. Nothing here sends an order, changes a
brake, or overwrites a field that is already set. A failed read leaves the flags as
they were.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import urlencode
import json
import logging

from execution.exness_client import safe_detail


LOG = logging.getLogger("ai_floor.session_combo")
HISTORY_START = date(2026, 11, 18)
# Tickets closed before #104 stored no exit_reason. Motivo comes from Floor logs, not from Exness
# (deal.reason was empty). Do not invent fix_30m: 382026 was a manual HQ close.
KNOWN_EXIT_REASONS = {
    "341984": "london_1100",
    "357063": "london_1100",
    "375978": "london_1100",
    "382026": "hq_manual",
}
_TERMINAL = ("done", "skipped", "cancelled")
_DEAL_KEYS = ("type", "position_id", "price", "profit", "instrument", "direction",
              "volume", "create_time", "event_time")


def _log(status, **detail):
    parts = " ".join(f"{key}={value}" for key, value in detail.items() if value is not None)
    LOG.info("component=session_combo event=HISTORY_BACKFILL status=%s%s%s",
             status, " " if parts else "", parts)


def _dec(value):
    if isinstance(value, bool) or value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    try:
        out = Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None
    return out if out.is_finite() else None


def _plain(value):
    amount = _dec(value)
    if amount is None:
        return None
    text = format(amount, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _present(value):
    if value is None or value is False:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    return True


def fill_if_empty(state, field, value):
    """Set ``field`` only when it is missing and ``value`` is usable. Returns True on a write."""
    if not _present(value) or not isinstance(state, dict):
        return False
    if _present(state.get(field)):
        return False
    state[field] = value if not isinstance(value, Decimal) else _plain(value)
    return True


def profit_amount(raw):
    if isinstance(raw, dict):
        return _dec(raw.get("amount"))
    return _dec(raw)


def derive_exit_reason(state, key=None):
    ticket = str((state or {}).get("position_id") or "").strip()
    if ticket in KNOWN_EXIT_REASONS:
        return KNOWN_EXIT_REASONS[ticket]
    if str(key or "").startswith("combo_lon_") or str((state or {}).get("client_id") or "").startswith("combo-lon-"):
        return "london_1100"
    return None


def history_client(broker):
    """The live REST client, or None when the broker cannot GET history."""
    transport = getattr(broker, "_transport", None)
    client = getattr(transport, "_client", None)
    if client is None or not callable(getattr(client, "get_json", None)):
        return None
    if not getattr(client, "account_id", None):
        return None
    return client


def _deal_node(item):
    if not isinstance(item, dict):
        return None
    inner = item.get("deal") or item.get("trade")
    node = inner if isinstance(inner, dict) else item
    if not isinstance(node, dict):
        return None
    row = {k: node.get(k) for k in _DEAL_KEYS}
    if item.get("event_time") and not row.get("event_time"):
        row["event_time"] = item.get("event_time")
    row["position_id"] = str(row.get("position_id") or "").strip() or None
    row["type"] = str(row.get("type") or "").strip().lower() or None
    return row


def fetch_deals(client, start, end, *, get_json=None):
    """GET deal history in ``[start, end]``. Never calls ``mutate``. Does not log the path."""
    if client is None:
        return []
    reader = get_json or getattr(client, "get_json", None)
    if not callable(reader):
        return []
    if not isinstance(start, datetime) or not isinstance(end, datetime):
        return []
    if start.tzinfo is None or end.tzinfo is None or end < start:
        return []
    account = getattr(client, "account_id", None)
    if not account:
        return []
    frm = start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    to = end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    base = "/v1/history/accounts/%s/deals?" % account
    query = {"from": frm, "to": to, "limit": 200}
    rows, pages, cursor = [], 0, None
    while pages < 8:
        path = base + urlencode({"cursor": cursor, "limit": 200} if cursor else query)
        if not path.startswith("/v1/history/accounts/") or "/deals" not in path.split("?", 1)[0]:
            raise RuntimeError("blocked_non_history_get")
        payload = reader(path)
        pages += 1
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            break
        for item in items:
            row = _deal_node(item)
            if row is not None and row.get("position_id"):
                rows.append(row)
        cursor = payload.get("next_cursor") if payload.get("has_more") else None
        if not cursor:
            break
    return rows


def fill_state_from_deals(state, deals, key=None):
    """Fill missing entry, exit_price, pnl and exit_reason. Does not overwrite."""
    if not isinstance(state, dict):
        return False
    changed = fill_if_empty(state, "exit_reason", derive_exit_reason(state, key))
    ticket = str(state.get("position_id") or "").strip()
    if not ticket:
        return changed
    opens, closes = [], []
    for deal in deals or ():
        if not isinstance(deal, dict) or str(deal.get("position_id") or "").strip() != ticket:
            continue
        kind = deal.get("type")
        if kind == "open":
            opens.append(deal)
        elif kind == "close":
            closes.append(deal)
    if opens:
        changed |= fill_if_empty(state, "entry", _plain(opens[0].get("price")))
    if closes:
        changed |= fill_if_empty(state, "exit_price", _plain(closes[-1].get("price")))
        changed |= fill_if_empty(state, "pnl", _plain(profit_amount(closes[-1].get("profit"))))
    return changed


def _aware(value):
    if isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None:
        return value.astimezone(timezone.utc)
    if isinstance(value, str) and value.strip():
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
    return None


def _leg_day(key):
    if not isinstance(key, str) or "_" not in key:
        return None
    try:
        return datetime.fromisoformat(key.rsplit("_", 1)[-1]).date()
    except ValueError:
        return None


def _terminal(state):
    status = (state or {}).get("status")
    return status in _TERMINAL or (status == "filled" and (state or {}).get("exited") is True)


def _incomplete(state):
    if not _terminal(state):
        return False
    return not all(_present(state.get(name)) for name in ("entry", "exit_price", "pnl", "exit_reason"))


def _dump(state):
    payload = dict(state)
    for field in ("exit_at", "opened_at", "protection_after"):
        value = payload.get(field)
        if isinstance(value, datetime):
            payload[field] = value.isoformat()
    return json.dumps(payload, sort_keys=True)


def _iter_combo_keys(store, today):
    day = HISTORY_START
    last = today + timedelta(days=1)
    while day <= last:
        for prefix in ("combo_lon_", "combo_fix_"):
            key = prefix + day.isoformat()
            raw = None
            try:
                raw = store.get_flag(key)
            except Exception:  # noqa: BLE001
                raw = None
            if raw:
                yield key, raw
        day += timedelta(days=1)


def _load_state(raw):
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _window(states):
    instants = []
    for key, state in states:
        for raw in (state.get("opened_at"), state.get("exit_at")):
            at = _aware(raw)
            if at is not None:
                instants.append(at)
        day = _leg_day(key)
        if day is not None:
            instants.append(datetime(day.year, day.month, day.day, tzinfo=timezone.utc))
    if not instants:
        start = datetime(HISTORY_START.year, HISTORY_START.month, HISTORY_START.day, tzinfo=timezone.utc)
        return start, datetime.now(timezone.utc) + timedelta(hours=1)
    start = min(instants) - timedelta(hours=6)
    end = max(instants) + timedelta(hours=6)
    now = datetime.now(timezone.utc) + timedelta(hours=1)
    if end < now:
        end = now
    return start, end


def remember_exit(state, ticket=None, *, key=None, broker=None, deals=None, get_json=None):
    """Fill missing exit fields on a just-closed leg. Swallows a failed deals read."""
    if ticket and not state.get("position_id"):
        state["position_id"] = ticket
    if deals is None and broker is not None and state.get("position_id"):
        try:
            client = history_client(broker)
            opened = _aware(state.get("opened_at")) or datetime.now(timezone.utc) - timedelta(days=2)
            ended = datetime.now(timezone.utc) + timedelta(hours=1)
            deals = fetch_deals(client, opened, ended, get_json=get_json)
        except Exception as exc:  # noqa: BLE001
            _log("failed", reason="read_failed", error_type=type(exc).__name__)
            deals = ()
    return fill_state_from_deals(state, deals or (), key=key)


def backfill_combo_history(broker, *, now=None, get_json=None):
    """Idempotent GET-only backfill of closed combo flags. Never raises to the caller."""
    result = {"updated": 0, "pending": 0, "fetched": False}
    try:
        store = getattr(broker, "flag_store", None)
        if store is None or not callable(getattr(store, "get_flag", None)):
            return result
        if now is None:
            clock = getattr(broker, "_clock", None)
            now = clock() if callable(clock) else datetime.now(timezone.utc)
        if not isinstance(now, datetime) or now.tzinfo is None:
            now = datetime.now(timezone.utc)
        today = now.astimezone(timezone.utc).date()
        loaded = []
        for key, raw in _iter_combo_keys(store, today):
            state = _load_state(raw)
            if state is not None and _incomplete(state):
                loaded.append((key, state))
        need_deals = [(key, state) for key, state in loaded if str(state.get("position_id") or "").strip()]
        result["pending"] = len(loaded)
        deals = []
        if need_deals:
            client = history_client(broker)
            if client is None:
                _log("skip", reason="no_client", pending=len(loaded))
                return result
            start, end = _window(need_deals)
            deals = fetch_deals(client, start, end, get_json=get_json)
            result["fetched"] = True
        for key, state in loaded:
            if not fill_state_from_deals(state, deals, key=key):
                continue
            store.set_flag(key, _dump(state))
            result["updated"] += 1
        still = 0
        for key, state in loaded:
            if _incomplete(state) and str(state.get("position_id") or "").strip():
                still += 1
        result["pending"] = still
        _log("ok", updated=result["updated"], pending=still)
        return result
    except Exception as exc:  # noqa: BLE001 - a history miss must not break the session
        _log("failed", reason="read_failed", error_type=type(exc).__name__,
             detail=safe_detail(exc))
        return result
