"""Daily loss limit (2 % of the day's opening NAV) + account drawdown halt (5 %) for the broker-demo router.

DRAFT, OFF by default: built only by ``limit_from_env`` when ``AI_FLOOR_DAILY_LOSS_LIMIT == "1"`` (exact). Spec:
plans/real_trading_readiness_plan.md section 4.1 (owner decisions D2/D3, 2026-10-17).

- Day = from one 17:00 America/New_York boundary to the next (``day_key``). ``E0`` is fixed at the first evaluation of
  the day and persisted (a restart never recomputes it): the last valid NAV observed before (``dl_last_nav`` /
  ``dl_last_at``, written on every evaluation) when it is at most 7 days old, else the current NAV (first ever
  evaluation, or stale). After downtime across 17:00 the movement between the last observation and the boundary is
  therefore attributed to the new day (conservative: a loss made while down is never erased by a later E0).
- P&L of the day = NAV - E0 - net transfers (realised + open P&L, NAV based; deposits/withdrawals are not P&L).
  ``P&L <= -fraction * E0`` -> DAILY_LOSS_HALT (fixed floor: gains never raise it). ``record_transfer`` adds to the
  current day's transfers when that day already has E0; otherwise it adjusts the pending baseline (``dl_last_nav``) so
  the next E0 already includes it (no baseline -> nothing to adjust: the next E0 is the current NAV, transfer included).
  The router feeds it the broker's TRANSFER_FUNDS transactions (``dl_tx_id`` = last transaction id seen).
- Before each entry: refused when the planned risk exceeds the remaining budget (``fraction * E0 + P&L``).
- Drawdown: NAV <= (start equity + transfers recorded since, ``dd_transfers``) * 0.95, or any finite NAV <= 0 ->
  DRAWDOWN_HALT (always flattens, never resumes by itself).
- The daily halt resumes automatically at the next day boundary; earlier only via ``clear_daily_halt`` (audited).
- Halt latches: a new halt is latched in memory (per tracker and per state DB path, process-local, like the registry's
  emergency latch) BEFORE its flag is written and is honoured even if the write fails: the drawdown latch is released
  only by ``clear_drawdown_halt``, the daily one by the next day key or ``clear_daily_halt``. ``halt_state`` exposes
  latch + flags so the router can keep flattening while NAV is unavailable.
- Independent from the kill switch / emergency latch: own flags in ``broker_demo_flags``, never ``blocked_reason``.

Pure functions + a small tracker over a DemoStateStore-like object (``flag``/``set_flag``/``event``). Any invalid input
or unreadable state refuses entries (fail closed). Events carry amounts and day keys only: never tokens or account ids.
"""
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
import logging
from pathlib import Path
import threading
from zoneinfo import ZoneInfo

LOG = logging.getLogger("ai_floor.broker.daily_loss")
LIMIT_FRACTION = Decimal("0.02")  # hard maximum (D2); env may only make it stricter
DRAWDOWN_FRACTION = Decimal("0.05")  # DEC-5.5, kept by D3
NEW_YORK = ZoneInfo("America/New_York")
RESET_HOUR = 17
ENABLE_ENV = "AI_FLOOR_DAILY_LOSS_LIMIT"
FLATTEN_ENV = "AI_FLOOR_DAILY_LOSS_FLATTEN"
PCT_ENV = "AI_FLOOR_DAILY_LOSS_PCT"
START_EQUITY_ENV = "AI_FLOOR_DAILY_LOSS_START_EQUITY"
DAY_FLAG, E0_FLAG, TRANSFERS_FLAG, HALT_FLAG = "dl_day", "dl_e0", "dl_transfers", "dl_halt"
DD_START_FLAG, DD_HALT_FLAG, DD_TRANSFERS_FLAG = "dd_start", "dd_halt", "dd_transfers"
LAST_NAV_FLAG, LAST_AT_FLAG, TX_FLAG = "dl_last_nav", "dl_last_at", "dl_tx_id"
LAST_NAV_MAX_AGE = timedelta(days=7)
DRAWDOWN_HALT, DAILY_LOSS_HALT, DAILY_LOSS_BUDGET = "drawdown_halt", "daily_loss_halt", "daily_loss_budget"
NAV_INVALID, STATE_UNREADABLE, UNAVAILABLE, INVALID_INPUT = ("nav_invalid", "state_unreadable",
                                                             "daily_loss_unavailable", "invalid_input")


