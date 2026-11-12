"""Exness Trader API adapter. The Floor decides; this class checks, sizes and hands one command to a transport.

Read-only unless ``AI_FLOOR_EXNESS_REAL_EXECUTION`` is exactly ``1`` and ``AI_FLOOR_EXNESS_REAL_CONFIRM`` is exactly
``EXNESS_REAL_AUTHORIZED``. ``REAL_EXECUTION_ENABLED`` stays False and does not arm this adapter.

A stale tick or a dropped WebSocket refuses new orders. The same idempotency key is never submitted twice.
"""
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
import logging

from execution.broker_port import (ACCEPTED, REJECTED, UNKNOWN, BrokerGuardError, BrokerHealth, BrokerPosition,
                                   BrokerUnavailable, CloseResult, ModifyResult, OrderPlan, SubmitResult,
                                   approval_problem)
from execution.dxtrade_broker import real_execution_enabled
from execution.exness_brakes import ExnessBrakes
from execution.exness_sizing import RISK_FRACTION_CAP, account_risk_cap, capped_lots, on_step, size_lots, spread_cost
from execution.exness_transport import Command
from execution.friday_close import OPEN, friday_window, minutes_from_env
from execution.trade_duration import entries_blocked


LOG = logging.getLogger("ai_floor.exness")
BROKER_ENV = "AI_FLOOR_BROKER"
REAL_FLAG = "AI_FLOOR_EXNESS_REAL_EXECUTION"
REAL_CONFIRM_ENV = "AI_FLOOR_EXNESS_REAL_CONFIRM"
REAL_CONFIRM = "EXNESS_REAL_AUTHORIZED"
COMMISSION_ENV = "AI_FLOOR_EXNESS_COMMISSION_PER_LOT_SIDE"
STALE_ENV = "AI_FLOOR_EXNESS_QUOTE_STALE_SECONDS"
DEFAULT_STALE = 5
ALLOWED = ("EURUSD", "GBPUSD", "USDJPY")
_SECRET_ENV = ("EXN_PRIVATE_KEY", "EXN_API_KEY", "OANDA_PRACTICE_TOKEN", "OANDA_LIVE_TOKEN")


class _Redact(logging.Filter):
    def __init__(self, env):
        super().__init__()
        self._secrets = [env.get(key) for key in _SECRET_ENV if isinstance(env.get(key), str) and len(env.get(key)) >= 8]

    def filter(self, record):
        message = record.getMessage()
        cleaned = message
        for secret in self._secrets:
            cleaned = cleaned.replace(secret, "***")
        if cleaned != message:
            record.msg = cleaned
            record.args = ()
        return True


def _install_redaction(env):
    redact = _Redact(env)
    if redact not in LOG.filters:
        LOG.addFilter(redact)


def real_execution_armed(env):
    return env.get(REAL_FLAG) == "1" and env.get(REAL_CONFIRM_ENV) == REAL_CONFIRM


def broker_selected(env):
    return str(env.get(BROKER_ENV) or "oanda").strip().lower() == "exness"


def _stale_seconds(env):
    raw = str(env.get(STALE_ENV) or "").strip()
    if not raw:
        return DEFAULT_STALE
    if not raw.isdigit() or not 1 <= int(raw) <= 120:
        return None
    return int(raw)


def _commission(env):
    raw = env.get(COMMISSION_ENV)
    if raw is None or str(raw).strip() == "":
        return Decimal(0)
    try:
        value = Decimal(str(raw).strip())
    except (ArithmeticError, ValueError):
        return None
    return value if value.is_finite() and value >= 0 else None


def _fraction(value):
    if isinstance(value, bool) or not isinstance(value, Decimal) or not value.is_finite() or not 0 < value <= RISK_FRACTION_CAP:
        return None
    return value


def _round(value, digits):
    return value.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)


def _exposure(book):
    """Positions and working orders. The quote is allowed to move between the two reads."""
    positions, orders, _quote = book.token()
    return positions, orders


