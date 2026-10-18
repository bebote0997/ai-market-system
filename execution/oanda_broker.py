"""OANDA v20 PRACTICE adapter, EURUSD only (DRAFT, NOT wired: nothing in runtime/, floor/, ui/ or ejecutar_* imports it).

The constructor fails closed (``BrokerGuardError``) BEFORE any client is built, so a refused construction never touches
the network. Required: ``REAL_EXECUTION_ENABLED`` still False, ``AI_FLOOR_BROKER_DEMO_ENABLED=1``,
``AI_FLOOR_BROKER_DEMO_PROVIDER=oanda``, ``mode == 'broker_demo'``, no emergency stop (env or registry), the
``broker_demo`` switch ON, a non-empty ``OANDA_PRACTICE_TOKEN`` and a practice account id (``OANDA_ACCOUNT_ID``, required, no default; prefix
``101-``; live ``001-`` accounts refused). The host is hard-coded to the practice REST URL (no env override exists).

``submit_plan`` refuses (REJECTED, before any network access) unless it gets an APPROVED ``RiskApproval`` matching the
plan exactly, the symbol is ``EURUSD`` and the quantity is an integer number of units. A read-only pre-submit gate then
checks idempotency (trade ``@<client id>`` already exists -> ACCEPTED ``already_submitted``, no second POST), no
hedging/netting and that EUR_USD is tradeable. Trading timeouts/5xx are UNKNOWN and are followed by exactly ONE
reconciliation read; an order is never re-posted: a client id whose outcome is UNKNOWN stays latched (in memory, per
process) and later submits with it only do the read-only lookup. The switch/emergency check is re-run by the client's
``pre_send_guard`` right before every transport attempt of a submit POST / modify PUT (after the rate-limiter wait and
each 429 backoff); ``close_market`` is never guarded. ``submit_plan``, ``modify_stop`` and ``close_market`` are serialized per instance (RLock held
across the gate reads and the write).

Single process, exclusive account ownership: a second process (or another tool) trading the same practice account is
unsupported — the lock and the UNKNOWN latch only cover this instance.

Assumption: the practice account currency is USD (EUR_USD is quoted in USD, so the value of 1 unit per price unit is
1 USD) — ``size_units`` relies on it.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_FLOOR, Decimal
import hashlib
import logging
import re
import threading

from execution.broker_config import load_broker_config
from execution.broker_port import (ACCEPTED, REJECTED, UNKNOWN, BrokerAuthError, BrokerBalance, BrokerError,
                                   BrokerGuardError, BrokerHealth, BrokerMalformedResponse, BrokerPosition,
                                   BrokerRateLimited, BrokerRejected, BrokerUnavailable, CloseResult, ModifyResult,
                                   OrderPlan, SubmitResult, approval_problem)
from execution.dxtrade_broker import real_execution_enabled
from execution.oanda_client import PRACTICE_ACCOUNT_RE, OandaClient, mask_account, reason_code, round_price


LOG = logging.getLogger("ai_floor.broker.oanda")
PROVIDER = "oanda"
DEMO_MODE = "broker_demo"
TOKEN_ENV = "OANDA_PRACTICE_TOKEN"
ACCOUNT_ENV = "OANDA_ACCOUNT_ID"  # required: no default account id lives in code (the repository is public)
ALLOWED_SYMBOLS = ("EURUSD",)
INSTRUMENTS = {"EURUSD": "EUR_USD"}
# Open trades on these instruments can be shown with a project symbol; anything else is unmappable.
SYMBOL_FOR_INSTRUMENT = {"EUR_USD": "EURUSD", "XAU_USD": "XAUUSD", "NAS100_USD": "NAS100"}
# 404 codes meaning "this trade does not exist" (UNCONFIRMED until the read-only spike: both spellings accepted).
TRADE_NOT_FOUND_CODES = ("TRADE_DOES_NOT_EXIST", "NO_SUCH_TRADE")
CLIENT_TAG = "aifloor"
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,128}$")
_UNSAFE_COMMENT_RE = re.compile(r"[^A-Za-z0-9_.:\-]")
_TRADE_ID_RE = re.compile(r"^\d{1,20}$")
_RFC3339_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?Z$")
IN_FLIGHT, OUTCOME_UNKNOWN = "in_flight", "unknown"


def size_units(risk_amount, entry, stop):
    """floor(risk_amount / |entry - stop|) units of EUR_USD (USD account). < 1 -> 0 (the caller skips the order)."""
    for name, value in (("risk_amount", risk_amount), ("entry", entry), ("stop", stop)):
        if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
            raise ValueError(f"{name} must be a finite positive Decimal")
    distance = abs(entry - stop)
    if distance == 0:
        raise ValueError("entry and stop must differ")
    units = int((risk_amount / distance).to_integral_value(rounding=ROUND_FLOOR))
    return units if units >= 1 else 0


def client_extension_id(client_id):
    """Deterministic OANDA clientExtensions id: the client id itself if safe (<= 128 chars, ``[A-Za-z0-9_.-]``),
    otherwise ``aif-<sha256[:32]>``."""
    if not isinstance(client_id, str) or not client_id:
        raise ValueError("client_id is required")
    if _SAFE_ID_RE.match(client_id):
        return client_id
    return "aif-" + hashlib.sha256(client_id.encode("utf-8")).hexdigest()[:32]


def _comment(source_run_id):
    return ("run:" + _UNSAFE_COMMENT_RE.sub("_", str(source_run_id)))[:100]


def check_oanda_guards(config, env, *, mode, registry):
    """Raise BrokerGuardError unless every practice-only guard holds; returns ``(token, account_id)``. No network."""
    if real_execution_enabled() is not False:
        raise BrokerGuardError("REAL_EXECUTION_ENABLED must stay False")
    if not config.demo_enabled:
        raise BrokerGuardError("AI_FLOOR_BROKER_DEMO_ENABLED is not 1")
    if config.demo_provider != PROVIDER:
        raise BrokerGuardError("AI_FLOOR_BROKER_DEMO_PROVIDER is not 'oanda'")
    if mode != DEMO_MODE:
        raise BrokerGuardError("only mode 'broker_demo' is accepted")
    if config.emergency_stop:
        raise BrokerGuardError("emergency stop active (AI_FLOOR_EMERGENCY_STOP)")
    if registry is None:
        raise BrokerGuardError("account registry required")
    if registry.emergency_active():
        raise BrokerGuardError("emergency stop active (registry)")
    if not registry.is_allowed(DEMO_MODE):
        raise BrokerGuardError("broker_demo switch is OFF")
    token = env.get(TOKEN_ENV)
    token = token.strip() if isinstance(token, str) else ""
    if not token:
        raise BrokerGuardError(f"{TOKEN_ENV} missing")
    account = env.get(ACCOUNT_ENV)
    account = account.strip() if isinstance(account, str) else ""
    if not account:
        raise BrokerGuardError(f"{ACCOUNT_ENV} missing")
    if account.startswith("001-"):
        raise BrokerGuardError("live OANDA accounts (001-) are refused")
    if not PRACTICE_ACCOUNT_RE.match(account):
        raise BrokerGuardError(f"{ACCOUNT_ENV} is not a practice account id (101-xxx-...-xxx)")
    return token, account


def parse_open_time(value):
    """OANDA RFC3339 UTC time (``2026-10-17T12:00:00.123456789Z``, nanoseconds) -> aware UTC datetime, else None."""
    match = _RFC3339_RE.match(value) if isinstance(value, str) else None
    if match is None:
        return None
    try:
        base = datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return base.replace(microsecond=int((match.group(2) or "0")[:6].ljust(6, "0")))


def _dec(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        out = Decimal(str(value))
    except ArithmeticError:
        return None
    return out if out.is_finite() else None


@dataclass(frozen=True)
class OpenTrade:
    trade_id: str
    instrument: str
    units: Decimal  # signed currentUnits
    price: Decimal
    client_ext_id: str = None
    stop: Decimal = None
    target: Decimal = None
    open_time: datetime = None  # aware UTC from ``openTime``; None when absent/unparseable

    @property
    def side(self):
        return "LONG" if self.units > 0 else "SHORT"


@dataclass(frozen=True)
class ReconcileReport:
    missing_at_broker: tuple  # local refs with no open trade at the broker
    unknown_at_broker: tuple  # broker tradeIDs no local ref knows about
    ok_refs: tuple  # local refs matched to an open trade
    unsupported: tuple  # (tradeID, instrument) open on instruments other than EUR_USD


def _optional_price(item, key):
    order = item.get(key)
    if order is None:
        return None
    price = _dec(order.get("price")) if isinstance(order, dict) else None
    if price is None or price <= 0:
        raise BrokerMalformedResponse(f"trade with invalid {key}")
    return price


def _parse_trade(item):
    if not isinstance(item, dict):
        raise BrokerMalformedResponse("trade entry is not an object")
    trade_id, instrument = item.get("id"), item.get("instrument")
    if not isinstance(trade_id, str) or not _TRADE_ID_RE.match(trade_id):
        raise BrokerMalformedResponse("trade without a valid id")
    if not isinstance(instrument, str) or not instrument:
        raise BrokerMalformedResponse("trade without instrument")
    units = _dec(item.get("currentUnits"))
    if units is None or units == 0 or units != units.to_integral_value():
        raise BrokerMalformedResponse("trade with invalid currentUnits")
    price = _dec(item.get("price"))
    if price is None or price <= 0:
        raise BrokerMalformedResponse("trade with invalid price")
    ext = item.get("clientExtensions")
    ext_id = ext.get("id") if isinstance(ext, dict) and isinstance(ext.get("id"), str) else None
    return OpenTrade(trade_id, instrument, units, price, ext_id, _optional_price(item, "stopLossOrder"),
                     _optional_price(item, "takeProfitOrder"), parse_open_time(item.get("openTime")))


def _trades(payload):
    value = payload.get("trades") if isinstance(payload, dict) else None
    if not isinstance(value, list):
        raise BrokerMalformedResponse("answer without a 'trades' list")
    return [_parse_trade(item) for item in value]


def _quote(payload, instrument):
    """(tradeable, bid, ask) for ``instrument``; malformed -> BrokerMalformedResponse."""
    prices = payload.get("prices") if isinstance(payload, dict) else None
    if not isinstance(prices, list):
        raise BrokerMalformedResponse("pricing answer without a 'prices' list")
    item = next((p for p in prices if isinstance(p, dict) and p.get("instrument") == instrument), None)
    if item is None:
        raise BrokerMalformedResponse("pricing answer without the instrument")

    def best(key):
        levels = item.get(key)
        level = levels[0] if isinstance(levels, list) and levels and isinstance(levels[0], dict) else {}
        return _dec(level.get("price"))

    bid, ask = best("bids"), best("asks")
    if bid is None or ask is None or bid <= 0 or ask < bid:
        raise BrokerMalformedResponse("pricing answer with invalid bid/ask")
    tradeable = item.get("tradeable")
    if not isinstance(tradeable, bool):
        raise BrokerMalformedResponse("pricing answer without a boolean 'tradeable'")
    return tradeable, bid, ask


def _transaction_id(transaction):
    ref = transaction.get("id") if isinstance(transaction, dict) else None
    return ref if isinstance(ref, str) and ref else None


def _reject_code(transaction, *fields):
    """Reason code of a VALID cancel/reject transaction (dict with a non-empty string ``id`` and a non-empty string
    in one of ``fields``, tried in order), else None: an invalid one is no proof the request was refused."""
    if not _transaction_id(transaction):
        return None
    reason = next((transaction[f] for f in fields if isinstance(transaction.get(f), str) and transaction[f]), None)
    return reason_code(reason) if reason else None


def _not_found(error):
    return isinstance(error, BrokerRejected) and error.status == 404 and error.code in TRADE_NOT_FOUND_CODES


class OandaBroker:
    mode = DEMO_MODE
    provider = PROVIDER

    def __init__(self, *, env, mode, registry, allow_scale_in=False, transport=None, clock=None, sleep=None,
                 limiter=None, client_options=None):
        config = load_broker_config(env)
        token, account = check_oanda_guards(config, env, mode=mode, registry=registry)
        self._config = config
        self._registry = registry
        self._account_masked = mask_account(account)
        self._allow_scale_in = bool(allow_scale_in)
        self._client = OandaClient(token, account, transport=transport, clock=clock, sleep=sleep, limiter=limiter,
                                   pre_send_guard=self._pre_send_guard, **(client_options or {}))
        self._unsupported = ()
        self._lock = threading.RLock()
        self._guard_sends = False  # True only under the lock while submit_plan/modify_stop send (never close_market).
        self._unresolved = {}  # client extension id -> IN_FLIGHT / OUTCOME_UNKNOWN (in memory, this process only)

    def __repr__(self):
        return f"OandaBroker(mode={self.mode!r}, provider={self.provider!r}, account={self._account_masked!r})"

    __str__ = __repr__

    def _ensure_open_allowed(self):
        """Opening/modifying needs the switch ON and no emergency. Closing is always allowed (reduces risk)."""
        if self._config.emergency_stop:
            raise BrokerGuardError("emergency stop active")
        if self._registry.emergency_active() or not self._registry.is_allowed(DEMO_MODE):
            raise BrokerGuardError("broker_demo not allowed (switch OFF or emergency)")

    def _pre_send_guard(self):
        """Run by the client right before every transport attempt of a trading request (after the limiter wait and
        each 429 backoff), so a switch OFF/emergency raised while waiting stops the send."""
        if self._guard_sends:
            self._ensure_open_allowed()

    @contextmanager
    def _guarded_sends(self):
        self._guard_sends = True
        try:
            yield
        finally:
            self._guard_sends = False

    def unresolved_client_ids(self):
        """Client extension ids whose submission outcome is still UNKNOWN; they are never posted again."""
        with self._lock:
            return tuple(sorted(self._unresolved))

    # -- trading -------------------------------------------------------------------------------------------------
    def submit_plan(self, plan: OrderPlan, approval) -> SubmitResult:
        if not isinstance(plan, OrderPlan):
            raise TypeError("plan must be an OrderPlan")
        with self._lock:
            return self._submit_plan(plan, approval)

    def _submit_plan(self, plan, approval):
        cid = plan.client_id
        problem = approval_problem(plan, approval)
        if problem:
            return SubmitResult(REJECTED, cid, reason=problem)
        if plan.symbol not in ALLOWED_SYMBOLS:
            return SubmitResult(REJECTED, cid, reason="symbol_not_supported")
        if not plan.geometry_ok():
            return SubmitResult(REJECTED, cid, reason="invalid_plan_geometry")
        if plan.quantity != plan.quantity.to_integral_value():
            return SubmitResult(REJECTED, cid, reason="quantity_not_integer")
        entry, stop, target = (round_price(p) for p in (plan.planned_entry, plan.stop, plan.target))
        rounded_ok = stop < entry < target if plan.side == "LONG" else target < entry < stop
        if not rounded_ok:
            return SubmitResult(REJECTED, cid, reason="price_rounding_breaks_geometry")
        self._ensure_open_allowed()
        instrument = INSTRUMENTS[plan.symbol]
        ext_id = client_extension_id(cid)
        units = int(plan.quantity) * (1 if plan.side == "LONG" else -1)
        if ext_id in self._unresolved:
            return self._resolve_previous(cid, ext_id)

        try:
            gate = self._pre_submit_gate(cid, instrument, ext_id, plan.side)
        except (BrokerAuthError, BrokerRateLimited):
            raise
        except BrokerError as error:  # Nothing was sent: the outcome is a definite "not submitted".
            return SubmitResult(REJECTED, cid, reason=f"pre_submit_check_failed:{type(error).__name__}")
        if gate is not None:
            return gate if isinstance(gate, SubmitResult) else SubmitResult(REJECTED, cid, reason=gate)

        extensions = {"id": ext_id, "tag": CLIENT_TAG, "comment": _comment(plan.source_run_id)}
        order = OandaClient.build_market_order(instrument=instrument, units=units, stop=stop, target=target,
                                               client_extensions=extensions, trade_client_extensions=extensions)
        self._unresolved[ext_id] = IN_FLIGHT  # Before the POST: any unexpected exit keeps the id latched.
        try:
            with self._guarded_sends():  # Fresh switch/emergency check right before every POST attempt.
                payload = self._client.create_order(order)
        except BrokerGuardError:  # Refused before sending (or after a 429, which was not processed): nothing exists.
            del self._unresolved[ext_id]
            raise
        except BrokerRejected as error:
            result = SubmitResult(REJECTED, cid, reason=reason_code(error.code, f"broker_rejected:{error.status}"))
        except (BrokerAuthError, BrokerRateLimited):  # Definite answers: the order was not processed.
            del self._unresolved[ext_id]
            raise
        except BrokerUnavailable as error:  # Timeout/5xx/transport/unparseable: the order may exist. Never repost.
            result = self._reconcile_submit(cid, ext_id, type(error).__name__)
        else:
            result = self._map_submit(cid, payload)
        if result.status == UNKNOWN:
            self._unresolved[ext_id] = OUTCOME_UNKNOWN
        else:
            del self._unresolved[ext_id]
        return result

    def _resolve_previous(self, cid, ext_id):
        """A previous submission with this id is UNKNOWN: read-only lookup, never a POST."""
        try:
            trade_id = self._trade_by_client_id(ext_id)
        except (BrokerAuthError, BrokerRateLimited):
            raise
        except BrokerError as error:
            LOG.warning("oanda lookup of an unresolved submission failed: %s", type(error).__name__)
            trade_id = None
        if trade_id is None:
            return SubmitResult(UNKNOWN, cid, reason="previous_submission_unresolved")
        del self._unresolved[ext_id]
        return SubmitResult(ACCEPTED, cid, broker_ref=trade_id, reason="already_submitted")

    def _pre_submit_gate(self, cid, instrument, ext_id, side):
        """None when the order may be sent; a REJECTED reason or a ready SubmitResult otherwise. Reads only."""
        existing = self._trade_by_client_id(ext_id)
        if existing is not None:
            return SubmitResult(ACCEPTED, cid, broker_ref=existing, reason="already_submitted")
        trades = _trades(self._client.get_open_trades())
        for trade in trades:
            if trade.client_ext_id == ext_id:
                return SubmitResult(ACCEPTED, cid, broker_ref=trade.trade_id, reason="already_submitted")
        same = [t for t in trades if t.instrument == instrument]
        if any(t.side != side for t in same):
            return "would_hedge_or_net"
        if same and not self._allow_scale_in:
            return "position_already_open"
        pricing = self._client.get_pricing(instrument)
        try:
            tradeable, _bid, _ask = _quote(pricing, instrument)
        except BrokerMalformedResponse:
            return "pricing_malformed"
        if not tradeable:
            return "instrument_not_tradeable"
        return None

    def _trade_by_client_id(self, ext_id):
        """tradeID for ``@ext_id`` (any state), None when the broker says it does not exist; other errors raise."""
        try:
            payload = self._client.get_trade("@" + ext_id)
        except BrokerRejected as error:
            if _not_found(error):
                return None
            raise
        trade = payload.get("trade") if isinstance(payload, dict) else None
        trade_id = trade.get("id") if isinstance(trade, dict) else None
        if not isinstance(trade_id, str) or not _TRADE_ID_RE.match(trade_id):
            raise BrokerMalformedResponse("trade answer without a valid id")
        return trade_id

    def _reconcile_submit(self, cid, ext_id, error_name):
        """ONE read after an ambiguous POST. Found -> ACCEPTED. Not found is still UNKNOWN: OANDA may not have
        processed the order yet (or the read raced it), so absence cannot prove it was never sent; the caller must
        reconcile later through get_positions/reconcile and must never re-post."""
        try:
            trade_id = self._trade_by_client_id(ext_id)
        except BrokerError as error:
            LOG.warning("oanda reconciliation read failed after %s: %s", error_name, type(error).__name__)
            return SubmitResult(UNKNOWN, cid, reason=f"{error_name}:reconcile_failed")
        if trade_id is None:
            return SubmitResult(UNKNOWN, cid, reason=f"{error_name}:not_found_unproven")
        return SubmitResult(ACCEPTED, cid, broker_ref=trade_id, reason="reconciled_after_timeout")

    @staticmethod
    def _map_submit(cid, payload):
        if not isinstance(payload, dict):
            return SubmitResult(UNKNOWN, cid, reason="no_acceptance_evidence")
        if "orderFillTransaction" in payload:
            fill = payload["orderFillTransaction"]
            opened = fill.get("tradeOpened") if isinstance(fill, dict) else None
            trade_id = opened.get("tradeID") if isinstance(opened, dict) else None
            if _transaction_id(fill) and isinstance(trade_id, str) and _TRADE_ID_RE.match(trade_id):
                return SubmitResult(ACCEPTED, cid, broker_ref=trade_id)
            return SubmitResult(UNKNOWN, cid, reason="malformed_fill_evidence")
        for key, fields in (("orderCancelTransaction", ("reason",)),
                            ("orderRejectTransaction", ("rejectReason", "reason"))):
            if key in payload:
                code = _reject_code(payload[key], *fields)
                if code is None:
                    return SubmitResult(UNKNOWN, cid, reason="malformed_reject_evidence")
                return SubmitResult(REJECTED, cid, reason=code)
        if isinstance(payload.get("orderCreateTransaction"), dict):
            return SubmitResult(UNKNOWN, cid, reason="no_fill_evidence")
        return SubmitResult(UNKNOWN, cid, reason="no_acceptance_evidence")

    def close_market(self, position_ref, *, reason: str, client_id: str) -> CloseResult:
        """Close ALL units of trade ``position_ref``. Allowed with the switch OFF or emergency (reduces risk)."""
        if not isinstance(position_ref, str) or not _TRADE_ID_RE.match(position_ref):
            return CloseResult(REJECTED, client_id, reason="invalid_position_ref")
        with self._lock:
            return self._close_market(position_ref, client_id)

    def _close_market(self, position_ref, client_id):
        try:
            payload = self._client.close_trade(position_ref)
        except BrokerRejected as error:
            if error.status != 404:
                return CloseResult(REJECTED, client_id, reason=reason_code(error.code, f"broker_rejected:{error.status}"))
            return self._verify_absent(position_ref, client_id)
        except BrokerUnavailable as error:
            return CloseResult(UNKNOWN, client_id, reason=type(error).__name__)
        if not isinstance(payload, dict):
            return CloseResult(UNKNOWN, client_id, reason="no_acceptance_evidence")
        if "orderFillTransaction" in payload:
            fill = payload["orderFillTransaction"]
            closed = fill.get("tradesClosed") if isinstance(fill, dict) else None
            ref = _transaction_id(fill)
            if ref and isinstance(closed, list) and any(
                    isinstance(item, dict) and item.get("tradeID") == position_ref for item in closed):
                return CloseResult(ACCEPTED, client_id, broker_ref=ref)
            return CloseResult(UNKNOWN, client_id, reason="malformed_fill_evidence")
        if "orderCancelTransaction" in payload:
            code = _reject_code(payload["orderCancelTransaction"], "reason")
            if code is None:
                return CloseResult(UNKNOWN, client_id, reason="malformed_reject_evidence")
            return CloseResult(REJECTED, client_id, reason=code)
        return CloseResult(UNKNOWN, client_id, reason="no_acceptance_evidence")

    def _verify_absent(self, position_ref, client_id):
        try:
            trades = _trades(self._client.get_open_trades())
        except BrokerError as error:  # State unknown: never report "no_open_position" on a guess.
            return CloseResult(UNKNOWN, client_id, reason=f"position_state_unknown:{type(error).__name__}")
        if any(t.trade_id == position_ref for t in trades):
            return CloseResult(UNKNOWN, client_id, reason="trade_state_inconsistent")
        return CloseResult(REJECTED, client_id, reason="no_open_position")

    def modify_stop(self, position_ref, new_stop: Decimal, *, client_id: str) -> ModifyResult:
        if not isinstance(new_stop, Decimal) or not new_stop.is_finite() or new_stop <= 0:
            return ModifyResult(REJECTED, client_id, reason="invalid_stop")
        if not isinstance(position_ref, str) or not _TRADE_ID_RE.match(position_ref):
            return ModifyResult(REJECTED, client_id, reason="invalid_position_ref")
        with self._lock:
            return self._modify_stop(position_ref, new_stop, client_id)

    def _modify_stop(self, position_ref, new_stop, client_id):
        self._ensure_open_allowed()
        stop = round_price(new_stop)
        try:
            payload = self._client.get_trade(position_ref)
            trade = payload.get("trade") if isinstance(payload, dict) else None
            if not isinstance(trade, dict):
                raise BrokerMalformedResponse("trade answer without 'trade'")
            if trade.get("state") != "OPEN":
                return ModifyResult(REJECTED, client_id, reason="trade_not_open")
            parsed = _parse_trade(trade)
            if parsed.instrument not in INSTRUMENTS.values():
                return ModifyResult(REJECTED, client_id, reason="symbol_not_supported")
            _tradeable, bid, ask = _quote(self._client.get_pricing(parsed.instrument), parsed.instrument)
        except BrokerRejected as error:
            if error.status == 404:
                return ModifyResult(REJECTED, client_id, reason="no_open_position")
            return ModifyResult(REJECTED, client_id, reason=f"pre_modify_check_failed:{type(error).__name__}")
        except (BrokerAuthError, BrokerRateLimited):
            raise
        except BrokerError as error:  # Nothing was sent.
            return ModifyResult(REJECTED, client_id, reason=f"pre_modify_check_failed:{type(error).__name__}")
        long = parsed.side == "LONG"
        if (long and stop >= bid) or (not long and stop <= ask):
            return ModifyResult(REJECTED, client_id, reason="stop_wrong_side_of_market")
        old = parsed.stop
        if old is not None and ((long and stop < old) or (not long and stop > old)):
            return ModifyResult(REJECTED, client_id, reason="would_widen_stop")
        try:
            with self._guarded_sends():  # Fresh switch/emergency check right before every PUT attempt.
                payload = self._client.replace_stop_loss(position_ref, stop)
        except BrokerRejected as error:
            return ModifyResult(REJECTED, client_id, reason=reason_code(error.code, f"broker_rejected:{error.status}"))
        except BrokerUnavailable as error:
            return ModifyResult(UNKNOWN, client_id, reason=type(error).__name__)
        if isinstance(payload, dict):
            if "stopLossOrderTransaction" in payload:
                created = payload["stopLossOrderTransaction"]
                ref = _transaction_id(created)
                if ref and created.get("type") == "STOP_LOSS" and _dec(created.get("price")) == stop:
                    return ModifyResult(ACCEPTED, client_id, broker_ref=ref)
                return ModifyResult(UNKNOWN, client_id, reason="malformed_stop_evidence")
            if "stopLossOrderRejectTransaction" in payload:
                code = _reject_code(payload["stopLossOrderRejectTransaction"], "rejectReason", "reason")
                if code is None:
                    return ModifyResult(UNKNOWN, client_id, reason="malformed_reject_evidence")
                return ModifyResult(REJECTED, client_id, reason=code)
        return ModifyResult(UNKNOWN, client_id, reason="no_acceptance_evidence")

    # -- data ----------------------------------------------------------------------------------------------------
    def _open_trades(self):
        trades = _trades(self._client.get_open_trades())
        self._unsupported = tuple((t.trade_id, t.instrument) for t in trades if t.instrument not in INSTRUMENTS.values())
        return trades

    def open_trades(self):
        """Fresh read of every open trade as ``OpenTrade`` (read-only; failures raise, never [] on a guess)."""
        return tuple(self._open_trades())

    def get_quote(self, symbol="EURUSD"):
        """``(tradeable, bid, ask)`` for a supported symbol (read-only)."""
        if symbol not in ALLOWED_SYMBOLS:
            raise BrokerGuardError("symbol not supported")
        instrument = INSTRUMENTS[symbol]
        return _quote(self._client.get_pricing(instrument), instrument)

    def trade_id_for_client(self, client_id):
        """tradeID created with ``client_extension_id(client_id)`` (any state), None when the broker says it does not
        exist; read failures raise. Read-only."""
        return self._trade_by_client_id(client_extension_id(client_id))

    def get_positions(self):
        """Open trades (position_ref = tradeID). Instruments mappable to a project symbol (EURUSD/XAUUSD/NAS100)
        are returned; unmappable ones are excluded with a warning and listed by ``unsupported_open_trades()``.
        [] only for a valid empty collection; malformed answers/entries raise BrokerMalformedResponse."""
        positions = []
        for trade in self._open_trades():
            symbol = SYMBOL_FOR_INSTRUMENT.get(trade.instrument)
            if symbol is None:
                LOG.warning("oanda account %s has an open trade on an unmappable instrument (trade %s)",
                            self._account_masked, trade.trade_id)
                continue
            positions.append(BrokerPosition(trade.trade_id, symbol, trade.side, abs(trade.units), trade.price,
                                            trade.stop, trade.target))
        return positions

    def unsupported_open_trades(self):
        """(tradeID, instrument) of every open trade not on EUR_USD (fresh read; drift for reconciliation)."""
        self._open_trades()
        return list(self._unsupported)

    def reconcile(self, local_refs) -> ReconcileReport:
        """Compare broker open trades against locally known tradeIDs / client ids. Read-only; a failed read raises
        (never reports "all missing")."""
        trades = self._open_trades()
        keys = {}
        for trade in trades:
            keys[trade.trade_id] = trade.trade_id
            if trade.client_ext_id:
                keys[trade.client_ext_id] = trade.trade_id
        ok, missing, matched = [], [], set()
        for ref in local_refs:
            ref = str(ref)
            candidates = (ref,) if _TRADE_ID_RE.match(ref) else (ref, client_extension_id(ref))
            trade_id = next((keys[c] for c in candidates if c in keys), None)
            if trade_id is None:
                missing.append(ref)
            else:
                ok.append(ref)
                matched.add(trade_id)
        unknown = [t.trade_id for t in trades if t.trade_id not in matched]
        return ReconcileReport(tuple(missing), tuple(unknown), tuple(ok), self._unsupported)

    def get_balance(self) -> BrokerBalance:
        payload = self._client.get_summary()
        account = payload.get("account") if isinstance(payload, dict) else None
        if not isinstance(account, dict):
            raise BrokerMalformedResponse("summary answer without 'account'")
        balance, equity = _dec(account.get("balance")), _dec(account.get("NAV"))
        if balance is None or equity is None:
            raise BrokerUnavailable("summary answer without balance/NAV")
        currency = account.get("currency")
        last_id = payload.get("lastTransactionID", account.get("lastTransactionID"))
        return BrokerBalance(balance, equity, _dec(account.get("marginUsed")),
                             currency if isinstance(currency, str) else None,
                             last_id if isinstance(last_id, str) and _TRADE_ID_RE.match(last_id) else None)

    def transfers_since(self, tx_id, upto=None):
        """Read-only: ``(net TRANSFER_FUNDS amount after tx_id, last transaction id)``. With ``upto`` (a summary's
        ``lastTransactionID``) only transactions ``<= upto`` count and ``upto`` is returned, so the net matches that
        summary's NAV exactly. Malformed answers/entries raise BrokerMalformedResponse (never a guessed 0)."""
        payload = self._client.get_transactions_since(tx_id, types=("TRANSFER_FUNDS",))
        if not isinstance(payload, dict):
            raise BrokerMalformedResponse("transactions answer is not an object")
        items, last_id = payload.get("transactions"), payload.get("lastTransactionID") if upto is None else upto
        if not isinstance(items, list) or not isinstance(last_id, str) or not _TRADE_ID_RE.match(last_id):
            raise BrokerMalformedResponse("transactions answer without 'transactions'/'lastTransactionID'")
        net = Decimal("0")
        for item in items:
            if not isinstance(item, dict):
                raise BrokerMalformedResponse("transaction entry is not an object")
            if item.get("type") != "TRANSFER_FUNDS":
                continue
            item_id, amount = item.get("id"), _dec(item.get("amount"))
            if not isinstance(item_id, str) or not _TRADE_ID_RE.match(item_id) or amount is None:
                raise BrokerMalformedResponse("TRANSFER_FUNDS transaction with invalid id/amount")
            if int(item_id) <= int(last_id):
                net += amount
        return net, last_id

    def health(self) -> BrokerHealth:
        client = self._client
        return BrokerHealth(DEMO_MODE, self._account_masked, True, connected=False, last_ok_at=client.last_ok_at,
                            last_error=client.last_error)
