"""DEC-4.6 fill-geometry audit (offline, evidence only — sets no tolerance).

For every P4.1A plan slot (1,940 V1 plans; identical VALID setups), rebuild the PLANNED geometry from the replay
store without rerunning the scouts:
  V1  planned entry = last closed 5m close <= t (raw float); SL = certified invalidation; TP = entry + 3 x risk.
  V2  fixed 3R (DEC-4.6): entry and SL normalized on the instrument grid (conservative), TP exact => planned 3.0.
Two fill models, frozen SL/TP, actual geometry recomputed at the fill:
  NEXT_BAR     open of the 5m bar starting at t (the P4.1/P4.1A replay model);
  NEXT_CYCLE   open of the 5m bar starting at t + 10 min = the current-cycle bar of the next 15-minute runtime cycle
               (where the runtime and the P1 gate actually evaluate a pending order).
Usage: python -m replay.fill_audit <store.db> <p41a_lab_out> <out.json>
"""
import json
import pickle
import sys
from collections import Counter
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from pathlib import Path
from statistics import median

import pandas as pd

from core.rr_contract import POLICY_V1, POLICY_V2_F3, geometry, normalize
from replay.engine import load_frames, simulate
from replay.lab import DISCOVERY_END
from replay.store import open_replay_store, replay_engine
from runtime.paper_contracts import paper_instruments

FIVE = pd.Timedelta(minutes=5)
MODELS = {"NEXT_BAR": pd.Timedelta(0), "NEXT_CYCLE": pd.Timedelta(minutes=10)}


def planned(policy, side, entry_raw, stop_raw, increment):
    if policy == POLICY_V1:
        risk = entry_raw - stop_raw if side == "LONG" else stop_raw - entry_raw
        target = entry_raw + 3 * risk if side == "LONG" else entry_raw - 3 * risk
        return entry_raw, stop_raw, target
    entry = normalize(entry_raw, increment, ROUND_CEILING if side == "LONG" else ROUND_FLOOR)
    stop = normalize(stop_raw, increment, ROUND_FLOOR if side == "LONG" else ROUND_CEILING)
    risk = abs(entry - stop)
    target = entry + 3 * risk if side == "LONG" else entry - 3 * risk
    return float(entry), float(stop), float(target)


def audit(store_path, p41a_dir):
    records = [r for f in sorted(Path(p41a_dir).glob("lab_*.pkl")) for r in pickle.loads(f.read_bytes())["records"]
               if "v1" in r]
    store = open_replay_store(store_path, readonly=True)
    frames = {s: load_frames(replay_engine(store), s)["5m"] for s in ("XAUUSD", "EURUSD")}
    store.close()
    rows = []
    for rec in records:
        five, side, slot = frames[rec["symbol"]], rec["side"], pd.Timestamp(rec["slot"])
        closed = five.loc[five.index + FIVE <= slot]
        entry_raw, stop_raw = float(closed["Close"].iloc[-1]), float(rec["invalidation"])
        increment = paper_instruments()[rec["symbol"]].price_increment
        for policy in (POLICY_V1, POLICY_V2_F3):
            entry, stop, target = planned(policy, side, entry_raw, stop_raw, increment)
            plan_shape, _ = geometry(side, entry, stop, target)
            for model, delay in MODELS.items():
                start = slot + delay
                bar = five.loc[five.index >= start]
                row = {"symbol": rec["symbol"], "side": side, "slot": slot, "policy": policy, "model": model,
                       "period": "discovery" if slot < DISCOVERY_END else "holdout",
                       "planned_rr": None if plan_shape is None else float(plan_shape.rr)}
                if bar.empty or plan_shape is None:
                    rows.append({**row, "fill": "NO_DATA" if bar.empty else "INVALID_PLAN"})
                    continue
                fill_price = float(bar["Open"].iloc[0])
                risk = abs(entry - stop)
                adverse = (fill_price - entry) if side == "LONG" else (entry - fill_price)
                shape, _ = geometry(side, fill_price, stop, target)
                fill, _, outcome, _, r = simulate(policy, five, bar.index[0].to_pydatetime(), side, stop, target)
                rows.append({**row, "fill": fill, "fill_displacement_r": adverse / risk,
                             "direction": "ADVERSE" if adverse > 0 else "FAVORABLE" if adverse < 0 else "EQUAL",
                             "fill_delay_min": (bar.index[0] - slot).total_seconds() / 60 + 0,
                             "actual_rr": None if shape is None else float(shape.rr),
                             "outcome": outcome, "r": r})
    return rows


def summarize(rows):
    out = {}
    for policy in (POLICY_V1, POLICY_V2_F3):
        for model in MODELS:
            for period in ("all", "discovery", "holdout"):
                sub = [r for r in rows if r["policy"] == policy and r["model"] == model
                       and (period == "all" or r["period"] == period)]
                filled = [r for r in sub if r["fill"] == "FILLED"]
                rejected = [r for r in sub if r["fill"] == "FILL_REJECTED"]
                disp = sorted(r["fill_displacement_r"] for r in sub if "fill_displacement_r" in r)
                actual = [r["actual_rr"] for r in sub if r.get("actual_rr") is not None]
                closed = [r for r in filled if r["outcome"] in ("TARGET", "STOP")]
                q = lambda xs, p: round(xs[min(len(xs) - 1, int(p * len(xs)))], 4) if xs else None
                out[f"{policy}|{model}|{period}"] = {
                    "plans": len(sub), "filled": len(filled), "fill_rejected": len(rejected),
                    "fill_rejection_pct": round(100 * len(rejected) / len(sub), 2) if sub else None,
                    "direction": dict(Counter(r.get("direction") for r in sub)),
                    "rejected_direction": dict(Counter(r.get("direction") for r in rejected)),
                    "displacement_r": {"p05": q(disp, .05), "p25": q(disp, .25), "median": q(disp, .5),
                                       "p75": q(disp, .75), "p95": q(disp, .95)} if disp else None,
                    "abs_displacement_r_median": round(median(abs(x) for x in disp), 4) if disp else None,
                    "actual_rr_below_3_pct": round(100 * sum(a < 3 for a in actual) / len(actual), 2) if actual else None,
                    "actual_rr_at_or_above_3_pct": round(100 * sum(a >= 3 for a in actual) / len(actual), 2) if actual else None,
                    "actual_rr_median": round(median(actual), 4) if actual else None,
                    "planned_rr_values": dict(Counter(round(r["planned_rr"], 9) for r in sub if r["planned_rr"] is not None)),
                    "outcomes": dict(Counter(r["outcome"] for r in filled)),
                    "gross_r": round(sum(r["r"] for r in closed), 4) if closed else None,
                    "gross_expectancy_r": round(sum(r["r"] for r in closed) / len(closed), 4) if closed else None,
                    "net": "UNAVAILABLE (no cost model)"}
    return out


if __name__ == "__main__":
    rows = audit(sys.argv[1], sys.argv[2])
    report = {"rows": len(rows), "summary": summarize(rows)}
    Path(sys.argv[3]).write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print("written", len(rows))
