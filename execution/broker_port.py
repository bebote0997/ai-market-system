"""Broker port (SKELETON, not wired): the minimal contract every broker adapter implements.

Nothing in runtime/, floor/, ui/ or the ejecutar_* scripts imports this module. Prices and quantities are Decimal.
Result states are explicit: ACCEPTED | REJECTED | UNKNOWN. UNKNOWN (timeout / ambiguous answer) means "reconcile
before doing anything else"; it is never retried blindly. ``submit_plan`` always takes the structured
``RiskApproval`` that authorises the plan; adapters refuse (REJECTED, no side effect) unless it matches exactly.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Protocol, runtime_checkable


ACCEPTED, REJECTED, UNKNOWN = "ACCEPTED", "REJECTED", "UNKNOWN"
APPROVED = "APPROVED"
RESULT_STATUSES = (ACCEPTED, REJECTED, UNKNOWN)
SIDES = ("LONG", "SHORT")
MODES = ("paper", "broker_demo", "real")


class BrokerError(Exception):
    """Base of every typed broker error. Messages never carry secrets (callers scrub them)."""


class BrokerAuthError(BrokerError):
    """Login refused or session rejected again after one re-login."""


class BrokerRateLimited(BrokerError):
    """HTTP 429 persisted after the bounded backoff."""


class BrokerApiNotPermitted(BrokerError):
    """HTTP 404 with broker code 2: the API is not enabled for this user. Never retried."""


class BrokerPreconditionFailed(BrokerError):
    """HTTP 412: the If-Match version is stale (after one re-read and retry)."""


class BrokerUnavailable(BrokerError):
    """Transport failure, 5xx or unparseable answer."""


class BrokerTimeout(BrokerUnavailable):
    """The request may or may not have reached the broker: trading calls map it to UNKNOWN."""


class BrokerRejected(BrokerError):
    """The broker answered with a business rejection (4xx other than 401/404-code-2/412/429)."""

    def __init__(self, message, *, status=None, code=None):
        super().__init__(message)
        self.status = status
        self.code = code


class BrokerMalformedResponse(BrokerError):
    """A successful HTTP answer whose shape cannot be trusted (missing/wrong collection, unknown side, bad quantity).

    The broker state is UNKNOWN: callers must not read it as "empty" or "no position"."""


class BrokerGuardError(BrokerError):
    """A local safety guard refused the operation (fail closed). Raised before any network call."""


def _decimal(name, value, *, positive=True, optional=False):
    if value is None and optional:
        return
    if not isinstance(value, Decimal) or not value.is_finite() or (positive and value <= 0):
        raise ValueError(f"{name} must be a finite{' positive' if positive else ''} Decimal")


@dataclass(frozen=True)
class OrderPlan:
    symbol: str
    side: str
    quantity: Decimal
    planned_entry: Decimal
    stop: Decimal
    target: Decimal
    client_id: str
    source_run_id: str
    risk_policy_version: Optional[str] = None

    def __post_init__(self):
        if not isinstance(self.symbol, str) or not self.symbol:
            raise ValueError("symbol is required")
        if self.side not in SIDES:
            raise ValueError("side must be LONG or SHORT")
        for name in ("quantity", "planned_entry", "stop", "target"):
            _decimal(name, getattr(self, name))
        if not isinstance(self.client_id, str) or not self.client_id:
            raise ValueError("client_id is required")
        if not isinstance(self.source_run_id, str) or not self.source_run_id:
            raise ValueError("source_run_id is required")

    def geometry_ok(self):
        """SL and TP on the correct sides of the planned entry."""
        if self.side == "LONG":
            return self.stop < self.planned_entry < self.target
        return self.target < self.planned_entry < self.stop


@dataclass(frozen=True)
class RiskApproval:
    """Structured Risk Engine decision that authorises exactly one OrderPlan (common to every adapter)."""
    status: str
    run_id: str
    symbol: str
    side: str
    quantity: Decimal
    entry: Decimal
    stop: Decimal
    target: Decimal

    def __post_init__(self):
        if not isinstance(self.status, str) or not isinstance(self.run_id, str) or not isinstance(self.symbol, str):
            raise ValueError("status, run_id and symbol must be strings")
        if self.side not in SIDES:
            raise ValueError("side must be LONG or SHORT")
        for name in ("quantity", "entry", "stop", "target"):
            _decimal(name, getattr(self, name))

    @classmethod
    def from_decision(cls, decision, *, run_id):
        """Build from a ``core.contracts.RiskDecision``-like object (floats converted through ``str``)."""
        values = {}
        for name in ("quantity", "entry", "stop", "target"):
            raw = getattr(decision, name, None)
            values[name] = raw if isinstance(raw, Decimal) else Decimal(str(raw)) if raw is not None else None
        return cls(getattr(decision, "status", None), run_id, getattr(decision, "symbol", None),
                   getattr(decision, "side", None), **values)


def approval_problem(plan, approval):
    """None when ``approval`` is an APPROVED RiskApproval for exactly this plan; otherwise a REJECTED reason."""
    if not isinstance(approval, RiskApproval):
        return "risk_approval_missing"
    if approval.status != APPROVED:
        return "risk_not_approved"
    approved = (approval.run_id, approval.symbol, approval.side, approval.quantity, approval.entry, approval.stop,
                approval.target)
    planned = (plan.source_run_id, plan.symbol, plan.side, plan.quantity, plan.planned_entry, plan.stop, plan.target)
    return None if approved == planned else "risk_approval_mismatch"


@dataclass(frozen=True)
class _Result:
    status: str
    client_id: str
    broker_ref: Optional[str] = None
    reason: Optional[str] = None

    def __post_init__(self):
        if self.status not in RESULT_STATUSES:
            raise ValueError(f"status must be one of {RESULT_STATUSES}")


@dataclass(frozen=True)
class SubmitResult(_Result):
    pass


@dataclass(frozen=True)
class CloseResult(_Result):
    pass


@dataclass(frozen=True)
class ModifyResult(_Result):
    pass


@dataclass(frozen=True)
class BrokerPosition:
    position_ref: str
    symbol: str
    side: str
    quantity: Decimal
    entry_price: Optional[Decimal] = None
    stop: Optional[Decimal] = None
    target: Optional[Decimal] = None

    def __post_init__(self):
        if self.side not in SIDES:
            raise ValueError("side must be LONG or SHORT")
        _decimal("quantity", self.quantity)
        for name in ("entry_price", "stop", "target"):
            _decimal(name, getattr(self, name), optional=True)


@dataclass(frozen=True)
class BrokerBalance:
    balance: Decimal
    equity: Decimal
    margin_used: Optional[Decimal] = None
    currency: Optional[str] = None
    last_transaction_id: Optional[str] = None  # OANDA summary ``lastTransactionID`` (snapshot marker), when known

    def __post_init__(self):
        _decimal("balance", self.balance, positive=False)
        _decimal("equity", self.equity, positive=False)
        _decimal("margin_used", self.margin_used, positive=False, optional=True)


@dataclass(frozen=True)
class BrokerHealth:
    mode: str
    account_masked: str
    session_active: bool
    connected: bool = False
    last_ok_at: Optional[float] = None
    last_error: Optional[str] = None

    def __post_init__(self):
        if self.mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")


@runtime_checkable
class BrokerPort(Protocol):
    def submit_plan(self, plan: OrderPlan, approval: RiskApproval) -> SubmitResult: ...

    def close_market(self, position_ref, *, reason: str, client_id: str) -> CloseResult: ...

    def modify_stop(self, position_ref, new_stop: Decimal, *, client_id: str) -> ModifyResult: ...

    def get_positions(self) -> list: ...

    def get_balance(self) -> BrokerBalance: ...

    def health(self) -> BrokerHealth: ...
