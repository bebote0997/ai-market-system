"""DEC-14.1 / DEC-REVERSAL-1 deterministic PAPER policy. Runtime owns the symbol lock for both CAS saves.

``STRUCTURE_CHANGE`` (DEC-14.1): an opposite setup acts only with a confirmed closed 15m BOS beyond the position's SL;
profit -> close and reverse, loss -> keep while the 1H structure is intact, else close and re-evaluate.
``REVERSE_IN_PROFIT`` (DEC-REVERSAL-1): an opposite PLAN_READY setup while the position is in profit (marked at the
last closed 5m close, before costs) -> close and open the new direction through a fresh Risk V2 reservation (every gate
applies; a rejection after the close stays flat); in loss or at zero -> keep; same direction -> no action.

Close + DEC14_CLOSE commit together. Open is a separate fresh Risk V2 reservation.
An incomplete close is abandoned on restart; historical evidence never authorizes a resumed reverse.
Similarity is same direction within one quarter of the original entry-to-stop distance.
"""
import json
from dataclasses import dataclass
from decimal import Decimal

import pandas as pd

from core.risk_policy import risk_policy_for
from execution.candidate_identity import CLAIMED, SAME, candidate_fields, claim_run
from execution.conflict_path import candidate
from execution.risk_engine_v2 import drawdown_basis_scope
from execution.risk_reservation import prepare_reservation
from runtime.gates import fresh_snapshot

VERSION = "DEC14_STRUCTURE_CHANGE_1"
REVERSAL_VERSION = "DEC_REVERSAL_1"
EXIT_REASONS = {"STRUCTURE_CHANGE": "EXIT_STRUCTURE_CHANGE", "REVERSE_IN_PROFIT": "EXIT_REVERSAL_IN_PROFIT"}
VERSIONS = {"STRUCTURE_CHANGE": VERSION, "REVERSE_IN_PROFIT": REVERSAL_VERSION}
DECISION = "DEC14_DECISION"
CLOSE = "DEC14_CLOSE"


@dataclass(frozen=True)
class Outcome:
    status: str
    reason: str
    order_id: str | None = None


def prior(store, run_id, event=DECISION):
    row = store.db.execute("SELECT payload FROM journal WHERE run_id=? AND event_type=? ORDER BY id LIMIT 1",
                           (run_id, event)).fetchone()
    return None if row is None else json.loads(row[0])


def abandon_incomplete(store, at, *, interrupted_run_id=None):
    """Restart-only, serialized durable terminal outcome; no economic write or symbol-lock takeover."""
    with store.transaction():
        for row in store.db.execute("SELECT run_id,symbol,payload FROM journal WHERE event_type=? ORDER BY id",
                                    (CLOSE,)).fetchall():
            running = store.db.execute("SELECT 1 FROM runs WHERE run_id=? AND status='RUNNING'",
                                       (row["run_id"],)).fetchone()
            if prior(store, row["run_id"]) is None and (not running or row["run_id"] == interrupted_run_id):
                payload = json.loads(row["payload"])
                store._event(at, row["run_id"], row["symbol"], "opposite_policy", DECISION, "WARNING",
                             {**payload, "status": "SKIPPED", "reason": "REVERSE_ABANDONED_AFTER_RESTART"})


def _confirmed_bos(signal, frame, timeframe, side, position, at, max_age):
    bos = signal.get("bos")
    if not bos or bos.get("direction") != ("bearish" if side == "SHORT" else "bullish"):
        return False
    stamp = pd.Timestamp(bos["break_timestamp"])
    duration = pd.Timedelta(timeframe)
    if (stamp.tzinfo is None or stamp.value % duration.value or stamp not in frame.index
            or stamp <= position.opened_at or stamp + duration > at
            or (pd.Timestamp(at) - stamp).total_seconds() > max_age):
        return False
    row = frame.loc[stamp]
    close = Decimal(str(row["Close"]))
    if row["is_closed"] != True or close != Decimal(str(bos["break_close"])):  # noqa: E712
        return False
    return close < Decimal(str(position.stop)) if side == "SHORT" else close > Decimal(str(position.stop))


