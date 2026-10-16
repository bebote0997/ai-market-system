"""Thin BrokerPort wrapper over the existing PaperBroker (SKELETON, not wired; nothing else imports it).

PaperBroker is not modified and its behaviour is not changed: every call delegates to it. Paper-specific context
(the Risk-approved floor report, the clock, the exit price) comes from callables injected in the constructor, so the
port methods take only BrokerPort arguments. The adapter never builds a Risk decision: ``submit_plan`` requires the
structured RiskApproval AND the provider's floor report, checks that both describe the same order, and lets
``PaperBroker.submit_plan`` apply its own APPROVED/PLAN_READY gates.
"""
from decimal import Decimal, InvalidOperation

from execution.broker_port import (ACCEPTED, REJECTED, BrokerBalance, BrokerHealth, BrokerPosition, CloseResult,
                                   ModifyResult, OrderPlan, SubmitResult, approval_problem)


def _dec(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _matches(plan, floor_report):
    decision = getattr(floor_report, "risk_decision", None)
    if decision is None:
        return False
    return (plan.source_run_id == getattr(floor_report, "run_id", None)
            and plan.symbol == getattr(floor_report, "symbol", None) == getattr(decision, "symbol", None)
            and plan.side == getattr(decision, "side", None)
            and plan.quantity == _dec(getattr(decision, "quantity", None))
            and plan.planned_entry == _dec(getattr(decision, "entry", None))
            and plan.stop == _dec(getattr(decision, "stop", None))
            and plan.target == _dec(getattr(decision, "target", None)))


class PaperBrokerAdapter:
    """``floor_report_for(plan) -> report | None``; ``clock() -> datetime``; ``price_for(symbol) -> price | None``;
    optional ``market_context() -> dict`` (default ``{}``)."""

    mode = "paper"

    def __init__(self, broker, *, floor_report_for, clock, price_for, market_context=None):
        if not all(callable(f) for f in (floor_report_for, clock, price_for)):
            raise TypeError("floor_report_for, clock and price_for must be callables")
        self._broker = broker
        self._floor_report_for, self._clock, self._price_for = floor_report_for, clock, price_for
        self._market_context = market_context or dict

    def submit_plan(self, plan: OrderPlan, approval) -> SubmitResult:
        if not isinstance(plan, OrderPlan):
            raise TypeError("plan must be an OrderPlan")
        problem = approval_problem(plan, approval)
        if problem:
            return SubmitResult(REJECTED, plan.client_id, reason=problem)
        floor_report = self._floor_report_for(plan)
        if floor_report is None:
            return SubmitResult(REJECTED, plan.client_id, reason="paper_floor_report_unavailable")
        if not _matches(plan, floor_report):
            return SubmitResult(REJECTED, plan.client_id, reason="plan_does_not_match_risk_decision")
        order = self._broker.submit_plan(floor_report, self._market_context() or {}, self._clock())
        if order is None:
            return SubmitResult(REJECTED, plan.client_id, reason="paper_broker_refused")
        return SubmitResult(ACCEPTED, plan.client_id, broker_ref=order.order_id)

    def close_market(self, position_ref, *, reason: str, client_id: str) -> CloseResult:
        position = self._broker.account.open_positions.get(position_ref)
        if position is None:
            return CloseResult(REJECTED, client_id, reason="no_open_position")
        price = _dec(self._price_for(position.symbol))
        if price is None or not price.is_finite() or price <= 0:
            return CloseResult(REJECTED, client_id, reason="paper_price_unavailable")
        try:
            self._broker.close_market(position_ref, float(price), self._clock(), exit_reason=reason,
                                      decision_run_id=client_id)
        except ValueError:
            return CloseResult(REJECTED, client_id, reason="invalid_paper_exit")
        return CloseResult(ACCEPTED, client_id, broker_ref=position.position_id)

    def modify_stop(self, position_ref, new_stop: Decimal, *, client_id: str) -> ModifyResult:
        # PAPER stop moves stay in the runtime trade-duration path; the adapter does not re-implement them.
        return ModifyResult(REJECTED, client_id, reason="not_supported_by_paper_broker")

    def get_positions(self):
        out = []
        for symbol, position in self._broker.account.open_positions.items():
            if getattr(position, "status", "OPEN") != "OPEN":
                continue
            out.append(BrokerPosition(symbol, position.symbol, position.side, _dec(position.quantity),
                                      _dec(position.entry_price), _dec(position.stop), _dec(position.target)))
        return out

    def get_balance(self) -> BrokerBalance:
        account = self._broker.account
        return BrokerBalance(_dec(account.cash), _dec(account.equity), None, None)

    def health(self) -> BrokerHealth:
        return BrokerHealth("paper", str(getattr(self._broker.account, "account_id", "paper")), True, connected=False)
