"""V2 Phase 8 / P8.4 R4 (DEC-8.8): visibility of provider REVISIONS of already-committed bars, and the pre-DEMO gate.

The certified rule is unchanged: the first committed bar stays authoritative and is never rewritten; nothing here
moves, closes or opens anything. Observability only:
- ``classify``: for each revised bar of a cycle, compare the presented bar with the committed one. A revision is
  MATERIAL when the presented bar crosses an open position's SL or TP (open beyond, or high/low touch) that the
  committed bar did not cross, for a position opened before the bar. Only 5m bars feed position management, so 1h/15m
  revisions are recorded as non-material.
- ``journal``: one ``EVIDENCE_REVISION`` row per REVISION anomaly (its ``anomaly_id`` in the evidence database is the
  identity and the review key), deduplicated across cycles/restarts, plus a ``evidence_revisions`` system_state
  summary for health views. A revision whose anomaly cannot be identified is not recorded, so it stays UNCLASSIFIED.
- ``record_review``: an Owner decision (``EVIDENCE_REVISION_REVIEWED``) for one anomaly. Decisions are append-only
  journal rows (history is never deleted or rewritten); the EFFECTIVE decision of an anomaly is its latest one.
  P8.4G (DEC-8.12) decisions:
  - ``ACCEPTED_FIRST_COMMITTED`` (FINAL): the Owner accepts that the first committed bar stays authoritative. It
    resolves the anomaly for the gate. Nothing in evidence or PAPER state changes.
  - ``ESCALATED`` (NOT final): the anomaly needs further investigation. While it is the effective decision the gate
    is BLOCKED (material or not). It is resolved only by a later explicit final decision; there is no in-system way
    to change a committed bar, so an escalation that concludes the committed bar was wrong keeps the gate BLOCKED
    until a separately authorized procedure exists.
  A later ESCALATED after a final decision re-opens the anomaly (the most conservative reading); earlier rows remain.
- ``demo_gate``: READ-ONLY; the evidence database is REQUIRED. CLEAR only with full coverage: every REVISION anomaly
  has a classification by ``anomaly_id``, every classification refers to an existing anomaly, no material one lacks a
  final decision and no anomaly is effectively ESCALATED. Otherwise BLOCKED (also when the evidence database is
  missing or unreadable). Non-material anomalies without any decision are warnings.
"""
from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import sqlite3
import sys

EVENT = "EVIDENCE_REVISION"
REVIEW_EVENT = "EVIDENCE_REVISION_REVIEWED"
SOURCE = "revision_review"
FINAL_DECISIONS = ("ACCEPTED_FIRST_COMMITTED",)
OPEN_DECISIONS = ("ESCALATED",)
DECISIONS = FINAL_DECISIONS + OPEN_DECISIONS


def _ohlc(bar):
    return {k: float(bar[k]) for k in ("open", "high", "low", "close")}


def _crosses(side, ohlc, level, kind):
    """Does the bar reach ``level`` (SL or TP) of a ``side`` position (open beyond or high/low touch)?"""
    stop_like = (kind == "SL") == (side == "LONG")  # LONG SL and SHORT TP lie below the price
    return (ohlc["low"] <= level or ohlc["open"] <= level) if stop_like else (ohlc["high"] >= level or ohlc["open"] >= level)


def classify(symbol, snapshot, ingest_results, committed_bars, positions, anomaly_ids):
    """Records for every revision reported by this cycle's ingestion.

    ``ingest_results``: {timeframe: IngestResult}; ``committed_bars``: {(timeframe, bar_start): bar with OHLC};
    ``positions``: open positions (PaperPosition) of ``symbol``; ``anomaly_ids``: {(timeframe, bar_start): anomaly_id
    of the REVISION anomaly the evidence database recorded for the presented content}. A revision without an
    identified anomaly is skipped (never guessed): the gate then reports that anomaly as UNCLASSIFIED."""
    records = []
    for timeframe, result in sorted((ingest_results or {}).items()):
        for bar_start in getattr(result, "revisions", ()) or ():
            anomaly_id = anomaly_ids.get((timeframe, bar_start))
            if anomaly_id is None:
                continue
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
            records.append({"anomaly_id": anomaly_id, "symbol": symbol, "timeframe": timeframe,
                            "bar_start": bar_start, "committed": committed, "presented": presented,
                            "material": bool(affected), "affected": affected,
                            "rule": "FIRST_COMMITTED_BAR_AUTHORITATIVE (unchanged)", "review_key": anomaly_id})
    return records


def _effective_decisions(db):
    """{review_key: latest decision}, from the append-only review rows in journal order."""
    effective = {}
    for (payload,) in db.execute("SELECT payload FROM journal WHERE event_type=? ORDER BY id", (REVIEW_EVENT,)):
        row = json.loads(payload)
        effective[row.get("review_key")] = row.get("decision")
    return effective


