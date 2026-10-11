"""Offline DEC-14.1 comparison on the Phase 6 Cohort (no AI, no live writes).

Usage: python -m replay.opposite_policy_impact [replay.db p41a_lab_dir]
Same DEC-5.7 event calendar, fixed-3R plans and production broker/Risk as conflict_audit.
Structure is reconstructed from closed original bars using the existing scouts/validator,
never inferred from setup_id changes. Missing corpus/evidence is explicitly NO_DATA.
"""
import argparse
import heapq
import json
from collections import Counter
from dataclasses import replace
from pathlib import Path

import pandas as pd

from core.rr_contract import POLICY_V2_F3
from data.macro_news import NoMacroDataProvider
from execution.conflict_engine import classify
from execution.contracts import PaperAccount
from execution.opposite_policy import assess
from execution.paper_broker import PaperBroker
from execution.risk_reservation import prepare_reservation
from floor.orchestrator import run as run_floor
from replay.engine import MAX_AGE, load_frames, snapshot_at
from replay.fill_audit import FIVE
from replay.risk_audit import Cohort, DELAY, INS, START, exit_of
from replay.store import open_replay_store, replay_engine
from riesgo import crear_configuracion_riesgo_v2
from runtime.gates import fresh_snapshot

COUNTS = ("opposite_conflicts", "would_close_profit", "would_keep", "would_close_loss",
          "reversals_approved_by_risk", "blocked_no_BOS", "duplicates")