def assess(report, position, snapshot, instrument, at, limits, *, original_stop=None, policy="STRUCTURE_CHANGE"):
    """Pure classification using validator evidence and the actual closed BOS bar; no AI input."""
    setup_id, invalid = candidate(report)
    if invalid:
        return invalid
    plan = report.trade_plan
    if plan.side == position.side:
        if original_stop is None:
            return "NO_DATA"
        entry = Decimal(str(position.entry_price))
        near = abs(Decimal(str(plan.entry)) - entry) <= Decimal("0.25") * abs(entry - Decimal(str(original_stop)))
        if near:
            return "SAME_SETUP_IGNORED"
        return "SAME_DIRECTION"
    if policy == "REVERSE_IN_PROFIT":  # DEC-REVERSAL-1: profit alone decides; no BOS-beyond-SL requirement
        price = float(snapshot["5m"].iloc[-1]["Close"])
        pnl = ((price - position.entry_price) if position.side == "LONG" else
               (position.entry_price - price)) * position.quantity * position.contract_multiplier
        return "WOULD_CLOSE_PROFIT" if pnl > 0 else "OPPOSITE_IN_LOSS_KEPT"
    structure = report.setup_assessment.evidence[0]
    if not _confirmed_bos(structure["15m"], snapshot["15m"], "15min", plan.side, position, at,
                          dict(limits)["15m"]):
        return "OPPOSITE_NO_STRUCTURE_CHANGE"
    price = float(snapshot["5m"].iloc[-1]["Close"])
    pnl = ((price - position.entry_price) if position.side == "LONG" else
           (position.entry_price - price)) * position.quantity * position.contract_multiplier
    if pnl > 0:
        return "WOULD_CLOSE_PROFIT"
    hour = structure["1h"]
    if hour["structure_state"] not in {"bullish", "bearish", "neutral"}:
        return "NO_DATA"
    against = "bearish" if position.side == "LONG" else "bullish"
    right_side = price > position.stop if position.side == "LONG" else price < position.stop
    hour_bos = hour.get("bos")
    broken = hour["structure_state"] == against or (
        hour_bos is not None and hour_bos["direction"] == against
        and pd.Timestamp(hour_bos["break_timestamp"]) > position.opened_at)
    if right_side and not broken:
        return "OPPOSITE_DISCARDED_STRUCTURE_INTACT"
    return "WOULD_CLOSE_LOSS"


