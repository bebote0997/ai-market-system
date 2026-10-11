"""P5.1 risk audit (offline, observational; never used to tune DEC-5.x constants).

Cohort: the 1,940 P4.1A fixed-3R plan slots (same VALID setups), planned geometry rebuilt exactly as
``replay.fill_audit.planned("V2", ...)``, NEXT_CYCLE fill (open of the 5m bar starting at t + 10 min), SL/TP frozen.
Every decision uses PRODUCTION code: ``execution.risk_engine_v2.evaluate`` (DEC-5.1/5.2/5.3/5.5 sizing and gates) and
``PaperBroker(rr_policy=POLICY_V2_F3)`` (DEC-4.7 R:R floor + DEC-5.7 money rule). The pre-P5.1 rule is evaluated on
the same rows: R:R floor + the former hard-coded ``real_risk > equity x 1%`` cap.

1) STANDALONE: each plan alone on a fresh 10,000 account (isolates the fill rule).
2) PORTFOLIO: chronological pass over all plans with one durable-like account (open + pending reservations, symbol
   rule, 2.30% aggregate, 5% drawdown gate); exits by TradeManager precedence (gap stop, gap target, stop, target),
   no costs; once with the DEC-5.7 rule and once with the former 1% cap.
Usage: python -m replay.risk_audit <store.db> <p41a_lab_out> <out.json>
"""
import heapq
import json
import pickle
import sys
from collections import Counter, defaultdict
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pandas as pd

from core.contracts import FloorRunReport, SetupAssessment, TradePlan
from core.risk_policy import RISK_POLICY_V2_P5
from core.rr_contract import POLICY_V2_F3
from execution.contracts import PaperAccount
from execution.paper_broker import PaperBroker
from execution.risk_engine_v2 import evaluate
from replay.engine import load_frames
from replay.fill_audit import FIVE, planned
from replay.lab import DISCOVERY_END
from replay.store import open_replay_store, replay_engine
from runtime.paper_contracts import paper_instruments

START = 10000.0
DELAY = pd.Timedelta(minutes=10)  # NEXT_CYCLE
INS = paper_instruments()
FACTOR = RISK_POLICY_V2_P5.fill_money_risk_factor


def exit_of(o, h, l, index, side, stop, target):
    """(kind, price, bar index) by TradeManager precedence from bar ``index`` + 1 on; (OPEN, None, None)."""
    for k in range(index + 1, len(o)):
        if side == "LONG":
            gs, gt, sh, th = o[k] <= stop, o[k] >= target, l[k] <= stop, h[k] >= target
        else:
            gs, gt, sh, th = o[k] >= stop, o[k] <= target, h[k] >= stop, l[k] <= target
        if gs or gt:
            return ("STOP" if gs else "TARGET"), float(o[k]), k
        if sh or th:
            return ("STOP", stop, k) if sh else ("TARGET", target, k)
    return "OPEN", None, None


class Cohort:
    def __init__(self, store_path, p41a_dir):
        self.records = sorted((r for f in sorted(Path(p41a_dir).glob("lab_*.pkl"))
                               for r in pickle.loads(f.read_bytes())["records"] if "v1" in r),
                              key=lambda r: (pd.Timestamp(r["slot"]), r["symbol"]))
        store = open_replay_store(store_path, readonly=True)
        frames = {s: load_frames(replay_engine(store), s)["5m"] for s in ("XAUUSD", "EURUSD")}
        store.close()
        self.five = {s: (f.index, f["Open"].to_numpy(), f["High"].to_numpy(), f["Low"].to_numpy(),
                         f["Close"].to_numpy()) for s, f in frames.items()}

    def plan(self, rec, run_id):
        index, _, _, _, close = self.five[rec["symbol"]]
        slot = pd.Timestamp(rec["slot"])
        entry_raw = float(close[int(index.searchsorted(slot - FIVE, side="right")) - 1])  # last bar closed <= slot
        entry, stop, target = planned("V2", rec["side"], entry_raw, float(rec["invalidation"]),
                                      INS[rec["symbol"]].price_increment)
        return TradePlan("1.0", rec["symbol"], rec["side"], "5m", entry, stop, target, 3.0, invalidation=str(stop),
                         run_id=run_id, as_of=slot.to_pydatetime(), policy_version=POLICY_V2_F3)

    def bar(self, symbol, k):
        index, o, h, l, c = self.five[symbol]
        return {"symbol": symbol, "timestamp": index[k].to_pydatetime(), "open": float(o[k]), "high": float(h[k]),
                "low": float(l[k]), "close": float(c[k]), "is_closed": True}

    def last_close(self, symbol, at):
        index, _, _, _, close = self.five[symbol]
        k = int(index.searchsorted(at - FIVE, side="right")) - 1
        return float(close[k])