def policy_pass(cohort, frames, policy):
    account = PaperAccount("1.0", "audit", START, START, START)
    orders, events, seq = {}, [], 0
    counts = Counter({key: 0 for key in COUNTS})

    def mark(at):
        account.unrealized_pnl = 0.
        for position in account.open_positions.values():
            position.last_price = cohort.last_close(position.symbol, at)
            account.unrealized_pnl += ((position.last_price - position.entry_price) if position.side == "LONG"
                                       else (position.entry_price - position.last_price)) * position.quantity * position.contract_multiplier
        account.equity = START + account.realized_pnl + account.unrealized_pnl

    def advance(until):
        nonlocal seq
        while events and events[0][0] <= until:
            at, _, kind, payload = heapq.heappop(events)
            mark(at)
            if kind == "FILL":
                order, k = payload
                broker = PaperBroker(account, INS[order.symbol], rr_policy=POLICY_V2_F3)
                position = broker.process_next_bar(order, cohort.bar(order.symbol, k))
                if position is not None:
                    _, o, h, l, _ = cohort.five[order.symbol]
                    exit_kind, price, j = exit_of(o, h, l, k, order.side, order.stop, order.target)
                    if j is not None:
                        seq += 1
                        heapq.heappush(events, (cohort.five[order.symbol][0][j] + FIVE, seq, "EXIT",
                                                (position.position_id, position.symbol, price, exit_kind)))
            else:
                pid, symbol, price, kind = payload
                position = account.open_positions.get(symbol)
                if position is not None and position.position_id == pid:
                    PaperBroker(account, INS[symbol]).close_market(
                        symbol, price, at.to_pydatetime(), exit_reason=kind, decision_run_id=position.run_id)
            mark(at)

    for n, rec in enumerate(cohort.records):
        slot, symbol = pd.Timestamp(rec["slot"]), rec["symbol"]
        advance(slot)
        mark(slot)
        position = account.open_positions.get(symbol)
        decision = classify(symbol=symbol, direction=rec["side"], setup_id=rec["setup_id"],
                            account=account, orders=orders)
        opposite = position is not None and position.side != rec["side"]
        counts["opposite_conflicts"] += int(opposite)
        snap = snapshot_at(frames[symbol], slot)
        if not fresh_snapshot(snap, symbol, slot.to_pydatetime(), MAX_AGE)[0]:
            counts["no_data_or_invalid_setup"] += 1
            continue
        if any(o.symbol == symbol and o.status == "PENDING" for o in orders.values()):
            counts["pending_blocked"] += 1
            continue
        report = run_floor(snap, slot.to_pydatetime(), symbol, NoMacroDataProvider(), INS[symbol],
                           crear_configuracion_riesgo_v2(), equity=START, run_id=f"dec14-{n}", planner_policy=POLICY_V2_F3)
        reversing = False
        if position is not None and policy == "STRUCTURE_CHANGE":
            if report.final_status != "PLAN_READY":
                counts["no_data_or_invalid_setup"] += 1
                continue
            # Cohort SL/TP are frozen: the filled position still carries its original stop.
            reason = assess(report, position, snap, INS[symbol], slot.to_pydatetime(), MAX_AGE,
                            original_stop=position.stop)
            counts[{"SAME_SETUP_IGNORED": "duplicates", "OPPOSITE_NO_STRUCTURE_CHANGE": "blocked_no_BOS",
                    "OPPOSITE_DISCARDED_STRUCTURE_INTACT": "would_keep", "WOULD_CLOSE_PROFIT": "would_close_profit",
                    "WOULD_CLOSE_LOSS": "would_close_loss"}.get(reason, "other_blocked")] += 1
            if reason not in {"WOULD_CLOSE_PROFIT", "WOULD_CLOSE_LOSS"}:
                continue
            PaperBroker(account, INS[symbol]).close_market(
                symbol, cohort.last_close(symbol, slot), slot.to_pydatetime(),
                exit_reason="EXIT_STRUCTURE_CHANGE", decision_run_id=report.run_id)
            reversing = True
        elif decision.status == "BLOCK":
            counts["duplicates"] += int(decision.classification == "DUPLICATE_SETUP")
            continue
        # Keep the original Phase 6 fixed-3R geometry, but require reconstructed validated setup.
        if report.setup_assessment.status != "VALID_SETUP" or report.setup_assessment.side != rec["side"]:
            counts["no_data_or_invalid_setup"] += 1
            continue
        report = replace(report, trade_plan=cohort.plan(rec, report.run_id), final_status="PLAN_READY")
        prepared = prepare_reservation(report, INS[symbol], account, orders, {}, as_of=slot.to_pydatetime(),
                                       setup_id=rec["setup_id"], stamp_setup_id=True)
        if prepared.status != "APPROVED":
            counts["risk_rejected"] += 1
            continue
        counts["reversals_approved_by_risk"] += int(reversing)
        order = prepared.order
        k = int(cohort.five[symbol][0].searchsorted(slot + DELAY))
        if k >= len(cohort.five[symbol][0]):
            orders.pop(order.order_id, None)
            continue
        seq += 1
        heapq.heappush(events, (cohort.five[symbol][0][k], seq, "FILL", (order, k)))
    return dict(sorted(counts.items()))


def main(store_path, lab_dir):
    paths = {"store": str(store_path), "lab_dir": str(lab_dir)}
    if not Path(store_path).is_file() or not list(Path(lab_dir).glob("lab_*.pkl")):
        return {"status": "NO_DATA", "inputs": paths, "reason": "Phase 6 replay DB / lab_*.pkl corpus unavailable",
                "cohort": "NO_DATA", "policies": {p: {key: "NO_DATA" for key in COUNTS}
                                                   for p in ("BLOCK", "STRUCTURE_CHANGE")}}
    cohort = Cohort(store_path, lab_dir)
    if not cohort.records:
        raise ValueError("empty Phase 6 replay corpus")
    store = open_replay_store(store_path, readonly=True)
    try:
        frames = {s: load_frames(replay_engine(store), s) for s in INS}
    finally:
        store.close()
    return {"status": "COMPLETE", "cohort": len(cohort.records), "inputs": paths,
            "policies": {p: policy_pass(cohort, frames, p) for p in ("BLOCK", "STRUCTURE_CHANGE")},
            "scope": "Offline conflict/Risk V2 impact; no AI simulation, no runtime activation"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("store", nargs="?", default="data/replay/market_replay.db")
    parser.add_argument("lab_dir", nargs="?", default="data/replay/p41a")
    args = parser.parse_args()
    print(json.dumps(main(args.store, args.lab_dir), indent=2))
