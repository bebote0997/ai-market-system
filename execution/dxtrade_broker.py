"""DXtrade demo adapter (SKELETON, NOT wired: nothing in runtime/, floor/, ui/ or ejecutar_* imports it).

The constructor fails closed (``BrokerGuardError``) BEFORE any client is built, so a refused construction never
touches the network. Required: ``AI_FLOOR_BROKER_DEMO_ENABLED=1`` in the injected env mapping, ``mode ==
'broker_demo'``, an account code exactly equal to ``DXTRADE_ACCOUNT_CODE`` starting with ``DEM_`` (``ECN_``/``VIP_``/
``NOC_`` refused), ``REAL_EXECUTION_ENABLED`` still False, no emergency stop (env or registry) and the ``broker_demo``
switch ON in the account registry. ``AI_FLOOR_BROKER_REAL_ENABLED`` is ignored.

``submit_plan`` refuses (REJECTED, before any network access) unless it gets an APPROVED ``RiskApproval`` matching the
plan exactly and the symbol is in ``ALLOWED_SYMBOLS`` (EURUSD/XAUUSD; NAS100 and anything else refused). A 2xx answer
without explicit acceptance evidence (an ``orderId``) is UNKNOWN, never ACCEPTED. Malformed position answers raise
``BrokerMalformedResponse``; ``close_market`` maps an unknown position state to UNKNOWN, never ``no_open_position``.

Not implemented on purpose (later phases): shadow mode, write-ahead intents, reconciliation, sizing, Day-1 mapping.
"""
from decimal import Decimal

from execution.broker_config import base_url_problem, load_broker_config
from execution.broker_port import (ACCEPTED, REJECTED, UNKNOWN, BrokerBalance, BrokerError, BrokerGuardError,
                                   BrokerHealth, BrokerMalformedResponse, BrokerPosition, BrokerPreconditionFailed,
                                   BrokerRejected, BrokerUnavailable, CloseResult, ModifyResult, OrderPlan,
                                   SubmitResult, approval_problem)
from execution.dxtrade_client import DxtradeClient


DEMO_MODE = "broker_demo"
ALLOWED_PREFIX = "DEM_"
BLOCKED_PREFIXES = ("ECN_", "VIP_", "NOC_")
ALLOWED_SYMBOLS = ("EURUSD", "XAUUSD")
_POSITION_SIDES = {"BUY": "LONG", "SELL": "SHORT", "LONG": "LONG", "SHORT": "SHORT"}
_SIDE = {"LONG": "BUY", "SHORT": "SELL"}
_OPPOSITE = {"LONG": "SELL", "SHORT": "BUY"}


def real_execution_enabled():
    from runtime.demo_runner import REAL_EXECUTION_ENABLED  # Checked only; never written.
    return REAL_EXECUTION_ENABLED


def check_demo_guards(config, *, mode, account_code, registry):
    """Raise BrokerGuardError unless every demo-only guard holds. No network, no side effects."""
    if real_execution_enabled() is not False:
        raise BrokerGuardError("REAL_EXECUTION_ENABLED must stay False")
    if not config.demo_enabled:
        raise BrokerGuardError("AI_FLOOR_BROKER_DEMO_ENABLED is not 1")
    if mode != DEMO_MODE:
        raise BrokerGuardError("only mode 'broker_demo' is accepted")
    code = account_code if isinstance(account_code, str) else ""
    if any(code.upper().startswith(prefix) for prefix in BLOCKED_PREFIXES):
        raise BrokerGuardError("live account prefixes are refused")
    if not code.startswith(ALLOWED_PREFIX):
        raise BrokerGuardError("account code must start with DEM_")
    if not config.account_code or code != config.account_code:
        raise BrokerGuardError("account code differs from DXTRADE_ACCOUNT_CODE")
    if config.emergency_stop:
        raise BrokerGuardError("emergency stop active (AI_FLOOR_EMERGENCY_STOP)")
    if registry is None:
        raise BrokerGuardError("account registry required")
    if registry.emergency_active():
        raise BrokerGuardError("emergency stop active (registry)")
    if not registry.is_allowed(DEMO_MODE):
        raise BrokerGuardError("broker_demo switch is OFF")
    if not (config.username and config.password and config.domain):
        raise BrokerGuardError("DXtrade credentials incomplete")
    problem = base_url_problem(config.base_url)
    if problem:
        raise BrokerGuardError(f"DXTRADE_BASE_URL refused: {problem}")


