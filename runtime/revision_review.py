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
- LOW-1 (V2 P8.6 design section 4, DEC-8.21): a review row counts only if it is VALID: ``source`` is this module,
  ``decision`` is known, ``review_key == anomaly_id`` of a recorded ``EVIDENCE_REVISION``, ``reviewer`` is non-blank,
  ``final`` matches the decision and ``previous_decision`` equals the literal decision of the immediately preceding
  review row for the same anomaly (``null`` for the first). The latest row decides; if it is invalid the anomaly is
  ``INVALID_DECISION`` and the gate is BLOCKED (``INVALID_REVIEW_RECORD``) with no fallback to an earlier row.
  Remediation is append-only: a new valid ``record_review`` row. Invalid rows are never rewritten or deleted.
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
INVALID_DECISION = "INVALID_DECISION"


def _ohlc(bar):
    return {k: float(bar[k]) for k in ("open", "high", "low", "close")}


def _crosses(side, ohlc, level, kind):
    """Does the bar reach ``level`` (SL or TP) of a ``side`` position (open beyond or high/low touch)?"""
    stop_like = (kind == "SL") == (side == "LONG")  # LONG SL and SHORT TP lie below the price
    return (ohlc["low"] <= level or ohlc["open"] <= level) if stop_like else (ohlc["high"] >= level or ohlc["open"] >= level)


def classify(symbol, snapshot, ingest_results, committed_bars, positions, anomaly_ids, *, in_scope=True):
    """Records for every revision reported by this cycle's ingestion.

    ``ingest_results``: {timeframe: IngestResult}; ``committed_bars``: {(timeframe, bar_start): bar with OHLC};
    ``positions``: open positions (PaperPosition) of ``symbol``; ``anomaly_ids``: {(timeframe, bar_start): anomaly_id
    of the REVISION anomaly the evidence database recorded for the presented content}. A revision without an
    identified anomaly is skipped (never guessed): the gate then reports that anomaly as UNCLASSIFIED.
    P1-B (design 1.6): for a symbol OUTSIDE the catch-up scope at recording time, positions are managed from the
    snapshot (newest bar), never from committed evidence, so the record is non-material with ``managed_by:
    NEWEST_BAR``; the value is frozen at recording (no re-classification when the symbol later joins the scope)."""
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
            if in_scope and timeframe == "5m" and presented is not None and committed is not None:
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
                            "rule": "FIRST_COMMITTED_BAR_AUTHORITATIVE (unchanged)", "review_key": anomaly_id,
                            "managed_by": "CATCH_UP" if in_scope else "NEWEST_BAR"})
    return records


def _parse_object(payload):
    """A journal payload as a dict, or None when it is not a JSON object (never raises)."""
    try:
        data = json.loads(payload)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _classification_errors(source, data):
    """Schema errors of one EVIDENCE_REVISION classification row ([] = valid). Mirrors what ``classify`` writes."""
    if data is None:
        return ["payload_not_object"]
    errors = []
    if source != SOURCE:
        errors.append("wrong_source")
    anomaly_id = data.get("anomaly_id")
    if not isinstance(anomaly_id, str) or not anomaly_id:
        errors.append("anomaly_id_invalid")
    if data.get("review_key") != anomaly_id:
        errors.append("review_key_mismatch")
    for field in ("symbol", "timeframe", "bar_start"):
        if not isinstance(data.get(field), str) or not data[field]:
            errors.append(f"{field}_invalid")
    if not isinstance(data.get("material"), bool):
        errors.append("material_invalid")
    if not isinstance(data.get("affected"), list):
        errors.append("affected_invalid")
    return errors


def _classification_rows(db):
    """Every EVIDENCE_REVISION row (any source, any payload), in journal order, validated; nothing is skipped."""
    rows = []
    for journal_id, source, payload in db.execute(
            "SELECT id, source, payload FROM journal WHERE event_type=? ORDER BY id", (EVENT,)):
        data = _parse_object(payload)
        rows.append({"journal_id": journal_id, "data": data, "errors": _classification_errors(source, data)})
    return rows


def _valid_classifications(db):
    return [row["data"] for row in _classification_rows(db) if not row["errors"]]


def _review_rows(db):
    """Every ``EVIDENCE_REVISION_REVIEWED`` journal row (any source), in journal order: (id, source, payload dict or
    None when the payload is not a JSON object)."""
    rows = []
    for journal_id, source, payload in db.execute(
            "SELECT id, source, payload FROM journal WHERE event_type=? ORDER BY id", (REVIEW_EVENT,)):
        rows.append((journal_id, source, _parse_object(payload)))
    return rows


