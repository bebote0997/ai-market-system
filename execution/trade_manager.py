import math
import uuid
from datetime import datetime

from execution.contracts import ClosedTrade


def _aware(value):
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def _valid_multiplier(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) and float(value) > 0


class TradeManager:
    def __init__(self, account, broker):
        self.account = account
        self.broker = broker

    def process_bar(self, bar):
        if not isinstance(bar, dict) or bar.get("timestamp") is None:
            return []
        closed = []
        bar_symbol = bar.get("symbol")
        timestamp = bar.get("timestamp")
        if bar_symbol is None or not _aware(timestamp) or bar.get("is_closed") is not True:
            return []
        for symbol, position in list(self.account.open_positions.items()):
            if position.status != "OPEN" or position.side not in {"LONG", "SHORT"}:
                continue
            if bar_symbol != symbol or not _aware(position.opened_at) or not _valid_multiplier(position.contract_multiplier):
                continue
            if timestamp <= position.opened_at:
                continue
            if position.last_processed_at is not None:
                if not _aware(position.last_processed_at) or timestamp <= position.last_processed_at:
                    continue
            open_price = bar.get("open")
            high = bar.get("high")
            low = bar.get("low")
            close = bar.get("close")
            if not all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) and float(value) > 0 for value in (open_price, high, low, close)):
                continue
            if not (low <= open_price <= high and low <= close <= high):
                continue
            if position.side == "LONG":
                gap_stop = open_price <= position.stop
                gap_target = open_price >= position.target
                stop_hit = low <= position.stop
                target_hit = high >= position.target
            else:
                gap_stop = open_price >= position.stop
                gap_target = open_price <= position.target
                stop_hit = high >= position.stop
                target_hit = low <= position.target
            exit_price = None
            reason = None
            if gap_stop:
                exit_price, reason = float(open_price), "stop"
            elif gap_target:
                exit_price, reason = float(open_price), "target"
            elif stop_hit:
                exit_price, reason = position.stop, "stop"
            elif target_hit:
                exit_price, reason = position.target, "target"
            if exit_price is None:
                position.last_price = float(close)
                position.last_processed_at = timestamp
                continue
            gross = ((exit_price - position.entry_price) if position.side == "LONG" else (position.entry_price - exit_price)) * position.quantity * position.contract_multiplier
            cost = position.cost_rate * position.quantity * position.entry_price / 100
            net = gross - cost
            trade = ClosedTrade("1.0", str(uuid.uuid4()), position.position_id, position.run_id, position.symbol, position.side, position.entry_price, exit_price, position.quantity, gross, cost, net, bar["timestamp"], reason)
            position.status = "CLOSED"
            self.account.realized_pnl += net
            self.account.closed_trades.append(trade)
            del self.account.open_positions[symbol]
            if reason in ("stop", "target"):
                self.broker._event(bar["timestamp"], position.run_id, symbol, trade.trade_id, "STOP_HIT" if reason == "stop" else "TARGET_HIT")
            self.broker._event(bar["timestamp"], position.run_id, symbol, trade.trade_id, "POSITION_CLOSED")
            closed.append(trade)
        self.account.unrealized_pnl = sum(self._unrealized(position) for position in self.account.open_positions.values())
        self.account.equity = self.account.starting_equity + self.account.realized_pnl + self.account.unrealized_pnl
        return closed

    def _unrealized(self, position):
        if position.last_price is None or not _valid_multiplier(position.contract_multiplier):
            return 0.0
        return ((position.last_price - position.entry_price) if position.side == "LONG" else (position.entry_price - position.last_price)) * position.quantity * position.contract_multiplier