def _items(payload, key):
    """The ``key`` collection (or a bare list). Missing or wrongly shaped -> BrokerMalformedResponse, never []."""
    value = payload.get(key) if isinstance(payload, dict) else payload
    if not isinstance(value, list):
        raise BrokerMalformedResponse(f"answer without a '{key}' list")
    return value


def _acceptance_ref(payload):
    """Explicit acceptance evidence: a non-empty ``orderId``. Anything else ({} / [] / unknown shape) -> None."""
    ref = payload.get("orderId") if isinstance(payload, dict) else None
    if ref is None or isinstance(ref, bool) or not isinstance(ref, (str, int)):
        return None
    ref = str(ref).strip()
    return ref or None


def _parse_position(item):
    if not isinstance(item, dict):
        raise BrokerMalformedResponse("position entry is not an object")
    side = _POSITION_SIDES.get(str(item.get("side")).upper())
    if side is None:
        raise BrokerMalformedResponse("position with unknown side")
    quantity = _dec(item.get("quantity"))
    if quantity is None or quantity == 0:
        raise BrokerMalformedResponse("position with invalid quantity")
    ref, symbol = item.get("positionCode"), item.get("symbol", item.get("instrument"))
    if not isinstance(ref, (str, int)) or isinstance(ref, bool) or not str(ref).strip():
        raise BrokerMalformedResponse("position without positionCode")
    if not isinstance(symbol, str) or not symbol:
        raise BrokerMalformedResponse("position without symbol")
    entry = _dec(item.get("openPrice"))
    if item.get("openPrice") is not None and (entry is None or entry <= 0):
        raise BrokerMalformedResponse("position with invalid openPrice")
    return BrokerPosition(str(ref), symbol, side, abs(quantity), entry)


def _dec(value):
    if value is None:
        return None
    try:
        out = Decimal(str(value))
    except ArithmeticError:
        return None
    return out if out.is_finite() else None