def report(plan, decision, setup_id):
    setup = SetupAssessment("1.0", plan.run_id, plan.as_of, plan.symbol, "VALID_SETUP", plan.side, ("1h", "15m", "5m"))
    return FloorRunReport("1.0", plan.run_id, plan.as_of, plan.symbol, {}, {}, None, setup, plan, decision,
                          "PLAN_READY")


def old_cap_rejects(order, fill_price, equity):
    """Pre-P5.1 money cap (float expression as it was): real_risk > equity x 1% or > equity_at_submission x 1%."""
    risk = (fill_price - order.stop) if order.side == "LONG" else (order.stop - fill_price)
    real = risk * order.quantity * order.contract_multiplier
    return real > equity * 0.01 or real > order.equity_at_submission * 0.01


def pct(values, p):
    values = sorted(values)
    return round(values[min(len(values) - 1, int(p * len(values)))], 4) if values else None


def dist(values):
    return {"n": len(values), "min": pct(values, 0), "p05": pct(values, .05), "median": pct(values, .5),
            "p95": pct(values, .95), "max": pct(values, 1)} if values else None


def standalone(cohort):
    rows = []
    for n, rec in enumerate(cohort.records):
        plan = cohort.plan(rec, f"sa-{n}")
        account = PaperAccount("1.0", "audit", START, START, START)
        result = evaluate(plan, account, {}, INS[plan.symbol], setup_id=rec.get("setup_id"), at=plan.as_of)
        row = {"symbol": plan.symbol, "period": "discovery" if pd.Timestamp(rec["slot"]) < DISCOVERY_END else "holdout",
               "risk": result.decision.reason}
        if result.decision.status != "APPROVED":
            rows.append(row)
            continue
        index = cohort.five[plan.symbol][0]
        k = int(index.searchsorted(pd.Timestamp(rec["slot"]) + DELAY))
        if k >= len(index):
            rows.append({**row, "fill": "NO_DATA"})
            continue
        broker = PaperBroker(account, INS[plan.symbol], rr_policy=POLICY_V2_F3)
        order = broker.submit_plan(report(plan, result.decision, rec.get("setup_id")), None, plan.as_of)
        bar = cohort.bar(plan.symbol, k)
        broker.process_next_bar(order, bar)
        event = broker.journal[-1] if broker.journal[-1].event_type != "POSITION_OPENED" else broker.journal[-2]
        geo = event.details.get("fill_geometry") or {}
        reason = event.details.get("reason") if event.event_type == "ORDER_REJECTED" else None
        planned_money = Fraction(Decimal(result.record["planned_monetary_risk"]))
        actual_risk = geo.get("actual_risk")
        actual_money = None if actual_risk is None else Fraction(Decimal(actual_risk)) * Fraction(Decimal(repr(order.quantity)))
        rr_ok = geo.get("actual_fill_rr") is not None and Decimal(geo["actual_fill_rr"]) >= Decimal("2.5")
        money_ok = actual_money is not None and actual_money <= planned_money * FACTOR
        new = "FILLED" if order.status == "FILLED" else reason
        old = ("fill_invalid_geometry" if actual_risk is None else "fill_rr_below_minimum" if not rr_ok
               else "old_1pct_money_cap" if old_cap_rejects(order, bar["open"], START) else "FILLED")
        rows.append({**row, "fill": "EVALUATED", "new": new, "old": old, "rr_ok": rr_ok, "money_ok": money_ok,
                     "planned_pct": float(planned_money) / START * 100,
                     "actual_pct": None if actual_money is None else float(actual_money) / START * 100,
                     "ratio": None if actual_money is None else float(actual_money / planned_money)})
    return rows


def summarize_standalone(rows):
    out = {}
    groups = {"all": rows}
    for key in ("period", "symbol"):
        for value in sorted({r[key] for r in rows}):
            groups[f"{key}={value}"] = [r for r in rows if r[key] == value]
    for name, sub in groups.items():
        evaluated = [r for r in sub if r.get("fill") == "EVALUATED"]
        valid = [r for r in evaluated if r["actual_pct"] is not None]
        out[name] = {
            "plans": len(sub), "risk_outcomes": dict(Counter(r["risk"] for r in sub)),
            "no_fill_data": sum(r.get("fill") == "NO_DATA" for r in sub), "fill_evaluated": len(evaluated),
            "old_rule": dict(Counter(r["old"] for r in evaluated)), "new_rule": dict(Counter(r["new"] for r in evaluated)),
            "rr_floor_vs_8_7_disagreements": sum(r["rr_ok"] != r["money_ok"] for r in valid),
            "planned_risk_pct": dist([r["planned_pct"] for r in sub if "planned_pct" in r]),
            "actual_fill_risk_pct_new_filled": dist([r["actual_pct"] for r in evaluated if r["new"] == "FILLED"]),
            "actual_fill_risk_pct_old_filled": dist([r["actual_pct"] for r in evaluated if r["old"] == "FILLED"]),
            "actual_over_planned_ratio_new_filled": dist([r["ratio"] for r in evaluated if r["new"] == "FILLED"]),
        }
    return out