def _summary(db):
    rows = [json.loads(r[0]) for r in db.execute("SELECT payload FROM journal WHERE event_type=?", (EVENT,))]
    effective = _effective_decisions(db)
    unresolved = [r for r in rows if effective.get(r.get("review_key")) not in FINAL_DECISIONS]
    return {"recorded": len(rows), "unreviewed": len(unresolved),
            "material_unreviewed": sum(1 for r in unresolved if r.get("material")),
            "escalated_unresolved": sum(1 for r in unresolved if effective.get(r.get("review_key")) in OPEN_DECISIONS)}


def journal(store, records, *, at, run_id):
    """Write each new anomaly once (deduplicated by anomaly_id) and refresh the health summary. Returns rows written."""
    written = 0
    with store.transaction():
        for record in records:
            exists = store.db.execute(
                "SELECT 1 FROM journal WHERE event_type=? AND json_extract(payload,'$.anomaly_id')=?",
                (EVENT, record["anomaly_id"])).fetchone()
            if exists is None:
                store._event(at, run_id, record["symbol"], SOURCE, EVENT,
                             "WARNING" if record["material"] else "INFO", record)
                written += 1
        store.set_state("evidence_revisions", json.dumps(_summary(store.db), sort_keys=True))
    return written


def record_review(store, key, *, decision, reviewer, note="", at=None):
    """Owner decision for one recorded anomaly (``key`` = its anomaly_id); never changes evidence or PAPER state.
    Appends an auditable row (decision, whether it is final, the previous effective decision, reviewer, note)."""
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {DECISIONS}")
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise ValueError("reviewer required")
    at = at or datetime.now(timezone.utc)
    with store.transaction():
        row = store.db.execute("SELECT symbol FROM journal WHERE event_type=? AND json_extract(payload,'$.anomaly_id')=?",
                               (EVENT, key)).fetchone()
        if row is None:
            raise ValueError("unknown review_key")
        previous = _effective_decisions(store.db).get(key)
        store._event(at, None, row[0], SOURCE, REVIEW_EVENT, "INFO" if decision in FINAL_DECISIONS else "WARNING",
                     {"review_key": key, "anomaly_id": key, "decision": decision,
                      "final": decision in FINAL_DECISIONS, "previous_decision": previous, "reviewer": reviewer,
                      "note": note})
        store.set_state("evidence_revisions", json.dumps(_summary(store.db), sort_keys=True))


def _ro(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)


def demo_gate(trading_db, evidence_db):
    """READ-ONLY pre-DEMO gate. CLEAR only with full anomaly coverage by ``anomaly_id``, a final decision for every
    material anomaly and no effectively ESCALATED anomaly."""
    blockers = []
    anomalies = {}
    if evidence_db is None or not Path(evidence_db).is_file():
        blockers.append("EVIDENCE_DB_MISSING")
    else:
        try:
            ev = _ro(evidence_db)
            try:
                for a in ev.execute("SELECT anomaly_id, symbol, timeframe, bar_start FROM evidence_anomalies "
                                    "WHERE kind='REVISION' ORDER BY symbol, timeframe, bar_start, anomaly_id"):
                    anomalies[a[0]] = {"anomaly_id": a[0], "symbol": a[1], "timeframe": a[2], "bar_start": a[3]}
            finally:
                ev.close()
        except sqlite3.Error:
            blockers.append("EVIDENCE_DB_UNREADABLE")
    db = _ro(trading_db)
    try:
        rows = [json.loads(r[0]) for r in db.execute("SELECT payload FROM journal WHERE event_type=? ORDER BY id",
                                                     (EVENT,))]
        effective = _effective_decisions(db)
    finally:
        db.close()
    classified = {}
    for row in rows:  # first record per anomaly; legacy rows without anomaly_id never count as coverage
        if row.get("anomaly_id"):
            classified.setdefault(row["anomaly_id"], row)
    unclassified = [a for key, a in anomalies.items() if key not in classified]
    orphans = [] if blockers else [r for key, r in classified.items() if key not in anomalies]
    unreviewed = [r for key, r in classified.items() if key not in effective]
    escalated = [dict(r, decision=effective[key]) for key, r in classified.items()
                 if effective.get(key) in OPEN_DECISIONS]
    material = [r for r in unreviewed if r["material"]]
    if unclassified:
        blockers.append("UNCLASSIFIED")
    if orphans:
        blockers.append("CLASSIFICATION_WITHOUT_ANOMALY")
    if material:
        blockers.append("MATERIAL_UNREVIEWED")
    if escalated:
        blockers.append("ESCALATED_UNRESOLVED")
    return {"status": "BLOCKED" if blockers else "CLEAR", "blockers": blockers, "anomalies": len(anomalies),
            "classified": len(classified), "material_unreviewed": material, "escalated_unresolved": escalated,
            "unclassified": unclassified, "orphan_classifications": orphans,
            "non_material_unreviewed": [r for r in unreviewed if not r["material"]],
            "resolved": sum(1 for key in classified if effective.get(key) in FINAL_DECISIONS),
            "recorded": len(rows), "reviewed": len(effective)}


def main(argv=None):
    parser = argparse.ArgumentParser(description="REVISION review gate (PAPER).")
    sub = parser.add_subparsers(dest="command", required=True)
    gate = sub.add_parser("gate")
    gate.add_argument("--trading-db", required=True)
    gate.add_argument("--evidence-db", required=True)
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
