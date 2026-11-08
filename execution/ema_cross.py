"""EURUSD EMA 10/21 cross, demo paper only. Pure functions: no network, no clock read, no broker.

Frozen candidate (owner authorization 2026-11-07 to prepare the PR; the flag stays OFF). This module does not
replace DEC-FX-SL-3. The H1 pullback limit (12.5 pips, SL 25 / TP 50, partials and trail) is a different strategy
and is not read or modified here.

Rules implemented:

* EMA 10 and EMA 21 on close, shift 0 (the bar's own close is inside its EMA). Seed is the simple average of the
  first ``period`` closes, same construction as ``execution.eurusd_pullback.ema``.
* Signal on the last **closed** M15 bar only: LONG when EMA10 crosses up through EMA21, SHORT when it crosses down.
* H1 filter on the last **completed** H1 bar only: LONG requires EMA10 > EMA21, SHORT requires EMA10 < EMA21.
* The order is not sent at the cross. It becomes eligible 15 minutes later, and only on the first evaluation inside
  that 15-minute slot. A later evaluation does not enter.
* Market order. SL 20 pips and TP 40 pips are measured from the actual fill quote (ask for LONG, bid for SHORT).
  No partials and no trail live in this module.
* Entry window UTC [12, 15), weekdays. The confirming bar close **and** the delayed entry time must both sit in
  that window. Weekend and the global Friday 16:45 America/New_York entry ban (``trade_duration.entries_blocked``)
  refuse the signal.

Discrepancies with the historical simulation are documented in ``docs/ema_cross.md``. This module does not loosen
the live news, session, or risk gates to match that simulation.
"""
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
import hashlib

from core import fx_policy
from execution import trade_duration as td


STRATEGY = "EURUSD_EMA_CROSS"
SYMBOL = "EURUSD"
FLAG = "AI_FLOOR_EMA_CROSS"
FAST = 10
SLOW = 21
WAIT = timedelta(minutes=15)
SLOT = timedelta(minutes=15)
ENTRY_HOUR_START = 12
ENTRY_HOUR_END = 15  # [12, 15)
SL_PIPS = Decimal(20)
TP_PIPS = Decimal(40)
_PRICE = Decimal("0.00001")
_MACRO_LINEAGE = ("macro_lineage_invalid", "macro_future_timestamp", "macro_timestamp_invalid")


def ema_flag(env):
    """OFF unless the value is exactly ``1``. ``0``, empty, ``true`` and whitespace stay off."""
    return (env or {}).get(FLAG) == "1"


def round_fx(price):
    """Same 5-decimal half-up grid as the EURUSD pullback and the OANDA client."""
    if not isinstance(price, Decimal) or not price.is_finite() or price <= 0:
        raise ValueError("price must be a finite positive Decimal")
    return price.quantize(_PRICE, rounding=ROUND_HALF_UP)


def _decimal(value):
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, bool) or value is None:
        return None
    try:
        out = Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None
    return out if out.is_finite() else None


def _utc(at):
    if not isinstance(at, datetime) or at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("timezone-aware datetime required")
    return at.astimezone(timezone.utc)


def ema(closes, period):
    """EMA of ``closes`` (oldest first). ``None`` until ``period`` closes exist."""
    if closes is None or len(closes) < period or period < 1:
        return None
    seed = sum(closes[:period], Decimal(0)) / Decimal(period)
    weight = Decimal(2) / Decimal(period + 1)
    value = seed
    for price in closes[period:]:
        value = price * weight + value * (1 - weight)
    return value


def in_entry_window(at):
    """Weekday UTC hour in [12, 15). Friday 16:45 New York is a separate gate."""
    utc = _utc(at)
    return utc.weekday() < 5 and ENTRY_HOUR_START <= utc.hour < ENTRY_HOUR_END


def client_id_for(signal_id):
    """Stable broker client id. The raw signal id is not used: it contains characters OANDA would hash anyway."""
    if not isinstance(signal_id, str) or not signal_id:
        raise ValueError("signal_id is required")
    return "aif-ema-" + hashlib.sha256(signal_id.encode("utf-8")).hexdigest()[:40]


def signal_id_for(confirmed_at, side):
    stamp = _utc(confirmed_at).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"ema:{SYMBOL}:{side}:{stamp}"


