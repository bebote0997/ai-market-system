"""V2 Phase 5 (DEC-5.4): atomic risk reservation for explicit V2 fixed-3R PAPER submissions.

The reservation IS the existing PENDING ``paper_orders`` row; there is no second reservation store and no schema
change. One call:

  1 loads the durable PAPER state (fresh connection state, never a cached copy) and captures the B2.3A
    whole-state comparison value;
  2 if an order for ``run_id`` already exists, returns it (retry/restart idempotency: no second reservation, no
    second order);
  3 evaluates the Phase 5 Risk Engine against that state (open risk + pending reserved risk + proposed reservation);
  4 on APPROVED, builds the PENDING order with the unchanged ``PaperBroker.submit_plan`` (fixed-3R fill policy) and
    persists it with ONE ``save_paper(expected_state=...)`` under BEGIN IMMEDIATE together with the RISK_V2_DECISION
    journal row. If any other writer (any process, any symbol) changed PAPER state after step 1, nothing is written
    and the whole evaluation is redone once from fresh durable state; a second conflict fails closed (STALE).

Consequences: two concurrent submissions can never both be admitted on stale state (the loser re-evaluates and sees
the winner's reservation); a crash before the commit leaves no reservation; a crash after it leaves exactly one,
which a retry with the same ``run_id`` returns. Pending -> fill replaces the order's reservation (8/7 x planned) by
the position's open risk (actual fill to SL, <= 8/7 x planned by DEC-5.7) in the same broker save, so there is no
gap and no double count. Not wired into the frozen V1 runtime.
"""
from dataclasses import dataclass, replace

from core.risk_policy import RISK_POLICY_V2_P5, stamps_registered_semantics
from core.rr_contract import POLICY_V2_F3
from execution.paper_broker import PaperBroker
from execution.risk_engine_v2 import evaluate

EVENT = "RISK_V2_DECISION"
SOURCE = "risk_engine_v2"


@dataclass(frozen=True)
class ReservationResult:
    status: str  # RESERVED | DUPLICATE_RUN | REJECTED | STALE | NOT_SUBMITTED
    reason: str
    order: object  # PaperOrder or None
    record: dict  # Phase 5 traceability record (None for DUPLICATE_RUN)
    attempts: int


def reserve_and_submit(store, floor_report, instrument, *, account_id, as_of, setup_id=None, owner_key=None,
                       policy=RISK_POLICY_V2_P5):
    """Risk-check and reserve one fixed-3R plan atomically against durable PAPER state (see module docstring)."""
    plan = getattr(floor_report, "trade_plan", None)
    if not stamps_registered_semantics(policy):
        return ReservationResult("NOT_SUBMITTED", "RISK_POLICY_NOT_REGISTERED", None, None, 0)
    if plan is None or getattr(plan, "policy_version", None) != POLICY_V2_F3 or floor_report.final_status != "PLAN_READY":
        return ReservationResult("NOT_SUBMITTED", "not_a_fixed_3r_plan_ready_report", None, None, 0)
    for attempt in (1, 2):
        account, orders, fills = store.load_paper(account_id)
        existing = next((o for o in orders.values() if o.run_id == floor_report.run_id), None)
        if existing is not None:
            return ReservationResult("DUPLICATE_RUN", "run_id_already_submitted", existing, None, attempt)
        expected_state = store.paper_state(account, orders, fills)
        result = evaluate(plan, account, orders, instrument, policy, setup_id=setup_id, at=as_of)
        record = result.record
        if result.decision.status != "APPROVED":
            store.event(as_of, floor_report.run_id, plan.symbol, SOURCE, EVENT, "INFO", record)
            return ReservationResult("REJECTED", result.decision.reason, None, record, attempt)
        broker = PaperBroker(account, instrument, rr_policy=POLICY_V2_F3)
        broker.orders, broker.fills = orders, fills
        order = broker.submit_plan(replace(floor_report, risk_decision=result.decision), None, as_of)
        if order is not None:
            # P5.1C: durable policy identity, persisted in the order payload in the same guarded save. It is what
            # lets any later evaluation (after restart) prove this order's worst permitted fill (8/7).
            order.risk_policy_version = policy.version
        if order is None:
            store.event(as_of, floor_report.run_id, plan.symbol, SOURCE, EVENT, "INFO",
                        {**record, "risk_status": "REJECTED", "risk_reason": "broker_refused_submission"})
            return ReservationResult("REJECTED", "broker_refused_submission", None, record, attempt)
        record = {**record, "order_id": order.order_id}
        broker._event(as_of, order.run_id, order.symbol, order.order_id, EVENT, record)
        if store.save_paper(broker, owner_key=owner_key, symbol=plan.symbol, expected_state=expected_state) is not False:
            return ReservationResult("RESERVED", "approved", order, record, attempt)
    return ReservationResult("STALE", "STALE_PAPER_STATE", None, record, 2)


__all__ = ["EVENT", "ReservationResult", "reserve_and_submit"]
