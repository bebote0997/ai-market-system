"""V2 Phase 6 composable conflict + Risk V2 + reservation path (DEC-6.9, DEC-6.11 A: library only, not wired).

One call, on the durable trading DB (no other database; no cross-DB atomicity):

  1 validate the candidate: PLAN_READY fixed-3R plan of a VALID_SETUP of the same symbol/side/run; its setup_id is the
    certified Phase 3 ``setup_identity`` of that assessment (and must equal the explanation's id when present).
  2 load durable PAPER state; capture the B2.3A whole-state comparison value.
  3 idempotency: an order for this run_id, or a decisive CONFLICT_DECISION row for it -> DUPLICATE_RUN, no write.
  4 conflict = ``conflict_engine.classify`` on that state.
  5 BLOCK  -> persist ONE CONFLICT_DECISION row, in a BEGIN IMMEDIATE transaction that first re-verifies the whole
             PAPER state is unchanged; no order is manufactured.
    ALLOW  -> certified Phase 5 ``prepare_reservation`` (Risk V2 unchanged: 1%, notional, conservative equity, 2.30%,
             pending 8/7, drawdown, policy identity, unknown-pending fail-closed, SYMBOL_EXPOSURE_LIMIT);
             REJECTED -> CONFLICT_DECISION + RISK_V2_DECISION rows in one state-verified transaction;
             APPROVED -> order (stamped with setup_id and risk policy) + RISK_V2_DECISION + CONFLICT_DECISION rows in
             ONE ``save_paper(expected_state)``. A recorded "RESERVED" therefore exists only together with its order.
  6 any state change between 2 and 5 (other symbol, a fill, a close, a competing reservation) -> nothing written;
    reload and recompute conflict AND Risk once; a second conflict -> STALE (an informative, non-economic row).
It never closes, cancels, replaces, modifies or re-stamps existing orders/positions and never moves SL/TP.
"""
from dataclasses import dataclass
from decimal import Decimal
import json

from agents.setup_validator import setup_identity
from core.risk_policy import RISK_POLICY_V2_P5, stamps_registered_semantics
from core.rr_contract import POLICY_V2_F3
from execution.conflict_engine import CONFLICT_POLICY_VERSION, classify
from execution.risk_reservation import EVENT as RISK_EVENT, SOURCE as RISK_SOURCE, prepare_reservation

EVENT = "CONFLICT_DECISION"
SOURCE = "conflict_engine"
DECISIVE = ("BLOCKED", "RISK_REJECTED", "RESERVED")


@dataclass(frozen=True)
class ConflictPathResult:
    status: str  # RESERVED | BLOCKED | RISK_REJECTED | DUPLICATE_RUN | STALE | NOT_SUBMITTED
    reason: str  # conflict reason code, Risk V2 reason, or path reason
    conflict: object  # ConflictDecision or None
    order: object
    record: dict  # the CONFLICT_DECISION payload (None when nothing was evaluated)
    attempts: int


def candidate(floor_report):
    """(setup_id, None) for a valid Phase 6 candidate, else (None, reason)."""
    plan = getattr(floor_report, "trade_plan", None)
    setup = getattr(floor_report, "setup_assessment", None)
    if floor_report is None or floor_report.final_status != "PLAN_READY" or plan is None or setup is None:
        return None, "not_a_plan_ready_report"
    if getattr(plan, "policy_version", None) != POLICY_V2_F3:
        return None, "not_a_fixed_3r_plan"
    if setup.status != "VALID_SETUP" or setup.symbol != plan.symbol or setup.symbol != floor_report.symbol \
            or setup.side != plan.side or plan.run_id != floor_report.run_id:
        return None, "candidate_lineage_invalid"
    setup_id, _ = setup_identity(setup)
    explained = (getattr(setup, "explanation", None) or {}).get("setup_id")
    if setup_id is None or (explained is not None and explained != setup_id):
        return None, "setup_identity_unavailable"
    return setup_id, None


def _decimal_sum(*values):
    return None if any(v is None for v in values) else str(sum(Decimal(v) for v in values))


def _risk_summary(prepared, policy):
    r = prepared.record
    return {"risk_policy_version": policy.version, "risk_status": r.get("risk_status"), "risk_reason": r.get("risk_reason"),
            "portfolio_risk_before": _decimal_sum(r.get("existing_open_risk"), r.get("existing_pending_risk")),
            "proposed_reservation": r.get("reserved_fill_risk"), "portfolio_risk_after": r.get("post_trade_portfolio_risk"),
            "portfolio_risk_limit": r.get("portfolio_risk_limit")}


def prior_decision(store, run_id):
    """The decisive CONFLICT_DECISION already journaled for ``run_id`` (retry/restart idempotency), else None."""
    for row in store.db.execute("SELECT payload FROM journal WHERE event_type=? AND run_id=? ORDER BY id",
                                (EVENT, run_id)).fetchall():
        payload = json.loads(row[0])
        if payload.get("decision") in DECISIVE:
            return payload
    return None