def levels_from_fill(side, fill):
    """``(fill, stop, target)`` on the price grid. SL 20 pips and TP 40 pips from ``fill``."""
    if side not in ("LONG", "SHORT"):
        raise ValueError("side must be LONG or SHORT")
    fill = round_fx(_decimal(fill))
    pip = fx_policy.pip_size(SYMBOL)
    risk, reward = SL_PIPS * pip, TP_PIPS * pip
    if side == "LONG":
        stop, target = fill - risk, fill + reward
    else:
        stop, target = fill + risk, fill - reward
    return fill, round_fx(stop), round_fx(target)


def anchor_plan(side, fill, stop, target, *, bid=None, ask=None):
    """How to make SL/TP 20/40 from the **actual** fill, not from the quote used at send time.

    Returns ``(action, stop, target, reason)``.

    * ``OK`` — the prices already sit on 20/40 within half a pip. Nothing is sent.
    * ``REPLACE`` — send those exact prices. ``risk_above_20`` means the current stop is farther than
      20.5 pips from the fill (or is not protective). A failed replace of that case must close.
      ``restore_20_40`` means the stop is tighter than 20; a failed replace must keep it (risk stays under the cap).
    * ``CLOSE`` — the live bid/ask is already through the 20-pip stop measured from the fill. No modify is sent.
    * ``WAIT`` — the fill is not known. The quote-based stop stays in place and is retried later.
    """
    fill_d, stop_d, target_d = _decimal(fill), _decimal(stop), _decimal(target)
    if side not in ("LONG", "SHORT") or fill_d is None or stop_d is None or target_d is None:
        return ("WAIT", None, None, "fill_unknown")
    pip = fx_policy.pip_size(SYMBOL)
    if side == "LONG":
        sl_pips, tp_pips = (fill_d - stop_d) / pip, (target_d - fill_d) / pip
    else:
        sl_pips, tp_pips = (stop_d - fill_d) / pip, (fill_d - target_d) / pip
    if not distance_problems(sl_pips, tp_pips):
        return ("OK", stop_d, target_d, "already_20_40")
    _fill, new_stop, new_target = levels_from_fill(side, fill_d)
    bid_d, ask_d = _decimal(bid), _decimal(ask)
    if (side == "LONG" and bid_d is not None and bid_d <= new_stop) or (
            side == "SHORT" and ask_d is not None and ask_d >= new_stop):
        return ("CLOSE", new_stop, new_target, "price_through_stop")
    reason = "risk_above_20" if sl_pips <= 0 or sl_pips > SL_PIPS + Decimal("0.5") else "restore_20_40"
    return ("REPLACE", new_stop, new_target, reason)


def distance_problems(sl_pips, tp_pips):
    """Empty when the distances are 20 and 40 pips (half-pip slack) and 2R."""
    if sl_pips is None or tp_pips is None or sl_pips <= 0:
        return ["distance_invalid"]
    problems = []
    if abs(sl_pips - SL_PIPS) > Decimal("0.5"):
        problems.append("sl_not_20")
    if abs(tp_pips - TP_PIPS) > Decimal("0.5"):
        problems.append("tp_not_40")
    rr = tp_pips / sl_pips
    if abs(rr - Decimal(2)) > Decimal("0.02"):
        problems.append("rr_not_2")
    return problems


def context_news_reason(context):
    """News gate for one cycle context. Missing macro state blocks (``macro_unavailable``)."""
    if not isinstance(context, dict):
        return news_gate(None)
    deterministic = context.get("deterministic")
    assessment = getattr(deterministic, "setup_assessment", None)
    warnings = tuple(getattr(assessment, "warnings", ()) or ())
    macro = getattr(deterministic, "macro_news_report", None)
    if macro is None:
        return news_gate(None, warnings)
    evidence = getattr(macro, "evidence", None) or ()
    events = evidence[0].get("macro_events") or () if evidence and isinstance(evidence[0], dict) else ()
    return news_gate(getattr(macro, "status", None), warnings, events)


def session_is_open(at, context):
    """London or New York session, and the cycle did not already record ``session_open`` False."""
    from runtime.scheduler import session_names
    if isinstance(context, dict) and context.get("session_open") is False:
        return False
    return bool(set(session_names(at)) & {"LONDON", "NEW_YORK"})


