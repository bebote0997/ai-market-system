"""P4.1A lab report: pre-registered protocol enforced in code order.
1) DISCOVERY metrics for every variant; 2) mechanical selection (replay.lab docstring); 3) HOLDOUT for the selected
variant only; 4) full-12-month descriptive numbers. Gross only; no probabilities below the sufficiency thresholds.
Usage: python -m replay.lab_report <lab_out_dir> [store.db]
"""
import json
import pickle
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path
from statistics import mean, median

from replay.compare import MIN_OBSERVATIONS_PER_BAND, MIN_TRADES_PER_SYMBOL
from replay.lab import DISCOVERY_END

ORDER = ("D1", "D2", "D3")
ALL = ("D0",) + ORDER
BANDS = ("BELOW_2", "2_TO_3", "3_TO_4", "4_TO_5", "EQ_5", "ABOVE_5")


def _q(values):
    values = sorted(values)
    if not values:
        return None
    return {"min": round(values[0], 4), "median": round(median(values), 4), "mean": round(mean(values), 4),
            "max": round(values[-1], 4)}


def _session(rec):
    names = set(rec["session"])
    return "LONDON+NEW_YORK" if names == {"LONDON", "NEW_YORK"} else "+".join(sorted(names)) or "NONE"


def outcomes(rows, key):
    """rows: dicts with fill/outcome/r/exit_at; key: label."""
    closed = [r for r in rows if r.get("outcome") in ("TARGET", "STOP")]
    curve = peak = dd = 0.0
    for r in sorted(closed, key=lambda r: r["exit_at"]):
        curve += r["r"]
        peak = max(peak, curve)
        dd = max(dd, peak - curve)
    return {"plans": len(rows), "fill_rejected": sum(r.get("fill") == "FILL_REJECTED" for r in rows),
            "target_first": sum(r.get("outcome") == "TARGET" for r in rows),
            "stop_first": sum(r.get("outcome") == "STOP" for r in rows),
            "unresolved": sum(r.get("outcome") not in ("TARGET", "STOP") for r in rows),
            "gross_pnl_r": round(sum(r["r"] for r in closed), 4) if closed else None,
            "gross_expectancy_r": round(sum(r["r"] for r in closed) / len(closed), 4) if closed else None,
            "max_drawdown_r": round(dd, 4) if closed else None, "label": key}


def variant_metrics(slots, records, variant):
    res = [(rec, rec["variants"][variant]) for rec in records]
    rr = [float(Decimal(v["rr"])) for _, v in res if v.get("rr") is not None]
    tradeable = [(rec, v) for rec, v in res if v["status"] == "TRADEABLE"]
    bands = Counter(v.get("band") for _, v in res if v.get("rr") is not None)
    per_symbol = {}
    for symbol in ("XAUUSD", "EURUSD"):
        sub = [(rec, v) for rec, v in res if rec["symbol"] == symbol]
        per_symbol[symbol] = {"valid_setups": len(sub), "tradeable": sum(v["status"] == "TRADEABLE" for _, v in sub),
                              "rr": _q([float(Decimal(v["rr"])) for _, v in sub if v.get("rr") is not None])}
    def pct(v, key):
        return float(abs(Decimal(v[key]) - Decimal(v["entry"])) / Decimal(v["entry"]) * 100)
    closed_symbol = Counter(rec["symbol"] for rec, v in tradeable if v.get("outcome") in ("TARGET", "STOP"))
    closed_band = Counter(v["band"] for rec, v in tradeable if v.get("outcome") in ("TARGET", "STOP"))
    sufficient = (all(closed_symbol.get(s, 0) >= MIN_TRADES_PER_SYMBOL for s in ("XAUUSD", "EURUSD"))
                  and bool(closed_band) and all(n >= MIN_OBSERVATIONS_PER_BAND for n in closed_band.values()))
    return {
        "decision_slots": slots, "valid_setups": len(res),
        "target_available": sum(v.get("rr") is not None for _, v in res),
        "no_valid_target": sum(v["status"] == "NO_VALID_TARGET" for _, v in res),
        "invalid_geometry": sum(v["status"] == "INVALID_GEOMETRY" for _, v in res),
        "bands": {b: bands.get(b, 0) for b in BANDS},
        "tradeable": len(tradeable), "tradeable_pct_of_valid": round(100 * len(tradeable) / len(res), 2) if res else None,
        "rr": _q(rr), "tradeable_rr": _q([float(Decimal(v["rr"])) for _, v in tradeable]),
        "target_sources_tradeable": dict(Counter(v["source"] for _, v in tradeable)),
        "target_sources_all": dict(Counter(v["source"] for _, v in res if v.get("source"))),
        "per_symbol": per_symbol,
        "per_session_tradeable": dict(Counter(_session(rec) for rec, _ in tradeable)),
        "target_distance_pct": _q([pct(v, "target") for _, v in res if v.get("target")]),
        "stop_distance_pct": _q([pct(v, "stop") for _, v in res if v.get("stop") and v.get("entry")]),
        "risk_check_tradeable": dict(Counter(v.get("risk") for _, v in tradeable)),
        "outcomes_observational": outcomes([v for _, v in tradeable], variant),
        "historical_probability": "ELIGIBLE_FOR_REVIEW (not computed)" if sufficient else "UNAVAILABLE / INSUFFICIENT_EVIDENCE",
    }


