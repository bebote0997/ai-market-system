"""Floor session in front of ``ExnessBroker``. Built only when ``AI_FLOOR_BROKER`` is exactly ``exness``.

Reuses the Friday entry ban, optional Friday pre-flatten, the one-position rule, a re-read inside the adapter,
and the dollar brakes. The OANDA pullback manager is not reimplemented here.
"""
from datetime import datetime, timezone
from decimal import Decimal
import logging

from execution.broker_port import APPROVED, OrderPlan, RiskApproval
from execution.exness_broker import broker_from_env
from execution.exness_sizing import RISK_FRACTION_CAP
from execution.friday_close import OPEN, friday_window, minutes_from_env
from execution.exness_m5 import M5Feed, m5_feed_enabled
from execution.session_combo import SessionCombo, combo_enabled
from execution.trade_duration import entries_blocked


LOG = logging.getLogger("ai_floor.exness")
PLAN_READY = "PLAN_READY"


def _dec(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        out = value if isinstance(value, Decimal) else Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None
    return out if out.is_finite() and out > 0 else None


def _fraction(decision):
    value = _dec(getattr(decision, "risk_fraction", None))
    return value if value is not None and value <= RISK_FRACTION_CAP else RISK_FRACTION_CAP


class ExnessSession:
    def __init__(self, broker, *, env, clock=None):
        self.broker = broker
        self._env = env
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.on_alert = None
        self._combo = SessionCombo(broker) if combo_enabled(env) else None
        self._feed = M5Feed(broker._transport.quotes) if m5_feed_enabled(env) else None
        logging.getLogger("ai_floor.session_combo").info(
            "component=session_combo event=%s", "SESSION_COMBO_ON" if self._combo is not None else "SESSION_COMBO_OFF")
        logging.getLogger("ai_floor.exness_m5").info(
            "component=exness_m5 event=%s", "M5_FEED_ON" if self._feed is not None else "M5_FEED_OFF")

    @property
    def on_alert(self):
        return self._on_alert

    @on_alert.setter
    def on_alert(self, value):
        self._on_alert = value
        self.broker._on_alert = value

    def __repr__(self):
        return f"ExnessSession(broker={self.broker!r})"

    def _alert(self, kind, detail):
        if self.on_alert is None:
            return
        try:
            self.on_alert(kind, dict(detail or {}))
        except Exception as exc:  # noqa: BLE001
            LOG.warning("component=exness event=ALERT_FAILED error_type=%s", type(exc).__name__)

    def manage(self, at, bars=None):
        try:
            if bars is None and self._feed is not None:
                self._feed.close_elapsed(at)
                bars = self._feed.bars()
            fresh = self.broker.note_link()
            brake = self.broker.evaluate_brakes(at)
            if brake.flatten:
                self._alert("EXNESS_BRAKE_HALT", {"reason": brake.reason})
            minutes = minutes_from_env(self._env)
            friday = entries_blocked(at) or (minutes is not None and friday_window(at, minutes) != OPEN)
            if not brake.flatten and not friday:
                self._run_combo(at, bars)
                return {}
            if not fresh:
                self._run_combo(at, bars)
                return {"_": "quote_stale"}
            results = {}
            for position in self.broker.open_trades():
                closed = self.broker.close_market(position.ticket, reason=brake.reason or "friday_close",
                                                   client_id=f"flatten-{position.ticket}")
                results[position.ticket] = closed.status
            self._run_combo(at, bars)
            return results
        except Exception as exc:  # noqa: BLE001
            LOG.warning("component=exness event=MANAGE_FAILED error_type=%s", type(exc).__name__)
            return {"_": type(exc).__name__}

    def _run_combo(self, at, bars):
        if self._combo is None:
            return
        try:
            self._combo.tick(at, bars or ())
        except Exception as exc:  # noqa: BLE001 - the next tick retries; paper is unchanged
            LOG.warning("component=session_combo event=TICK_FAILED error_type=%s", type(exc).__name__)

    def _candidate(self, context, status):
        if status != PLAN_READY or not isinstance(context, dict):
            return ("IGNORED", None)
        symbol, run_id = context.get("symbol"), context.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            return ("NOT_ROUTED", "run_id_missing")
        if symbol != "EURUSD":
            return ("NOT_ROUTED", "symbol_not_supported")
        if context.get("eligible") is not True:
            return ("NOT_ROUTED", "not_eligible")
        deterministic, ai = context.get("deterministic"), context.get("ai")
        decision = getattr(deterministic, "risk_decision", None)
        if (getattr(deterministic, "final_status", None) != PLAN_READY or getattr(ai, "final_status", None) != PLAN_READY
                or getattr(decision, "status", None) != APPROVED
                or getattr(getattr(ai, "risk_decision", None), "status", None) != APPROVED):
            return ("NOT_ROUTED", "risk_not_approved")
        entry, stop, target = (_dec(getattr(decision, "entry", None)), _dec(getattr(decision, "stop", None)),
                               _dec(getattr(decision, "target", None)))
        side = getattr(decision, "side", None)
        if entry is None or stop is None or target is None or side not in ("LONG", "SHORT"):
            return ("NOT_ROUTED", "plan_decision_mismatch")
        if not (stop < entry < target if side == "LONG" else target < entry < stop):
            return ("NOT_ROUTED", "invalid_plan_geometry")
        return decision, entry, stop, target, side, symbol, run_id

    def on_cycle(self, context, status, at, h1_bars=None, m15_bars=None, h1_closes=None):
        del h1_bars, m15_bars, h1_closes
        try:
            self.manage(at)
            candidate = self._candidate(context, status)
            if len(candidate) == 2:
                return candidate
            decision, entry, stop, target, side, symbol, run_id = candidate
            client_id = f"aif-{run_id}"
            fraction = _fraction(decision)
            lots, why, _spec = self.broker.preview_size(symbol, entry, stop, fraction)
            if why:
                LOG.info("component=exness event=SKIPPED reason=%s", why)
                return ("SKIPPED_BELOW_MIN" if why == "below_volume_min" else "NOT_ROUTED", why)
            plan = OrderPlan(symbol, side, lots, entry, stop, target, client_id, run_id)
            approval = RiskApproval(APPROVED, run_id, symbol, side, lots, entry, stop, target)
            result = self.broker.submit_plan(plan, approval, risk_fraction=fraction)
            return result.status, result.broker_ref or result.reason
        except Exception as exc:  # noqa: BLE001
            LOG.warning("component=exness event=CYCLE_FAILED error_type=%s", type(exc).__name__)
            return ("NOT_ROUTED", type(exc).__name__)

    def process_operator_requests(self, at):
        return self.manage(at)

    def consume_provider_transition(self, store, config, env, at):
        return None

    def sync_transition_fence(self, store):
        return None

    def drawdown_basis(self):
        return {"source": "EXNESS", "error": "exness_basis_not_oanda"}


def session_from_env(env, *, transport=None, clock=None):
    return ExnessSession(broker_from_env(env, transport=transport, clock=clock), env=env, clock=clock)
