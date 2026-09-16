import math
import uuid
from datetime import datetime

import pandas as pd

from execution.contracts import PaperAccount, PaperFill, PaperOrder, PaperPosition


ORDER_STATUSES = {"PENDING", "FILLED", "CANCELLED", "REJECTED"}


def _valid(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) and float(value) > 0


def _aware(value):
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def _valid_ohlc(bar):
    values = [bar.get(field) for field in ("open", "high", "low", "close")]
    return all(_valid(value) for value in values) and bar["low"] <= bar["open"] <= bar["high"] and bar["low"] <= bar["close"] <= bar["high"]


class PaperBroker:
    def __init__(self, account, instrument=None):
        self.account = account
        self.instrument = instrument
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
        if (not _valid(current_equity) or not _valid(order.equity_at_submission)
                or risk_per_unit <= 0 or reward <= 0 or reward / risk_per_unit < 3
                or real_risk > current_equity * 0.01 or real_risk > order.equity_at_submission * 0.01):
            order.status = "REJECTED"
            self._event(bar["timestamp"], order.run_id, order.symbol, order.order_id, "ORDER_REJECTED", {"reason": "post_fill_risk_or_geometry"})
            return None
        fill = PaperFill("1.0", str(uuid.uuid4()), order.order_id, order.run_id, order.symbol, order.side, order.quantity, order.planned_entry, fill_price, bar["timestamp"])
        order.status = "FILLED"
        self.fills[fill.fill_id] = fill
        position = PaperPosition("1.0", str(uuid.uuid4()), order.order_id, order.run_id, order.symbol, order.side, order.quantity, order.planned_entry, fill.fill_price, order.stop, order.target, fill.fill_timestamp, last_price=fill.fill_price, contract_multiplier=order.contract_multiplier, cost_rate=order.cost_rate)
        self.account.open_positions[position.symbol] = position
        self._event(fill.fill_timestamp, order.run_id, order.symbol, fill.fill_id, "ORDER_FILLED")
        self._event(fill.fill_timestamp, order.run_id, order.symbol, position.position_id, "POSITION_OPENED")
        return position