def v1_benchmark(records):
    rows = [rec["v1"] for rec in records if "v1" in rec]
    out = outcomes(rows, "V1")
    out["fill_rejection_rate_pct"] = round(100 * out["fill_rejected"] / out["plans"], 2) if out["plans"] else None
    closed = out["target_first"] + out["stop_first"]
    out["target_rate_pct"] = round(100 * out["target_first"] / closed, 2) if closed else None
    return out


def feasible(m):
    tradeable_bands = len([b for b in ("2_TO_3", "3_TO_4", "4_TO_5", "EQ_5") if m["bands"].get(b, 0) > 0])
    return (m["tradeable"] >= 30 and all(m["per_symbol"][s]["tradeable"] >= 10 for s in ("XAUUSD", "EURUSD"))
            and tradeable_bands >= 2), {"tradeable": m["tradeable"], "per_symbol": {s: m["per_symbol"][s]["tradeable"] for s in m["per_symbol"]}, "tradeable_bands": tradeable_bands}


def slot_counts(store_path):
    """Decision slots per period, recomputed with the replay's own slot/freshness rules (no scouts)."""
    from replay.engine import MAX_AGE, decision_slots, load_frames, snapshot_at
    from replay.lab_run import END, START
    from replay.store import open_replay_store, replay_engine
    from runtime.gates import fresh_snapshot
    store = open_replay_store(store_path, readonly=True)
    counts = Counter()
    for symbol in ("XAUUSD", "EURUSD"):
        frames = load_frames(replay_engine(store), symbol)
        for slot in decision_slots(frames, START, END, 15):
            if fresh_snapshot(snapshot_at(frames, slot), symbol, slot, MAX_AGE)[0]:
                counts["discovery" if slot < DISCOVERY_END.to_pydatetime() else "holdout"] += 1
    store.close()
    return dict(counts)


def main(out_dir, store_path=None):
    out = Path(out_dir)
    chunks = [pickle.loads(p.read_bytes()) for p in sorted(out.glob("lab_*.pkl"))]
    records = [r for c in chunks for r in c["records"]]
    disc = [r for r in records if r["slot"] < DISCOVERY_END.to_pydatetime()]
    hold = [r for r in records if r["slot"] >= DISCOVERY_END.to_pydatetime()]
    total_slots = sum(c["slots"] for c in chunks)
    periods = slot_counts(store_path) if store_path else {}
    report = {"protocol": "pre-registered in replay/lab.py; selection on DISCOVERY only",
              "total_decision_slots": total_slots, "slots_by_period": periods,
              "valid_setups": {"discovery": len(disc), "holdout": len(hold)}}
    # 1) DISCOVERY for every variant (D0 baseline included).
    report["discovery"] = {v: variant_metrics(periods.get("discovery"), disc, v) for v in ALL}
    # 2) Mechanical selection.
    selection = []
    chosen = None
    for v in ORDER:
        ok, why = feasible(report["discovery"][v])
        selection.append({"variant": v, "feasible": ok, **why})
        if ok and chosen is None:
            chosen = v
    report["selection"] = {"criteria": "tradeable>=30, >=10 per symbol, >=2 tradeable R:R bands; first in D1,D2,D3",
                           "evaluated": selection, "chosen": chosen}
    # 3) HOLDOUT once, chosen only.
    report["holdout"] = {chosen: variant_metrics(periods.get("holdout"), hold, chosen)} if chosen else "NO FEASIBLE VARIANT — holdout not used"
    # 4) Full 12 months, descriptive only.
    report["full_12_months_descriptive"] = {v: variant_metrics(total_slots, records, v) for v in ALL}
    report["v1_benchmark"] = {"discovery": v1_benchmark(disc), "holdout": v1_benchmark(hold),
                              "full": v1_benchmark(records)}
    (out / "lab_report.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    return report


if __name__ == "__main__":
    r = main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
    print(json.dumps({"selection": r["selection"], "valid_setups": r["valid_setups"]}, indent=1, default=str))
