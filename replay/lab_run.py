"""P4.1A lab runner (offline): one production floor run per slot (scouts shared only within the slot), then D0
(production Policy D, unchanged), the pre-registered lab variants and the V1 benchmark on the identical setup.

No cross-slot caching: each scout report carries the slot's run_id/timestamp, which the Setup Validator's lineage
check requires, so outputs cannot be reused across slots without altering them. The 15-minute runtime cadence is
reached by parallel computation instead. Usage: python -m replay.lab_run <store.db> <out_dir> [cadence_minutes]
"""
import logging
import pickle
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agents.target_planner import plan_policy_d
from core.contracts import TradePlan
from core.rr_contract import POLICY_V1, POLICY_V2_D
from data.macro_news import NoMacroDataProvider
from floor.orchestrator import run as run_floor
from replay import lab
from replay.engine import EQUITY, MAX_AGE, _shared_scouts, decision_slots, load_frames, simulate, snapshot_at
from replay.store import open_replay_store, replay_engine
from riesgo import crear_configuracion_riesgo_phase4, crear_configuracion_riesgo_v2, evaluar_trade_plan
from runtime.gates import fresh_snapshot
from runtime.paper_contracts import paper_instruments
from runtime.scheduler import session_names

START = datetime(2025, 11, 3, tzinfo=timezone.utc)
END = datetime(2026, 10, 5, tzinfo=timezone.utc)


def chunk(args):
    store_path, symbol, start, end, cadence = args
    logging.disable(logging.CRITICAL)
    store = open_replay_store(store_path, readonly=True)
    frames = load_frames(replay_engine(store), symbol)
    store.close()
    instrument, macro = paper_instruments()[symbol], NoMacroDataProvider()
    v1_config, p4_config = crear_configuracion_riesgo_v2(), crear_configuracion_riesgo_phase4()
    slots, records = 0, []
    structure, liquidity = _shared_scouts()
    with structure, liquidity:
        for slot in decision_slots(frames, start, end, cadence):
            snapshot = snapshot_at(frames, slot)
            if not fresh_snapshot(snapshot, symbol, slot, MAX_AGE)[0]:
                continue
            slots += 1
            run_id = f"lab:{symbol}:{slot.isoformat()}"
            report = run_floor(snapshot, slot, symbol, macro, instrument, v1_config, equity=EQUITY, run_id=run_id)
            setup = report.setup_assessment
            if setup.status != "VALID_SETUP":
                continue
            rec = {"symbol": symbol, "slot": slot, "session": tuple(session_names(slot)), "side": setup.side,
                   "setup_id": (setup.explanation or {}).get("setup_id"), "invalidation": setup.invalidation,
                   "v1_status": report.final_status, "variants": {}}
            plan = report.trade_plan
            if report.final_status == "PLAN_READY":
                rec["v1"] = dict(zip(("fill", "fill_price", "outcome", "exit_at", "r"),
                                     simulate(POLICY_V1, frames["5m"], slot, plan.side, plan.stop, plan.target)))
            _, d0 = plan_policy_d(setup, {"data": snapshot["5m"]}, symbol, run_id, slot, instrument)
            rec["variants"]["D0"] = {"status": "TRADEABLE" if d0["status"] == "PLAN_READY" else d0["status"],
                                     "rr": d0.get("gross_rr"), "band": d0.get("band"), "entry": d0.get("entry"),
                                     "stop": d0.get("stop"),
                                     "target": (d0.get("selected") or {}).get("target_price"),
                                     "source": (d0.get("selected") or {}).get("source")}
            for variant in lab.VARIANTS:
                res = lab.evaluate(variant, setup, snapshot, slot, instrument)
                if res["status"] == "TRADEABLE":
                    candidate = TradePlan("1.0", symbol, setup.side, "5m", float(res["entry"]), float(res["stop"]),
                                          float(res["target"]), float(res["rr"]), invalidation=res["stop"],
                                          run_id=run_id, as_of=slot, policy_version=POLICY_V2_D)
                    res["risk"] = evaluar_trade_plan(candidate, EQUITY, instrument, p4_config).status
                    res.update(zip(("fill", "fill_price", "outcome", "exit_at", "r"),
                                   simulate(POLICY_V2_D, frames["5m"], slot, setup.side, candidate.stop, candidate.target)))
                rec["variants"][variant] = res
            records.append(rec)
    return {"symbol": symbol, "start": start, "end": end, "slots": slots, "records": records}


def main(store_path, out_dir, cadence=15, chunks=28, workers=14):
    """Resumable: each chunk is saved atomically the moment it completes (any order); chunks whose file already
    exists are skipped. Per-slot evaluation is independent, so chunking never changes results."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    span = (END - START) / chunks
    jobs = [(index, (store_path, s, START + i * span, START + (i + 1) * span - timedelta(seconds=1), cadence))
            for index, (s, i) in enumerate((s, i) for s in ("XAUUSD", "EURUSD") for i in range(chunks))]
    pending = [(index, job) for index, job in jobs if not (out / f"lab_{index:02d}.pkl").exists()]
    print("chunks total", len(jobs), "already saved", len(jobs) - len(pending), "to run", len(pending), flush=True)
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(chunk, job): index for index, job in pending}
        for future in as_completed(futures):
            index = futures[future]
            result = future.result()
            temporary = out / f"lab_{index:02d}.pkl.tmp"
            temporary.write_bytes(pickle.dumps(result))
            temporary.replace(out / f"lab_{index:02d}.pkl")  # atomic: a crash never leaves a partial result
            print("chunk", index, result["symbol"], result["slots"], len(result["records"]), round(time.time() - t0),
                  flush=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 15)
