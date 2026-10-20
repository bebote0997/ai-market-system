"""OANDA practice-demo numbers for the dashboard, counted from the demo epoch only (never PAPER/V1 history).

Pure functions over the safe trade dicts of ``execution.oanda_readonly`` (``TRADE_FIELDS``) plus a read-only loader of
the router's ``broker_demo_trades`` rows (risk geometry; the table holds no P&L). Only routed demo trades
(``demo_epoch.is_demo_trade``) opened at/after the epoch count. Closed P&L = ``realizedPL + financing``; "today" is
the America/New_York date of ``closeTime``; drawdown is on cumulative realized P&L (peak starts at 0);
R = P&L / (|fill (or planned entry) - original stop| * units). Nothing here writes, orders or touches the network.
"""
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal
import math
from pathlib import Path
import re
import sqlite3
from zoneinfo import ZoneInfo

from execution import daily_loss_limit as dl
from execution.demo_epoch import is_demo_trade, parse_aware

ET = ZoneInfo("America/New_York")
DEMO_LENGTH_DAYS = 30
# Same keys as ui.views.luxury.EMPTY_STATS (the overview tiles render either).
STAT_KEYS = ("trades", "wins", "total_pnl", "today_pnl", "max_drawdown", "avg_r", "avg_r_reason")
NO_CLOSED_REASON = "aún sin operaciones cerradas del demo"
NO_RISK_REASON = "sin datos del stop original"
RISK_COLUMNS = ("client_id", "units", "entry", "stop", "initial_stop", "fill_price", "opened_at", "status")
_RFC3339 = re.compile(r"^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|z|[+-]\d{2}:\d{2})?$")


def parse_time(value):
    """OANDA RFC 3339 (nanoseconds) or ISO 8601 with offset -> aware UTC datetime; naive/invalid -> None."""
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo is not None else None
    if not isinstance(value, str):
        return None
    match = _RFC3339.match(value.strip())
    if not match or match.group(3) is None:
        return None
    stamp, fraction, zone = match.groups()
    return parse_aware(f"{stamp.replace(' ', 'T')}.{(fraction or '0')[:6].ljust(6, '0')}"
                       f"{'+00:00' if zone in ('Z', 'z') else zone}")


def as_float(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value if isinstance(value, (int, float, Decimal)) else str(value))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _symbol(instrument):
    return instrument.replace("_", "") if isinstance(instrument, str) else None


def _side(units):
    return None if units is None or units == 0 else "LONG" if units > 0 else "SHORT"


def _in_demo(trade, epoch):
    opened = parse_time(trade.get("openTime"))
    return is_demo_trade(trade.get("client_id")) and opened is not None and opened >= epoch


def realized_r(pnl, trade, risk_row):
    """(R, None) or (None, reason). Original stop = ``initial_stop`` once moved (Day-2), else the stored ``stop``."""
    if risk_row is None:
        return None, "sin datos de la orden del router"
    stop = as_float(risk_row.get("initial_stop"))
    if stop is None:
        stop = as_float(risk_row.get("stop"))
    entry = as_float(risk_row.get("fill_price"))
    if entry is None:
        entry = as_float(risk_row.get("entry"))
    units = as_float(risk_row.get("units"))
    if units is None or units == 0:
        units = as_float(trade.get("initialUnits"))
    if stop is None or entry is None or units is None or pnl is None:
        return None, NO_RISK_REASON
    risk = abs(entry - stop) * abs(units)
    if risk <= 0:
        return None, NO_RISK_REASON
    return pnl / risk, None


