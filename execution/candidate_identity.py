"""V2 Phase 6 (P6.1E): immutable run_id -> candidate identity (fingerprint + durable non-economic claim).

INV-6.1 one run_id is bound to ONE candidate. The binding is a ``RUN_CANDIDATE_CLAIM`` journal row in the trading DB,
written by check-then-insert inside ONE ``BEGIN IMMEDIATE`` (serialized across processes and connections) and never
updated or deleted. Because a claim is immutable once written, a caller that verified "the claim is mine" can never be
contradicted later by a different candidate: different-fingerprint callers are refused BEFORE any conflict, Risk,
reservation or decision write. That is why no check is needed inside ``save_paper``.

The claim is identity only: not a conflict decision, Risk approval, reservation, submission or rejection; it carries
``economic_action: NONE`` and does not block a same-fingerprint caller (crash after claim -> the same candidate
resumes; a different candidate stays refused).

Fingerprint ``V2_P6_CANDIDATE_1`` = sha256 of canonical JSON (sorted keys, no spaces, ASCII) over the fields that give
the candidate its economic meaning:
  account_id, symbol, setup_id (Phase 3), side, planner policy, planned entry / SL / TP and declared R:R (canonical
  decimals: ``Decimal(repr(float))`` normalized and printed in fixed notation, so 2650 == 2650.0), plan as_of (UTC ISO;
  it becomes the order's as_of and gates fill bars), conflict policy version, the full risk policy record (versioned
  limits and fill semantics), and the instrument contract (symbol, price/quantity increments, multiplier).
Excluded: run_id (the key), quantity (Risk output), decision timestamps, warnings, scout evidence.
"""
from datetime import timezone
import hashlib
import json

from core.rr_contract import to_decimal
from storage.codec import utc

FINGERPRINT_VERSION = "V2_P6_CANDIDATE_1"
CLAIM_EVENT = "RUN_CANDIDATE_CLAIM"
CLAIMED, SAME, MISMATCH, UNKNOWN_HISTORY = "CLAIMED", "SAME", "MISMATCH", "UNKNOWN_HISTORY"


def _number(value):
    number = to_decimal(value)
    if number is None:
        raise ValueError("candidate fingerprint requires finite positive numbers")
    return format(number.normalize(), "f")


def candidate_fields(floor_report, instrument, *, account_id, setup_id, policy, conflict_policy_version):
    plan = floor_report.trade_plan
    return {"fingerprint_version": FINGERPRINT_VERSION, "account_id": account_id, "symbol": floor_report.symbol,
            "setup_id": setup_id, "side": plan.side, "planner_policy": plan.policy_version,
            "entry": _number(plan.entry), "stop": _number(plan.stop), "target": _number(plan.target),
            "declared_rr": _number(plan.risk_reward), "plan_as_of": utc(plan.as_of.astimezone(timezone.utc)),
            "conflict_policy_version": conflict_policy_version, "risk_policy": policy.as_record(),
            "instrument": {"symbol": instrument.symbol, "price_increment": _number(instrument.price_increment),
                           "quantity_increment": _number(instrument.quantity_increment),
                           "contract_multiplier": _number(instrument.contract_multiplier)}}


def fingerprint(fields):
    canonical = json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def claimed_fingerprint(store, run_id):
    """The fingerprint bound to ``run_id``, or None. The FIRST claim row is authoritative (only one can exist)."""
    row = store.db.execute("SELECT payload FROM journal WHERE event_type=? AND run_id=? ORDER BY id LIMIT 1",
                           (CLAIM_EVENT, run_id)).fetchone()
    return None if row is None else json.loads(row[0])["fingerprint"]


def claim_run(store, *, run_id, symbol, fields, as_of, has_history, owner_key=None):
    """Acquire or verify the run's candidate claim under ONE BEGIN IMMEDIATE.

    CLAIMED (new claim written) | SAME (already ours) | MISMATCH (bound to another candidate; nothing written) |
    UNKNOWN_HISTORY (no claim, but the run already has an order or a decisive decision written before claims existed;
    its identity cannot be proven, so no claim is invented and nothing is written). ``has_history(store)`` runs inside
    the same transaction."""
    print_ = fingerprint(fields)
    with store.transaction():
        if owner_key is not None and not store.owns_slot(owner_key, symbol):
            raise RuntimeError("candidate claim without slot ownership")
        existing = claimed_fingerprint(store, run_id)
        if existing is not None:
            return SAME if existing == print_ else MISMATCH
        if has_history(store):
            return UNKNOWN_HISTORY
        store._event(as_of, run_id, symbol, "conflict_engine", CLAIM_EVENT, "INFO",
                     {"fingerprint_version": FINGERPRINT_VERSION, "fingerprint": print_, "candidate": fields,
                      "economic_action": "NONE", "decisive": False})
    return CLAIMED


__all__ = ["CLAIM_EVENT", "CLAIMED", "FINGERPRINT_VERSION", "MISMATCH", "SAME", "UNKNOWN_HISTORY", "candidate_fields",
           "claim_run", "claimed_fingerprint", "fingerprint"]