def news_gate(macro_status, warnings=(), events=()):
    """The same deterministic news facts the setup validator already uses, or ``None`` when the entry may continue.

    ``NO_DATA`` with an empty calendar is allowed: the live validator does not block on an empty calendar. ``None``
    status, ``ERROR``, a broken lineage, or a HIGH event in ``ACTIVE_WINDOW`` blocks. The historical simulation had
    no news filter; blocking here is the stricter live gate.
    """
    warnings = tuple(warnings or ())
    if macro_status is None:
        return "macro_unavailable"
    if macro_status == "ERROR" or "macro_error" in warnings:
        return "macro_error"
    if any(name in warnings for name in _MACRO_LINEAGE):
        return "macro_lineage"
    if macro_status not in {"OK", "PARTIAL", "NO_DATA"}:
        return "macro_unavailable"
    high = any(isinstance(item, dict) and item.get("impact") == "HIGH" and item.get("window") == "ACTIVE_WINDOW"
               for item in events or ())
    if high or "high_impact_active_window" in warnings:
        return "high_impact_active_window"
    return None


def closes_from_frame(frame, duration):
    """``((close_time, close), ...)`` oldest first from a provider frame, or ``None`` when the frame is unusable.

    The index is the bar **start** (Twelve Data / the evidence adapter). Close time is start + ``duration``.
    A bar with ``is_closed`` other than True is dropped. Prices are not invented.
    """
    if frame is None:
        return None
    if isinstance(frame, (list, tuple)):
        return _pairs(frame)
    try:
        ordered = frame.sort_index() if hasattr(frame, "sort_index") else frame
        index = list(ordered.index)
        closes = list(ordered["Close"])
        closed_flags = list(ordered["is_closed"]) if "is_closed" in ordered.columns else [True] * len(index)
    except (TypeError, KeyError, AttributeError, ValueError):
        return None
    rows = []
    for stamp, close, flag in zip(index, closes, closed_flags):
        if flag is not True:
            continue
        start = stamp.to_pydatetime() if hasattr(stamp, "to_pydatetime") else stamp
        if not isinstance(start, datetime):
            return None
        if start.tzinfo is None or start.utcoffset() is None:
            return None
        rows.append((start.astimezone(timezone.utc) + duration, close))
    return _pairs(rows)


def _pairs(rows):
    out = []
    for row in rows:
        try:
            close_time, close = row[0], _decimal(row[1])
        except (TypeError, IndexError, KeyError):
            return None
        if close is None or close <= 0:
            return None
        try:
            close_time = _utc(close_time)
        except ValueError:
            return None
        out.append((close_time, close))
    return tuple(out)


def _usable(bars, at):
    """Closed bars whose close time is at or before ``at``, oldest first. ``None`` when any bar is unusable."""
    parsed = _pairs(bars) if bars is not None else None
    if parsed is None:
        return None
    at = _utc(at)
    kept = tuple((stamp, close) for stamp, close in parsed if stamp <= at)
    if len({stamp for stamp, _ in kept}) != len(kept):
        return None
    return tuple(sorted(kept))


def _cross_side(closes):
    """Direction of the cross on the last close, or ``None`` when there is no cross or not enough history."""
    if closes is None or len(closes) < SLOW + 1:
        return None
    fast, slow = ema(closes, FAST), ema(closes, SLOW)
    prev_fast, prev_slow = ema(closes[:-1], FAST), ema(closes[:-1], SLOW)
    if None in (fast, slow, prev_fast, prev_slow):
        return None
    if prev_fast <= prev_slow and fast > slow:
        return "LONG"
    if prev_fast >= prev_slow and fast < slow:
        return "SHORT"
    return None


def _trend(closes):
    """``LONG`` / ``SHORT`` / ``FLAT`` / ``None`` (not enough closes) on the last value of ``closes``."""
    fast, slow = ema(closes, FAST), ema(closes, SLOW)
    if fast is None or slow is None:
        return None
    if fast > slow:
        return "LONG"
    if fast < slow:
        return "SHORT"
    return "FLAT"


def _opposite_since(bars, confirmed_at, side):
    """True when a later closed M15 bar crosses the other way. ``None`` when the confirming bar is absent."""
    stamps = [stamp for stamp, _ in bars]
    if confirmed_at not in stamps:
        return None
    start = stamps.index(confirmed_at)
    closes = [close for _, close in bars]
    for index in range(start + 1, len(bars)):
        found = _cross_side(closes[:index + 1])
        if found is not None and found != side:
            return True
    return False