def demo_rows(trades, epoch, risk_rows=None):
    """(closed_rows oldest-first by close time, open_rows newest-first) for demo trades opened >= ``epoch``."""
    risk_rows = risk_rows or {}
    closed, opened = [], []
    for trade in trades or ():
        if not isinstance(trade, dict) or not _in_demo(trade, epoch):
            continue
        units = as_float(trade.get("initialUnits"))
        base = {"trade_id": trade.get("id"), "client_id": trade.get("client_id"),
                "symbol": _symbol(trade.get("instrument")), "opened_at": parse_time(trade.get("openTime")),
                "entry_price": as_float(trade.get("price")), "stop": as_float(trade.get("stop_loss")),
                "target": as_float(trade.get("take_profit"))}
        exited = parse_time(trade.get("closeTime"))
        if trade.get("state") == "CLOSED" or exited is not None:
            realized, financing = as_float(trade.get("realizedPL")), as_float(trade.get("financing"))
            pnl = None if realized is None else realized + (financing or 0.0)
            value, reason = realized_r(pnl, trade, risk_rows.get(trade.get("client_id")))
            closed.append({**base, "side": _side(units), "units": abs(units) if units is not None else None,
                           "quantity": abs(units) if units is not None else None, "exited_at": exited,
                           "exit_price": as_float(trade.get("averageClosePrice")), "net_pnl": pnl,
                           "realized_r": value, "realized_r_reason": reason})
        else:
            current = as_float(trade.get("currentUnits"))
            current = units if current is None else current
            opened.append({**base, "side": _side(current), "units": abs(current) if current is not None else None,
                           "quantity": abs(current) if current is not None else None,
                           "unrealized_pnl": as_float(trade.get("unrealizedPL"))})
    far = datetime.max.replace(tzinfo=timezone.utc)
    closed.sort(key=lambda row: (row["exited_at"] or far, str(row["trade_id"])))
    opened.sort(key=lambda row: (row["opened_at"], str(row["trade_id"])), reverse=True)
    return closed, opened


def demo_statistics(trades, now, epoch, risk_rows=None):
    """(stats, curve, open_rows). No closed demo trade -> zeros (the demo starts from zero), never PAPER numbers."""
    closed, opened = demo_rows(trades, epoch, risk_rows)
    stats = {"trades": 0, "wins": 0, "total_pnl": 0.0, "today_pnl": 0.0, "max_drawdown": 0.0, "avg_r": None,
             "avg_r_reason": NO_CLOSED_REASON}
    if not closed:
        return stats, [], opened
    today = now.astimezone(ET).date()
    total = peak = drawdown = today_pnl = sum_r = 0.0
    wins = unknown = 0
    reason = None
    curve = []
    for row in closed:
        pnl, at = row["net_pnl"], row["exited_at"]
        if pnl is None or at is None:  # unreadable P&L: no trustworthy totals ("sin datos")
            return {**stats, "trades": len(closed), "total_pnl": None, "today_pnl": None, "max_drawdown": None,
                    "avg_r_reason": "datos de cierre incompletos en OANDA"}, [], opened
        total += pnl
        peak = max(peak, total)
        drawdown = max(drawdown, peak - total)
        wins += pnl > 0
        if at.astimezone(ET).date() == today:
            today_pnl += pnl
        if row["realized_r"] is None:
            unknown += 1
            reason = reason or row["realized_r_reason"]
        else:
            sum_r += row["realized_r"]
        curve.append({"Closed at": at, "Cumulative realized PnL": total})
    count = len(closed)
    return {"trades": count, "wins": wins, "total_pnl": total, "today_pnl": today_pnl, "max_drawdown": drawdown,
            "avg_r": None if unknown else sum_r / count,
            "avg_r_reason": f"{reason} en {unknown} de {count} operaciones" if unknown else None}, curve, opened


def demo_capital(nav, balance, stats):
    """``{"start", "change"}``: the demo's starting capital = balance - realized P&L since the epoch, and NAV - start
    (realized + open result since the demo began). Unknown inputs -> None values."""
    balance, nav = as_float(balance), as_float(nav)
    realized = as_float((stats or {}).get("total_pnl"))
    start = balance - realized if balance is not None and realized is not None else None
    return {"start": start, "change": nav - start if nav is not None and start is not None else None}


def demo_day(first_open_at, now, length=DEMO_LENGTH_DAYS):
    """1-based ET calendar day of the demo counted from the first routed trade (weekends count; capped at ``length``,
    ``length=None`` = uncapped so callers can tell "complete"); None if none."""
    first = parse_time(first_open_at)
    if first is None or not isinstance(now, datetime) or now.tzinfo is None:
        return None
    day = max(1, (now.astimezone(ET).date() - first.astimezone(ET).date()).days + 1)
    return day if length is None else min(int(length), day)