def _key(data):
    key = None if data is None else data.get("review_key")
    return key if isinstance(key, str) else None


def _latest_literal_decisions(db):
    """{review_key: literal ``decision`` of its latest review row, valid or not} (design 4.1)."""
    latest = {}
    for _, _, data in _review_rows(db):
        latest[_key(data)] = None if data is None else data.get("decision")
    return latest


def _review_states(db):
    """LOW-1 validation (design 4.2-4.4). Returns (states, historical_invalid, without_record):
    ``states``: {anomaly_id: latest row state}; ``historical_invalid``: superseded invalid rows (warnings);
    ``without_record``: review rows whose key is not a recorded EVIDENCE_REVISION."""
    recorded = {row["anomaly_id"] for row in _valid_classifications(db)}
    previous, states, historical_invalid, without_record = {}, {}, [], []
    for journal_id, source, data in _review_rows(db):
        key = _key(data)
        reasons = []
        if data is None:
            reasons.append("payload_not_object")
        else:
            decision = data.get("decision")
            if source != SOURCE:
                reasons.append("wrong_source")
            if decision not in DECISIONS:
                reasons.append("unknown_decision")
            if key is None or data.get("anomaly_id") != key:
                reasons.append("review_key_mismatch")
            if not isinstance(data.get("reviewer"), str) or not data["reviewer"].strip():
                reasons.append("reviewer_missing")
            if not isinstance(data.get("final"), bool) or data["final"] != (decision in FINAL_DECISIONS):
                reasons.append("final_inconsistent")
            if "previous_decision" not in data or data["previous_decision"] != previous.get(key):
                reasons.append("previous_decision_mismatch")
        if key not in recorded:
            reasons.append("review_without_record")
            without_record.append({"journal_id": journal_id, "review_key": key})
        literal = None if data is None else data.get("decision")
        prior = states.get(key)
        if prior is not None and not prior["valid"]:
            historical_invalid.append(prior)  # superseded, listed so the Owner sees the tampering point
        states[key] = {"anomaly_id": key, "journal_id": journal_id, "decision": literal, "valid": not reasons,
                       "reasons": reasons}
        previous[key] = literal
    return states, historical_invalid, without_record


def _effective(states):
    """{anomaly_id: decision of a VALID latest row, else INVALID_DECISION}; never falls back to an earlier row."""
    return {key: (state["decision"] if state["valid"] else INVALID_DECISION)
            for key, state in states.items() if key is not None}


