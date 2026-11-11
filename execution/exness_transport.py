"""What ``ExnessBroker`` calls. A live transport and a fake one implement the same two methods.

The live transport speaks the documented REST paths. WebSocket frames update the quote book; tests push ticks
directly. Partial close is not sent: the DELETE position page we could read does not name a volume field.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import logging
from typing import Optional, Protocol

from execution.broker_port import BrokerBalance
from execution.exness_brakes import MemoryFlags
from execution.exness_sizing import spec_from_conditions
from execution.exness_stream import QuoteBook


LOG = logging.getLogger("ai_floor.exness")


@dataclass(frozen=True)
class Position:
    ticket: str
    symbol: str
    side: str
    volume: Decimal
    price: Decimal
    stop: Optional[Decimal] = None
    target: Optional[Decimal] = None
    comment: str = ""


@dataclass(frozen=True)
class WorkingOrder:
    ticket: str
    symbol: str
    side: str
    volume: Decimal
    price: Decimal
    stop: Optional[Decimal] = None
    target: Optional[Decimal] = None
    kind: str = "limit"


@dataclass(frozen=True)
class Book:
    balance: Optional[BrokerBalance]
    positions: tuple
    orders: tuple
    instruments: tuple
    specs: dict
    quotes: QuoteBook
    currency: str = ""

    def token(self):
        positions = tuple((p.ticket, p.side, format(p.volume, "f")) for p in self.positions)
        orders = tuple((o.ticket, o.kind, format(o.volume, "f")) for o in self.orders)
        quote = None if self.quotes.bid is None else (format(self.quotes.bid, "f"), format(self.quotes.ask, "f"),
                                                      self.quotes.connected)
        return positions, orders, quote


@dataclass(frozen=True)
class Command:
    idempotency_key: str
    kind: str
    symbol: str
    side: Optional[str] = None
    volume: Optional[Decimal] = None
    price: Optional[Decimal] = None
    stop: Optional[Decimal] = None
    target: Optional[Decimal] = None
    position_ref: Optional[str] = None


@dataclass(frozen=True)
class CommandResult:
    status: str
    idempotency_key: str
    broker_ref: Optional[str] = None
    reason: Optional[str] = None


class ExnessTransport(Protocol):
    def read_state(self) -> Book: ...

    def submit(self, command: Command) -> CommandResult: ...


def _dec(value):
    if isinstance(value, bool) or isinstance(value, float) or value is None:
        return None
    try:
        out = Decimal(value) if isinstance(value, int) else Decimal(str(value).strip())
    except (ArithmeticError, ValueError):
        return None
    return out if out.is_finite() else None


class FakeExnessTransport:
    """In-memory terminal. The same idempotency key never opens a second position."""

    name = "fake"

    def __init__(self, *, clock=None):
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.flags = MemoryFlags()
        self.balance = None
        self.positions = []
        self.orders = []
        self.instruments = ("EURUSD",)
        self.conditions = {}
        self.quotes = QuoteBook()
        self.commands = []
        self._done = {}
        self._seq = 0
        self.fail = None

    def get_flag(self, key):
        return self.flags.get_flag(key)

    def set_flag(self, key, value):
        self.flags.set_flag(key, value)

    def push_tick(self, bid, ask, at=None):
        self.quotes.note_tick(bid, ask, self._clock() if at is None else at)

    def read_state(self):
        if self.fail == "read":
            raise RuntimeError("fake read failed")
        specs = {}
        currency = None if self.balance is None else self.balance.currency
        for name, payload in self.conditions.items():
            spec, _reason = spec_from_conditions(payload, currency)
            if spec is not None:
                specs[name] = spec
        return Book(self.balance, tuple(self.positions), tuple(self.orders), tuple(self.instruments), specs,
                    self.quotes, currency or "")

    def submit(self, command):
        prior = self._done.get(command.idempotency_key)
        if prior is not None:
            return prior
        if self.fail == "submit":
            raise RuntimeError("fake submit failed")
        self._seq += 1
        ref = str(self._seq)
        if command.kind == "MARKET":
            self.positions.append(Position(ref, command.symbol, command.side, command.volume, command.price,
                                           command.stop, command.target, command.idempotency_key))
            result = CommandResult("ACCEPTED", command.idempotency_key, ref, "filled")
        elif command.kind == "LIMIT":
            self.orders.append(WorkingOrder(ref, command.symbol, command.side, command.volume, command.price,
                                            command.stop, command.target, "limit"))
            result = CommandResult("ACCEPTED", command.idempotency_key, ref, "resting")
        elif command.kind == "CANCEL":
            before = len(self.orders)
            self.orders = [o for o in self.orders if o.ticket != command.position_ref]
            result = (CommandResult("ACCEPTED", command.idempotency_key, command.position_ref, "cancelled")
                      if len(self.orders) != before else CommandResult("REJECTED", command.idempotency_key, None, "order_not_found"))
        elif command.kind == "CLOSE":
            before = len(self.positions)
            self.positions = [p for p in self.positions if p.ticket != command.position_ref]
            result = (CommandResult("ACCEPTED", command.idempotency_key, command.position_ref, "closed")
                      if len(self.positions) != before else CommandResult("REJECTED", command.idempotency_key, None, "position_not_found"))
        elif command.kind == "MODIFY":
            found = any(p.ticket == command.position_ref for p in self.positions)
            result = (CommandResult("ACCEPTED", command.idempotency_key, command.position_ref, "modified") if found
                      else CommandResult("REJECTED", command.idempotency_key, None, "position_not_found"))
        else:
            result = CommandResult("REJECTED", command.idempotency_key, None, "kind_not_supported")
        self._done[command.idempotency_key] = result
        self.commands.append(command)
        return result


def _side(value):
    if value in ("buy", "LONG"):
        return "LONG"
    if value in ("sell", "SHORT"):
        return "SHORT"
    return None


class RestExnessTransport:
    """Live transport. ``client`` is an ``ExnessClient``. Ticks are pushed by the WebSocket loop via ``quotes``."""

    name = "exness_rest"

    def __init__(self, client, *, quotes=None):
        self._client = client
        self.quotes = quotes or QuoteBook()
        self.flags = MemoryFlags()

    def get_flag(self, key):
        return self.flags.get_flag(key)

    def set_flag(self, key, value):
        self.flags.set_flag(key, value)

    def read_state(self):
        account_id = self._client.account_id
        account = self._client.get_json(f"/v1/configuration/accounts/{account_id}/account")
        snapshot = self._client.get_json(f"/v1/trading/accounts/{account_id}/snapshot")
        listed = self._client.get_json(f"/v1/configuration/accounts/{account_id}/instruments")
        state = snapshot.get("account_state") if isinstance(snapshot.get("account_state"), dict) else account
        balance = _dec(state.get("balance"))
        equity = _dec(state.get("equity"))
        margin = _dec(state.get("used_margin"))
        currency = state.get("currency") if isinstance(state.get("currency"), str) else account.get("currency")
        money = None
        if balance is not None and equity is not None and balance >= 0 and equity >= 0 and isinstance(currency, str):
            money = BrokerBalance(balance, equity, margin if margin is not None and margin >= 0 else None, currency.upper())
        names = listed.get("instruments") if isinstance(listed.get("instruments"), list) else []
        instruments = tuple(item for item in names if isinstance(item, str))
        specs = {}
        if "EURUSD" in instruments:
            payload = self._client.get_json(f"/v1/configuration/accounts/{account_id}/instruments/EURUSD/conditions")
            spec, _reason = spec_from_conditions(payload, None if money is None else money.currency)
            if spec is not None:
                specs["EURUSD"] = spec
        positions, orders = [], []
        for item in snapshot.get("positions") or []:
            if not isinstance(item, dict) or _side(item.get("side")) is None:
                continue
            volume, price = _dec(item.get("volume")), _dec(item.get("price") or item.get("open_price"))
            ticket = item.get("id") or item.get("position_id")
            if volume is None or price is None or not isinstance(ticket, str):
                continue
            positions.append(Position(ticket, str(item.get("instrument") or ""), _side(item.get("side")), volume, price,
                                      _dec(item.get("stop_loss_price")), _dec(item.get("take_profit_price")),
                                      str(item.get("client_request_id") or "")))
        for item in snapshot.get("orders") or []:
            if not isinstance(item, dict) or _side(item.get("side")) is None:
                continue
            volume, price = _dec(item.get("volume")), _dec(item.get("price"))
            ticket = item.get("id") or item.get("order_id")
            if volume is None or price is None or not isinstance(ticket, str):
                continue
            orders.append(WorkingOrder(ticket, str(item.get("instrument") or ""), _side(item.get("side")), volume, price,
                                       _dec(item.get("stop_loss_price")), _dec(item.get("take_profit_price")),
                                       str(item.get("order_type") or "limit")))
        return Book(money, tuple(positions), tuple(orders), instruments, specs, self.quotes,
                    "" if money is None else money.currency)

    def submit(self, command):
        account = self._client.account_id
        side = "buy" if command.side == "LONG" else "sell" if command.side == "SHORT" else None
        if command.kind == "MARKET":
            body = {"instrument": command.symbol, "side": side, "volume": format(command.volume, "f"),
                    "stop_loss_price": format(command.stop, "f"), "take_profit_price": format(command.target, "f")}
            path = f"/v1/trading/accounts/{account}/positions"
            method = "POST"
        elif command.kind == "LIMIT":
            # TODO: the pending-order page names the types limit/stop/sltp but not the JSON key. ``order_type`` is unverified.
            body = {"instrument": command.symbol, "side": side, "volume": format(command.volume, "f"),
                    "order_type": "limit", "price": format(command.price, "f"),
                    "stop_loss_price": format(command.stop, "f"), "take_profit_price": format(command.target, "f")}
            path = f"/v1/trading/accounts/{account}/orders"
            method = "POST"
        elif command.kind == "CANCEL":
            body, method = None, "DELETE"
            path = f"/v1/trading/accounts/{account}/orders/{command.position_ref}"
        elif command.kind == "CLOSE":
            body, method = None, "DELETE"
            path = f"/v1/trading/accounts/{account}/positions/{command.position_ref}"
        elif command.kind == "MODIFY":
            body = {"stop_loss_price": format(command.stop, "f")}
            if command.target is not None:
                body["take_profit_price"] = format(command.target, "f")
            method = "PATCH"
            path = f"/v1/trading/accounts/{account}/orders/{command.position_ref}"
        else:
            return CommandResult("REJECTED", command.idempotency_key, None, "kind_not_supported")
        result = self._client.mutate(method, path, body, command.idempotency_key)
        status = result["status"]
        payload = result["payload"]
        if result.get("uncertain") or status >= 500 or status == 0:
            return CommandResult("UNKNOWN", command.idempotency_key, payload.get("operation_id"), "uncertain")
        if status == 429:
            return CommandResult("REJECTED", command.idempotency_key, None, "rate_limited")
        if status in (200, 202):
            # ACK is not a fill. UNKNOWN until a transaction_event or operation status says otherwise.
            return CommandResult("UNKNOWN", command.idempotency_key, payload.get("operation_id"),
                                 payload.get("status") or "accepted")
        reason = payload.get("error_message") if isinstance(payload.get("error_message"), str) else f"http_{status}"
        return CommandResult("REJECTED", command.idempotency_key, None, reason[:64])