class ExnessBroker:
    provider = "exness"

    def __init__(self, transport, *, env, brakes, clock=None, on_alert=None):
        if transport is None or brakes is None or _stale_seconds(env) is None:
            raise BrokerGuardError("exness transport, brakes or quote window is invalid")
        self._transport = transport
        self._env = env
        self._brakes = brakes
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._on_alert = on_alert
        self._stale_alerted = False
        self._cache_reads = False
        self._read_cache = None
        _install_redaction(env)

    def __repr__(self):
        return f"ExnessBroker(provider='exness', armed={real_execution_armed(self._env)!r})"

    @property
    def flag_store(self):
        """Durable flags shared with the brakes. The session combo keeps its day state here."""
        return self._brakes._store

    def _now(self):
        now = self._clock()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise BrokerGuardError("clock must return an aware datetime")
        return now.astimezone(timezone.utc)

    def _alert(self, kind, detail):
        LOG.warning("component=exness event=%s reason=%s", kind, (detail or {}).get("reason"))
        if self._on_alert is None:
            return
        try:
            self._on_alert(kind, dict(detail or {}))
        except Exception as exc:  # noqa: BLE001
            LOG.warning("component=exness event=ALERT_FAILED error_type=%s", type(exc).__name__)

    def cache_account_reads(self, enabled):
        """Reuse one account snapshot for the checks inside a single ``manage``.

        ``open_market`` still reads twice. That second read is what keeps the lot inside the cap
        when the quote moves. The cache is off outside ``ExnessSession.manage``.
        """
        self._cache_reads = bool(enabled)
        if not enabled:
            self._read_cache = None

    def _read(self, *, fresh=False):
        if not fresh and self._read_cache is not None:
            return self._read_cache
        try:
            book = self._transport.read_state()
        except Exception as exc:  # noqa: BLE001
            raise BrokerUnavailable(type(exc).__name__) from None
        if self._cache_reads and not fresh:
            self._read_cache = book
        return book

    def note_link(self, book=None):
        book = self._read() if book is None else book
        fresh = book.quotes.fresh(self._now(), _stale_seconds(self._env))
        if fresh:
            self._stale_alerted = False
            return True
        if not self._stale_alerted:
            self._stale_alerted = True
            self._alert("EXNESS_DISCONNECTED", {"reason": "quote_stale"})
        return False

    def _refuse(self, client_id, reason, result_type=SubmitResult):
        LOG.warning("component=exness event=ORDER_REFUSED reason=%s", reason)
        return result_type(REJECTED, client_id if isinstance(client_id, str) else "", reason=reason)

    def _readonly(self, client_id, result_type=SubmitResult):
        if real_execution_enabled() is not False or not real_execution_armed(self._env):
            if not real_execution_armed(self._env):
                return self._refuse(client_id, "read_only", result_type)
        return None

    def health(self):
        try:
            book = self._read()
            fresh = self.note_link(book)
        except BrokerUnavailable as exc:
            return BrokerHealth("broker_demo", "***", False, connected=False, last_error=type(exc).__name__)
        mode = "real" if real_execution_armed(self._env) else "broker_demo"
        return BrokerHealth(mode, "***", fresh, connected=fresh, last_error=None if fresh else "quote_stale")

    def get_balance(self):
        book = self._read()
        if book.balance is None:
            raise BrokerUnavailable("NO_DATA")
        return book.balance

    def get_positions(self):
        return [BrokerPosition(p.ticket, p.symbol, p.side, p.volume, p.price, p.stop, p.target) for p in self._read().positions]

    def open_trades(self):
        return list(self._read().positions)

    def pending_orders(self):
        return list(self._read().orders)

    def get_quote(self, symbol="EURUSD"):
        book = self._read()
        slot = book.quotes.quote(symbol)
        if symbol not in book.instruments or slot is None or slot.bid is None:
            raise BrokerUnavailable("NO_DATA")
        return self.note_link(book), slot.bid, slot.ask

    def candles(self, symbol="EURUSD", timeframe="5M", start=None, end=None):
        """M5 bid bars from ``GET /v1/market-data/accounts/{id}/candles``.

        Query keys are ``instrument``, ``timeframe=M5``, ``price_type=bid``, ``from`` inclusive and ``to``
        exclusive. A candle is ``{time, open, high, low, close, volume}``. ``time`` is ISO. Prices may be
        numbers. An unknown payload is a gap and is not turned into a bar. No order is sent.
        """
        from execution.exness_m5 import (candle_request_path, candle_spans, floor_m5_utc, parse_candle_payload)
        if timeframe not in ("5M", "M5") or symbol not in ALLOWED:
            return ()
        now = self._now()
        if end is None:
            end = floor_m5_utc(now) + timedelta(minutes=5)
        if start is None:
            end_utc = end.astimezone(timezone.utc)
            start = end_utc - timedelta(hours=24)
        if start >= end:
            return ()
        client = getattr(self._transport, "_client", None)
        getter = getattr(self._transport, "candle_payload", None)
        if client is None and getter is None:
            return ()
        bars = []
        for span_start, span_end in candle_spans(start, end):
            if client is not None:
                path = candle_request_path(client.account_id, symbol, span_start, span_end)
                try:
                    payload = client.get_json(path)
                except Exception as exc:  # noqa: BLE001 - the message can contain the host
                    LOG.warning("component=exness_m5 event=M5_GAP symbol=%s reason=candles_unavailable error_type=%s",
                                symbol, type(exc).__name__)
                    continue
            else:
                payload = getter(symbol, span_start, span_end)
                if payload is None:
                    continue
            parsed, reason = parse_candle_payload(payload, symbol)
            if reason:
                LOG.info("component=exness_m5 event=M5_GAP symbol=%s reason=%s", symbol, reason)
                continue
            bars.extend(parsed)
        bars.sort(key=lambda bar: bar.open_time)
        return tuple(bars)

    def trade_id_for_client(self, client_id):
        book = self._read()
        for position in book.positions:
            if position.comment == client_id:
                return position.ticket
        return None

    def preview_size(self, symbol, entry, stop, risk_fraction):
        book = self._read()
        if symbol not in ALLOWED or symbol not in book.instruments or symbol not in book.specs:
            return Decimal(0), "symbol_unresolved", None
        fraction = _fraction(risk_fraction)
        if fraction is None or book.balance is None:
            return Decimal(0), "sizing_invalid", book.specs.get(symbol)
        lots, reason = size_lots(book.balance.balance, fraction, entry, stop, book.specs[symbol])
        return lots, reason, book.specs[symbol]

    def evaluate_brakes(self, now=None):
        now = self._now() if now is None else now
        book = self._read()
        balance = book.balance
        return self._brakes.evaluate(None if balance is None else balance.equity,
                                     None if balance is None else balance.currency, now)

    def _entry_blocked(self, plan, book, now):
        if plan.symbol not in ALLOWED or plan.symbol not in book.instruments:
            return "symbol_not_supported" if plan.symbol not in ALLOWED else "symbol_unresolved"
        if not book.quotes.fresh(now, _stale_seconds(self._env), plan.symbol):
            self.note_link(book)
            return "quote_stale"
        try:
            again = self._read(fresh=True)
        except BrokerUnavailable:
            return "reread_failed"
        if again.token() != book.token():
            return "reread_changed"
        owned = [p.ticket for p in again.positions if p.comment == plan.client_id]
        if owned:
            return ("already_submitted", owned[0])
        if again.positions or again.orders:
            return "position_already_open"
        if entries_blocked(now):
            return "friday_close_window"
        minutes = minutes_from_env(self._env)
        if minutes is not None and friday_window(now, minutes) != OPEN:
            return "friday_close_window"
        brake = self._brakes.evaluate(again.balance.equity if again.balance else None,
                                      again.balance.currency if again.balance else None, now)
        if not brake.entry_allowed:
            if brake.flatten:
                self._alert("EXNESS_BRAKE_HALT", {"reason": brake.reason})
            return brake.reason or "brake_halt"
        return None

    def submit_plan(self, plan, approval, *, risk_fraction=RISK_FRACTION_CAP):
        return self._submit(plan, approval, "MARKET", None, risk_fraction)

    def submit_limit(self, plan, approval, *, price, risk_fraction=RISK_FRACTION_CAP):
        if not isinstance(price, Decimal) or not price.is_finite() or price <= 0:
            return self._refuse(getattr(plan, "client_id", ""), "limit_price_invalid")
        return self._submit(plan, approval, "LIMIT", price, risk_fraction)

    def _submit(self, plan, approval, kind, limit_price, risk_fraction):
        if not isinstance(plan, OrderPlan):
            return SubmitResult(REJECTED, "", reason="plan_invalid")
        if approval_problem(plan, approval):
            return self._refuse(plan.client_id, approval_problem(plan, approval))
        refused = self._readonly(plan.client_id)
        if refused is not None:
            return refused
        fraction = _fraction(risk_fraction if isinstance(risk_fraction, Decimal) else None)
        if fraction is None:
            return self._refuse(plan.client_id, "risk_fraction_invalid")
        try:
            now = self._now()
            book = self._read()
        except BrokerUnavailable as exc:
            return SubmitResult(UNKNOWN, plan.client_id, reason=f"read_failed:{exc}")
        reason = self._entry_blocked(plan, book, now)
        if isinstance(reason, tuple):
            return SubmitResult(ACCEPTED, plan.client_id, broker_ref=reason[1], reason="already_submitted")
        if reason:
            return self._refuse(plan.client_id, reason)
        slot = book.quotes.quote(plan.symbol)
        spec = book.specs.get(plan.symbol)
        if spec is None or book.balance is None or slot is None or slot.bid is None:
            return self._refuse(plan.client_id, "sizing_invalid")
        lots, why = size_lots(book.balance.balance, fraction, plan.planned_entry, plan.stop, spec)
        if why or plan.quantity > lots or not on_step(plan.quantity, spec):
            return self._refuse(plan.client_id, why or ("above_risk" if plan.quantity > lots else "volume_step"))
        try:
            entry, stop, target = (_round(plan.planned_entry, spec.point_digits), _round(plan.stop, spec.point_digits),
                                   _round(plan.target, spec.point_digits))
        except (ArithmeticError, ValueError):
            return self._refuse(plan.client_id, "price_rounding_breaks_geometry")
        if not (stop < entry < target if plan.side == "LONG" else target < entry < stop):
            return self._refuse(plan.client_id, "price_rounding_breaks_geometry")
        price = entry
        if kind == "LIMIT":
            price = _round(limit_price, spec.point_digits)
            if price != entry or (plan.side == "LONG" and not price < slot.ask) or (
                    plan.side == "SHORT" and not price > slot.bid):
                return self._refuse(plan.client_id, "limit_price_invalid")
        else:
            price = slot.ask if plan.side == "LONG" else slot.bid
        spread = spread_cost(slot.bid, slot.ask, plan.quantity, spec)
        commission = _commission(self._env)
        if spread is None or commission is None:
            return self._refuse(plan.client_id, "cost_unknown")
        cost = spread + commission * plan.quantity * 2
        LOG.info("component=exness event=PRETRADE_COST symbol=%s lots=%s spread_cost=%s commission=%s expected_cost=%s",
                 plan.symbol, format(plan.quantity, "f"), format(spread, "f"), format(commission, "f"), format(cost, "f"))
        command = Command(plan.client_id, kind, plan.symbol, plan.side, plan.quantity, price, stop, target)
        return self._send(command, plan.client_id, SubmitResult)

    def open_market(self, *, symbol, side, quantity, entry, stop, client_id, source_run_id, risk_fraction=RISK_FRACTION_CAP,
                    target=None):
        """Market order from the session-combo strategy. ``target`` None means no take-profit (the fix fade).

        The pending-order JSON type is still undocumented, so stop entries are not sent as broker stops.
        """
        del source_run_id
        if not isinstance(client_id, str) or not client_id or side not in ("LONG", "SHORT"):
            return self._refuse(client_id if isinstance(client_id, str) else "", "plan_invalid")
        refused = self._readonly(client_id)
        if refused is not None:
            return refused
        fraction = _fraction(risk_fraction if isinstance(risk_fraction, Decimal) else None)
        if fraction is None or not isinstance(quantity, Decimal) or not isinstance(entry, Decimal) or not isinstance(stop, Decimal):
            return self._refuse(client_id, "sizing_invalid")
        try:
            now = self._now()
            book = self._read(fresh=True)
        except BrokerUnavailable as exc:
            return SubmitResult(UNKNOWN, client_id, reason=f"read_failed:{exc}")
        if symbol not in ALLOWED or symbol not in book.instruments:
            return self._refuse(client_id, "symbol_not_supported" if symbol not in ALLOWED else "symbol_unresolved")
        if not book.quotes.fresh(now, _stale_seconds(self._env), symbol):
            self.note_link(book)
            return self._refuse(client_id, "quote_stale")
        try:
            again = self._read(fresh=True)
        except BrokerUnavailable:
            return self._refuse(client_id, "reread_failed")
        if _exposure(again) != _exposure(book):
            return self._refuse(client_id, "reread_changed")
        if not again.quotes.fresh(now, _stale_seconds(self._env), symbol):
            self.note_link(again)
            return self._refuse(client_id, "quote_stale")
        owned = [row.ticket for row in again.positions if row.comment == client_id]
        if owned:
            return SubmitResult(ACCEPTED, client_id, broker_ref=owned[0], reason="already_submitted")
        if again.positions or again.orders:
            return self._refuse(client_id, "position_already_open")
        if entries_blocked(now):
            return self._refuse(client_id, "friday_close_window")
        minutes = minutes_from_env(self._env)
        if minutes is not None and friday_window(now, minutes) != OPEN:
            return self._refuse(client_id, "friday_close_window")
        brake = self._brakes.evaluate(again.balance.equity if again.balance else None,
                                      again.balance.currency if again.balance else None, now)
        if not brake.entry_allowed:
            if brake.flatten:
                self._alert("EXNESS_BRAKE_HALT", {"reason": brake.reason})
            return self._refuse(client_id, brake.reason or "brake_halt")
        slot = again.quotes.quote(symbol)
        spec = again.specs.get(symbol)
        price = None if slot is None else (slot.ask if side == "LONG" else slot.bid)
        cap = None if again.balance is None else account_risk_cap(again.balance.currency)
        if spec is None or again.balance is None or price is None or cap is None:
            return self._refuse(client_id, "sizing_invalid" if cap is not None else "currency_unsupported")
        allowed, why = capped_lots(again.balance.equity, again.balance.balance, price, stop, spec, fraction, cap)
        if why:
            return self._refuse(client_id, why)
        if not on_step(quantity, spec):
            return self._refuse(client_id, "volume_step")
        if quantity > allowed:
            quantity = allowed
        try:
            stop_r = _round(stop, spec.point_digits)
            price_r = _round(price, spec.point_digits)
            target_r = None if target is None else _round(target, spec.point_digits)
        except (ArithmeticError, ValueError):
            return self._refuse(client_id, "price_rounding_breaks_geometry")
        if side == "LONG" and not stop_r < price_r:
            return self._refuse(client_id, "price_rounding_breaks_geometry")
        if side == "SHORT" and not stop_r > price_r:
            return self._refuse(client_id, "price_rounding_breaks_geometry")
        if target_r is not None and not (stop_r < price_r < target_r if side == "LONG" else target_r < price_r < stop_r):
            return self._refuse(client_id, "price_rounding_breaks_geometry")
        spread = spread_cost(slot.bid, slot.ask, quantity, spec)
        commission = _commission(self._env)
        if spread is None or commission is None:
            return self._refuse(client_id, "cost_unknown")
        LOG.info("component=exness event=PRETRADE_COST symbol=%s lots=%s spread_cost=%s commission=%s expected_cost=%s",
                 symbol, format(quantity, "f"), format(spread, "f"), format(commission, "f"),
                 format(spread + commission * quantity * 2, "f"))
        return self._send(Command(client_id, "MARKET", symbol, side, quantity, price, stop_r, target_r), client_id,
                          SubmitResult)

    def _send(self, command, client_id, result_type):
        self._read_cache = None
        try:
            result = self._transport.submit(command)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("component=exness event=SUBMIT_UNKNOWN error_type=%s", type(exc).__name__)
            return result_type(UNKNOWN, client_id, reason=type(exc).__name__)
        status = ACCEPTED if result.status == ACCEPTED else REJECTED if result.status == REJECTED else UNKNOWN
        return result_type(status, client_id, broker_ref=result.broker_ref, reason=result.reason)

    def cancel_order(self, order_id, *, client_id):
        refused = self._readonly(client_id, CloseResult)
        if refused is not None:
            return refused
        if not self.note_link():
            return CloseResult(REJECTED, client_id, reason="quote_stale")
        return self._send(Command(f"cancel-{client_id}", "CANCEL", "", position_ref=str(order_id)), client_id, CloseResult)

    def close_market(self, position_ref, *, reason, client_id):
        del reason
        refused = self._readonly(client_id, CloseResult)
        if refused is not None:
            return refused
        if not self.note_link():
            return CloseResult(REJECTED, client_id, reason="quote_stale")
        return self._send(Command(f"close-{position_ref}-{client_id}", "CLOSE", "", position_ref=str(position_ref)),
                          client_id, CloseResult)

    def close_units(self, position_ref, units, *, client_id):
        """Partial close. The live DELETE accepts ``volume`` as a query parameter."""
        refused = self._readonly(client_id, CloseResult)
        if refused is not None:
            return refused
        if not isinstance(units, Decimal) or not units.is_finite() or units <= 0:
            return CloseResult(REJECTED, client_id, reason="volume_invalid")
        try:
            book = self._read()
        except BrokerUnavailable as exc:
            return CloseResult(UNKNOWN, client_id, reason=f"read_failed:{exc}")
        match = next((row for row in book.positions if row.ticket == str(position_ref)), None)
        if match is None:
            return CloseResult(REJECTED, client_id, reason="position_not_found")
        if units > match.volume:
            return CloseResult(REJECTED, client_id, reason="volume_invalid")
        if not self.note_link():
            return CloseResult(REJECTED, client_id, reason="quote_stale")
        return self._send(Command(f"close-{position_ref}-{client_id}", "CLOSE", "", volume=units,
                                 position_ref=str(position_ref)), client_id, CloseResult)

    def modify_stop(self, position_ref, new_stop, *, client_id):
        return self.replace_protection(position_ref, new_stop, None, client_id=client_id)

    def replace_protection(self, position_ref, stop, target, *, client_id):
        refused = self._readonly(client_id, ModifyResult)
        if refused is not None:
            return refused
        if not isinstance(stop, Decimal) or not stop.is_finite() or stop <= 0:
            return ModifyResult(REJECTED, client_id, reason="stop_invalid")
        if not self.note_link():
            return ModifyResult(REJECTED, client_id, reason="quote_stale")
        return self._send(Command(f"modify-{position_ref}-{client_id}", "MODIFY", "", stop=stop, target=target,
                                 position_ref=str(position_ref)), client_id, ModifyResult)

    def connectivity_report(self):
        book = self._read()
        spec = book.specs.get("EURUSD")
        return {"connected": book.quotes.fresh(self._now(), _stale_seconds(self._env)),
                "armed": real_execution_armed(self._env),
                "currency": None if book.balance is None else book.balance.currency,
                "balance": None if book.balance is None else format(book.balance.balance, "f"),
                "equity": None if book.balance is None else format(book.balance.equity, "f"),
                "instruments": list(book.instruments),
                "contract_size": None if spec is None else format(spec.contract_size, "f"),
                "volume_min": None if spec is None else format(spec.volume_min, "f"),
                "volume_step": None if spec is None else format(spec.volume_step, "f"),
                "profit_rate": None if spec is None else format(spec.profit_rate, "f"),
                "bid": None if book.quotes.bid is None else format(book.quotes.bid, "f"),
                "ask": None if book.quotes.ask is None else format(book.quotes.ask, "f"),
                "halted": self._brakes.halted()}


def broker_from_env(env, *, transport=None, clock=None, on_alert=None):
    if transport is None:
        from execution.exness_client import ExnessClient
        from execution.exness_transport import RestExnessTransport
        from execution.exness_live import signer_from_env, urllib_caller
        client = ExnessClient(env, caller=urllib_caller, sign=signer_from_env(env), clock=clock)
        transport = RestExnessTransport(client)
    from execution.exness_brakes import flag_store_from_env
    brakes = ExnessBrakes.from_env(flag_store_from_env(env), env)
    if brakes is None:
        raise BrokerGuardError("exness brake configuration is invalid")
    return ExnessBroker(transport, env=env, brakes=brakes, clock=clock, on_alert=on_alert)
