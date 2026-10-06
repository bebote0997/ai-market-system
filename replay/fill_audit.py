"""DEC-4.6/4.7 fill-geometry audit (offline evidence; never used to tune the owner's execution floor).

For every P4.1A plan slot (1,940 V1 plans; identical VALID setups), rebuild the PLANNED geometry from the replay store
without rerunning the scouts:
  V1  planned entry = last closed 5m close <= t (raw float); SL = certified invalidation; TP = entry + 3 x risk.
  V2  fixed 3R (DEC-4.6): entry and SL normalized on the instrument grid (conservative), TP exact => planned 3.00R.
Two fill models, frozen SL/TP, actual geometry recomputed at the fill:
  NEXT_BAR     open of the 5m bar starting at t (the P4.1/P4.1A replay model);
  NEXT_CYCLE   open of the 5m bar starting at t + 10 min = the current-cycle bar of the next 15-minute runtime cycle.
Fill rules compared on the SAME rows: V1 strict (frozen V1 float expression), V2 STRICT_3 (actual >= 3) and
V2 DEC_4_7 (actual >= 2.50). Outcomes (gross R from the actual fill, TradeManager precedence) are observational.
Usage: python -m replay.fill_audit <store.db> <p41a_lab_out> <out.json>
"""
import json
import pickle
import sys
from collections import Counter
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from pathlib import Path
from statistics import median

import numpy as np
import pandas as pd

from core.rr_contract import geometry, normalize
from replay.lab import DISCOVERY_END
from replay.store import open_replay_store, replay_engine
from replay.engine import load_frames
from runtime.paper_contracts import paper_instruments

FIVE = pd.Timedelta(minutes=5)
MODELS = {"NEXT_BAR": pd.Timedelta(0), "NEXT_CYCLE": pd.Timedelta(minutes=10)}
RULES = {"V1_STRICT_3": ("V1", None), "V2_STRICT_3": ("V2", Decimal(3)), "V2_DEC_4_7": ("V2", Decimal("2.5"))}


def planned(kind, side, entry_raw, stop_raw, increment):
    if kind == "V1":
        risk = entry_raw - stop_raw if side == "LONG" else stop_raw - entry_raw
        return entry_raw, stop_raw, (entry_raw + 3 * risk if side == "LONG" else entry_raw - 3 * risk)
    entry = normalize(entry_raw, increment, ROUND_CEILING if side == "LONG" else ROUND_FLOOR)
    stop = normalize(stop_raw, increment, ROUND_FLOOR if side == "LONG" else ROUND_CEILING)
    risk = abs(entry - stop)
    target = entry + 3 * risk if side == "LONG" else entry - 3 * risk
    return float(entry), float(stop), float(target)


def walk(o, h, l, index, side, stop, target):
    """Vectorized TradeManager precedence from bar ``index`` + 1 on: gap stop, gap target, stop, target."""
    o, h, l = o[index + 1:], h[index + 1:], l[index + 1:]
    if side == "LONG":
        gs, gt, sh, th = o <= stop, o >= target, l <= stop, h >= target
    else:
        gs, gt, sh, th = o >= stop, o <= target, h >= stop, l <= target
    hit = gs | gt | sh | th
    if not hit.any():
        return "OPEN", None
    k = int(np.argmax(hit))
    if gs[k]:
        return "STOP", float(o[k])
    if gt[k]:
        return "TARGET", float(o[k])
    return ("STOP", stop) if sh[k] else ("TARGET", target)


