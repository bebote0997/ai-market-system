import math
import uuid

from execution.contracts import ClosedTrade


class TradeManager:
    def __init__(self, account, broker):
        self.account = account
        self.broker = broker

    def process_bar(self, bar):
        if not isinstance(bar, dict) or bar.get("timestamp") is None:
            return []
        closed = []
        for symbol, position in list(self.account.open_positions.items()):
            if position.status != "OPEN":
                continue
            open_price = bar.get("open")
            high = bar.get("high")
            low = bar.get("low")
            close = bar.get("close")
            if not all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) and float(value) > 0 for value in (open_price, high, low, close)):
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
                continue
            gross = ((exit_price - position.entry_price) if position.side == "LONG" else (position.entry_price - exit_price)) * position.quantity
            cost = 0.0
            net = gross - cost
            trade = ClosedTrade("1.0", str(uuid.uuid4()), position.position_id, position.run_id, position.symbol, position.side, position.entry_price, exit_price, position.quantity, gross, cost, net, bar["timestamp"], reason)
            position.status = "CLOSED"
            self.account.realized_pnl += net
            self.account.closed_trades.append(trade)
            del self.account.open_positions[symbol]
            self.broker._event(bar["timestamp"], position.run_id, symbol, trade.trade_id, "POSITION_CLOSED")
            closed.append(trade)
        self.account.unrealized_pnl = sum(self._unrealized(position) for position in self.account.open_positions.values())
        self.account.equity = self.account.starting_equity + self.account.realized_pnl + self.account.unrealized_pnl
        return closed

    def _unrealized(self, position):
        if position.last_price is None:
            return 0.0
        return ((position.last_price - position.entry_price) if position.side == "LONG" else (position.entry_price - position.last_price)) * position.quantity
