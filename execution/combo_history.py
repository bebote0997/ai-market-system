"""Read-only fill of session-combo history from Exness deals.

GET ``/v1/history/accounts/{id}/deals`` only. Nothing here sends an order, changes a
brake, or overwrites a field that is already set. A failed read leaves the flags as
they were.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
import json
import logging
import sys

from execution.exness_client import safe_detail
from execution.session_combo import LONDON_SYMBOL


LOG = logging.getLogger("ai_floor.session_combo")
HISTORY_START = date(2026, 11, 18)
# The table starts on HISTORY_START. The cache also keeps earlier closes so the
# P/L-total note can show them without putting those rows in the Historial.
CACHE_LOOKBACK_DAYS = 14
NY = ZoneInfo("America/New_York")
_MONTHS_ES = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")
_MATCH_SECONDS = 120
# Tickets closed before #104 stored no exit_reason. Motivo comes from Floor logs, not from Exness
# (deal.reason was empty). Do not invent fix_30m: 382026 was a manual HQ close.
# HQ confirmed the 25 Nov 2026 close of position 440549 was manual. The deal reason was empty.
# This is the only ticket forced here. Any other ticket keeps the flag's own reason.
TICKET_EXIT_OVERRIDE = {"440549": "hq_manual"}
CLOSED_DEALS_FLAG = "exness_closed_deals"
LONDON_FLAG_KEY = "combo_lon_2026-11-25"

KNOWN_EXIT_REASONS = {
    "341984": "london_1100",
    "357063": "london_1100",
    "375978": "london_1100",
    "382026": "hq_manual",
    # 25 Nov USDJPY 440549, closed by hand about 7 min after 11:00 ET. The deal reason was empty.
    "440549": "hq_manual",
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


def _deal_side(direction):
    text = str(direction or "").strip().lower()
    if text in ("sell", "short"):
        return "SHORT"
    if text in ("buy", "long"):
        return "LONG"
    return None


def _write_exit_reason(state, key=None):
    """A ticket in ``KNOWN_EXIT_REASONS`` wins over a reason already stored.

    Other rows stay as they are: a blank reason is filled, a stored one is not.
    """
    ticket = str((state or {}).get("position_id") or "").strip()
    known = KNOWN_EXIT_REASONS.get(ticket)
    if known:
        if state.get("exit_reason") != known:
            state["exit_reason"] = known
            return True
        return False
    return fill_if_empty(state, "exit_reason", derive_exit_reason(state, key))


def fill_state_from_deals(state, deals, key=None):
    """Fill missing entry, exit_price, pnl, volume and side. A known exit reason wins."""
    if not isinstance(state, dict):
        return False
    changed = _write_exit_reason(state, key)
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
        changed |= fill_if_empty(state, "volume", _plain(opens[0].get("volume")))
        changed |= fill_if_empty(state, "side", _deal_side(opens[0].get("direction")))
    if closes:
        changed |= fill_if_empty(state, "exit_price", _plain(closes[-1].get("price")))
        amounts = []
        for deal in closes:
            amount = profit_amount(deal.get("profit"))
            if amount is None:
                amounts = None
                break
            amounts.append(amount)
        if amounts:
            changed |= fill_if_empty(state, "pnl", _plain(sum(amounts, Decimal(0))))
    return changed


def _fill_times_from_deals(state, deals):
    """Horario de entrada y salida del propio deal, solo si el flag no lo tiene."""
    ticket = str((state or {}).get("position_id") or "").strip()
    if not ticket:
        return False
    opens, closes = [], []
    for deal in deals or ():
        if not isinstance(deal, dict) or str(deal.get("position_id") or "").strip() != ticket:
            continue
        if deal.get("type") == "open":
            opens.append(deal)
        elif deal.get("type") == "close":
            closes.append(deal)
    changed = False
    if not state.get("opened_at") and opens:
        stamp = _deal_time(opens[0])
        if stamp is not None:
            state["opened_at"] = stamp.astimezone(timezone.utc).isoformat()
            changed = True
    if not state.get("exit_at") and closes:
        stamp = _deal_time(closes[-1])
        if stamp is not None:
            state["exit_at"] = stamp.astimezone(timezone.utc).isoformat()
            changed = True
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


def _price_gap(state):
    """Ticket ya guardado, pero falta la entrada, la salida, el lado o los lotes."""
    if _no_ticket(state):
        return False
    return any(not _present(state.get(name)) for name in ("entry", "exit_price", "side", "volume"))


def _deal_gap(state):
    """Ese hueco, o la hora de apertura o de cierre todavía vacía."""
    if _price_gap(state):
        return True
    if _no_ticket(state):
        return False
    return _aware(state.get("exit_at")) is None or _aware(state.get("opened_at")) is None


def _filled_open(state):
    """Pata ``filled`` que el flag aún no dio por salida."""
    return (state or {}).get("status") == "filled" and (state or {}).get("exited") is not True


def _no_ticket(state):
    return not str((state or {}).get("position_id") or "").strip()


def _blank_terminal(state):
    """Cerrada en el flag, pero sin ticket: el Historial no puede pintarla."""
    return _incomplete(state) and _no_ticket(state)


def _with_london_symbol(state, key):
    """combo_lon_ sin par es USDJPY. No se guarda si el ticket no se cruza."""
    if str((state or {}).get("symbol") or "").strip():
        return state
    if not str(key or "").startswith("combo_lon_"):
        return state
    guessed = dict(state)
    guessed["symbol"] = LONDON_SYMBOL
    return guessed


def _never_filled(state, key):
    """Un Fix omitido, o una pata sin hora de entrada que no es el Londres ya cerrado.

    El ``combo_lon_`` ``done`` con ``exited`` sigue siendo candidato aunque no tenga
    ``opened_at``: en vivo el lado o el par guardados pueden estar mal.
    """
    status = (state or {}).get("status")
    if status in ("skipped", "cancelled"):
        return True
    if _aware((state or {}).get("opened_at")):
        return False
    return not (str(key or "").startswith("combo_lon_") and _terminal(state) and (state or {}).get("exited") is True)


def _london_day_ticket(key, deals, rows):
    """El único cierre USDJPY de ese día ET. Ignora el lado y el par guardados.

    Cero o dos o más candidatos: None. No se escribe el flag.
    """
    if rows is None or not str(key or "").startswith("combo_lon_"):
        return None
    day = _leg_day(key)
    if day is None:
        return None
    open_ids = {_row_ticket(row) for row in rows}
    grouped = {}
    for deal in deals or ():
        if not isinstance(deal, dict):
            continue
        ticket = str(deal.get("position_id") or "").strip()
        if ticket:
            grouped.setdefault(ticket, []).append(deal)
    found = []
    for ticket, items in grouped.items():
        if not ticket or ticket in open_ids:
            continue
        opens = [item for item in items if item.get("type") == "open"]
        closes = [item for item in items if item.get("type") == "close"]
        if not closes:
            continue
        sample = opens[0] if opens else closes[-1]
        inst = str(sample.get("instrument") or "").strip().upper()
        if inst != LONDON_SYMBOL:
            continue
        stamp = _deal_time(opens[0]) if opens else _deal_time(closes[-1])
        if stamp is None or stamp.astimezone(NY).date() != day:
            continue
        found.append(ticket)
    if len(found) == 1:
        return found[0]
    return None


def _open_side(deals, ticket):
    for deal in deals or ():
        if not isinstance(deal, dict) or str(deal.get("position_id") or "").strip() != ticket:
            continue
        if deal.get("type") == "open":
            return _deal_side(deal.get("direction"))
    return None


def _correct(key, field, old, new):
    if not _present(new) or old == new:
        return
    if not _present(old):
        return
    LOG.warning("component=session_combo event=HISTORY_BACKFILL status=correct key=%s field=%s old=%s new=%s",
                key, field, old, new)


def _pending_line(key, state):
    """Una línea, sin importes ni secretos, para ver el flag que sigue pendiente.

    ``flag`` es el status guardado. ``status`` de la línea ya es el evento (pending).
    """
    LOG.info(
        "component=session_combo event=HISTORY_BACKFILL status=pending key=%s flag=%s exited=%s "
        "side=%s symbol=%s has_ticket=%s has_pnl=%s opened_at=%s",
        key, (state or {}).get("status"), (state or {}).get("exited"),
        (state or {}).get("side") or "none", (state or {}).get("symbol") or "none",
        "no" if _no_ticket(state) else "yes",
        "yes" if _present((state or {}).get("pnl")) else "no",
        (state or {}).get("opened_at") or "none")


def _row_ticket(row):
    if isinstance(row, dict):
        return str(row.get("ticket") or row.get("position_id") or "").strip()
    return str(getattr(row, "ticket", None) or getattr(row, "position_id", None) or "").strip()


def _row_symbol(row):
    if isinstance(row, dict):
        raw = row.get("symbol") or row.get("instrument")
    else:
        raw = getattr(row, "symbol", None) or getattr(row, "instrument", None)
    return str(raw or "").strip().upper()


def _row_side(row):
    if isinstance(row, dict):
        raw = row.get("side") or row.get("direction")
    else:
        raw = getattr(row, "side", None) or getattr(row, "direction", None)
    text = str(raw or "").strip().upper()
    if text in ("BUY", "LONG"):
        return "LONG"
    if text in ("SELL", "SHORT"):
        return "SHORT"
    return ""


def open_rows(broker):
    """Posiciones abiertas, o None si el libro no se pudo leer."""
    reader = getattr(broker, "open_trades", None)
    if not callable(reader):
        return None
    try:
        return list(reader() or ())
    except Exception:  # noqa: BLE001
        _log("skip", reason="book_unread")
        return None


def _still_held(state, rows):
    """True si el broker todavía tiene esta pata. Sin id, basta el mismo símbolo y lado."""
    ticket = str((state or {}).get("position_id") or "").strip()
    if ticket:
        return any(_row_ticket(row) == ticket for row in rows)
    symbol = str((state or {}).get("symbol") or "").strip().upper()
    side = (state or {}).get("side")
    if not symbol or side not in ("LONG", "SHORT"):
        return False
    return any(_row_symbol(row) == symbol and _row_side(row) == side for row in rows)


def _deal_time(deal):
    if not isinstance(deal, dict):
        return None
    # open_time vacío no tumba el cruce: se prueba create_time y event_time.
    for name in ("open_time", "create_time", "event_time"):
        stamp = _aware(deal.get(name))
        if stamp is not None:
            return stamp
    return None


def resolve_closed_ticket(state, key, deals, rows):
    """Id de la pata ya cerrada. None si sigue abierta, si no hay un solo candidato, o si el libro no se leyó."""
    if not isinstance(state, dict) or rows is None:
        return None
    held = str(state.get("position_id") or "").strip()
    if held:
        return None if _still_held(state, rows) else held
    symbol = str(state.get("symbol") or "").strip().upper()
    side = state.get("side")
    want = {"LONG": "buy", "SHORT": "sell"}.get(side)
    day = _leg_day(key)
    opened_flag = _aware(state.get("opened_at"))
    grouped = {}
    for deal in deals or ():
        if not isinstance(deal, dict):
            continue
        ticket = str(deal.get("position_id") or "").strip()
        if ticket:
            grouped.setdefault(ticket, []).append(deal)
    open_ids = {_row_ticket(row) for row in rows}
    near, rest = [], []
    for ticket, items in grouped.items():
        if not ticket or ticket in open_ids:
            continue
        opens = [item for item in items if item.get("type") == "open"]
        closes = [item for item in items if item.get("type") == "close"]
        if not closes:
            continue
        sample = opens[0] if opens else closes[-1]
        inst = str(sample.get("instrument") or "").strip().upper()
        if symbol and inst and inst != symbol:
            continue
        direction = str((opens[0].get("direction") if opens else "") or "").strip().lower()
        if want and direction and direction not in (want, str(side or "").lower()):
            continue
        stamp = _deal_time(opens[0]) if opens else _deal_time(closes[-1])
        if day is not None and stamp is not None and stamp.astimezone(NY).date() != day:
            continue
        if opened_flag is not None and stamp is not None and abs((stamp - opened_flag).total_seconds()) <= _MATCH_SECONDS:
            near.append(ticket)
        else:
            rest.append(ticket)
    if len(near) == 1:
        return near[0]
    if not near and len(rest) == 1:
        return rest[0]
    return None


def settle_flat_leg(state, key, broker, deals=None, rows=None):
    """Si el broker ya no tiene la pata, la marca salida y guarda id y P/L cuando se pueden cruzar.

    No envía órdenes. Un libro ilegible no toca el flag.
    """
    if not _filled_open(state):
        return False
    if rows is None:
        rows = open_rows(broker)
    if rows is None or _still_held(state, rows):
        return False
    if deals is None and broker is not None:
        client = history_client(broker)
        if client is None:
            return False
        try:
            opened = _aware(state.get("opened_at")) or datetime.now(timezone.utc) - timedelta(days=3)
            ended = datetime.now(timezone.utc) + timedelta(hours=1)
            deals = fetch_deals(client, opened, ended)
        except Exception as exc:  # noqa: BLE001
            _log("failed", reason="read_failed", error_type=type(exc).__name__)
            return False
    ticket = resolve_closed_ticket(state, key, deals, rows)
    if ticket and not str(state.get("position_id") or "").strip():
        state["position_id"] = ticket
    state["exited"] = True
    state["status"] = "done"
    fill_state_from_deals(state, deals or (), key=key)
    return True


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


def _json_deals(deals):
    rows = []
    for deal in deals or ():
        if not isinstance(deal, dict):
            continue
        rows.append({
            "type": deal.get("type"),
            "position_id": str(deal.get("position_id") or "").strip() or None,
            "price": deal.get("price"),
            "profit": deal.get("profit"),
            "instrument": deal.get("instrument"),
            "direction": deal.get("direction"),
            "volume": deal.get("volume"),
            "create_time": deal.get("create_time"),
            "event_time": deal.get("event_time"),
            "open_time": deal.get("open_time"),
        })
    return rows


def store_closed_deals(store, deals):
    """The panel reads this cache. It does not call Exness itself."""
    if store is None or not callable(getattr(store, "set_flag", None)):
        return
    store.set_flag(CLOSED_DEALS_FLAG, json.dumps(_json_deals(deals), sort_keys=True))



def cache_deals_from():
    """UTC instant for the deals cache: two weeks before the Historial start."""
    start = datetime(HISTORY_START.year, HISTORY_START.month, HISTORY_START.day, tzinfo=timezone.utc)
    return start - timedelta(days=CACHE_LOOKBACK_DAYS)


def ensure_session_combo_log():
    """Idempotent: give the Streamlit process a stdout handler if it has none.

    The runtime already logs ``ai_floor.session_combo`` to Render. The panel
    process often has no stdout handler, so HISTORY_VIEW never appeared.
    """
    log = logging.getLogger("ai_floor.session_combo")
    for handler in log.handlers:
        stream = getattr(handler, "stream", None)
        if isinstance(handler, logging.StreamHandler) and stream in (sys.stdout, sys.stderr):
            if log.level == logging.NOTSET or log.level > logging.INFO:
                log.setLevel(logging.INFO)
            return log
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
    log.addHandler(handler)
    if log.level == logging.NOTSET or log.level > logging.INFO:
        log.setLevel(logging.INFO)
    return log


def _signed_usc(amount):
    text = _plain(abs(amount))
    if amount > 0:
        return "+" + text
    if amount < 0:
        return "\N{MINUS SIGN}" + text
    return text or "0"


def _date_es(day):
    return "%s %s" % (day.day, _MONTHS_ES[day.month - 1])


def prior_history_closes(flags):
    """Closes before HISTORY_START: (total P/L, ((day, symbol), ...))."""
    raw = flags.get(CLOSED_DEALS_FLAG) if isinstance(flags, dict) else None
    try:
        deals = json.loads(raw) if isinstance(raw, str) else (raw or [])
    except (TypeError, ValueError):
        deals = []
    if not isinstance(deals, list):
        return Decimal(0), ()
    grouped = {}
    for deal in deals:
        if not isinstance(deal, dict):
            continue
        ticket = str(deal.get("position_id") or "").strip()
        if ticket:
            grouped.setdefault(ticket, []).append(deal)
    total = Decimal(0)
    parts = []
    for ticket, items in grouped.items():
        opens = [item for item in items if item.get("type") == "open"]
        closes = [item for item in items if item.get("type") == "close"]
        if not closes:
            continue
        closes.sort(key=lambda item: _deal_time(item) or datetime.min.replace(tzinfo=timezone.utc))
        closed = _deal_time(closes[-1])
        if closed is None:
            continue
        day = closed.astimezone(NY).date()
        if day >= HISTORY_START:
            continue
        amounts = []
        for deal in closes:
            amount = profit_amount(deal.get("profit"))
            if amount is None:
                amounts = None
                break
            amounts.append(amount)
        if not amounts:
            continue
        pnl = sum(amounts, Decimal(0))
        sample = opens[0] if opens else closes[-1]
        symbol = str(sample.get("instrument") or "").strip().upper()
        if len(symbol) != 6 or not symbol.isalpha():
            continue
        total += pnl
        parts.append((day, symbol))
    return total, tuple(sorted(set(parts)))


def prior_history_pnl(flags):
    """Realized P/L of closes before HISTORY_START, or 0 when there are none."""
    total, parts = prior_history_closes(flags)
    return total if parts else Decimal(0)


def prior_history_note(flags, currency="USC"):
    """One line for closes before HISTORY_START, or None when their P/L sums to 0."""
    total, parts = prior_history_closes(flags)
    if total == 0 or not parts:
        return None
    parts = sorted(set(parts))
    bits = ["%s (%s)" % (_date_es(day), symbol) for day, symbol in parts]
    if len(bits) == 1:
        when = "del " + bits[0]
    elif len(bits) == 2:
        when = "del " + bits[0] + " y " + bits[1]
    else:
        when = "del " + ", ".join(bits[:-1]) + " y " + bits[-1]
    return "Incluye %s %s %s, anterior al historial" % (_signed_usc(total), currency or "USC", when)


def log_london_flag(store):
    """One non-secret line for the 25 Nov flag, even when it is not pending."""
    raw = None
    try:
        raw = store.get_flag(LONDON_FLAG_KEY) if store is not None else None
    except Exception:  # noqa: BLE001
        raw = None
    state = _load_state(raw) if isinstance(raw, str) else None
    if not state:
        LOG.info("component=session_combo event=HISTORY_FLAG key=%s status=absent exited=none "
                 "side=none symbol=none has_ticket=no has_pnl=no opened_at=none", LONDON_FLAG_KEY)
        return
    LOG.info("component=session_combo event=HISTORY_FLAG key=%s status=%s exited=%s side=%s symbol=%s "
             "has_ticket=%s has_pnl=%s opened_at=%s",
             LONDON_FLAG_KEY, state.get("status"), state.get("exited"),
             state.get("side") or "none", state.get("symbol") or "none",
             "no" if _no_ticket(state) else "yes",
             "yes" if _present(state.get("pnl")) else "no",
             state.get("opened_at") or "none")


def _reason_on_day(flags, day):
    if not isinstance(flags, dict) or day is None:
        return None
    for prefix in ("combo_lon_", "combo_fix_"):
        raw = flags.get(prefix + day.isoformat())
        state = _load_state(raw) if isinstance(raw, str) else None
        reason = state.get("exit_reason") if isinstance(state, dict) else None
        if isinstance(reason, str) and reason.strip():
            return reason.strip()
    return None


def extra_rows_from_deals(flags, shown, now):
    """Closed deals whose ticket is not already a visible flag row. One ticket, one row."""
    raw = flags.get(CLOSED_DEALS_FLAG) if isinstance(flags, dict) else None
    try:
        deals = json.loads(raw) if isinstance(raw, str) else []
    except (TypeError, ValueError):
        deals = []
    if not isinstance(deals, list):
        return []
    grouped = {}
    for deal in deals:
        if not isinstance(deal, dict):
            continue
        ticket = str(deal.get("position_id") or "").strip()
        if ticket:
            grouped.setdefault(ticket, []).append(deal)
    taken = {str(item) for item in shown or () if item}
    rows = []
    for ticket, items in grouped.items():
        if ticket in taken:
            continue
        opens = [item for item in items if item.get("type") == "open"]
        closes = [item for item in items if item.get("type") == "close"]
        if not closes:
            continue
        closes.sort(key=lambda item: _deal_time(item) or datetime.min.replace(tzinfo=timezone.utc))
        closed = _deal_time(closes[-1])
        if closed is None or closed > now:
            continue
        day = closed.astimezone(NY).date()
        if day < HISTORY_START:
            continue
        opened = _deal_time(opens[0]) if opens else None
        sample = opens[0] if opens else closes[-1]
        symbol = str(sample.get("instrument") or "").strip().upper()
        if len(symbol) != 6 or not symbol.isalpha():
            continue
        amounts = []
        for deal in closes:
            amount = profit_amount(deal.get("profit"))
            if amount is None:
                amounts = None
                break
            amounts.append(amount)
        reason = TICKET_EXIT_OVERRIDE.get(ticket) or _reason_on_day(flags, day) or "cierre_manual"
        rows.append({
            "key": "deal_" + ticket, "kind": "london" if symbol == LONDON_SYMBOL else "fix",
            "day": day, "status": "done", "symbol": symbol,
            "_side": _deal_side(opens[0].get("direction")) if opens else None,
            "position_id": ticket,
            "volume": _dec(opens[0].get("volume")) if opens else None,
            "opened_at": opened, "exit_at": closed, "exited": True,
            "entry": _dec(opens[0].get("price")) if opens else None,
            "exit_price": _dec(closes[-1].get("price")),
            "exit_reason": reason,
            "pnl": None if not amounts else sum(amounts, Decimal(0)),
        })
    return rows


def backfill_combo_history(broker, *, now=None, get_json=None):
    """Idempotent GET-only backfill of closed combo flags. Never raises to the caller."""
    result = {"updated": 0, "pending": 0, "fetched": False}
    try:
        store = getattr(broker, "flag_store", None)
        if store is None or not callable(getattr(store, "get_flag", None)):
            return result
        log_london_flag(store)
        if now is None:
            clock = getattr(broker, "_clock", None)
            now = clock() if callable(clock) else datetime.now(timezone.utc)
        if not isinstance(now, datetime) or now.tzinfo is None:
            now = datetime.now(timezone.utc)
        today = now.astimezone(timezone.utc).date()
        loaded, flat, blank = [], [], []
        for key, raw in _iter_combo_keys(store, today):
            state = _load_state(raw)
            if state is None:
                continue
            if _blank_terminal(state) and not _never_filled(state, key):
                blank.append((key, state))
            elif _filled_open(state):
                flat.append((key, state))
            elif _incomplete(state) or _deal_gap(state):
                loaded.append((key, state))
        rows = open_rows(broker) if (flat or blank) else None
        # Una pata filled que ya no está en el libro se cierra aquí. El backfill viejo solo miraba patas ya cerradas.
        waiting = [(key, state) for key, state in flat if rows is not None and not _still_held(state, rows)]
        # Sin ticket también hay que pedir deals: si no, updated=0 y pending=0 y la fila no sale.
        need_deals = [(key, state) for key, state in loaded if str(state.get("position_id") or "").strip()]
        need_deals.extend(waiting)
        if rows is not None:
            need_deals.extend(blank)
        result["pending"] = len(loaded) + len(waiting) + len(blank)
        deals = []
        if need_deals:
            client = history_client(broker)
            if client is None:
                _log("skip", reason="no_client", pending=result["pending"])
                return result
            start, end = _window(need_deals)
            deals = fetch_deals(client, start, end, get_json=get_json)
            result["fetched"] = True
        for key, state in loaded:
            # fill_if_empty no pisa un precio ya guardado, ni con blanco ni con otro número.
            changed = fill_state_from_deals(state, deals, key=key)
            changed = _fill_times_from_deals(state, deals) or changed
            if not changed:
                continue
            store.set_flag(key, _dump(state))
            result["updated"] += 1
        for key, state in waiting:
            if not settle_flat_leg(state, key, broker, deals=deals, rows=rows):
                continue
            store.set_flag(key, _dump(state))
            result["updated"] += 1
        for key, state in blank:
            # Sin libro no se cruza. Cero o varios cierres: no se reescribe el done vacío.
            if rows is None:
                continue
            if str(key).startswith("combo_lon_"):
                ticket = _london_day_ticket(key, deals, rows)
            else:
                guessed = _with_london_symbol(state, key)
                if not str(guessed.get("symbol") or "").strip():
                    continue
                ticket = resolve_closed_ticket(guessed, key, deals, rows)
            if not ticket:
                continue
            filled = dict(state)
            old_symbol = str(filled.get("symbol") or "").strip()
            if str(key).startswith("combo_lon_"):
                _correct(key, "symbol", old_symbol.upper(), LONDON_SYMBOL)
                filled["symbol"] = LONDON_SYMBOL
            filled["position_id"] = ticket
            fill_state_from_deals(filled, deals, key=key)
            deal_side = _open_side(deals, ticket)
            if deal_side:
                _correct(key, "side", filled.get("side"), deal_side)
                filled["side"] = deal_side
            _fill_times_from_deals(filled, deals)
            if _no_ticket(filled) or not _present(filled.get("pnl")):
                continue
            state.clear()
            state.update(filled)
            store.set_flag(key, _dump(state))
            result["updated"] += 1
        pending = []
        for key, state in loaded:
            if str(state.get("position_id") or "").strip() and (_incomplete(state) or _price_gap(state)):
                pending.append((key, state))
        for key, state in blank:
            if _no_ticket(state) or not _present(state.get("pnl")):
                pending.append((key, state))
        if rows is None:
            pending.extend(flat)
        else:
            for key, state in flat:
                if _filled_open(state):
                    pending.append((key, state))
                elif _incomplete(state) and str(state.get("position_id") or "").strip():
                    pending.append((key, state))
        for key, state in pending:
            _pending_line(key, state)
        result["pending"] = len(pending)
        _log("ok", updated=result["updated"], pending=result["pending"])
        try:
            client = history_client(broker)
            if client is not None:
                cached = fetch_deals(client, cache_deals_from(), now + timedelta(hours=1), get_json=get_json)
                store_closed_deals(store, cached)
        except Exception as exc:  # noqa: BLE001 - the panel keeps the previous cache
            _log("skip", reason="cache_unread", error_type=type(exc).__name__)
        return result
    except Exception as exc:  # noqa: BLE001 - a history miss must not break the session
        _log("failed", reason="read_failed", error_type=type(exc).__name__,
             detail=safe_detail(exc))
        return result