class DxtradeBroker:
    mode = DEMO_MODE

    def __init__(self, *, env, mode, account_code, registry, symbol_map=None, transport=None, clock=None,
                 sleep=None, client_options=None):
        config = load_broker_config(env)
        check_demo_guards(config, mode=mode, account_code=account_code, registry=registry)
        self._config = config
        self._account = account_code
        self._registry = registry
        self._symbols = dict(symbol_map or {})
        self._client = DxtradeClient(config.base_url, config.username, config.password, config.domain,
                                     transport=transport, clock=clock, sleep=sleep, **(client_options or {}))
        self._client.register_account(account_code)

    def __repr__(self):
        return f"DxtradeBroker(mode={self.mode!r}, account={self._config.account_masked!r})"

    __str__ = __repr__

    def _ensure_open_allowed(self):
        """Opening/modifying needs the switch ON and no emergency. Closing is always allowed (reduces risk)."""
        if self._config.emergency_stop:
            raise BrokerGuardError("emergency stop active")
        if self._registry.emergency_active() or not self._registry.is_allowed(DEMO_MODE):
            raise BrokerGuardError("broker_demo not allowed (switch OFF or emergency)")

    # -- trading -------------------------------------------------------------------------------------------------
    def submit_plan(self, plan: OrderPlan, approval) -> SubmitResult:
        if not isinstance(plan, OrderPlan):
            raise TypeError("plan must be an OrderPlan")
        problem = approval_problem(plan, approval)
        if problem:
            return SubmitResult(REJECTED, plan.client_id, reason=problem)
        if plan.symbol not in ALLOWED_SYMBOLS:
            return SubmitResult(REJECTED, plan.client_id, reason="symbol_not_allowed")
        self._ensure_open_allowed()
        instrument = self._symbols.get(plan.symbol)
        if instrument is None:
            return SubmitResult(REJECTED, plan.client_id, reason="symbol_not_mapped")
        if not plan.geometry_ok():
            return SubmitResult(REJECTED, plan.client_id, reason="invalid_plan_geometry")
        common = {"instrument": instrument, "quantity": plan.quantity}
        orders = [
            DxtradeClient.build_order("MARKET", order_code=plan.client_id, side=_SIDE[plan.side],
                                      position_effect="OPEN", **common),
            DxtradeClient.build_order("STOP", order_code=f"{plan.client_id}-SL", side=_OPPOSITE[plan.side],
                                      position_effect="CLOSE", price=plan.stop, **common),
            DxtradeClient.build_order("LIMIT", order_code=f"{plan.client_id}-TP", side=_OPPOSITE[plan.side],
                                      position_effect="CLOSE", price=plan.target, **common),
        ]
        return self._trade(SubmitResult, plan.client_id,
                           lambda: self._client.place_group(self._account, orders, "IF_THEN"))

    def close_market(self, position_ref, *, reason: str, client_id: str) -> CloseResult:
        try:
            positions = self.get_positions()
        except BrokerError as error:  # State unknown: never report "no_open_position" on a guess.
            return CloseResult(UNKNOWN, client_id, reason=f"position_state_unknown:{type(error).__name__}")
        position = next((p for p in positions if p.position_ref == position_ref), None)
        if position is None:
            return CloseResult(REJECTED, client_id, reason="no_open_position")
        instrument = self._symbols.get(position.symbol, position.symbol)
        order = DxtradeClient.build_order("MARKET", order_code=client_id, instrument=instrument,
                                          side=_OPPOSITE[position.side], quantity=position.quantity,
                                          position_effect="CLOSE", position_code=position_ref)
        return self._trade(CloseResult, client_id, lambda: self._client.place_order(self._account, order))

    def modify_stop(self, position_ref, new_stop: Decimal, *, client_id: str) -> ModifyResult:
        if not isinstance(new_stop, Decimal) or not new_stop.is_finite() or new_stop <= 0:
            return ModifyResult(REJECTED, client_id, reason="invalid_stop")
        self._ensure_open_allowed()
        for attempt in range(2):  # 412: re-read the order version once and retry once.
            order = self._stop_order(position_ref)
            if order is None:
                return ModifyResult(REJECTED, client_id, reason="stop_order_not_found")
            version = order.get("version")
            if version is None:
                return ModifyResult(REJECTED, client_id, reason="stop_order_version_unknown")
            body = {key: value for key, value in order.items() if key != "version"}
            body["stopPrice"] = str(new_stop)
            try:
                return self._trade(ModifyResult, client_id,
                                   lambda: self._client.modify_order(self._account, body, version))
            except BrokerPreconditionFailed:
                if attempt == 1:
                    raise

    def _stop_order(self, position_ref):
        for order in _items(self._client.get_orders(self._account), "orders"):
            if (isinstance(order, dict) and order.get("type") == "STOP" and order.get("positionEffect") == "CLOSE"
                    and order.get("positionCode") == position_ref):
                return dict(order)
        return None

    def _trade(self, result_type, client_id, send):
        try:
            payload = send()
        except BrokerRejected as error:
            return result_type(REJECTED, client_id, reason=f"broker_rejected:{error.status}")
        except BrokerUnavailable as error:  # Timeout, transport failure, 5xx, unparseable: outcome unknown.
            return result_type(UNKNOWN, client_id, reason=type(error).__name__)
        ref = _acceptance_ref(payload)
        if ref is None:  # 2xx without explicit acceptance: the order may or may not exist.
            return result_type(UNKNOWN, client_id, reason="no_acceptance_evidence")
        return result_type(ACCEPTED, client_id, broker_ref=ref)

    # -- data ----------------------------------------------------------------------------------------------------
    def get_positions(self):
        """[] only for a valid empty collection; any malformed answer or entry raises BrokerMalformedResponse."""
        return [_parse_position(item) for item in _items(self._client.get_positions(self._account), "positions")]

    def get_balance(self) -> BrokerBalance:
        payload = self._client.get_metrics(self._account)
        metrics = payload.get("metrics") if isinstance(payload, dict) else None
        item = metrics[0] if isinstance(metrics, list) and metrics else payload
        if not isinstance(item, dict):
            raise BrokerMalformedResponse("balance answer is not an object")
        balance, equity = _dec(item.get("balance")), _dec(item.get("equity"))
        if balance is None or equity is None:
            raise BrokerUnavailable("balance answer without balance/equity")
        return BrokerBalance(balance, equity, _dec(item.get("margin")), item.get("currency"))

    def health(self) -> BrokerHealth:
        client = self._client
        return BrokerHealth(DEMO_MODE, self._config.account_masked, client.session_active, connected=False,
                            last_ok_at=client.last_ok_at, last_error=client.last_error)