LOSS_FLAGS = (dl.DAY_FLAG, dl.E0_FLAG, dl.TRANSFERS_FLAG, dl.HALT_FLAG, dl.DD_HALT_FLAG)


def load_loss_flags(path):
    """The router's daily-loss / drawdown flags from ``broker_demo_flags`` opened read-only (``mode=ro``, never creates
    the file). ``{}`` when the DB/table is missing (router never ran); None on any read error. Never raises."""
    try:
        target = Path(path)
        if not target.is_file():
            return {}
        with closing(sqlite3.connect(target.resolve().as_uri() + "?mode=ro", uri=True, timeout=1.0)) as con:
            marks = ", ".join("?" * len(LOSS_FLAGS))
            rows = con.execute(f"SELECT key, value FROM broker_demo_flags WHERE key IN ({marks})",
                               LOSS_FLAGS).fetchall()
    except sqlite3.OperationalError as exc:
        return {} if "no such table" in str(exc) else None
    except Exception:  # noqa: BLE001 - locked / corrupt: unknown state
        return None
    return {key: value for key, value in rows}


def daily_loss_view(flags, nav, now, env=None):
    """Daily-loss status of the OANDA demo for the dashboard (DEC-5.5a): today's realised + open loss (NAV-based, as
    the router evaluates it) vs ``fraction`` of the day-start NAV ``dl_e0``. ``status``: ``OFF`` (limit not enabled),
    ``NO_DATA`` (flags unreadable, or no valid day-start NAV / transfers / NAV), ``PENDING`` (not yet evaluated this trading day), ``TRIGGERED`` (latched for the
    trading day), ``OK``. ``drawdown_halted`` mirrors the 5 % drawdown latch. Amounts None when unknown. Pure."""
    env = env or {}
    fraction = dl.fraction_setting(env)
    view = {"status": "OFF", "fraction": float(fraction), "day_start_nav": None, "pnl": None, "loss": None,
            "used_pct": None, "drawdown_halted": False}
    if not dl.enabled_flag(env):
        return view
    if flags is None:
        return {**view, "status": "NO_DATA"}
    view["drawdown_halted"] = flags.get(dl.DD_HALT_FLAG) is not None
    try:
        key = dl.day_key(now)
    except ValueError:
        return {**view, "status": "NO_DATA"}
    if flags.get(dl.DAY_FLAG) != key:
        return {**view, "status": "PENDING"}
    e0, transfers, nav = as_float(flags.get(dl.E0_FLAG)), as_float(flags.get(dl.TRANSFERS_FLAG)), as_float(nav)
    view["status"] = "TRIGGERED" if flags.get(dl.HALT_FLAG) == key else "NO_DATA"
    if e0 is None or e0 <= 0:
        return view
    view["day_start_nav"] = e0
    if nav is not None and transfers is not None:
        pnl = nav - e0 - transfers
        loss = max(0.0, -pnl)
        view.update(pnl=pnl, loss=loss, used_pct=100.0 * loss / (float(fraction) * e0))
        if view["status"] != "TRIGGERED":
            view["status"] = "OK"
    return view


def load_risk_rows(path):
    """``{client_id: row}`` from ``broker_demo_trades`` opened read-only (``mode=ro``, never creates the file).
    Missing DB/table or any read error -> {}. Never raises."""
    try:
        target = Path(path)
        if not target.is_file():
            return {}
        with closing(sqlite3.connect(target.resolve().as_uri() + "?mode=ro", uri=True, timeout=1.0)) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(f"SELECT {', '.join(RISK_COLUMNS)} FROM broker_demo_trades").fetchall()
    except Exception:  # noqa: BLE001 - router never ran / locked / corrupt: no risk data
        return {}
    return {row["client_id"]: dict(row) for row in rows if is_demo_trade(row["client_id"])}


def first_demo_open(risk_rows, epoch):
    """Earliest ``opened_at`` of a routed demo trade (``aif-test-*`` excluded) at/after ``epoch``, or None."""
    times = [parse_time(row.get("opened_at")) for client_id, row in (risk_rows or {}).items()
             if is_demo_trade(client_id) and isinstance(row, dict)]
    times = [at for at in times if at is not None and at >= epoch]
    return min(times) if times else None
