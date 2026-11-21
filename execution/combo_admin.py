"""Guarded adopt of a London combo position. Read-only unless the confirm token matches.

There is no dashboard button. The default CLI prints the combo flags. ``--adopt`` writes only when
``AI_FLOOR_COMBO_ADOPT_CONFIRM`` is exactly ``COMBO_ADOPT_AUTHORIZED``. The write is one flag and one
audit row in the same flag store. It does not send an order.
"""
from datetime import date, datetime, timezone
import argparse
import json
import logging
import os

from execution.session_combo import _blank, london_key


LOG = logging.getLogger("ai_floor.combo_admin")
CONFIRM = "COMBO_ADOPT_AUTHORIZED"
CONFIRM_ENV = "AI_FLOOR_COMBO_ADOPT_CONFIRM"
AUDIT_KEY = "combo_admin_audit"
_SYMBOLS = ("USDJPY", "EURUSD", "GBPUSD")
_AUDIT_CAP = 50


def _flags(store):
    path = getattr(store, "path", None)
    if path is not None:
        import sqlite3
        with sqlite3.connect(path) as con:
            rows = con.execute("SELECT key, value FROM flags WHERE key LIKE 'combo_%' ORDER BY key").fetchall()
        return [(key, value) for key, value in rows if key != AUDIT_KEY]
    raw = getattr(store, "_flags", None)
    if isinstance(raw, dict):
        return sorted((key, value) for key, value in raw.items() if str(key).startswith("combo_") and key != AUDIT_KEY)
    return []


def inspect_flags(store):
    """``[{key, status}]``. Read-only. Status is the stored status or ``unreadable``."""
    found = []
    for key, value in _flags(store):
        status = "unreadable"
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict) and isinstance(parsed.get("status"), str):
                status = parsed["status"]
        except (TypeError, ValueError):
            status = "unreadable"
        found.append({"key": key, "status": status})
    return found


def _audit(store, entry):
    try:
        raw = store.get_flag(AUDIT_KEY)
        prior = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        prior = []
    if not isinstance(prior, list):
        prior = []
    prior.append(entry)
    store.set_flag(AUDIT_KEY, json.dumps(prior[-_AUDIT_CAP:], sort_keys=True))


def _book_matches(positions, ticket, symbol, side):
    """True when the open-position snapshot has this ticket with the same symbol and side."""
    if positions is None:
        return False
    try:
        rows = list(positions)
    except TypeError:
        return False
    for row in rows:
        if isinstance(row, dict):
            got_ticket, got_symbol, got_side = row.get("ticket"), row.get("symbol"), row.get("side")
        else:
            got_ticket = getattr(row, "ticket", None)
            got_symbol = getattr(row, "symbol", None)
            got_side = getattr(row, "side", None)
        if str(got_ticket) == ticket:
            return str(got_symbol) == symbol and str(got_side) == side
    return False


def adopt_position(store, *, day, position_id, symbol, side, confirm=None, now=None, positions=None):
    """Plan by default. Writes the ``combo_lon`` flag only when ``confirm`` is the exact token.

    The returned dict never includes a host, a key, or an account id. ``wrote`` is false unless the flag changed.
    """
    now = now or datetime.now(timezone.utc)
    plan = {"day": None if day is None else day.isoformat(), "symbol": symbol, "side": side,
            "position_tail": str(position_id or "")[-4:], "wrote": False, "reason": None}
    if not isinstance(day, date) or symbol not in _SYMBOLS or side not in ("LONG", "SHORT"):
        plan["reason"] = "invalid"
        return plan
    ticket = str(position_id or "")
    if not ticket.isdigit():
        plan["reason"] = "position_invalid"
        return plan
    if confirm != CONFIRM:
        plan["reason"] = "confirm_required"
        return plan
    key = london_key(day)
    state = _blank()
    raw = None
    try:
        raw = store.get_flag(key)
    except Exception:  # noqa: BLE001
        raw = None
    if raw:
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            state.update({field: parsed.get(field, state[field]) for field in state})
    if state.get("exited") or state.get("status") in ("done", "cancelled"):
        plan["reason"] = "already_closed"
        return plan
    held = state.get("position_id")
    if state.get("status") == "filled" and held and str(held) != ticket:
        plan["reason"] = "position_conflict"
        return plan
    if not _book_matches(positions, ticket, symbol, side):
        plan["reason"] = "not_in_book"
        return plan
    state.update(status="filled", symbol=symbol, side=side, position_id=ticket,
                 client_id=state.get("client_id") or f"combo-lon-{day.isoformat()}-{side}")
    if "exited" not in state:
        state["exited"] = False
    payload = dict(state)
    store.set_flag(key, json.dumps(payload, sort_keys=True, default=str))
    _audit(store, {"at": now.isoformat(), "event": "ADOPT", "key": key, "symbol": symbol, "side": side,
                   "position_tail": ticket[-4:]})
    LOG.info("component=combo_admin event=ADOPT key=%s symbol=%s side=%s position_tail=%s",
             key, symbol, side, ticket[-4:])
    plan["wrote"] = True
    plan["reason"] = "adopted"
    plan["key"] = key
    return plan


def main(argv=None):
    """Read-only unless ``--adopt`` and the confirm env match. Prints statuses, never flag bodies."""
    parser = argparse.ArgumentParser(description="Inspect or adopt a London combo flag")
    parser.add_argument("--db", default=os.environ.get("AI_FLOOR_EXNESS_STATE_DB_PATH") or "data/runtime/exness_state.db")
    parser.add_argument("--adopt", action="store_true")
    parser.add_argument("--day")
    parser.add_argument("--position-id")
    parser.add_argument("--symbol")
    parser.add_argument("--side")
    args = parser.parse_args(argv)
    from execution.exness_brakes import SqliteFlags
    store = SqliteFlags(args.db)
    if not args.adopt:
        for row in inspect_flags(store):
            print(f"{row['key']} status={row['status']}")
        return 0
    try:
        day = datetime.strptime(args.day or "", "%Y-%m-%d").date()
    except ValueError:
        print("day must be YYYY-MM-DD")
        return 2
    plan = adopt_position(store, day=day, position_id=args.position_id, symbol=args.symbol, side=args.side,
                          confirm=os.environ.get(CONFIRM_ENV))
    print(f"wrote={str(plan['wrote']).lower()} reason={plan['reason']}")
    return 0 if plan["wrote"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
