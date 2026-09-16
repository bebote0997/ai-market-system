import math
import uuid

import pandas as pd

from execution.contracts import PaperAccount, PaperFill, PaperOrder, PaperPosition


ORDER_STATUSES = {"PENDING", "FILLED", "CANCELLED", "REJECTED"}


def _valid(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) and float(value) > 0


class PaperBroker:
    def __init__(self, account):
        self.account = account
        self.orders = {}
        self.fills = {}
        self.journal = []

    def _event(self, timestamp, run_id, symbol, entity_id, event_type, details=None):
        from execution.contracts import JournalEvent
        self.journal.append(JournalEvent(timestamp, run_id, symbol, entity_id, event_type, details or {}))

    def submit_plan(self, floor_report, market_context, as_of):
        if not floor_report or floor_report.final_status != "PLAN_READY":
            return None
        plan = floor_report.trade_plan
        decision = floor_report.risk_decision
        if plan is None or decision is None or decision.status != "APPROVED":
            return None
        if plan.run_id != floor_report.run_id or decision.symbol != floor_report.symbol or decision.side != plan.side:
            return None
        if floor_report.run_id in {order.run_id for order in self.orders.values()}:
            return next(order for order in self.orders.values() if order.run_id == floor_report.run_id)
        if not _valid(decision.quantity) or not _valid(decision.entry) or not _valid(decision.stop) or not _valid(decision.target):
            return None
        if floor_report.symbol in self.account.open_positions:
            return None
        order = PaperOrder("1.0", str(uuid.uuid4()), floor_report.run_id, floor_report.symbol, decision.side, decision.quantity, decision.entry, decision.stop, decision.target)
        self.orders[order.order_id] = order
        self._event(as_of, order.run_id, order.symbol, order.order_id, "ORDER_SUBMITTED")
        return order

    def process_next_bar(self, order, bar):
        if order is None or order.status != "PENDING":
            return None
        if not isinstance(bar, dict) or not _valid(bar.get("open")) or bar.get("timestamp") is None:
            order.status = "CANCELLED"
            self._event(bar.get("timestamp") if isinstance(bar, dict) else None, order.run_id, order.symbol, order.order_id, "ORDER_CANCELLED")
            return None
        fill = PaperFill("1.0", str(uuid.uuid4()), order.order_id, order.run_id, order.symbol, order.side, order.quantity, order.planned_entry, float(bar["open"]), bar["timestamp"])
        order.status = "FILLED"
        self.fills[fill.fill_id] = fill
        position = PaperPosition("1.0", str(uuid.uuid4()), order.order_id, order.run_id, order.symbol, order.side, order.quantity, order.planned_entry, fill.fill_price, order.stop, order.target, fill.fill_timestamp, last_price=fill.fill_price)
        self.account.open_positions[position.symbol] = position
        self._event(fill.fill_timestamp, order.run_id, order.symbol, fill.fill_id, "ORDER_FILLED")
        self._event(fill.fill_timestamp, order.run_id, order.symbol, position.position_id, "POSITION_OPENED")
        return position