def audit(store_path, p41a_dir):
    records = [r for f in sorted(Path(p41a_dir).glob("lab_*.pkl")) for r in pickle.loads(f.read_bytes())["records"]
               if "v1" in r]
    store = open_replay_store(store_path, readonly=True)
    frames = {s: load_frames(replay_engine(store), s)["5m"] for s in ("XAUUSD", "EURUSD")}
    store.close()
    arrays = {s: (f.index, f["Open"].to_numpy(), f["High"].to_numpy(), f["Low"].to_numpy()) for s, f in frames.items()}
    rows = []
    for rec in records:
        index, o, h, l = arrays[rec["symbol"]]
        five, side, slot = frames[rec["symbol"]], rec["side"], pd.Timestamp(rec["slot"])
        entry_raw = float(five.loc[five.index + FIVE <= slot]["Close"].iloc[-1])
        stop_raw = float(rec["invalidation"])
        increment = paper_instruments()[rec["symbol"]].price_increment
        for kind in ("V1", "V2"):
            entry, stop, target = planned(kind, side, entry_raw, stop_raw, increment)
            plan_shape, _ = geometry(side, entry, stop, target)
            for model, delay in MODELS.items():
                position = int(index.searchsorted(slot + delay))
                row = {"symbol": rec["symbol"], "side": side, "slot": slot, "kind": kind, "model": model,
                       "session": "+".join(rec["session"]),
                       "period": "discovery" if slot < DISCOVERY_END else "holdout",
                       "planned_rr": None if plan_shape is None else float(plan_shape.rr)}
                if position >= len(index) or plan_shape is None:
                    rows.append({**row, "status": "NO_DATA"})
                    continue
                fill_price = float(o[position])
                adverse = (fill_price - entry) if side == "LONG" else (entry - fill_price)
                shape, _ = geometry(side, fill_price, stop, target)
                outcome, exit_price = (None, None) if shape is None else walk(o, h, l, position, side, stop, target)
                risk = abs(fill_price - stop)
                r = None if exit_price is None else ((exit_price - fill_price) if side == "LONG"
                                                     else (fill_price - exit_price)) / risk
                rows.append({**row, "status": "GEOMETRY" if shape is not None else "GAP_THROUGH_SL_OR_TP",
                             "fill_price": fill_price, "displacement_r": adverse / abs(entry - stop),
                             "direction": "ADVERSE" if adverse > 0 else "FAVORABLE" if adverse < 0 else "EQUAL",
                             "actual_rr": None if shape is None else Decimal(shape.rr),
                             "v1_float_reject": (((target - fill_price) if side == "LONG" else (fill_price - target))
                                                 / ((fill_price - stop) if side == "LONG" else (stop - fill_price)) < 3)
                             if shape is not None else True,
                             "outcome": outcome, "r": r, "exit_at": None})
    return rows


def accepted(row, rule):
    kind, floor = RULES[rule]
    if row["kind"] != kind or row["status"] != "GEOMETRY":
        return False
    return (not row["v1_float_reject"]) if floor is None else row["actual_rr"] >= floor


def summarize(rows):
    out = {}
    for rule, (kind, _) in RULES.items():
        for model in MODELS:
            for period in ("all", "discovery", "holdout"):
                sub = [r for r in rows if r["kind"] == kind and r["model"] == model and r["status"] != "NO_DATA"
                       and (period == "all" or r["period"] == period)]
                fills = [r for r in sub if accepted(r, rule)]
                rejected = [r for r in sub if not accepted(r, rule)]
                rr = sorted(float(r["actual_rr"]) for r in sub if r["actual_rr"] is not None)
                q = lambda xs, p: round(xs[min(len(xs) - 1, int(p * len(xs)))], 4) if xs else None
                closed = [r for r in fills if r["outcome"] in ("TARGET", "STOP")]
                buckets = Counter("<2.5" if r["actual_rr"] < Decimal("2.5") else "2.5-3" if r["actual_rr"] < 3 else ">=3"
                                  for r in sub if r["actual_rr"] is not None)
                out[f"{rule}|{model}|{period}"] = {
                    "plans": len(sub), "fills": len(fills), "rejections": len(rejected),
                    "rejection_pct": round(100 * len(rejected) / len(sub), 2) if sub else None,
                    "favorable_fills": sum(r["direction"] == "FAVORABLE" for r in fills),
                    "equal_fills": sum(r["direction"] == "EQUAL" for r in fills),
                    "adverse_accepted": sum(r["direction"] == "ADVERSE" for r in fills),
                    "rejected_direction": dict(Counter(r["direction"] for r in rejected)),
                    "gap_through_sl_or_tp": sum(r["status"] == "GAP_THROUGH_SL_OR_TP" for r in sub),
                    "actual_rr_buckets": dict(buckets),
                    "actual_rr": {"p01": q(rr, .01), "p05": q(rr, .05), "p25": q(rr, .25), "median": q(rr, .5),
                                  "p75": q(rr, .75), "p95": q(rr, .95)} if rr else None,
                    "planned_rr_values": dict(Counter(round(r["planned_rr"], 9) for r in sub)),
                    "outcomes": dict(Counter(r["outcome"] for r in fills)),
                    "gross_r": round(sum(r["r"] for r in closed), 4) if closed else None,
                    "gross_expectancy_r": round(sum(r["r"] for r in closed) / len(closed), 4) if closed else None,
                    "net": "UNAVAILABLE (no cost model)"}
    return out


if __name__ == "__main__":
    rows = audit(sys.argv[1], sys.argv[2])
    report = {"rows": len(rows), "summary": summarize(rows)}
    Path(sys.argv[3]).write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print("written", len(rows))