def _dec(value):
    """Finite Decimal or None (bool, None, NaN/inf and non-numbers are rejected)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        out = Decimal(str(value).strip())
    except (ArithmeticError, ValueError):
        return None
    return out if out.is_finite() else None


def _positive(value):
    out = _dec(value)
    return out if out is not None and out > 0 else None


def day_key(now):
    """ISO string of the most recent 17:00 America/New_York boundary <= ``now`` (every calendar day, DST-correct)."""
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be a timezone-aware datetime")
    local = now.astimezone(NEW_YORK)
    boundary = datetime.combine(local.date(), time(RESET_HOUR), tzinfo=NEW_YORK)
    if local < boundary:
        boundary = datetime.combine(local.date() - timedelta(days=1), time(RESET_HOUR), tzinfo=NEW_YORK)
    return boundary.isoformat()


@dataclass(frozen=True)
class DailyLossStatus:
    pnl: Decimal | None
    limit_amount: Decimal | None
    floor: Decimal | None
    remaining_budget: Decimal | None
    breached: bool
    entry_allowed: bool
    reason: str | None


def daily_loss_status(e0, nav, transfers, planned_risk=None, fraction=LIMIT_FRACTION):
    """Pure daily-loss check. ``pnl = nav - e0 - transfers``; breached at ``pnl <= -fraction * e0`` (exact boundary
    breaches). Entries need ``0 < planned_risk <= remaining_budget`` (when given). Invalid inputs -> refused."""
    e0, nav, transfers, fraction = _positive(e0), _positive(nav), _dec(transfers), _positive(fraction)
    risk = None if planned_risk is None else _dec(planned_risk)
    if e0 is None or nav is None or transfers is None or fraction is None or fraction >= 1 or (
            planned_risk is not None and risk is None):
        return DailyLossStatus(None, None, None, None, False, False, INVALID_INPUT)
    pnl = nav - e0 - transfers
    limit_amount = fraction * e0
    remaining = limit_amount + pnl
    floor = e0 * (1 - fraction)
    if pnl <= -limit_amount:
        return DailyLossStatus(pnl, limit_amount, floor, remaining, True, False, DAILY_LOSS_HALT)
    if risk is not None and not 0 < risk <= remaining:
        return DailyLossStatus(pnl, limit_amount, floor, remaining, False, False, DAILY_LOSS_BUDGET)
    return DailyLossStatus(pnl, limit_amount, floor, remaining, False, True, None)


@dataclass(frozen=True)
class DrawdownStatus:
    valid: bool
    breached: bool
    drawdown: Decimal | None  # start - nav (USD, positive = loss)
    floor: Decimal | None


def drawdown_status(start_equity, nav, fraction=DRAWDOWN_FRACTION):
    """Pure drawdown check: breached iff ``nav <= start * (1 - fraction)``. Invalid -> valid False (refuse entries)."""
    start, nav, fraction = _positive(start_equity), _positive(nav), _positive(fraction)
    if start is None or nav is None or fraction is None or fraction >= 1:
        return DrawdownStatus(False, False, None, None)
    floor = start * (1 - fraction)
    return DrawdownStatus(True, nav <= floor, start - nav, floor)


@dataclass(frozen=True)
class LossDecision:
    entry_allowed: bool
    flatten_required: bool
    reason: str | None
    halted: bool
    dd_halted: bool
    daily: DailyLossStatus | None
    newly_halted: bool


def refused(reason):
    """Decision that refuses entries and leaves open trades alone (unreadable NAV/state)."""
    return LossDecision(False, False, reason, False, False, None, False)


_HALT_LATCHES = {}  # resolved state DB path -> {"daily": day key | None, "dd": bool}; process-local
_LATCHES_LOCK = threading.Lock()


def _latch_for(store):
    """Shared latch dict for the store's DB path (survives tracker instances); private dict when it has no path."""
    path = getattr(store, "path", None)
    if not isinstance(path, (str, Path)):
        return {"daily": None, "dd": False}
    try:
        key = str(Path(path).resolve())
    except Exception:  # noqa: BLE001
        key = str(path)
    with _LATCHES_LOCK:
        return _HALT_LATCHES.setdefault(key, {"daily": None, "dd": False})


