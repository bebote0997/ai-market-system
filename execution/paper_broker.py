import math
import uuid
from datetime import datetime

import pandas as pd

from core.rr_contract import POLICY_V2_F3
from execution.contracts import PaperAccount, PaperFill, PaperOrder, PaperPosition


ORDER_STATUSES = {"PENDING", "FILLED", "CANCELLED", "REJECTED"}


def _valid(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) and float(value) > 0


def _text(fraction):
    from decimal import Decimal
    return str(Decimal(fraction.numerator) / Decimal(fraction.denominator))


def _aware(value):
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def _valid_ohlc(bar):
    values = [bar.get(field) for field in ("open", "high", "low", "close")]
    return all(_valid(value) for value in values) and bar["low"] <= bar["open"] <= bar["high"] and bar["low"] <= bar["close"] <= bar["high"]


class PaperBroker:
    def __init__(self, account, instrument=None, rr_policy=None):
        self.account = account
        self.instrument = instrument
        # V2 Phase 4: fill-gate R:R policy. None = frozen V1 (``reward / risk >= 3`` at the fill price).
        self.rr_policy = rr_policy
        self.last_fill_geometry = None
        self.orders = {}
        self.fills = {}
        self.journal = []

    def _event(self, timestamp, run_id, symbol, entity_id, event_type, details=None):
        from execution.contracts import JournalEvent
        self.journal.append(JournalEvent(timestamp, run_id, symbol, entity_id, event_type, details or {}))

    def submit_plan(self, floor_report, market_context, as_of):
        if not _aware(as_of) or not floor_report or floor_report.final_status != "PLAN_READY":
            return None
        plan = floor_report.trade_plan
        decision = floor_report.risk_decision
        if plan is None or decision is None or decision.status != "APPROVED":
            return None
        if plan.run_id != floor_report.run_id or decision.symbol != floor_report.symbol or decision.side != plan.side:
            return None
        if decision.side not in {"LONG", "SHORT"}:
            return None
        plan_as_of = getattr(plan, "as_of", as_of)
        if not _aware(plan_as_of):
            return None
        if floor_report.run_id in {order.run_id for order in self.orders.values()}:
            return next(order for order in self.orders.values() if order.run_id == floor_report.run_id)
        if not _valid(decision.quantity) or not _valid(decision.entry) or not _valid(decision.stop) or not _valid(decision.target):
            return None
        if self.instrument is None or not _valid(getattr(self.instrument, "contract_multiplier", None)):
            return None
        if decision.symbol != self.instrument.symbol:
            return None
        if not _valid(self.account.equity):
            return None
        if floor_report.symbol in self.account.open_positions:
            return None
        order = PaperOrder("1.0", str(uuid.uuid4()), floor_report.run_id, floor_report.symbol, decision.side, decision.quantity, decision.entry, decision.stop, decision.target, float(self.instrument.contract_multiplier), float(self.account.equity), 0.0, plan_as_of)
        self.orders[order.order_id] = order
        self._event(as_of, order.run_id, order.symbol, order.order_id, "ORDER_SUBMITTED")
        return order

    def _fill_rr_rejected(self, order, fill_price, risk_per_unit, reward):
        """Frozen V1 fill-time R:R expression (used only when ``rr_policy`` is None)."""
        return reward / risk_per_unit < 3

    def _fill_check(self, order, fill_price, policy=None):
        """V2 policies: recompute the actual fill geometry (actual fill + frozen SL + frozen TP) with the single R:R
        contract and the policy's execution tolerance. Returns (rejection reason or None, telemetry). SL/TP are never
        moved, re-anchored or normalized to rescue or 'correct' a fill."""
        from core.rr_contract import FILL_LIMITS, LIMITS, WITHIN_POLICY, classify, geometry, to_decimal
        policy = policy or self.rr_policy
        planned, _ = geometry(order.side, order.planned_entry, order.stop, order.target)
        actual, why = geometry(order.side, fill_price, order.stop, order.target)
        entry, fill = to_decimal(order.planned_entry), to_decimal(fill_price)
        adverse = None if entry is None or fill is None else (fill - entry if order.side == "LONG" else entry - fill)
        telemetry = {
            "policy": policy, "planned_entry": None if entry is None else str(entry),
            "fill_price": None if fill is None else str(fill), "stop": str(to_decimal(order.stop)),
            "target": str(to_decimal(order.target)), "planned_rr": None if planned is None else str(planned.rr),
            "actual_risk": None if actual is None else str(actual.risk),
            "actual_reward": None if actual is None else str(actual.reward),
            "actual_fill_rr": None if actual is None else str(actual.rr),
            "fill_rr_floor": str(FILL_LIMITS[policy][0]),
            "displacement_price": None if adverse is None else str(adverse),
            "displacement_r": None if adverse is None or planned is None else str(adverse / planned.risk),
            "fill_direction": None if adverse is None else ("ADVERSE" if adverse > 0 else "FAVORABLE" if adverse < 0 else "EQUAL")}
        if actual is None:
            return "fill_invalid_geometry", {**telemetry, "geometry_error": why}  # at/through SL or past TP
        if classify(actual.rr, policy, FILL_LIMITS) != WITHIN_POLICY:
            return "fill_rr_below_minimum", telemetry
        return None, telemetry

    def _fill_money_check(self, order, risk_policy):
        """DEC-5.7 (fixed 3R only): actual fill money risk <= TRUE planned money risk x fill factor, where the factor
        is derived by the single V2 risk policy from planned R:R 3.00 and the 2.50 fill floor (8/7). Planned money risk
        is the order's own quantity x |planned entry - SL| x multiplier (the risk after quantity normalization), so the
        limit follows the approved plan, never a hard-coded equity percentage. Exact arithmetic, no tolerance, no
        resizing; SL/TP are never moved. Telemetry is added to ``last_fill_geometry``."""
        from fractions import Fraction
        from decimal import Decimal
        from core.rr_contract import to_decimal
        telemetry = self.last_fill_geometry
        values = [to_decimal(v) for v in (order.planned_entry, order.stop, order.quantity, order.contract_multiplier)]
        if any(v is None for v in values) or telemetry.get("actual_risk") is None:
            return "post_fill_risk_or_geometry"
        actual = Decimal(telemetry["actual_risk"])  # Exact geometry risk computed by _fill_check.
        entry, stop, quantity, multiplier = (Fraction(v) for v in values)
        factor = risk_policy.fill_money_risk_factor
        planned_money = abs(entry - stop) * quantity * multiplier
        actual_money = Fraction(actual) * quantity * multiplier
        limit = planned_money * factor
        telemetry.update(risk_policy_version=risk_policy.version, fill_money_risk_factor=str(factor),
                         planned_money_risk=_text(planned_money), actual_fill_money_risk=_text(actual_money),
                         maximum_fill_money_risk=_text(limit), fill_money_risk_ratio=_text(actual_money / planned_money))
        return "fill_money_risk_above_limit" if actual_money > limit else None

    def process_next_bar(self, order, bar):
        if order is None or order.status != "PENDING":
            return None
        if order.side not in {"LONG", "SHORT"} or not _valid(order.contract_multiplier):
            order.status = "REJECTED"
            self._event(None, order.run_id, order.symbol, order.order_id, "ORDER_REJECTED", {"reason": "invalid_order_contract"})
            return None
        timestamp = bar.get("timestamp") if isinstance(bar, dict) else None
        if (not isinstance(bar, dict) or bar.get("symbol") != order.symbol or not _aware(timestamp)
                or not _aware(order.as_of) or bar.get("is_closed") is not True or not _valid_ohlc(bar)):
            order.status = "CANCELLED"
            self._event(timestamp, order.run_id, order.symbol, order.order_id, "ORDER_CANCELLED", {"reason": "invalid_fill_bar"})
            return None
        if timestamp <= order.as_of:
            order.status = "CANCELLED"
            self._event(timestamp, order.run_id, order.symbol, order.order_id, "ORDER_CANCELLED", {"reason": "bar_not_after_as_of"})
            return None
        fill_price = float(bar["open"])
        risk_per_unit = (fill_price - order.stop) if order.side == "LONG" else (order.stop - fill_price)
        reward = (order.target - fill_price) if order.side == "LONG" else (fill_price - order.target)
        real_risk = risk_per_unit * order.quantity * order.contract_multiplier
        current_equity = self.account.equity
        # P5.1C: an order stamped with a durable risk-policy identity is filled under THAT policy's rules, whatever
        # broker processes it, so its reservation (worst permitted fill) always holds. Unstamped orders: unchanged.
        policy, risk_policy = self.rr_policy, None
        stamped = getattr(order, "risk_policy_version", None)  # Absent on pre-P5.1C order objects = unstamped.
        if stamped is not None:
            from core.risk_policy import registered_risk_policy
            risk_policy = registered_risk_policy(stamped)
            if risk_policy is None:
                order.status = "REJECTED"
                self._event(bar["timestamp"], order.run_id, order.symbol, order.order_id, "ORDER_REJECTED",
                            {"reason": "unknown_order_risk_policy"})
                return None
            policy = risk_policy.rr_policy
        elif self.rr_policy == POLICY_V2_F3:
            from core.risk_policy import RISK_POLICY_V2_P5
            risk_policy = RISK_POLICY_V2_P5
        if policy is None:  # Frozen V1 fill gate (byte-identical behavior).
            rejected = (not _valid(current_equity) or not _valid(order.equity_at_submission)
                        or risk_per_unit <= 0 or reward <= 0 or self._fill_rr_rejected(order, fill_price, risk_per_unit, reward)
                        or real_risk > current_equity * 0.01 or real_risk > order.equity_at_submission * 0.01)
            details = {"reason": "post_fill_risk_or_geometry"}
        else:
            reason, self.last_fill_geometry = self._fill_check(order, fill_price, policy)
            if reason is None and (not _valid(current_equity) or not _valid(order.equity_at_submission)):
                reason = "post_fill_risk_or_geometry"  # Invalid account/order state fails closed.
            if reason is None and policy == POLICY_V2_F3:
                reason = self._fill_money_check(order, risk_policy)  # DEC-5.7 replaces the hard-coded 1% fill cap.
            elif reason is None and (real_risk > current_equity * 0.01 or real_risk > order.equity_at_submission * 0.01):
                reason = "post_fill_risk_or_geometry"  # Existing risk gates, unchanged for other V2 policies.
            rejected = reason is not None
            details = {"reason": reason, "fill_geometry": self.last_fill_geometry}
        if rejected:
            order.status = "REJECTED"
            self._event(bar["timestamp"], order.run_id, order.symbol, order.order_id, "ORDER_REJECTED", details)
            return None
        fill = PaperFill("1.0", str(uuid.uuid4()), order.order_id, order.run_id, order.symbol, order.side, order.quantity, order.planned_entry, fill_price, bar["timestamp"])
        order.status = "FILLED"
        self.fills[fill.fill_id] = fill
        position = PaperPosition("1.0", str(uuid.uuid4()), order.order_id, order.run_id, order.symbol, order.side, order.quantity, order.planned_entry, fill.fill_price, order.stop, order.target, fill.fill_timestamp, last_price=fill.fill_price, contract_multiplier=order.contract_multiplier, cost_rate=order.cost_rate)
        self.account.open_positions[position.symbol] = position
        self._event(fill.fill_timestamp, order.run_id, order.symbol, fill.fill_id, "ORDER_FILLED",
                    {"fill_geometry": self.last_fill_geometry} if policy is not None else None)
        self._event(fill.fill_timestamp, order.run_id, order.symbol, position.position_id, "POSITION_OPENED")
        return position
