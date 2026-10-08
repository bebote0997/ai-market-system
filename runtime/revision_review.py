"""V2 Phase 8 / P8.4 R4 (DEC-8.8): visibility of provider REVISIONS of already-committed bars, and the pre-DEMO gate.

The certified rule is unchanged: the first committed bar stays authoritative and is never rewritten; nothing here
moves, closes or opens anything. Observability only:
- ``classify``: for each revised bar of a cycle, compare the presented bar with the committed one. A revision is
  MATERIAL when the presented bar crosses an open position's SL or TP (open beyond, or high/low touch) that the
  committed bar did not cross, for a position opened before the bar. Only 5m bars feed position management, so 1h/15m
  revisions are recorded as non-material.
- ``journal``: one ``EVIDENCE_REVISION`` row per (symbol, timeframe, bar_start, presented OHLC) in the trading journal,
  deduplicated across cycles/restarts, plus a ``evidence_revisions`` system_state summary for health views.
- ``record_review``: an Owner decision (``EVIDENCE_REVISION_REVIEWED``) for one revision.
- ``demo_gate``: READ-ONLY. BLOCKED while any material revision is unreviewed, or any REVISION anomaly of the evidence
  database has no classification in the trading journal (UNCLASSIFIED). Non-material unreviewed ones are warnings.
"""
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

EVENT = "EVIDENCE_REVISION"
REVIEW_EVENT = "EVIDENCE_REVISION_REVIEWED"
SOURCE = "revision_review"
DECISIONS = ("ACCEPTED_FIRST_COMMITTED", "ESCALATED")


def _ohlc(bar):
    return {k: float(bar[k]) for k in ("open", "high", "low", "close")}


def _crosses(side, ohlc, level, kind):
    """Does the bar reach ``level`` (SL or TP) of a ``side`` position (open beyond or high/low touch)?"""
    stop_like = (kind == "SL") == (side == "LONG")  # LONG SL and SHORT TP lie below the price
    return (ohlc["low"] <= level or ohlc["open"] <= level) if stop_like else (ohlc["high"] >= level or ohlc["open"] >= level)