def _parse_at(value):
    try:
        out = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return out if out.tzinfo is not None and out.utcoffset() is not None else None


class DailyLossLimit:
    def __init__(self, store, *, fraction=LIMIT_FRACTION, flatten=True, start_equity_override=None, clock=None):
        fraction = _positive(fraction)
        self._store = store
        self.fraction = fraction if fraction is not None and fraction <= LIMIT_FRACTION else LIMIT_FRACTION
        self.flatten = bool(flatten)
        self.start_equity_override = _positive(start_equity_override)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.RLock()
        self._latch = _latch_for(store)
        self.on_event = None  # optional callable(kind, detail) set by the router (best-effort alerts)

    def __repr__(self):
        return f"DailyLossLimit(fraction={self.fraction}, flatten={self.flatten})"

    def _event(self, kind, **detail):
        try:
            self._store.event(kind, None, {k: v if v is None or isinstance(v, bool) else str(v)
                                           for k, v in detail.items()})
        except Exception as exc:  # noqa: BLE001 - audit is best effort; the decision uses the flags
            LOG.warning("component=daily_loss event=EVENT_WRITE_FAILED kind=%s error_type=%s", kind,
                        type(exc).__name__)
        if self.on_event is not None:
            try:
                self.on_event(kind, detail)
            except Exception as exc:  # noqa: BLE001 - alerting never alters a decision
                LOG.warning("component=daily_loss event=ALERT_HOOK_FAILED error_type=%s", type(exc).__name__)

    def _state(self):
        """Persisted flags, parsed. Raises ValueError on corrupt values (callers fail closed)."""
        state = {key: self._store.flag(key) for key in (DAY_FLAG, E0_FLAG, TRANSFERS_FLAG, HALT_FLAG, DD_START_FLAG,
                                                        DD_HALT_FLAG, DD_TRANSFERS_FLAG, LAST_NAV_FLAG, LAST_AT_FLAG)}
        for key in (E0_FLAG, DD_START_FLAG):
            if state[key] is not None and _positive(state[key]) is None:
                raise ValueError(f"corrupt {key}")
        for key in (TRANSFERS_FLAG, DD_TRANSFERS_FLAG):
            if state[key] is not None and _dec(state[key]) is None:
                raise ValueError(f"corrupt {key}")
        if state[DAY_FLAG] is not None and (state[E0_FLAG] is None or state[TRANSFERS_FLAG] is None):
            raise ValueError("incomplete day state")
        return state

    def _baseline(self, state, now):
        """Last valid NAV observed (``dl_last_nav``) if at most LAST_NAV_MAX_AGE old (and not in the future)."""
        last, at = _positive(state[LAST_NAV_FLAG]), _parse_at(state[LAST_AT_FLAG])
        if last is None or at is None or not timedelta(0) <= now - at <= LAST_NAV_MAX_AGE:
            return None
        return last

    def _dd_start(self, state):
        """Drawdown baseline (override or persisted first NAV) plus the transfers recorded since it was set."""
        base = self.start_equity_override or _positive(state[DD_START_FLAG])
        return None if base is None else base + (_dec(state[DD_TRANSFERS_FLAG]) or 0)

    def _roll(self, state, key, nav, now):
        """New day: E0 = last observed NAV (fresh) else nav, transfers 0, daily halt cleared (auto-resume, audited when
        it was halted)."""
        e0 = self._baseline(state, now) or nav
        old_day = state[DAY_FLAG]
        was_halted = state[HALT_FLAG] is not None or self._latch["daily"] not in (None, key)
        self._store.set_flag(E0_FLAG, str(e0))
        self._store.set_flag(TRANSFERS_FLAG, "0")
        self._store.set_flag(HALT_FLAG, None)
        self._store.set_flag(DAY_FLAG, key)  # last: a crash mid-roll simply rolls again
        if self._latch["daily"] != key:
            self._latch["daily"] = None
        if was_halted:
            self._event("DAILY_LOSS_RESUMED", day=old_day, new_day=key, e0=e0)
        return {**state, DAY_FLAG: key, E0_FLAG: str(e0), TRANSFERS_FLAG: "0", HALT_FLAG: None}

    def _persist(self, flag, value, kind):
        try:
            self._store.set_flag(flag, value)
        except Exception as exc:  # noqa: BLE001 - the in-memory latch keeps the halt; retried on the next evaluate
            LOG.warning("component=daily_loss event=HALT_WRITE_FAILED kind=%s error_type=%s", kind, type(exc).__name__)

    def _halt(self, flag, value, kind, **detail):
        if flag == DD_HALT_FLAG:  # latch first: honoured even if the write below fails
            self._latch["dd"] = True
        else:
            self._latch["daily"] = value
        self._persist(flag, value, kind)
        self._event(kind, **detail)
        LOG.warning("component=daily_loss event=%s", kind)

    def _remember(self, nav, now):
        """Persist the last valid NAV + time (next day's E0 baseline). Best effort."""
        try:
            self._store.set_flag(LAST_NAV_FLAG, str(nav))
            self._store.set_flag(LAST_AT_FLAG, now.isoformat())
        except Exception as exc:  # noqa: BLE001
            LOG.warning("component=daily_loss event=LAST_NAV_WRITE_FAILED error_type=%s", type(exc).__name__)

    def _non_positive(self, nav, key, now):
        """Finite NAV <= 0: drawdown breach (halt + flatten), no other state touched."""
        daily, dd_halted = self.halt_state(now)
        if not dd_halted:
            self._halt(DD_HALT_FLAG, now.isoformat(), "DRAWDOWN_HALT", nav=nav, day=key)
        return LossDecision(False, True, DRAWDOWN_HALT, daily, True, None, not dd_halted)

    def evaluate(self, nav, planned_risk=None, now=None):
        """Roll the day if needed, evaluate both limits, persist new halts. Returns a LossDecision. NaN/inf/non-numeric
        NAV -> ``nav_invalid`` (nothing touched); a finite NAV <= 0 -> drawdown halt."""
        nav = _dec(nav)
        if nav is None:
            return refused(NAV_INVALID)
        with self._lock:
            now = now or self._clock()
            key = day_key(now)
            if nav <= 0:
                return self._non_positive(nav, key, now)
            try:
                state = self._state()
                if state[DAY_FLAG] != key:
                    state = self._roll(state, key, nav, now)
                if self.start_equity_override is None and state[DD_START_FLAG] is None:
                    if state[DD_TRANSFERS_FLAG] is not None:  # belonged to an older baseline
                        self._store.set_flag(DD_TRANSFERS_FLAG, None)
                    self._store.set_flag(DD_START_FLAG, str(nav))
                    state[DD_START_FLAG], state[DD_TRANSFERS_FLAG] = str(nav), None
            except Exception as exc:  # noqa: BLE001 - unreadable/corrupt state: fail closed, touch nothing
                LOG.warning("component=daily_loss event=STATE_UNREADABLE error_type=%s", type(exc).__name__)
                return refused(STATE_UNREADABLE)
            start = self._dd_start(state)
            e0 = _positive(state[E0_FLAG])
            daily = daily_loss_status(e0, nav, _dec(state[TRANSFERS_FLAG]), planned_risk, self.fraction)
            drawdown = drawdown_status(start, nav)
            dd_halted = state[DD_HALT_FLAG] is not None or self._latch["dd"]
            halted = state[HALT_FLAG] == key or self._latch["daily"] == key
            newly = False
            if self._latch["dd"] and state[DD_HALT_FLAG] is None:  # an earlier halt write failed: retry
                self._persist(DD_HALT_FLAG, now.isoformat(), "DRAWDOWN_HALT")
            if self._latch["daily"] == key and state[HALT_FLAG] != key:
                self._persist(HALT_FLAG, key, "DAILY_LOSS_HALT")
            if drawdown.breached and not dd_halted:
                self._halt(DD_HALT_FLAG, now.isoformat(), "DRAWDOWN_HALT", nav=nav, start=start, floor=drawdown.floor,
                           drawdown=drawdown.drawdown)
                dd_halted = newly = True
            if daily.breached and not halted:
                self._halt(HALT_FLAG, key, "DAILY_LOSS_HALT", day=key, pnl=daily.pnl, floor=daily.floor, nav=nav,
                           e0=e0)
                halted = newly = True
            self._remember(nav, now)
            if dd_halted:
                reason = DRAWDOWN_HALT
            elif halted:
                reason = DAILY_LOSS_HALT
            elif not drawdown.valid:
                reason = STATE_UNREADABLE
            else:  # None, daily_loss_budget, or invalid_input (unusable planned_risk)
                reason = daily.reason
            return LossDecision(reason is None, dd_halted or (halted and self.flatten), reason, halted, dd_halted,
                                daily, newly)

    def halt_state(self, now=None):
        """``(daily_halted, dd_halted)`` from the in-memory latches and the persisted flags (latch only when the flags
        are unreadable). Never raises."""
        key = day_key(now or self._clock())
        daily, dd_halted = self._latch["daily"] == key, bool(self._latch["dd"])
        try:
            daily = daily or self._store.flag(HALT_FLAG) == key
            dd_halted = dd_halted or self._store.flag(DD_HALT_FLAG) is not None
        except Exception as exc:  # noqa: BLE001
            LOG.warning("component=daily_loss event=STATE_UNREADABLE error_type=%s", type(exc).__name__)
        return daily, dd_halted

    def record_transfer(self, amount, actor, reason, now=None):
        """Record a deposit (+) / withdrawal (-) so it is excluded from the day's P&L. When the current day already has
        E0 it is added to the day's transfers (returns the day total); otherwise it adjusts the pending E0 baseline
        (``dl_last_nav``, used by the next roll) and returns None. Without a fresh baseline nothing is adjusted: the
        next E0 is the current NAV, which already contains the transfer."""
        amount = _dec(amount)
        if amount is None or amount == 0:
            raise ValueError("amount must be a non-zero number")
        if not actor:
            raise ValueError("actor required")
        with self._lock:
            now = now or self._clock()
            key = day_key(now)
            state = self._state()
            if self.start_equity_override or state[DD_START_FLAG] is not None:  # the drawdown base excludes it too
                dd_total = (_dec(state[DD_TRANSFERS_FLAG]) or 0) + amount
                self._store.set_flag(DD_TRANSFERS_FLAG, str(dd_total))
            if state[DAY_FLAG] == key:
                total = _dec(state[TRANSFERS_FLAG]) + amount
                self._store.set_flag(TRANSFERS_FLAG, str(total))
                self._event("TRANSFER_RECORDED", day=key, amount=amount, total=total, actor=actor, reason=reason)
                return total
            baseline = self._baseline(state, now)
            if baseline is not None:
                self._store.set_flag(LAST_NAV_FLAG, str(baseline + amount))
            self._event("TRANSFER_RECORDED", day=key, amount=amount, total=None, actor=actor, reason=reason,
                        applied_to="next_e0" if baseline is not None else "none")
            return None

    def transaction_id(self):
        """Last broker transaction id seen (``dl_tx_id``) or None. Raises ValueError when corrupt."""
        value = self._store.flag(TX_FLAG)
        if value is not None and not (isinstance(value, str) and value.isascii() and value.isdigit()):
            raise ValueError(f"corrupt {TX_FLAG}")
        return value

    def set_transaction_id(self, tx_id):
        self._store.set_flag(TX_FLAG, tx_id)

    def clear_drawdown_halt(self, actor, reason):
        """Operator release of the drawdown halt (written review); the baseline is re-initialised on next evaluate.
        The in-memory latch is released only after the flags were written."""
        if not actor:
            raise ValueError("actor required")
        with self._lock:
            self._store.set_flag(DD_HALT_FLAG, None)
            self._store.set_flag(DD_START_FLAG, None)
            self._store.set_flag(DD_TRANSFERS_FLAG, None)
            self._latch["dd"] = False
            self._event("DRAWDOWN_HALT_CLEARED", actor=actor, reason=reason)

    def clear_daily_halt(self, actor, reason):
        """Manual early resume (audited). Re-triggers on the next evaluate if the day is still breached."""
        if not actor:
            raise ValueError("actor required")
        with self._lock:
            day = self._store.flag(DAY_FLAG)
            self._store.set_flag(HALT_FLAG, None)
            self._latch["daily"] = None
            self._event("DAILY_LOSS_HALT_CLEARED", day=day, actor=actor, reason=reason)

    def status(self):
        """Read-only view (amounts, day key, halt flags + latches). Never raises."""
        base = {"enabled": True, "fraction": str(self.fraction), "flatten": self.flatten}
        try:
            state = self._state()
        except Exception:  # noqa: BLE001
            return {**base, "readable": False}
        e0 = _positive(state[E0_FLAG])
        start = self._dd_start(state)
        day = state[DAY_FLAG]
        daily_halted = day is not None and (state[HALT_FLAG] == day or self._latch["daily"] == day)
        return {**base, "readable": True, "day": day, "e0": None if e0 is None else str(e0),
                "floor": None if e0 is None else str(e0 * (1 - self.fraction)), "transfers": state[TRANSFERS_FLAG],
                "daily_halted": daily_halted,
                "drawdown_halted": state[DD_HALT_FLAG] is not None or bool(self._latch["dd"]),
                "drawdown_floor": None if start is None else str(start * (1 - DRAWDOWN_FRACTION))}


def enabled_flag(env):
    """Only the exact value ``1`` turns the limit on."""
    return env.get(ENABLE_ENV) == "1"


def flatten_flag(env):
    """Flatten on breach is ON unless the value is exactly ``0``."""
    return env.get(FLATTEN_ENV) != "0"


def fraction_setting(env):
    """Optional stricter fraction ``0 < x <= 0.02``; anything else -> LIMIT_FRACTION."""
    value = _positive(env.get(PCT_ENV))
    return value if value is not None and value <= LIMIT_FRACTION else LIMIT_FRACTION


def start_equity_setting(env):
    """Optional positive drawdown baseline override; anything else -> None (first observed NAV, persisted)."""
    return _positive(env.get(START_EQUITY_ENV))


def limit_from_env(env, store, clock=None):
    """DailyLossLimit when ``AI_FLOOR_DAILY_LOSS_LIMIT == "1"``; else None (nothing read or written)."""
    if not enabled_flag(env):
        return None
    return DailyLossLimit(store, fraction=fraction_setting(env), flatten=flatten_flag(env),
                          start_equity_override=start_equity_setting(env), clock=clock)