def commit_decision_rows(store, account_id, expected_state, rows, *, run_id, owner_key=None, symbol=None):
    """Journal ``rows`` only if the whole PAPER state is still ``expected_state`` and no decisive row exists for
    ``run_id``, under one BEGIN IMMEDIATE. Returns COMMITTED | STALE | DUPLICATE. No economic state is written."""
    with store.transaction():
        if owner_key is not None and not store.owns_slot(owner_key, symbol):
            raise RuntimeError("conflict journal without slot ownership")
        if prior_decision(store, run_id) is not None:
            return "DUPLICATE"
        if store.paper_state(*store.load_paper(account_id)) != expected_state:
            return "STALE"
        for timestamp, source, event, payload in rows:
            store._event(timestamp, run_id, symbol, source, event, "INFO", payload)
    return "COMMITTED"


def submit_with_conflict_control(store, floor_report, instrument, *, account_id, as_of, owner_key=None,
                                 policy=RISK_POLICY_V2_P5):
    """Phase 6 candidate submission: conflict control, then certified Risk V2 + reservation (module docstring)."""
    if not stamps_registered_semantics(policy):
        return ConflictPathResult("NOT_SUBMITTED", "RISK_POLICY_NOT_REGISTERED", None, None, None, 0)
    setup_id, invalid = candidate(floor_report)
    if invalid is not None:
        return ConflictPathResult("NOT_SUBMITTED", invalid, None, None, None, 0)
    plan, run_id, symbol = floor_report.trade_plan, floor_report.run_id, floor_report.symbol
    record = None
    for attempt in (1, 2):
        account, orders, fills = store.load_paper(account_id)
        existing = next((o for o in orders.values() if o.run_id == run_id), None)
        if existing is not None:
            return ConflictPathResult("DUPLICATE_RUN", "run_id_already_submitted", None, existing, None, attempt)
        prior = prior_decision(store, run_id)
        if prior is not None:
            return ConflictPathResult("DUPLICATE_RUN", "run_id_already_decided", None, None, prior, attempt)
        expected_state = store.paper_state(account, orders, fills)
        conflict = classify(symbol=symbol, direction=plan.side, setup_id=setup_id, account=account, orders=orders,
                            entry=plan.entry, stop=plan.stop, target=plan.target, as_of=as_of)
        base = {"run_id": run_id, "setup_id": setup_id, "attempt": attempt, **conflict.as_record(),
                "risk_policy_version": None, "risk_status": "NOT_EVALUATED", "risk_reason": None,
                "portfolio_risk_before": None, "proposed_reservation": None, "portfolio_risk_after": None,
                "portfolio_risk_limit": None, "order_id": None}
        if conflict.status == "BLOCK":
            record = {**base, "decision": "BLOCKED", "execution_status": "NOT_SUBMITTED"}
            outcome = commit_decision_rows(store, account_id, expected_state, [(as_of, SOURCE, EVENT, record)],
                                           run_id=run_id, owner_key=owner_key, symbol=symbol)
            if outcome == "COMMITTED":
                return ConflictPathResult("BLOCKED", conflict.reason_code, conflict, None, record, attempt)
            if outcome == "DUPLICATE":
                return ConflictPathResult("DUPLICATE_RUN", "run_id_already_decided", conflict, None,
                                          prior_decision(store, run_id), attempt)
            continue
        prepared = prepare_reservation(floor_report, instrument, account, orders, fills, as_of=as_of,
                                       setup_id=setup_id, policy=policy, stamp_setup_id=True)
        risk = _risk_summary(prepared, policy)
        if prepared.status != "APPROVED":
            record = {**base, **risk, "decision": "RISK_REJECTED", "execution_status": "NOT_SUBMITTED"}
            outcome = commit_decision_rows(store, account_id, expected_state,
                                           [(as_of, RISK_SOURCE, RISK_EVENT, prepared.journal_record),
                                            (as_of, SOURCE, EVENT, record)],
                                           run_id=run_id, owner_key=owner_key, symbol=symbol)
            if outcome == "COMMITTED":
                return ConflictPathResult("RISK_REJECTED", prepared.reason, conflict, None, record, attempt)
            if outcome == "DUPLICATE":
                return ConflictPathResult("DUPLICATE_RUN", "run_id_already_decided", conflict, None,
                                          prior_decision(store, run_id), attempt)
            continue
        order = prepared.order
        record = {**base, **risk, "decision": "RESERVED", "execution_status": "SUBMITTED", "order_id": order.order_id}
        prepared.broker._event(as_of, run_id, symbol, order.order_id, EVENT, record)  # same guarded save as the order
        if store.save_paper(prepared.broker, owner_key=owner_key, symbol=symbol,
                            expected_state=expected_state) is not False:
            return ConflictPathResult("RESERVED", "approved", conflict, order, record, attempt)
    stale = {**(record or {}), "run_id": run_id, "setup_id": setup_id, "decision": "STALE_STATE",
             "economic_action": "NONE", "execution_status": "NOT_SUBMITTED", "attempts": 2,
             "conflict_policy_version": CONFLICT_POLICY_VERSION}
    store.event(as_of, run_id, symbol, SOURCE, EVENT, "WARNING", stale)  # informative only; claims no approval
    return ConflictPathResult("STALE", "STALE_STATE", None, None, stale, 2)


__all__ = ["ConflictPathResult", "EVENT", "candidate", "commit_decision_rows", "prior_decision",
           "submit_with_conflict_control"]