def _summary(db):
    rows = _valid_classifications(db)
    effective = _effective(_review_states(db)[0])
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
    Appends an auditable row (decision, whether it is final, ``previous_decision`` = the literal decision of the
    immediately preceding review row for this anomaly, valid or not (design 4.1), reviewer, note). A new valid row is
    also the only remediation of an invalid latest row (design 4.4)."""
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
        previous = _latest_literal_decisions(store.db).get(key)
        store._event(at, None, row[0], SOURCE, REVIEW_EVENT, "INFO" if decision in FINAL_DECISIONS else "WARNING",
                     {"review_key": key, "anomaly_id": key, "decision": decision,
                      "final": decision in FINAL_DECISIONS, "previous_decision": previous, "reviewer": reviewer,
                      "note": note})
        store.set_state("evidence_revisions", json.dumps(_summary(store.db), sort_keys=True))


def _ro(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)


def demo_gate(trading_db, evidence_db, enabled_symbols=None):
    """READ-ONLY pre-DEMO gate. CLEAR only with full anomaly coverage by ``anomaly_id``, a VALID final decision for every
    material anomaly, no effectively ESCALATED anomaly, no invalid latest review (LOW-1), no review without a recorded
    anomaly, no classification that mismatches its anomaly, exactly one classification per anomaly, and no malformed
    classification row."""
    from runtime.config import DEFAULT_ENABLED_SYMBOLS
    enabled = tuple(DEFAULT_ENABLED_SYMBOLS if enabled_symbols is None else enabled_symbols)
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
        classification_rows = _classification_rows(db)
        states, historical_invalid, without_record = _review_states(db)
    finally:
        db.close()
    effective = _effective(states)
    # P1-A hotfix 2: every row is validated before grouping. A malformed row never counts as coverage and is never
    # skipped silently (CLASSIFICATION_MALFORMED); if it carries a usable anomaly_id it still counts as a duplicate.
    rows, malformed, ids_per_anomaly = [], [], {}
    for item in classification_rows:
        data = item["data"]
        anomaly_id = data.get("anomaly_id") if data is not None else None
        if isinstance(anomaly_id, str) and anomaly_id:
            ids_per_anomaly[anomaly_id] = ids_per_anomaly.get(anomaly_id, 0) + 1
        if item["errors"]:
            malformed.append({"journal_id": item["journal_id"],
                              "anomaly_id": anomaly_id if isinstance(anomaly_id, str) else None,
                              "errors": item["errors"]})
        else:
            rows.append(data)
    classified, all_classifications = {}, {}
    for row in rows:
        classified.setdefault(row["anomaly_id"], row)
        all_classifications.setdefault(row["anomaly_id"], []).append(row)
    # P1-A hotfix: journal() writes exactly one classification per anomaly_id, so more than one (even identical) is
    # tampering or a defect and blocks; no row can hide another (fail-closed).
    conflicts = [{"anomaly_id": key, "count": count} for key, count in ids_per_anomaly.items() if count > 1]
    evidence_ok = not blockers
    unclassified = [a for key, a in anomalies.items() if key not in classified]
    orphans = [] if not evidence_ok else [r for key, r in classified.items() if key not in anomalies]
    # LOW-1 (design 4.5): a classification counts only if it matches its anomaly's symbol / timeframe / bar_start.
    # P1-A hotfix: EVERY classification row of the anomaly is checked, whatever its order.
    mismatches = [] if not evidence_ok else [
        {"anomaly_id": key, "classification": [r.get("symbol"), r.get("timeframe"), r.get("bar_start")],
         "anomaly": [anomalies[key]["symbol"], anomalies[key]["timeframe"], anomalies[key]["bar_start"]]}
        for key, group in all_classifications.items() if key in anomalies for r in group
        if (r.get("symbol"), r.get("timeframe"), r.get("bar_start")) !=
        (anomalies[key]["symbol"], anomalies[key]["timeframe"], anomalies[key]["bar_start"])]
    invalid_latest = [states[key] for key in classified if key in states and not states[key]["valid"]]
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
    # P1-B (design 1.6, DEC-8.15): coverage is never filtered by scope; an anomaly of a symbol that is not enabled
    # (e.g. NAS100) cannot come from the runtime and blocks for Owner investigation.
    disabled = [a for a in anomalies.values() if a["symbol"] not in enabled]
    if disabled:
        blockers.append("ANOMALY_FOR_DISABLED_SYMBOL")
    if invalid_latest:
        blockers.append("INVALID_REVIEW_RECORD")
    if without_record:
        blockers.append("REVIEW_WITHOUT_RECORD")
    if mismatches:
        blockers.append("CLASSIFICATION_MISMATCH")
    if conflicts:
        blockers.append("CLASSIFICATION_CONFLICT")
    if malformed:
        blockers.append("CLASSIFICATION_MALFORMED")
    return {"status": "BLOCKED" if blockers else "CLEAR", "blockers": blockers, "anomalies": len(anomalies),
            "classified": len(classified), "material_unreviewed": material, "escalated_unresolved": escalated,
            "unclassified": unclassified, "orphan_classifications": orphans,
            "non_material_unreviewed": [r for r in unreviewed if not r["material"]],
            "invalid_reviews": invalid_latest, "historical_invalid_reviews": historical_invalid,
            "reviews_without_record": without_record, "classification_mismatches": mismatches,
            "classification_conflicts": conflicts, "classification_malformed": malformed,
            "anomalies_for_disabled_symbols": disabled,
            "resolved": sum(1 for key in classified if effective.get(key) in FINAL_DECISIONS),
            "recorded": len(classification_rows), "reviewed": len(effective)}


def main(argv=None):
    parser = argparse.ArgumentParser(description="REVISION review gate (PAPER).")
    sub = parser.add_subparsers(dest="command", required=True)
    gate = sub.add_parser("gate")
    gate.add_argument("--trading-db", required=True)
    gate.add_argument("--evidence-db", required=True)
    gate.add_argument("--enabled-symbols", default="XAUUSD,EURUSD")
    review = sub.add_parser("review")
    review.add_argument("--trading-db", required=True)
    review.add_argument("--key", required=True)
    review.add_argument("--decision", required=True, choices=DECISIONS)
    review.add_argument("--reviewer", required=True)
    review.add_argument("--note", default="")
    args = parser.parse_args(argv)
    if args.command == "gate":
        result = demo_gate(args.trading_db, args.evidence_db,
                           tuple(s for s in args.enabled_symbols.split(",") if s))
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