@dataclass(frozen=True)
class Signal:
    signal_id: str
    side: str
    confirmed_at: datetime
    ready_at: datetime
    status: str = "PENDING"
    reason: str | None = None
    client_id: str | None = None

    def cancelled(self, reason):
        return replace(self, status="CANCELLED", reason=reason)

    def expired(self, reason):
        return replace(self, status="EXPIRED", reason=reason)


@dataclass(frozen=True)
class Step:
    """What the router should do with ``signal``. ``ENTER`` is the only action that may send an order."""
    action: str
    reason: str
    signal: Signal | None = None


def step(pending, *, at, m15, h1, news_reason=None, session_open=False, flat=True, terminal_ids=()):
    """Advance the delayed signal. Fail-closed: bad bars, a closed session, news, or a busy book never returns ENTER.

    ``terminal_ids`` are signal ids already submitted, cancelled, expired, or rejected. They are not armed again.
    """
    at = _utc(at)
    m15_bars = _usable(m15, at)
    h1_bars = _usable(h1, at)
    if m15_bars is None or h1_bars is None:
        return Step("HOLD", "no_data", pending if pending and pending.status == "PENDING" else None)
    if pending is not None and pending.status == "PENDING":
        return _pending(pending, at, m15_bars, h1_bars, news_reason, session_open, flat)
    return _arm(at, m15_bars, h1_bars, session_open, set(terminal_ids or ()))


def _pending(pending, at, m15_bars, h1_bars, news_reason, session_open, flat):
    confirmed, ready = _utc(pending.confirmed_at), _utc(pending.ready_at)
    trend = _trend([close for _, close in h1_bars])
    if trend != pending.side:
        reason = "h1_no_data" if trend is None else "h1_filter"
        return Step("CANCEL", reason, pending.cancelled(reason))
    opposite = _opposite_since(m15_bars, confirmed, pending.side)
    if opposite is None:
        return Step("CANCEL", "stale_bars", pending.cancelled("stale_bars"))
    if opposite:
        return Step("CANCEL", "opposite_cross", pending.cancelled("opposite_cross"))
    if not in_entry_window(confirmed) or not in_entry_window(ready) or td.entries_blocked(ready):
        return Step("CANCEL", "session_boundary", pending.cancelled("session_boundary"))
    if at < ready:
        return Step("HOLD", "waiting", pending)
    if at >= ready + SLOT:
        return Step("EXPIRE", "stale", pending.expired("stale"))
    if news_reason:
        return Step("HOLD", news_reason, pending)
    if not session_open or not in_entry_window(at) or td.entries_blocked(at):
        return Step("CANCEL", "session_boundary", pending.cancelled("session_boundary"))
    if not flat:
        return Step("HOLD", "position_open", pending)
    return Step("ENTER", "entry", pending)


def _arm(at, m15_bars, h1_bars, session_open, terminal_ids):
    closes = [close for _, close in m15_bars]
    side = _cross_side(closes)
    if side is None:
        return Step("HOLD", "no_cross")
    confirmed = m15_bars[-1][0]
    ready = confirmed + WAIT
    signal_id = signal_id_for(confirmed, side)
    if signal_id in terminal_ids:
        return Step("HOLD", "already_resolved")
    trend = _trend([close for _, close in h1_bars])
    if trend != side:
        return Step("HOLD", "h1_no_data" if trend is None else "h1_filter")
    if (not session_open or not in_entry_window(confirmed) or not in_entry_window(ready)
            or td.entries_blocked(ready) or td.entries_blocked(at)):
        return Step("HOLD", "session_boundary")
    signal = Signal(signal_id, side, confirmed, ready, client_id=client_id_for(signal_id))
    return Step("ARM", "confirmed", signal)


__all__ = ["ENTRY_HOUR_END", "ENTRY_HOUR_START", "FAST", "FLAG", "SL_PIPS", "SLOW", "SLOT", "STRATEGY",
           "anchor_plan",
           "SYMBOL", "TP_PIPS", "WAIT", "Signal", "Step", "client_id_for", "closes_from_frame", "distance_problems",
           "ema", "ema_flag", "in_entry_window", "levels_from_fill", "news_gate", "round_fx", "signal_id_for", "step"]