def execute(runtime, report, snapshot, *, key, at, ai_allowed, writer=None, drawdown_basis=None):
    """Called only for STRUCTURE_CHANGE / REVERSE_IN_PROFIT. Both writes reuse runtime CAS, halt/REX and slot ownership.
    ``drawdown_basis`` (routing ON only): Risk V2 Gate 4 on the OANDA demo account (DEC-DEMO-GATE-1)."""
    store, symbol, run_id = runtime.store, report.symbol, report.run_id
    mode = runtime.config.v2_opposite_setup_policy
    version = VERSIONS[mode]
    if not store.owns_slot(key, symbol):
        raise RuntimeError("opposite policy requires symbol lock")
    instrument = runtime.instruments[symbol]
    setup_id, invalid = candidate(report)
    if invalid:
        store.event(at, run_id, symbol, "opposite_policy", DECISION, "WARNING",
                    {"status": "SKIPPED", "reason": invalid})
        return Outcome("SKIPPED", invalid)
    policy = risk_policy_for(report.trade_plan)  # DEC-FX-SL-1: an FX plan is reserved under the FX risk policy
    fields = candidate_fields(report, instrument, account_id=runtime.config.account_id, setup_id=setup_id,
                              policy=policy, conflict_policy_version=version)
    claim = runtime._persist("D", lambda: claim_run(
        store, run_id=run_id, symbol=symbol, fields=fields, as_of=at, owner_key=key,
        has_history=lambda s: prior(s, run_id) is not None or any(
            o.run_id == run_id for o in s.load_paper(runtime.config.account_id)[1].values())))
    if claim not in (CLAIMED, SAME):
        return Outcome("SKIPPED", "RUN_ID_CANDIDATE_MISMATCH" if claim == "MISMATCH" else
                       "RUN_ID_IDENTITY_UNKNOWN" if claim else "HALTED")

    old = prior(store, run_id)
    if old:
        return Outcome(old["status"], old["reason"], old.get("order_id"))
    if prior(store, run_id, CLOSE):
        # Even an in-process retry may not resume an interrupted close.
        abandon_incomplete(store, at, interrupted_run_id=run_id)
        return Outcome("SKIPPED", "REVERSE_ABANDONED_AFTER_RESTART")

    def note(broker, outcome, event=DECISION):
        trade = {}
        if event == "STRUCTURE_REVERSE_SUBMITTED":
            order = broker.orders[outcome.order_id]
            trade = {"side": order.side, "entry": order.planned_entry, "stop_loss": order.stop,
                     "take_profit": order.target, "quantity": order.quantity}
        broker._event(at, run_id, symbol, "opposite_policy", event,
                      {"policy": version, "status": outcome.status, "reason": outcome.reason,
                       "order_id": outcome.order_id, "setup_id": setup_id, **trade})

    def gate():
        fresh, state, _ = fresh_snapshot(snapshot, symbol, runtime.clock(), runtime.config.max_age_seconds)
        if not fresh:
            return state
        for tf, duration in (("1h", "1h"), ("15m", "15min"), ("5m", "5min")):
            if snapshot[tf].index[-1] + pd.Timedelta(duration) > runtime.clock():
                return "NO_DATA"
        if not ai_allowed:
            return "AI_CAUTION_OR_UNHEALTHY"
        return None

    def close_or_block(broker):
        reason = gate()
        if reason is None and any(o.symbol == symbol and o.status == "PENDING" for o in broker.orders.values()):
            reason = "PENDING_ORDER"
        position = broker.account.open_positions.get(symbol)
        if reason is None and position is not None:
            origin = broker.orders.get(position.origin_order_id)
            reason = assess(report, position, snapshot, instrument, runtime.clock(), runtime.config.max_age_seconds,
                            original_stop=None if origin is None else origin.stop, policy=mode)
        if reason in {"WOULD_CLOSE_PROFIT", "WOULD_CLOSE_LOSS"}:
            broker.close_market(symbol, float(snapshot["5m"].iloc[-1]["Close"]), at,
                                exit_reason=EXIT_REASONS[mode], decision_run_id=run_id)
            outcome = Outcome("CLOSED", reason)
            note(broker, outcome, CLOSE)
        elif reason is not None:
            outcome = Outcome("SKIPPED", reason)
            note(broker, outcome)
        else:
            return False, Outcome("CONTINUE", "NO_CONFLICT")
        return True, outcome

    broker, outcome = runtime._guarded_paper_write(symbol, key, close_or_block, writer, "structure_close")
    if broker is None:
        return Outcome("SKIPPED", "STALE_PAPER_STATE")
    if outcome.status == "SKIPPED":
        return outcome
    reversing = outcome.status == "CLOSED"

    def reserve(broker):
        reason = gate()
        if reason is not None:
            outcome = Outcome("SKIPPED", reason)
        else:
            with drawdown_basis_scope(drawdown_basis):  # risk_reservation.py is frozen: the basis is scoped
                prepared = prepare_reservation(report, instrument, broker.account, broker.orders, broker.fills,
                                               as_of=at, setup_id=setup_id, policy=policy, stamp_setup_id=True)
            if prepared.status == "APPROVED":
                broker.journal.extend(prepared.broker.journal)
                outcome = Outcome("SUBMITTED", "STRUCTURE_REVERSE_SUBMITTED" if reversing else "ORDER_SUBMITTED",
                                  prepared.order.order_id)
                if reversing:
                    note(broker, outcome, "STRUCTURE_REVERSE_SUBMITTED")
            else:
                outcome = Outcome("SKIPPED", "RISK_REJECTED_AFTER_CLOSE" if reversing else "RISK_REJECTED")
                broker._event(at, run_id, symbol, "risk_engine_v2", "RISK_V2_DECISION", prepared.journal_record)
                broker._event(at, run_id, symbol, "risk_engine_v2", "RISK_REJECTED",
                              {"reason": prepared.reason, "after_structure_close": reversing})
        note(broker, outcome)
        return True, outcome

    broker, outcome = runtime._guarded_paper_write(symbol, key, reserve, writer, "structure_reverse")
    return outcome if broker is not None else Outcome("SKIPPED", "STALE_PAPER_STATE")
