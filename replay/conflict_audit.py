"""P6.0 conflict characterization (offline, observational; never used to tune anything).

Cohort: the 1,940 P4.1A VALID_SETUP slots with a V1 plan (same records as ``replay.risk_audit``). Two views:

1) IDENTITY: how setup_id behaves across consecutive VALID records of a symbol (no portfolio): runs of the same
   setup_id, and every change classified by what the record proves changed (side / invalidation / neither — the
   last means only the undisclosed 15m anchor changed). The 15m anchor itself is NOT in the records, so
   SAME THESIS / NEW STRUCTURE cannot be labelled.
2) EXPOSURE: the chronological DEC-5.7 single-account pass of ``replay.risk_audit`` (production Risk V2 + broker),
   recording, for every VALID record, the same-symbol exposure that existed at that slot (OPEN / PENDING / none),
   direction relation, setup_id relation and invalidation relation to the exposure's originating setup.
P6.1: also classifies every record with the implemented ``execution.conflict_engine`` (blocked candidates
never reach Risk V2, exactly as the Phase 6 path).
Usage: python -m replay.conflict_audit <store.db> <p41a_lab_out> <out.json>
"""
import heapq
import json
import sys
from collections import Counter

import pandas as pd

from core.risk_policy import RISK_POLICY_V2_P5
from core.rr_contract import POLICY_V2_F3
from execution.conflict_engine import classify
from execution.contracts import PaperAccount
from execution.paper_broker import PaperBroker
from execution.risk_engine_v2 import evaluate
from replay.fill_audit import FIVE
from replay.risk_audit import DELAY, INS, START, Cohort, exit_of, report


def identity_view(records):
    out = {}
    for symbol in sorted({r["symbol"] for r in records}):
        rows = [r for r in records if r["symbol"] == symbol]
        changes, runs, current = Counter(), [], 1
        for prev, cur in zip(rows, rows[1:]):
            if cur["setup_id"] == prev["setup_id"]:
                current += 1
                continue
            runs.append(current)
            current = 1
            changes["SIDE_CHANGED" if cur["side"] != prev["side"]
                    else "INVALIDATION_CHANGED" if cur["invalidation"] != prev["invalidation"]
                    else "ANCHOR_ONLY_CHANGED (same side + invalidation)"] += 1
        runs.append(current)
        runs.sort()
        out[symbol] = {"valid_records": len(rows), "distinct_setup_ids": len({r["setup_id"] for r in rows}),
                       "consecutive_same_id_runs": len(runs), "run_length_median": runs[len(runs) // 2],
                       "run_length_max": runs[-1], "setup_id_changes_by_proven_cause": dict(changes),
                       "same_id_with_different_side_or_invalidation": sum(
                           1 for a in rows for b in rows if a["setup_id"] == b["setup_id"]
                           and (a["side"], a["invalidation"]) != (b["side"], b["invalidation"]))}
    return out


def exposure_view(cohort):
    """The DEC-5.7 chronological pass of replay.risk_audit.portfolio, observing conflicts before each decision."""
    account = PaperAccount("1.0", "audit", START, START, START)
    orders, events, seq, origin = {}, [], 0, {}  # origin: run_id -> originating record
    states, by_symbol, engine = Counter(), Counter(), Counter()

    def mark(at):
        unrealized = 0.0
        for p in account.open_positions.values():
            price = cohort.last_close(p.symbol, at)
            unrealized += ((price - p.entry_price) if p.side == "LONG" else (p.entry_price - price)) * p.quantity
        account.unrealized_pnl = unrealized
        account.equity = START + account.realized_pnl + unrealized

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
                                                (position, price)))
                del orders[order.order_id]
            else:
                position, price = payload
                pnl = ((price - position.entry_price) if position.side == "LONG"
                       else (position.entry_price - price)) * position.quantity
                account.realized_pnl += pnl
                del account.open_positions[position.symbol]
                mark(at)

    for n, rec in enumerate(cohort.records):
        slot = pd.Timestamp(rec["slot"])
        advance(slot)
        mark(slot)
        symbol = rec["symbol"]
        exposure = account.open_positions.get(symbol)
        kind = "OPEN" if exposure is not None else None
        if exposure is None:
            exposure = next((o for o in orders.values() if o.symbol == symbol and o.status == "PENDING"), None)
            kind = "PENDING" if exposure is not None else None
        if exposure is None:
            other = bool(account.open_positions) or any(o.status == "PENDING" for o in orders.values())
            key = ("NO_SAME_SYMBOL_EXPOSURE", "OTHER_SYMBOL_EXPOSED" if other else "FLAT")
        else:
            first = origin[exposure.run_id]
            key = (kind, "SAME_DIRECTION" if exposure.side == rec["side"] else "OPPOSITE_DIRECTION",
                   "SAME_SETUP_ID" if first["setup_id"] == rec["setup_id"] else "DIFFERENT_SETUP_ID",
                   "SAME_INVALIDATION" if first["invalidation"] == rec["invalidation"] else "DIFFERENT_INVALIDATION")
        states[" | ".join(key)] += 1
        by_symbol[symbol + " | " + key[0]] += 1
        # P6.1: the implemented pure engine on the same state (durable setup_id carried by orders -> positions).
        decision = classify(symbol=symbol, direction=rec["side"], setup_id=rec["setup_id"], account=account,
                            orders=orders)
        engine[f"{decision.classification} | {decision.reason_code}"] += 1
        if decision.status == "BLOCK":
            continue  # P6.1 policy: same-symbol exposure blocks before Risk V2 (Risk's symbol rule agrees)
        plan = cohort.plan(rec, f"cf-{n}")
        result = evaluate(plan, account, orders, INS[symbol], setup_id=rec.get("setup_id"), at=plan.as_of)
        if result.decision.status != "APPROVED":
            continue
        broker = PaperBroker(account, INS[symbol], rr_policy=POLICY_V2_F3)
        broker.orders = orders
        order = broker.submit_plan(report(plan, result.decision, rec.get("setup_id")), None, plan.as_of)
        index = cohort.five[symbol][0]
        k = int(index.searchsorted(slot + DELAY))
        if order is None or k >= len(index):
            orders.pop(getattr(order, "order_id", None), None)
            continue
        order.risk_policy_version = RISK_POLICY_V2_P5.version
        order.setup_id = rec["setup_id"]  # as the Phase 6 path stamps it; the fill copies it to the position
        origin[order.run_id] = rec
        seq += 1
        heapq.heappush(events, (index[k], seq, "FILL", (order, k)))
    return {"states": dict(sorted(states.items())), "by_symbol": dict(sorted(by_symbol.items())),
            "engine_classification": dict(sorted(engine.items()))}


def main(store_path, p41a_dir, out_path):
    cohort = Cohort(store_path, p41a_dir)
    out = {"cohort": len(cohort.records), "identity": identity_view(cohort.records),
           "exposure_DEC_5_7_portfolio": exposure_view(cohort),
           "not_determinable": ["SAME_THESIS", "NEW_STRUCTURE", "TREND_CHANGE"],
           "why_not_determinable": "records carry setup_id, side, invalidation and slot only; the 15m anchor, "
                                   "swings and 1h bias that would define thesis/structure are not retained"}
    from pathlib import Path
    Path(out_path).write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    return out


if __name__ == "__main__":
    print(json.dumps(main(*sys.argv[1:4]), indent=2, default=str))
