"""View model for the Liquid Glass panel.

Numbers come from the readers the dashboard already uses (account, flags, brakes,
session-combo legs, health). Nothing here writes a flag, a brake, or an order.
A missing figure stays ``sin datos``.

Bug #95: stop and target on a position are ignored. SL/TP are taken only from an
order whose type is ``sltp`` and whose ``position_id`` is that position.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import re

from execution.exness_brakes import LIVE_FLAG, USC_DAILY, USC_DRAWDOWN, _money
from execution.session_combo import FIX_SYMBOLS, LONDON, LONDON_SYMBOL, NY, fix_key, london_key
from ui.exness_present import LABEL, OFF_SAFETY, broker_is_exness, brake_view, clock_line, live_badge, money_line, read_account, session_clock
from ui.i18n_es import datetime_et, long_date_et

MISSING = "sin datos"
CLOSED = "mercado cerrado"
VERSION = "V2.1"
# The session reads the book at most every 15 minutes. Older than this, the flag is not a live book.
_BOOK_FRESH_SECONDS = 45 * 60
_STATUSES = ("pending", "armed", "submitted", "filled", "skipped", "cancelled", "done")
_LEG_TEXT = {
    "pending": "Esperando", "armed": "Armada", "submitted": "Enviada", "filled": "Abierta",
    "skipped": "Omitida", "cancelled": "Cancelada", "done": "Hecha",
}
_SIDES = {"buy": ("Compra", "buy"), "long": ("Compra", "buy"), "sell": ("Venta", "sell"), "short": ("Venta", "sell")}
_SYS = {
    "ONLINE": "En línea", "DEGRADED": "Degradado", "MARKET_CLOSED": "Mercado cerrado",
    "STOPPED": "Detenido", "RUNNING": "En marcha",
    "DISABLED": "Apagado", "NOT STARTED": "Sin arrancar", "COMPLETED": "Completado", "FAILED": "Fallido",
    "ERROR": "Error", "NO_DATA": MISSING, "NOT CONFIGURED": "Sin configurar", "CURRENT": "Al día",
    "STALE_DATA": "Datos viejos",
}
_CODE = re.compile(r"[A-Z0-9_]{1,40}")
_MONTHS = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")


def _now(now):
    if isinstance(now, datetime) and now.tzinfo is not None and now.utcoffset() is not None:
        return now.astimezone(timezone.utc)
    return datetime.now(timezone.utc)


def _aware(value):
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _get(obj, *names):
    if isinstance(obj, dict):
        for name in names:
            if name in obj and obj[name] is not None:
                return obj[name]
        return None
    for name in names:
        if hasattr(obj, name):
            value = getattr(obj, name)
            if value is not None:
                return value
    return None


def _ident(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int) or (isinstance(value, str) and value.strip()):
        return str(value).strip()
    return None


def _symbol(value):
    if not isinstance(value, str):
        return None
    text = value.strip().upper()
    if len(text) == 6 and text.isalpha():
        return text
    return None


def _side(value):
    if not isinstance(value, str):
        return None
    return _SIDES.get(value.strip().lower())


def _plain(amount):
    if amount is None:
        return MISSING
    text = format(amount, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _signed(amount, currency):
    if amount is None:
        return MISSING, ""
    text = money_line(abs(amount), currency)
    if text == MISSING:
        return MISSING, ""
    if amount > 0:
        return "+" + text, "pos"
    if amount < 0:
        # El menos va en el USC y en el USD: «−139.89 USC · −1.40 USD».
        if " · " in text:
            left, right = text.split(" · ", 1)
            return f"−{left} · −{right}", "neg"
        return "−" + text, "neg"
    return text, "flat"


def _pct(part, whole):
    if part is None or whole is None or whole <= 0:
        return None
    return part / whole * Decimal(100)


def _status_text(value):
    if isinstance(value, str) and value.strip() in _SYS:
        return _SYS[value.strip()]
    if isinstance(value, str) and _CODE.fullmatch(value.strip()):
        words = value.strip().replace("_", " ").lower()
        return words[:1].upper() + words[1:]
    parsed = _aware(value)
    if parsed is not None:
        return datetime_et(parsed)
    return MISSING


def _is_sltp(order):
    kind = _get(order, "type", "order_type", "kind")
    return isinstance(kind, str) and kind.strip().lower() == "sltp"


def linked_protection(position, orders):
    """SL and TP from the linked ``sltp`` order. Position stop/target fields are not read."""
    pid = _ident(_get(position, "position_id", "ticket", "id"))
    stop = target = None
    if pid is None:
        return None, None
    for order in orders or ():
        if not _is_sltp(order) or _ident(_get(order, "position_id")) != pid:
            continue
        found_stop = _money(_get(order, "stop_loss_price", "stop"))
        found_target = _money(_get(order, "take_profit_price", "target"))
        if found_stop is not None:
            stop = found_stop
        if found_target is not None:
            target = found_target
    return stop, target


def _legs(flags, now):
    rows = []
    if not isinstance(flags, dict):
        return rows
    for key, raw in flags.items():
        if not isinstance(key, str) or not key.startswith(("combo_lon_", "combo_fix_")) or not isinstance(raw, str):
            continue
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(parsed, dict) or parsed.get("status") not in _STATUSES:
            continue
        opened = _aware(parsed.get("opened_at"))
        if opened is not None and opened > now:
            continue
        exit_at = _aware(parsed.get("exit_at"))
        if exit_at is not None and opened is not None and exit_at < opened:
            exit_at = None
        day = key.rsplit("_", 1)[-1]
        try:
            day = datetime.fromisoformat(day).date()
        except ValueError:
            day = None
        rows.append({
            "key": key, "kind": "london" if key.startswith("combo_lon_") else "fix", "day": day,
            "status": parsed.get("status"), "symbol": _leg_symbol(parsed), "side": _leg_side(parsed),
            "position_id": _ident(parsed.get("position_id")), "volume": _money(parsed.get("volume")),
            "opened_at": opened, "exit_at": exit_at, "exited": parsed.get("exited") is True,
            "entry": _money(_get(parsed, "entry", "fill", "open_price")),
            "exit_price": _money(_get(parsed, "exit_price", "close_price")),
            "exit_reason": parsed.get("exit_reason") if isinstance(parsed.get("exit_reason"), str) else None,
            "pnl": _leg_pnl(parsed),
        })
    return rows


def _pnl_value(value):
    """A number, ``{amount}``, or the sum of every part. A missing part is not a partial sum."""
    if isinstance(value, dict):
        if "amount" in value:
            return _money(value.get("amount"))
        return None
    if isinstance(value, (list, tuple)):
        if not value:
            return None
        parts = []
        for item in value:
            piece = item
            if isinstance(item, dict) and "amount" not in item:
                piece = item.get("pnl") if item.get("pnl") is not None else item.get("profit")
            amount = _pnl_value(piece)
            if amount is None:
                return None
            parts.append(amount)
        return sum(parts, Decimal(0))
    return _money(value)


def _leg_parts(parsed):
    parts = parsed.get("parts") if isinstance(parsed, dict) else None
    return parts if isinstance(parts, list) else []


def _leg_pnl(parsed):
    raw = None
    if isinstance(parsed, dict) and parsed.get("pnl") is not None:
        raw = parsed.get("pnl")
    elif isinstance(parsed, dict) and parsed.get("profit") is not None:
        raw = parsed.get("profit")
    amount = _pnl_value(raw) if raw is not None else None
    if amount is not None:
        return amount
    parts = _leg_parts(parsed)
    return _pnl_value(parts) if parts else None


def _one_part(parsed, reader):
    found = []
    for part in _leg_parts(parsed):
        if not isinstance(part, dict):
            continue
        got = reader(part)
        if got and got not in found:
            found.append(got)
    return found[0] if len(found) == 1 else None


def _leg_symbol(parsed):
    symbol = _symbol(parsed.get("symbol")) if isinstance(parsed, dict) else None
    if symbol:
        return symbol
    return _one_part(parsed, lambda part: _symbol(part.get("symbol") or part.get("instrument")))


def _leg_side(parsed):
    side = _side(parsed.get("side")) if isinstance(parsed, dict) else None
    if side:
        return side
    return _one_part(parsed, lambda part: _side(part.get("side") or part.get("direction")))


def _leg_row(leg, currency):
    side = leg["side"] or (MISSING, "")
    pnl, tone = _signed(leg["pnl"], currency)
    return {
        "day": _day_text(leg["day"]), "kind": "Londres" if leg["kind"] == "london" else "Fix",
        "symbol": leg["symbol"] or MISSING, "side": side[0], "side_tone": side[1],
        "state": _LEG_TEXT.get(leg["status"], MISSING),
        "opened": datetime_et(leg["opened_at"]) if leg["opened_at"] else MISSING,
        "close": datetime_et(leg["exit_at"]) if leg["exit_at"] else MISSING,
        "ticket": leg["position_id"] or MISSING, "pnl": pnl, "pnl_tone": tone,
        "lots": _plain(leg["volume"]), "entry": _plain(leg.get("entry")),
        "exit_price": _plain(leg.get("exit_price")), "reason": _exit_label(leg),
        "epoch": "" if leg["opened_at"] is None else str(int(leg["opened_at"].timestamp())),
    }


def _exit_label(leg):
    """Close reason. A ticket in the known map wins over a stored ``london_1100``."""
    from execution.combo_history import KNOWN_EXIT_REASONS
    ticket = str(leg.get("position_id") or "").strip()
    known = KNOWN_EXIT_REASONS.get(ticket)
    text = known if known else leg.get("exit_reason")
    labels = {"london_1100": "cierre 11:00 ET", "fix_30m": "30 min", "protection_missing": "sin protección", "hq_manual": "cierre manual HQ"}
    if isinstance(text, str) and text.strip() in labels:
        return labels[text.strip()]
    if isinstance(text, str) and re.fullmatch(r"[A-Za-z0-9_]{1,32}", text.strip()):
        return text.strip().replace("_", " ")
    return MISSING


def _day_text(day):
    if day is None:
        return MISSING
    return f"{day.day:02d} {_MONTHS[day.month - 1]} {day.year}"


def _close_index(legs):
    found = {}
    for leg in legs:
        if leg["position_id"] and leg["exit_at"] is not None:
            found[leg["position_id"]] = leg["exit_at"]
    return found


def _position_row(position, orders, closes, currency):
    side = _side(_get(position, "side", "direction")) or (MISSING, "")
    stop, target = linked_protection(position, orders)
    profit = _money(_get(position, "profit", "floating_pnl", "unrealized_pnl", "unrealized"))
    pnl, tone = _signed(profit, currency)
    ticket = _ident(_get(position, "position_id", "ticket", "id"))
    close = closes.get(ticket) if ticket else None
    current = _money(_get(position, "current_price", "price_current", "market_price"))
    entry = _money(_get(position, "open_price", "entry", "price"))
    return {
        "symbol": _symbol(_get(position, "instrument", "symbol")) or MISSING,
        "side": side[0], "side_tone": side[1],
        "lots": _plain(_money(_get(position, "volume", "lots"))),
        "entry": _plain(entry), "current": _plain(current), "sl": _plain(stop), "tp": _plain(target),
        "floating": pnl, "floating_tone": tone, "ticket": ticket or MISSING,
        "close": datetime_et(close) if close is not None else MISSING,
        "_profit": profit,
    }


def _positions(book, legs, currency, now):
    closes = _close_index(legs)
    if book is None:
        rows = []
        for leg in legs:
            if leg["status"] == "filled" and not leg["exited"] and leg["position_id"]:
                rows.append({
                    "symbol": leg["symbol"] or MISSING, "side": (leg["side"] or (MISSING, ""))[0],
                    "side_tone": (leg["side"] or (MISSING, ""))[1], "lots": _plain(leg["volume"]),
                    "entry": MISSING, "current": MISSING, "sl": MISSING, "tp": MISSING,
                    "floating": MISSING, "floating_tone": "", "ticket": leg["position_id"],
                    "close": datetime_et(leg["exit_at"]) if leg["exit_at"] else MISSING, "_profit": None,
                })
        if not rows:
            if _market_closed(now):
                return {"state": "closed", "rows": [], "note": CLOSED, "floating": CLOSED, "floating_tone": ""}
            return {"state": "sin datos", "rows": [], "note": "El libro de posiciones no está en el panel.", "floating": MISSING, "floating_tone": ""}
        return {"state": "rows", "rows": rows, "note": "Entrada, precio, SL y TP: sin datos. No hay libro del broker en el panel.",
                "floating": MISSING, "floating_tone": ""}
    if not isinstance(book, dict) or not isinstance(book.get("positions"), (list, tuple)):
        return {"state": "sin datos", "rows": [], "note": "El libro de posiciones no se pudo leer.", "floating": MISSING, "floating_tone": ""}
    orders = book.get("orders") if isinstance(book.get("orders"), (list, tuple)) else ()
    rows = [_position_row(item, orders, closes, currency) for item in book["positions"] if isinstance(item, dict)]
    if not rows:
        # Libro leído y sin posiciones: el flotante es cero, no «sin datos».
        amount = _money(book.get("floating_pnl"))
        text, tone = _signed(Decimal(0) if amount is None else amount, currency)
        return {"state": "empty", "rows": [], "note": "", "floating": text, "floating_tone": tone}
    known = [row["_profit"] for row in rows]
    if all(item is not None for item in known):
        text, tone = _signed(sum(known, Decimal(0)), currency)
    else:
        text, tone = _signed(_money(book.get("floating_pnl")), currency)
    for row in rows:
        row.pop("_profit", None)
    return {"state": "rows", "rows": rows, "note": "", "floating": text, "floating_tone": tone}


def _shown_in_history(leg):
    """Una pata sin par y sin P/L no es una operación. No se pinta."""
    return bool(leg["symbol"]) or leg["pnl"] is not None


def _closed_leg(leg, now):
    """Cerrada de verdad. Una pata ``filled`` que sigue abierta no entra en el historial.

    El 25 nov quedó ``filled`` con ``exited`` en falso y sin ticket: el cierre ya pasó
    (o el P/L ya está guardado) y el libro no la tiene. Esa sí es una operación.
    """
    status = leg["status"]
    if status in ("done", "skipped", "cancelled"):
        return True
    if status != "filled":
        return False
    if leg["exited"] or leg["pnl"] is not None:
        return True
    exit_at = leg["exit_at"]
    return exit_at is not None and exit_at <= now and not leg["position_id"]


def _ticket_rank(ticket):
    text = str(ticket or "")
    if text.isdigit():
        return (0, int(text))
    return (1, text)


def _history(legs, currency, now):
    chosen = [leg for leg in legs if _closed_leg(leg, now) and _shown_in_history(leg)]
    # Cierre más reciente primero. Mismo cierre: el ticket mayor queda arriba. Vale para todas las páginas.
    floor = datetime.min.replace(tzinfo=timezone.utc)
    chosen.sort(key=lambda leg: (leg["exit_at"] or floor, _ticket_rank(leg["position_id"])), reverse=True)
    rows = [_leg_row(leg, currency) for leg in chosen]
    return {"state": "rows" if chosen else "empty", "rows": rows, "recent": rows[:3], "stats": _stats(chosen, currency)}


def _stats(legs, currency):
    """Summary over legs that have a real P/L. Rows without a deal stay visible but do not count."""
    counted = [leg for leg in legs if leg["pnl"] is not None]
    known = [leg["pnl"] for leg in counted]
    count = len(known)
    blank = {"count": str(count), "winners": MISSING, "win_pct": MISSING, "losers": MISSING, "loss_pct": MISSING,
             "gross_win": MISSING, "gross_loss": MISSING, "net": MISSING, "net_tone": "",
             "profit_factor": MISSING, "drawdown": MISSING, "avg_win": MISSING, "avg_loss": MISSING}
    if count == 0:
        return blank
    winners = [item for item in known if item > 0]
    losers = [item for item in known if item < 0]
    gross_win = sum(winners, Decimal(0))
    gross_loss = sum((abs(item) for item in losers), Decimal(0))
    net, net_tone = _signed(sum(known, Decimal(0)), currency)
    ordered = sorted(counted, key=lambda leg: leg["opened_at"] or datetime.min.replace(tzinfo=timezone.utc))
    equity = peak = worst = Decimal(0)
    for leg in ordered:
        equity += leg["pnl"]
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    factor = MISSING if not losers else f"{(gross_win / gross_loss):.2f}"
    return {"count": str(count), "winners": str(len(winners)),
            "win_pct": f"{(Decimal(len(winners)) / Decimal(count) * 100):.1f} %",
            "losers": str(len(losers)), "loss_pct": f"{(Decimal(len(losers)) / Decimal(count) * 100):.1f} %",
            "gross_win": money_line(gross_win, currency), "gross_loss": money_line(gross_loss, currency),
            "net": net, "net_tone": net_tone, "profit_factor": factor,
            "drawdown": money_line(worst, currency),
            "avg_win": money_line(gross_win / len(winners), currency) if winners else MISSING,
            "avg_loss": money_line(gross_loss / len(losers), currency) if losers else MISSING}


def _strategy(legs, now):
    ny, lon = now.astimezone(NY).date(), now.astimezone(LONDON).date()

    def one(kind, key, symbol, window, rule):
        leg = next((item for item in legs if item["key"] == key), None)
        detail = MISSING if leg is None or leg["exit_at"] is None else datetime_et(leg["exit_at"])
        if leg is not None and leg["symbol"]:
            symbol = leg["symbol"]
        state, tone = _pill(leg)
        return {"title": "Londres" if kind == "london" else "Fix", "symbol": symbol, "window": window,
                "rule": rule, "state": MISSING if leg is None else _LEG_TEXT.get(leg["status"], MISSING),
                "close": detail, "side": (leg["side"][0] if leg and leg["side"] else MISSING),
                "pill": state, "tone": tone, "leg": leg}

    london = one("london", london_key(ny), LONDON_SYMBOL, "02:00–05:00 ET", "Cierre de la pata: 11:00 ET")
    fix = one("fix", fix_key(lon), ", ".join(FIX_SYMBOLS), "16:00 Europe/London · primeros 5 min",
              "Si entra, el cierre queda en el registro (30 min después de la entrada)")
    london_leg, fix_leg = london.pop("leg"), fix.pop("leg")
    close_state, close_tone = _close_pill(london_leg, now)
    limit_state, limit_tone = _limit_pill(london_leg)
    fix_state, fix_tone = _pill(fix_leg)
    usdjpy_state, usdjpy_tone = _pill(london_leg)
    return {
        "now": long_date_et(now), "london": london, "fix": fix,
        "steps": [
            {"time": "2:00", "title": "Rango y órdenes OCO", "state": london["pill"], "tone": london["tone"]},
            {"time": "5:00", "title": "Límite de entrada", "state": limit_state, "tone": limit_tone},
            {"time": "11:00", "title": "Cierre Londres → Fix EURUSD/GBPUSD", "state": close_state, "tone": close_tone},
            {"time": "Fix", "title": "EURUSD / GBPUSD", "state": fix_state, "tone": fix_tone},
        ],
        "pairs": [{"symbol": LONDON_SYMBOL, "state": usdjpy_state, "tone": usdjpy_tone},
                  _pair(fix_leg, "EURUSD"), _pair(fix_leg, "GBPUSD")],
    }


def _history_curve(legs, now, start_balance, start_at):
    """P/L realizado acumulado por hora de cierre, anclado al balance de inicio del Historial.

    ``start_balance`` already includes closes before HISTORY_START (brake_start plus
    that earlier P/L). If the flag has no clock, the point uses 11:00 ET of the key date.
    """
    if start_balance is None:
        return None
    chosen = []
    for leg in legs:
        if leg.get("pnl") is None or not _closed_leg(leg, now) or not _shown_in_history(leg):
            continue
        stamp = leg.get("exit_at") or leg.get("opened_at")
        day = leg.get("day")
        if stamp is None and day is not None:
            stamp = datetime(day.year, day.month, day.day, 16, 0, tzinfo=timezone.utc)
        if stamp is None or stamp > now:
            continue
        rank = 0 if leg.get("kind") == "london" else 1
        chosen.append((stamp, rank, leg.get("key") or "", leg["pnl"]))
    if not chosen:
        return None
    chosen.sort()
    anchor = start_at if start_at is not None and start_at <= chosen[0][0] else chosen[0][0] - timedelta(minutes=1)
    if anchor > now:
        return None
    equity = start_balance
    points = [{"at": anchor.isoformat(), "equity": format(equity, "f")}]
    for stamp, _rank, _key, pnl in chosen:
        equity += pnl
        points.append({"at": stamp.isoformat(), "equity": format(equity, "f")})
    return points if len(points) >= 2 else None


def _curve(curve, now, currency):
    points = []
    if not isinstance(curve, (list, tuple)):
        empty = {"state": "sin datos", "points": []}
        empty["ranges"] = {key: {"state": "sin datos", "points": []} for key in ("30", "90", "365", "all")}
        return empty
    for item in curve:
        if not isinstance(item, dict):
            continue
        equity = _money(item.get("equity"))
        stamp = _aware(item.get("at"))
        if equity is None or equity < 0 or stamp is None or stamp > now:
            continue
        points.append((stamp, equity))
    points.sort(key=lambda item: item[0])

    def pack(picked):
        if len(picked) < 2:
            return {"state": "sin datos", "points": []}
        return {"state": "ok", "points": [
        {"at": datetime_et(stamp), "equity": format(equity, "f"), "text": money_line(equity, currency),
         "epoch": str(int(stamp.timestamp())), "axis": f"{stamp.day:02d}/{stamp.month:02d}",
         "y": f"{equity:,.2f}"}
            for stamp, equity in picked]}

    full = pack(points)
    full["ranges"] = {
        "30": pack([item for item in points if now - item[0] <= timedelta(days=30)]),
        "90": pack([item for item in points if now - item[0] <= timedelta(days=90)]),
        "365": pack([item for item in points if now - item[0] <= timedelta(days=365)]),
        "all": pack(points),
    }
    return full


def _used(base_text, equity):
    base = _money(base_text)
    if base is None or equity is None:
        return None
    loss = base - equity
    return loss if loss > 0 else Decimal(0)


def _brake_line(cap, used, currency):
    if cap is None:
        return {"cap": MISSING, "used": MISSING, "text": MISSING, "bar": None}
    cap_text = money_line(cap, currency)
    if used is None:
        return {"cap": cap_text, "used": MISSING, "text": MISSING, "bar": None}
    percent = _pct(used, cap)
    bar = None if percent is None else float(min(percent, Decimal(100)))
    shown = f"{percent:.1f} %" if percent is not None else MISSING
    return {"cap": cap_text, "used": money_line(used, currency), "text": f"{money_line(used, currency)} · {shown}", "bar": bar}


def _trade_use(cap, positions):
    """Consumo del tope por operación. Con el libro vacío es 0. Sin riesgo medible, no se inventa."""
    if isinstance(positions, dict) and positions.get("state") == "empty":
        return _brake_line(cap, Decimal(0), "USC")
    line = _brake_line(cap, None, "USC")
    return {**line, "text": ""}


def _system(health, errors):
    if not isinstance(health, dict) and errors is None:
        return {"state": "sin datos", "heartbeat": MISSING, "scheduler": MISSING, "status": MISSING,
                "last_cycle": MISSING, "last_run": MISSING, "errors_state": "sin datos", "errors": []}
    health = health if isinstance(health, dict) else {}
    if errors is None:
        err_state, rows = "sin datos", []
    else:
        rows = []
        dropped = False
        for item in errors:
            if not isinstance(item, dict):
                dropped = True
                continue
            event = item.get("event")
            if not isinstance(event, str) or not _CODE.fullmatch(event.strip()):
                dropped = True
                continue
            rows.append({"at": _status_text(item.get("at")), "event": event.strip()})
        err_state = "sin datos" if dropped and not rows else ("rows" if rows else "empty")
    return {"state": "ok", "heartbeat": _status_text(health.get("runtime_heartbeat")),
            "scheduler": _status_text(health.get("scheduler")), "status": _status_text(health.get("status")),
            "last_cycle": _status_text(health.get("last_cycle_status")), "last_run": _status_text(health.get("last_run")),
            "errors_state": err_state, "errors": rows[:8]}


def _split(text):
    if not isinstance(text, str) or " · " not in text:
        return text, ""
    main, minor = text.split(" · ", 1)
    return main, minor


def _kpi(text, sub, tone="", note=None):
    main, minor = _split(text)
    item = {"text": text, "sub": sub, "tone": tone, "main": main, "minor": minor}
    if note:
        item["note"] = note
    return item


def _pill(leg):
    """Layout pill. Missing leg is sin datos, never a pretended Esperando."""
    if not isinstance(leg, dict):
        return MISSING, "na"
    status = leg.get("status")
    if status == "pending":
        return "Esperando", "wait"
    if status in ("armed", "submitted") or (status == "filled" and not leg.get("exited")):
        return "En curso", "run"
    if status == "done" or (status == "filled" and leg.get("exited")):
        return "Hecha", "done"
    if status == "skipped":
        return "Omitida", "wait"
    if status == "cancelled":
        return "Cancelada", "wait"
    return MISSING, "na"


def _limit_pill(leg):
    if not isinstance(leg, dict):
        return MISSING, "na"
    if leg.get("status") == "pending":
        return "Esperando", "wait"
    if leg.get("status") == "armed":
        return "En curso", "run"
    if leg.get("status") in ("submitted", "filled", "done"):
        return "Hecha", "done"
    return _pill(leg)


def _close_pill(leg, now):
    if not isinstance(leg, dict):
        return MISSING, "na"
    if leg.get("status") == "pending":
        return "Esperando", "wait"
    if leg.get("exited") or leg.get("status") in ("done", "skipped", "cancelled"):
        return _pill(leg)
    exit_at = leg.get("exit_at")
    if exit_at is None:
        return MISSING, "na"
    if now >= exit_at:
        return "En curso", "run"
    return "Esperando", "wait"


def _pair(fix, symbol):
    if not isinstance(fix, dict):
        return {"symbol": symbol, "state": MISSING, "tone": "na"}
    own = fix.get("symbol")
    status = fix.get("status")
    if status == "skipped" and not own:
        return {"symbol": symbol, "state": "Omitida", "tone": "wait"}
    if not own:
        if status == "pending":
            state, tone = _pill(fix)
            return {"symbol": symbol, "state": state, "tone": tone}
        return {"symbol": symbol, "state": "Sin operación", "tone": "na"}
    if own != symbol:
        return {"symbol": symbol, "state": "Sin operación", "tone": "na"}
    state, tone = _pill(fix)
    return {"symbol": symbol, "state": state, "tone": tone}


def _market_closed(now):
    from execution.trade_duration import entries_blocked
    try:
        return entries_blocked(now) is True
    except Exception:  # noqa: BLE001 - an unreadable clock stays on the explicit missing label
        return False


def _gap(now):
    return CLOSED if _market_closed(now) else MISSING


def _when(stamp):
    parsed = _aware(stamp)
    if parsed is None:
        return None
    return f"última lectura {datetime_et(parsed)}"


def _deploy_label(env):
    """Identificador que Render ya inyecta. No pide variables nuevas."""
    if not hasattr(env, "get"):
        return MISSING
    commit = env.get("RENDER_GIT_COMMIT")
    if isinstance(commit, str) and re.fullmatch(r"[0-9a-fA-F]{7,40}", commit.strip()):
        return commit.strip()[:7]
    instance = env.get("RENDER_INSTANCE_ID")
    if isinstance(instance, str) and re.fullmatch(r"[A-Za-z0-9_-]{4,64}", instance.strip()):
        token = instance.strip()
        return token if len(token) <= 12 else token[-12:]
    return MISSING


def _saved_account(flags):
    """Last startup reading, else the day-base equity. Neither is a live broker call."""
    from execution.exness_brakes import (
        DAY_EQUITY_FLAG, LAST_BALANCE_FLAG, LAST_CURRENCY_FLAG, LAST_EQUITY_FLAG, LAST_READ_FLAG)
    equity = _money(flags.get(LAST_EQUITY_FLAG))
    balance = _money(flags.get(LAST_BALANCE_FLAG))
    code = flags.get(LAST_CURRENCY_FLAG)
    code = code.strip().upper() if isinstance(code, str) else ""
    if equity is not None and code in ("USC", "USD"):
        return {"balance": balance, "equity": equity, "currency": code, "stored": True,
                "kind": "last", "at": flags.get(LAST_READ_FLAG)}
    day = _money(flags.get(DAY_EQUITY_FLAG))
    if day is None:
        return None
    return {"balance": None, "equity": day, "currency": "USC", "stored": True, "kind": "day", "at": None}


def _heartbeat_fresh(health, now, limit=60):
    stamp = _aware(health.get("runtime_heartbeat")) if isinstance(health, dict) else None
    if stamp is None:
        return False
    age = (now - stamp).total_seconds()
    return 0 <= age <= limit


def _connection(flags, health, now):
    """Conexión Exness y latido. Con el mercado cerrado sigue «mercado cerrado»."""
    if _market_closed(now):
        return CLOSED
    if not isinstance(flags, dict):
        return MISSING
    link = flags.get("exness_link")
    at = _aware(flags.get("exness_link_at"))
    if link not in ("0", "1") or at is None:
        return MISSING
    age = (now - at).total_seconds()
    if not (link == "1" and 0 <= age <= 90):
        return "Sin conexión"
    if not _heartbeat_fresh(health, now, 90):
        return "Sin latido"
    return "Conectada"


def _brake_state_line(flags, brakes, equity, currency):
    """Equity, límite de pérdida diaria y si el freno está activo. La bandera guardada manda."""
    raw = flags.get("exness_brake_state") if isinstance(flags, dict) else None
    parsed = None
    if isinstance(raw, str) and raw.startswith("{"):
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = None
    if isinstance(parsed, dict):
        code = parsed.get("currency") if parsed.get("currency") in ("USC", "USD") else currency
        halt = "activo" if parsed.get("halt") is True else "no"
        return (f"equity {money_line(_money(parsed.get('equity')), code)} · "
                f"límite diario {money_line(_money(parsed.get('daily')), code)} · freno {halt}")
    halt = "activo" if brakes.get("halt") else "no"
    shown = money_line(equity, currency) if equity is not None else MISSING
    return f"equity {shown} · límite diario {money_line(brakes.get('daily'), 'USC')} · freno {halt}"


def _apply_marks(positions, marks, currency):
    """Precio y P/L flotante de una lectura ya hecha. No inventa un precio que no vino."""
    if not isinstance(marks, dict) or not isinstance(positions, dict):
        return
    rows_in = marks.get("positions")
    if not isinstance(rows_in, (list, tuple)):
        return
    by_id = {}
    for item in rows_in:
        if isinstance(item, dict):
            ident = _ident(item.get("position_id"))
            if ident:
                by_id[ident] = item
    profits = []
    rows = positions.get("rows") or []
    for row in rows:
        found = by_id.get(row.get("ticket"))
        if not isinstance(found, dict):
            continue
        current = _money(found.get("current"))
        profit = _money(found.get("profit"))
        if current is not None:
            row["current"] = _plain(current)
        if profit is not None:
            text, tone = _signed(profit, currency)
            row["floating"] = text
            row["floating_tone"] = tone
            profits.append(profit)
    if rows and len(profits) == len(rows):
        text, tone = _signed(sum(profits, Decimal(0)), currency)
        positions["floating"] = text
        positions["floating_tone"] = tone


def _panel_health(health, now):
    """On a closed market, only a stale snapshot becomes «Mercado cerrado».

    A missing or old heartbeat, or a market provider that was never configured, stays degraded.
    """
    if not isinstance(health, dict) or health.get("status") != "DEGRADED" or not _market_closed(now):
        return health
    if health.get("last_cycle_status") == "FAILED":
        return health
    if health.get("scheduler") in {"STOPPED", "DISABLED", "NOT STARTED"}:
        return health
    if not _heartbeat_fresh(health, now):
        return health
    provider = health.get("market_data")
    if not isinstance(provider, str) or provider.strip() in ("", "NOT CONFIGURED"):
        return health
    if health.get("freshness") not in {"STALE_DATA", "NO_DATA"}:
        return health
    shown = dict(health)
    shown["status"] = "MARKET_CLOSED"
    return shown


def _account_from_snapshot(snapshot):
    """Balance and equity only from keys the stored snapshot actually has."""
    if not isinstance(snapshot, dict):
        return None
    state = snapshot.get("account_state") if isinstance(snapshot.get("account_state"), dict) else {}
    account = snapshot.get("account") if isinstance(snapshot.get("account"), dict) else {}
    balance = state.get("balance", snapshot.get("balance", account.get("balance")))
    equity = state.get("equity", snapshot.get("equity", account.get("equity")))
    currency = state.get("currency", snapshot.get("currency", account.get("currency")))
    bal, eq = _money(balance), _money(equity)
    if bal is None and eq is None:
        return None
    code = currency.strip().upper() if isinstance(currency, str) else ""
    if code not in ("USC", "USD"):
        code = "USC"
    return {"balance": bal, "equity": eq, "currency": code, "stored": False}


def _book_from_snapshot(snapshot, book):
    if isinstance(snapshot, dict) and isinstance(snapshot.get("positions"), (list, tuple)):
        orders = snapshot.get("orders") if isinstance(snapshot.get("orders"), (list, tuple)) else ()
        floating = snapshot.get("floating_pnl")
        return {"positions": list(snapshot["positions"]), "orders": list(orders), "floating_pnl": floating}
    return book


def _book_from_marks(flags, now):
    """Libro vacío que el ciclo ya publicó en ``exness_live_marks``.

    La foto del panel a veces no trae la clave ``positions`` (se guardó antes de copiar
    el libro). Una lista vacía y reciente es una lectura real: P/L 0. Una lista con
    posiciones no sustituye al libro (no trae par ni lado). Sin marca, con el enlace
    caído o con la marca vieja, no hay libro.
    """
    if not isinstance(flags, dict) or flags.get("exness_link") == "0":
        return None
    raw = flags.get(LIVE_FLAG)
    if not isinstance(raw, str) or not raw.startswith("{"):
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(parsed, dict) or parsed.get("positions") != []:
        return None
    stamp = _aware(parsed.get("at"))
    if stamp is None:
        return None
    age = (now - stamp).total_seconds()
    if not 0 <= age <= _BOOK_FRESH_SECONDS:
        return None
    orders = parsed.get("orders") if isinstance(parsed.get("orders"), list) else []
    return {"positions": [], "orders": orders, "floating_pnl": None}


def build_dashboard(*, env=None, now=None, flags=None, account_reader=None, book=None, health=None,
                    errors=None, curve=None, snapshot=None, marks=None):
    """One read-only view. Positions' SL/TP come from the linked ``sltp`` order, not the position fields."""
    import os
    from execution.exness_broker import real_execution_armed
    env = os.environ if env is None else env
    now = _now(now)
    flags = {} if flags is None else flags
    snap = snapshot if isinstance(snapshot, dict) else {}
    account = _account_from_snapshot(snap)
    if account is not None:
        account = {**account, "kind": "snapshot", "at": snap.get("as_of")}
    elif account_reader is not None:
        account = {**read_account(env, reader=account_reader, flags=flags), "kind": "reader", "at": None}
    else:
        account = _saved_account(flags) or {
            "balance": None, "equity": None, "currency": "USC", "stored": True, "kind": "none", "at": None}
    brakes = brake_view(env, flags=flags)
    clock = session_clock(env, now=now, flags=flags)
    currency = account.get("currency") or "USC"
    known = account.get("equity") if account.get("kind") in ("snapshot", "reader", "last") else None
    today_amount = None if known is None else (None if _money(brakes.get("day_base")) is None else known - _money(brakes["day_base"]))
    total_amount = None if known is None else (None if _money(brakes.get("brake_start")) is None else known - _money(brakes["brake_start"]))
    today_text, today_tone = _signed(today_amount, currency)
    total_text, total_tone = _signed(total_amount, currency)
    if today_amount is None:
        today_text, today_tone = _gap(now), ""
    if total_amount is None:
        total_text, total_tone = _gap(now), ""
    legs = _legs(flags, now)
    from execution.combo_history import HISTORY_START, extra_rows_from_deals, prior_history_note, prior_history_pnl
    shown = {leg["position_id"] for leg in legs
             if leg.get("position_id") and _closed_leg(leg, now) and _shown_in_history(leg)}
    added = extra_rows_from_deals(flags, shown, now)
    for row in added:
        row["side"] = _side(row.pop("_side", None))
    legs = legs + added
    book = _book_from_snapshot(snap, book)
    if book is None:
        book = _book_from_marks(flags, now)
    # desk_from_book (execution/exness_desk.py) stores current equity at the cycle
    # read time; attach_desk appends those points to ui_snapshots.curve. Prefer the
    # realized series at each close so Rendimiento is not that flat sample.
    start_balance = _money(brakes.get("brake_start"))
    if start_balance is not None:
        start_balance = start_balance + prior_history_pnl(flags)
    start_at = _aware(flags.get("exness_launch_start") if isinstance(flags, dict) else None)
    history_open = datetime(HISTORY_START.year, HISTORY_START.month, HISTORY_START.day, tzinfo=NY)
    if start_at is None or start_at < history_open:
        start_at = history_open
    derived = _history_curve(legs, now, start_balance, start_at)
    if derived:
        curve = derived
    elif curve is None and isinstance(snap.get("curve"), (list, tuple)):
        curve = snap.get("curve")
    positions = _positions(book, legs, currency, now)
    for row in positions["rows"]:
        row.pop("_profit", None)
    _apply_marks(positions, marks, currency)
    day = clock.get("day") if clock.get("waiting") is False else 0
    if not isinstance(day, int) or isinstance(day, bool) or day < 1:
        day = 0
    shown_day = 30 if day > 30 else day
    cycle_pct = 0 if day == 0 else 100 if day > 30 else float(Decimal(day) / Decimal(30) * 100)
    daily_cap = brakes.get("daily") if brakes.get("daily") is not None else USC_DAILY
    total_cap = brakes.get("drawdown") if brakes.get("drawdown") is not None else USC_DRAWDOWN
    daily = _brake_line(daily_cap, None if known is None else _used(brakes.get("day_base"), known), "USC")
    total_brake = _brake_line(total_cap, None if known is None else _used(brakes.get("brake_start"), known), "USC")
    if known is None:
        daily = {**daily, "used": _gap(now), "text": _gap(now)}
        total_brake = {**total_brake, "used": _gap(now), "text": _gap(now)}
    reason = brakes.get("halt_reason")
    reasons = {"daily_loss": "pérdida diaria", "drawdown": "caída máxima", "none": "ninguno"}
    if not isinstance(reason, str) or reason.strip() in ("", "none"):
        reason_text = "ninguno"
    elif reason.strip() in reasons:
        reason_text = reasons[reason.strip()]
    elif re.fullmatch(r"[A-Za-z0-9_]{1,32}", reason.strip()):
        reason_text = reason.strip().replace("_", " ")
    else:
        reason_text = "ninguno"
    system = _system(_panel_health(health, now), errors)
    system["connection"] = _connection(flags, health, now)
    system["deploy"] = _deploy_label(env)
    curve_view = _curve(curve, now, currency)
    equity_text = money_line(account.get("equity"), currency)
    if account.get("equity") is None:
        equity_text = _gap(now)
    balance_text = money_line(account.get("balance"), currency)
    if account.get("balance") is None:
        balance_text = _gap(now)
    read_at = _when(account.get("at"))
    if account.get("kind") == "day":
        equity_sub = "Base del día, 17:00 ET"
    elif read_at:
        equity_sub = read_at
    elif account.get("kind") in ("snapshot", "last", "reader"):
        equity_sub = "Equity vivo" if account.get("kind") == "reader" else _gap(now)
    else:
        equity_sub = _gap(now)
    unavailable = []
    if account.get("balance") is None:
        unavailable.append("Balance")
    if account.get("equity") is None:
        unavailable.append("Equity")
    if today_amount is None:
        unavailable.append("P/L de hoy")
    if total_amount is None:
        unavailable.append("P/L total")
    if positions["state"] == "sin datos":
        unavailable.append("Posiciones")
    elif positions["state"] == "rows" and any(row["sl"] == MISSING or row["tp"] == MISSING for row in positions["rows"]):
        unavailable.append("SL/TP de alguna posición")
    if positions["state"] == "rows" and any(row["current"] == MISSING for row in positions["rows"]):
        unavailable.append("Precio actual")
    if positions["floating"] == MISSING:
        unavailable.append("P/L flotante")
    if curve_view["state"] != "ok":
        unavailable.append("Curva de equity")
    if daily["used"] in (MISSING, CLOSED) or total_brake["used"] in (MISSING, CLOSED):
        unavailable.append("Consumo de frenos")
    if system["state"] != "ok":
        unavailable.append("Estado del sistema")
    else:
        if system["heartbeat"] == MISSING:
            unavailable.append("Latido")
        if system["last_cycle"] == MISSING:
            unavailable.append("Último ciclo")
        if system["errors_state"] == "sin datos":
            unavailable.append("Errores")
    if system.get("connection") == MISSING:
        unavailable.append("Conexión Exness")
    if system.get("deploy") == MISSING:
        unavailable.append("Último despliegue")
    title = LABEL if broker_is_exness(env) else "Jt Trading Floor"
    armed = real_execution_armed(env) is True
    trades = "0" if clock.get("waiting") is not False else str(clock.get("trades") or 0)
    view = {
        "title": title, "version": VERSION, "safety": live_badge(env) or OFF_SAFETY,
        "live": armed, "safety_short": "Ejecución real activa" if armed else "Ejecución real desactivada",
        "clock": long_date_et(now), "now_epoch": str(int(now.timestamp())), "currency": currency,
        "kpis": {
            "balance": _kpi(balance_text, "Saldo de la cuenta" if account.get("balance") is not None else (read_at or _gap(now))),
            "equity": _kpi(equity_text, equity_sub),
            "today": _kpi(today_text, "Respecto a la base del día" if today_amount is not None else MISSING, today_tone),
            "total": _kpi(total_text, "Desde el inicio del ciclo" if total_amount is not None else MISSING, total_tone,
                          note=prior_history_note(flags, currency)),
            "cycle": _kpi(f"Día {shown_day} de 30", clock_line(clock), ""),
            "trades": _kpi(trades, "Operaciones del ciclo"),
        },
        "cycle_pct": cycle_pct, "active": "0" if positions["state"] == "closed" else (MISSING if positions["state"] == "sin datos" else str(len(positions["rows"]))),
        "positions": positions, "curve": curve_view, "strategy": _strategy(legs, now),
        "brakes": {"daily": daily, "total": total_brake, "trade_cap": money_line(brakes.get("cap"), "USC"),
                   "trade": _trade_use(brakes.get("cap"), positions),
                   "halt": "Activo" if brakes.get("halt") else "No",
                   "halt_badge": "Sí" if brakes.get("halt") else "No", "reason": reason_text,
                   "state_line": _brake_state_line(flags, brakes, known, currency)},
        "history": _history(legs, currency, now), "system": system, "unavailable": unavailable, "banner": None,
    }
    return _log_history_view(view, added)


_view_logged = False


def _range_points(ranges, key):
    return len((ranges.get(key) or {}).get("points") or [])


def _log_history_view(view, added):
    """Once per process. No amounts. The next deploy compares this line with the nine real tickets."""
    global _view_logged
    if _view_logged:
        return view
    _view_logged = True
    import logging
    from execution.combo_history import ensure_session_combo_log
    ensure_session_combo_log()
    rows = (view.get("history") or {}).get("rows") or []
    tickets = [row.get("ticket") for row in rows if row.get("ticket") and row.get("ticket") != MISSING]
    ranges = (view.get("curve") or {}).get("ranges") or {}
    points = _range_points(ranges, "30")
    logging.getLogger("ai_floor.session_combo").info(
        "component=session_combo event=HISTORY_VIEW rows=%s tickets=%s curve_points=%s "
        "range_30=%s range_90=%s range_365=%s range_all=%s source=exness_flags",
        len(rows), ",".join(tickets), points,
        points, _range_points(ranges, "90"), _range_points(ranges, "365"), _range_points(ranges, "all"))
    logging.getLogger("ai_floor.session_combo").info(
        "component=session_combo event=HISTORY_DEALS_MERGE added=%s", len(added))
    return view