def portfolio(cohort, rule):
    account = PaperAccount("1.0", "audit", START, START, START)
    orders, events, seq = {}, [], 0
    counts, fills, closes = Counter(), Counter(), Counter()
    peak_risk_pct, min_equity, by_symbol = 0.0, START, defaultdict(Counter)

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
                if order.status != "PENDING":
                    continue
                broker = PaperBroker(account, INS[order.symbol], rr_policy=POLICY_V2_F3)
                bar = cohort.bar(order.symbol, k)
                old_reject = rule == "OLD_1PCT" and old_cap_rejects(order, bar["open"], account.equity)
                position = broker.process_next_bar(order, bar)
                if position is not None and old_reject:
                    del account.open_positions[order.symbol]
                    order.status, position = "REJECTED", None
                    fills["old_1pct_money_cap"] += 1
                elif position is None:
                    fills[broker.journal[-1].details.get("reason")] += 1
                else:
                    fills["FILLED"] += 1
                    _, o, h, l, _ = cohort.five[order.symbol]
                    kind_, price, j = exit_of(o, h, l, k, order.side, order.stop, order.target)
                    if j is not None:
                        seq += 1
                        exit_at = cohort.five[order.symbol][0][j] + FIVE
                        heapq.heappush(events, (exit_at, seq, "EXIT", (position, kind_, price)))
                del orders[order.order_id]
            else:
                position, kind_, price = payload
                pnl = ((price - position.entry_price) if position.side == "LONG"
                       else (position.entry_price - price)) * position.quantity
                account.realized_pnl += pnl
                del account.open_positions[position.symbol]
                closes[kind_] += 1
                mark(at)

    for n, rec in enumerate(cohort.records):
        slot = pd.Timestamp(rec["slot"])
        advance(slot)
        mark(slot)
        min_equity = min(min_equity, account.equity)
        plan = cohort.plan(rec, f"pf-{n}")
        result = evaluate(plan, account, orders, INS[plan.symbol], setup_id=rec.get("setup_id"), at=plan.as_of)
        counts[result.decision.reason] += 1
        by_symbol[plan.symbol][result.decision.reason] += 1
        if result.decision.status != "APPROVED":
            continue
        peak_risk_pct = max(peak_risk_pct, float(Decimal(result.record["post_trade_portfolio_risk"])
                                                 / Decimal(result.record["conservative_equity"])) * 100)
        broker = PaperBroker(account, INS[plan.symbol], rr_policy=POLICY_V2_F3)
        broker.orders = orders
        order = broker.submit_plan(report(plan, result.decision, rec.get("setup_id")), None, plan.as_of)
        if order is not None:
            order.risk_policy_version = RISK_POLICY_V2_P5.version  # as reserve_and_submit stamps it (P5.1C)
        index = cohort.five[plan.symbol][0]
        k = int(index.searchsorted(slot + DELAY))
        if order is None or k >= len(index):
            counts["submit_or_fill_data_unavailable"] += 1
            orders.pop(getattr(order, "order_id", None), None)
            continue
        seq += 1
        heapq.heappush(events, (index[k], seq, "FILL", (order, k)))
    advance(pd.Timestamp.max.tz_localize("UTC"))
    return {"risk_decisions": dict(counts), "risk_decisions_by_symbol": {s: dict(c) for s, c in by_symbol.items()},
            "fill_outcomes": dict(fills), "exits": dict(closes), "still_open": len(account.open_positions),
            "final_realized_pnl": round(account.realized_pnl, 2), "min_equity_at_decisions": round(min_equity, 2),
            "peak_post_trade_portfolio_risk_pct": round(peak_risk_pct, 4)}


def main(store_path, p41a_dir, out_path):
    cohort = Cohort(store_path, p41a_dir)
    rows = standalone(cohort)
    out = {"cohort": len(cohort.records), "policy": RISK_POLICY_V2_P5.as_record(),
           "standalone": summarize_standalone(rows),
           "portfolio": {rule: portfolio(cohort, rule) for rule in ("DEC_5_7", "OLD_1PCT")}}
    Path(out_path).write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    return out


if __name__ == "__main__":
    print(json.dumps(main(*sys.argv[1:4]), indent=2, default=str))