def review_key(symbol, timeframe, bar_start, presented):
    canonical = json.dumps({"symbol": symbol, "timeframe": timeframe, "bar_start": bar_start, "presented": presented},
                           sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def classify(symbol, snapshot, ingest_results, committed_bars, positions):
    """Records for every revision reported by this cycle's ingestion.

    ``ingest_results``: {timeframe: IngestResult}; ``committed_bars``: {(timeframe, bar_start): bar with OHLC};
    ``positions``: open positions (PaperPosition) of ``symbol``."""
    records = []
    for timeframe, result in sorted((ingest_results or {}).items()):
        for bar_start in getattr(result, "revisions", ()) or ():
            frame = snapshot.get(timeframe)
            start = datetime.fromisoformat(bar_start)
            try:
                row = frame.loc[frame.index == start].iloc[0]
            except (AttributeError, IndexError, KeyError):
                row = None
            presented = None if row is None else {"open": float(row["Open"]), "high": float(row["High"]),
                                                  "low": float(row["Low"]), "close": float(row["Close"])}
            committed = committed_bars.get((timeframe, bar_start))
            committed = None if committed is None else _ohlc(committed)
            affected = []
            if timeframe == "5m" and presented is not None and committed is not None:
                for position in positions:
                    if position.symbol != symbol or position.opened_at >= start:
                        continue
                    for kind, level in (("SL", position.stop), ("TP", position.target)):
                        if _crosses(position.side, presented, level, kind) and not _crosses(position.side, committed,
                                                                                            level, kind):
                            affected.append({"position_id": position.position_id, "level": kind, "price": level})
            records.append({"symbol": symbol, "timeframe": timeframe, "bar_start": bar_start, "committed": committed,
                            "presented": presented, "material": bool(affected), "affected": affected,
                            "rule": "FIRST_COMMITTED_BAR_AUTHORITATIVE (unchanged)",
                            "review_key": review_key(symbol, timeframe, bar_start, presented)})
    return records


def _summary(db):
    rows = [json.loads(r[0]) for r in db.execute("SELECT payload FROM journal WHERE event_type=?", (EVENT,))]
    reviewed = {json.loads(r[0]).get("review_key") for r in db.execute(
        "SELECT payload FROM journal WHERE event_type=?", (REVIEW_EVENT,))}
    unreviewed = [r for r in rows if r.get("review_key") not in reviewed]
    return {"recorded": len(rows), "unreviewed": len(unreviewed),
            "material_unreviewed": sum(1 for r in unreviewed if r.get("material"))}


def journal(store, records, *, at, run_id):
    """Write each new revision once (deduplicated by review_key) and refresh the health summary. Returns rows written."""
    written = 0
    with store.transaction():
        for record in records:
            exists = store.db.execute(
                "SELECT 1 FROM journal WHERE event_type=? AND json_extract(payload,'$.review_key')=?",
                (EVENT, record["review_key"])).fetchone()
            if exists is None:
                store._event(at, run_id, record["symbol"], SOURCE, EVENT,
                             "WARNING" if record["material"] else "INFO", record)
                written += 1
        store.set_state("evidence_revisions", json.dumps(_summary(store.db), sort_keys=True))
    return written


def record_review(store, key, *, decision, reviewer, note="", at=None):
    """Owner decision for one recorded revision (never changes evidence or PAPER state)."""
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {DECISIONS}")
    at = at or datetime.now(timezone.utc)
    with store.transaction():
        row = store.db.execute("SELECT symbol FROM journal WHERE event_type=? AND json_extract(payload,'$.review_key')=?",
                               (EVENT, key)).fetchone()
        if row is None:
            raise ValueError("unknown review_key")
        store._event(at, None, row[0], SOURCE, REVIEW_EVENT, "INFO",
                     {"review_key": key, "decision": decision, "reviewer": reviewer, "note": note})
        store.set_state("evidence_revisions", json.dumps(_summary(store.db), sort_keys=True))


def _ro(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)


def demo_gate(trading_db, evidence_db=None):
    """READ-ONLY pre-DEMO gate. CLEAR only with no material unreviewed and no unclassified revision."""
    db = _ro(trading_db)
    try:
        rows = [json.loads(r[0]) for r in db.execute("SELECT payload FROM journal WHERE event_type=?", (EVENT,))]
        reviewed = {json.loads(r[0]).get("review_key") for r in db.execute(
            "SELECT payload FROM journal WHERE event_type=?", (REVIEW_EVENT,))}
    finally:
        db.close()
    classified = {(r["symbol"], r["timeframe"], r["bar_start"]) for r in rows}
    unclassified = []
    if evidence_db is not None:
        ev = _ro(evidence_db)
        try:
            anomalies = ev.execute("SELECT symbol, timeframe, bar_start, anomaly_id FROM evidence_anomalies "
                                   "WHERE kind='REVISION' ORDER BY symbol, bar_start").fetchall()
        finally:
            ev.close()
        unclassified = [{"symbol": a[0], "timeframe": a[1], "bar_start": a[2], "anomaly_id": a[3]}
                        for a in anomalies if (a[0], a[1], a[2]) not in classified]
    unreviewed = [r for r in rows if r["review_key"] not in reviewed]
    material = [r for r in unreviewed if r["material"]]
    status = "BLOCKED" if material or unclassified else "CLEAR"
    return {"status": status, "material_unreviewed": material, "unclassified": unclassified,
            "non_material_unreviewed": [r for r in unreviewed if not r["material"]], "recorded": len(rows),
            "reviewed": len(reviewed)}


def main(argv=None):
    parser = argparse.ArgumentParser(description="REVISION review gate (PAPER).")
    sub = parser.add_subparsers(dest="command", required=True)
    gate = sub.add_parser("gate")
    gate.add_argument("--trading-db", required=True)
    gate.add_argument("--evidence-db")
    review = sub.add_parser("review")
    review.add_argument("--trading-db", required=True)
    review.add_argument("--key", required=True)
    review.add_argument("--decision", required=True, choices=DECISIONS)
    review.add_argument("--reviewer", required=True)
    review.add_argument("--note", default="")
    args = parser.parse_args(argv)
    if args.command == "gate":
        result = demo_gate(args.trading_db, args.evidence_db)
        print(json.dumps(result, indent=2, default=str))
        return 0 if result["status"] == "CLEAR" else 3
    from storage.database import Store
    store = Store(args.trading_db)
    try:
        record_review(store, args.key, decision=args.decision, reviewer=args.reviewer, note=args.note)
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
