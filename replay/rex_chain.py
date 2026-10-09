"""V2 Phase 8 / P3 G15 chain and coverage verifier (design P8.6 5.1.3.1, 5.1.5). READ-ONLY.

The source trading database is opened ``mode=ro`` and copied with SQLite's backup API into a private temporary
directory; every check runs on the copy and the source (with ``-wal`` / ``-shm``) is hashed before and after.

SQLite-provable checks (each failure is classified):
- every REX row parses and its ``rex_digest`` matches its canonical text (INVALID otherwise);
- REX_WRITE entries: complete, integrity flags clear (foreign / same-connection write, unexpected change, CAS state),
  ``psh`` recomputed from the recorded states (NG13), canonical state order;
- the EDG chain and the ``psh`` chain over every write in journal order (``edg_before`` = previous ``edg_after``;
  the first ``edg_before`` = ``edg_start`` when one is supplied); the final database EDG / psh = the last ``*_after``;
- every economic journal event referenced by exactly one committed write, with the same type, entity and run
  (orphans INVALID); no economic event of another write inside a write's evidence window (backfill / late REX: NG-B3);
- run coverage: one REX_RUN per claimed run (RUN_STARTED), each listing exactly its REX_WRITE rows; a write without a
  complete run record is an unevidenced write (INVALID); a run without REX and without a write is an NWR (R-B3:
  above 2 distinct UTC days -> INVALID; DEC-8.21b parameter, PROVISIONAL);
- one rule identity for the whole window (one code baseline);
- G14 (``replay.rex_oracle``) for every run: a failing run with a committed write is INVALID (R-B1); without one,
  it is a G14 failure (not certifiable).

NOT provable here, so never asserted: the absence of foreign writers (single-writer attestation), the anchored
``edg_start`` (OAR-G genesis anchor), chain-head anchors, the deployed code SHA, and any alteration made and reverted
between two observations. Without that external evidence the best possible result is
``SQLITE_CHECKS_PASS / NOT VERIFIED``; this tool never emits VERIFIED. It classifies no FS/EX/PD/RS state (DEC-8.18).

Usage: python -m replay.rex_chain --trading-db COPY.db [--account paper-main] [--edg-start HEX]
       [--since-journal-id N] [--out report.json]
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

from replay import rex_oracle
from storage.economic_digest import H, edg, psh

VERIFIER_VERSION = "V2_P3_G15_CHAIN/1"
REX_VERSION = "V2REX/1"
ECONOMIC_EVENTS = frozenset({"ORDER_SUBMITTED", "ORDER_FILLED", "ORDER_REJECTED", "ORDER_CANCELLED",
                             "POSITION_OPENED", "STOP_HIT", "TARGET_HIT", "POSITION_CLOSED"})
NWR_DAY_LIMIT = 2  # DEC-8.21b parameter (PROVISIONAL)
REQUIRES_EXTERNAL = ("single-writer attestation for the whole period (trust root 3.2)",
                     "OAR-G genesis anchor binding edg_start (5.1.3.2)",
                     "chain-head anchors per UTC day with gaps <= 26 h (5.1.3.3)",
                     "the code SHA actually deployed",
                     "alterations made and reverted between two observations (not provable by any snapshot)")


class _Findings:
    def __init__(self):
        self.items = []

    def add(self, severity, code, detail=""):
        self.items.append({"severity": severity, "code": code, "detail": str(detail)})

    def invalid(self, code, detail=""):
        self.add("INVALID", code, detail)

    def fail(self, code, detail=""):
        self.add("FAIL", code, detail)


def _canonical(state):
    return isinstance(state, list) and len(state) == 5 and all(
        isinstance(part, list) and part == sorted(part) for part in state[1:])


def _state_tuple(state):
    return (state[0], *(tuple(part) for part in state[1:]))


def analyze(conn, store, *, account_id="paper-main", edg_start=None, since_journal_id=0):
    """All checks on an open (copy) connection. ``store`` is a read-only ``Store`` on the same copy."""
    findings = _Findings()
    rows = conn.execute("SELECT id,timestamp,run_id,symbol,source,event_type,payload FROM journal WHERE id>? "
                        "ORDER BY id", (since_journal_id,)).fetchall()
    writes, runs, failures_logged = [], {}, []
    economic = {}
    started = {}
    for row in rows:
        journal_id, timestamp, run_id, symbol, source, event_type, payload = row
        if source == "rex":
            if event_type == "REX_FAILURE":
                failures_logged.append({"journal_id": journal_id, "run_id": run_id})
                continue
            try:
                body = json.loads(payload)
                text = body["rex"]
                if H(REX_VERSION, text) != body["rex_digest"]:
                    findings.invalid("REX_DIGEST_MISMATCH", f"journal {journal_id}")
                    continue
                record = json.loads(text)
            except (ValueError, KeyError, TypeError) as exc:
                findings.invalid("REX_UNPARSEABLE", f"journal {journal_id}: {type(exc).__name__}")
                continue
            if record.get("run_id") != run_id or record.get("rex_version") != REX_VERSION:
                findings.invalid("REX_IDENTITY_CONTRADICTION", f"journal {journal_id}")
                continue
            if event_type == "REX_WRITE" and record.get("kind") == "WRITE":
                writes.append((journal_id, record))
            elif event_type == "REX_RUN" and record.get("kind") == "RUN":
                if run_id in runs:
                    findings.invalid("REX_RUN_DUPLICATED", run_id)
                runs[run_id] = (journal_id, record)
            else:
                findings.invalid("REX_KIND_CONTRADICTION", f"journal {journal_id}")
        elif event_type in ECONOMIC_EVENTS:
            economic[journal_id] = {"event_type": event_type, "entity_id": source, "run_id": run_id}
        elif event_type == "RUN_STARTED" and source == "runtime":
            started[run_id] = timestamp

    # -- write evidence and the two chains ---------------------------------------------------------------------------
    referenced = {}
    previous_edg, previous_psh = edg_start, None
    committed_count = 0
    write_ids = [journal_id for journal_id, _ in writes]
    for index, (journal_id, w) in enumerate(writes):
        label = f"REX_WRITE journal {journal_id} (run {w.get('run_id')}, write {w.get('write_seq')})"
        if w.get("complete") is not True:
            findings.invalid("WRITE_INCOMPLETE", label)
        integrity = w.get("integrity") or {}
        for flag in ("foreign_write_detected", "same_connection_write_detected", "unexpected_change"):
            if integrity.get(flag):
                findings.invalid(flag.upper(), label)
        for flag in ("expected_matches_pre", "saved_matches_post"):
            if flag in integrity and integrity[flag] is not True:
                findings.invalid(flag.upper() + "_FALSE", label)
        pre, post = w.get("pre"), w.get("post")
        if pre is None or post is None or w.get("pre_state") is None or w.get("post_state") is None:
            findings.invalid("WRITE_EVIDENCE_MISSING", label)
            previous_edg = previous_psh = None
            continue
        for name, state, recorded in (("pre", w["pre_state"], pre["psh"]), ("post", w["post_state"], post["psh"])):
            if not _canonical(state):
                findings.invalid("STATE_NOT_CANONICAL", f"{label} {name}")
            try:
                if psh(_state_tuple(state)) != recorded:
                    findings.invalid("PSH_RECOMPUTE_MISMATCH", f"{label} {name} (NG13)")
            except (ValueError, TypeError):
                findings.invalid("PSH_RECOMPUTE_MISMATCH", f"{label} {name}")
        if w.get("edg_before") != pre.get("edg") or w.get("psh_before") != pre.get("psh"):
            findings.invalid("WRITE_FIELDS_CONTRADICTION", label)
        if previous_edg is not None and pre["edg"] != previous_edg:
            findings.invalid("EDG_CHAIN_BREAK", f"{label}: edg_before {pre['edg']} != {previous_edg}")
        if previous_psh is not None and pre["psh"] != previous_psh:
            findings.invalid("PSH_CHAIN_BREAK", f"{label} (NG-B4)")
        result = w.get("result")
        events = w.get("journal_events") or []
        if result == "COMMITTED":
            committed_count += 1
            if w.get("edg_after") != post["edg"] or w.get("psh_after") != post["psh"]:
                findings.invalid("WRITE_FIELDS_CONTRADICTION", label)
            previous_edg, previous_psh = post["edg"], post["psh"]
            own = []
            for event in events:
                if event["event_type"] not in ECONOMIC_EVENTS:
                    findings.invalid("NON_ECONOMIC_EVENT_IN_WRITE", f"{label}: {event['event_type']}")
                    continue
                actual = economic.get(event["journal_id"])
                if actual is None or actual["event_type"] != event["event_type"] \
                        or actual["entity_id"] != event["entity_id"] or actual["run_id"] != event["run_id"]:
                    findings.invalid("WRITE_EVENT_MISMATCH", f"{label}: journal {event['journal_id']}")
                    continue
                if event["journal_id"] in referenced:
                    findings.invalid("EVENT_REFERENCED_TWICE", event["journal_id"])
                referenced[event["journal_id"]] = journal_id
                own.append(event["journal_id"])
            window = {jid for jid in economic if pre["journal_max_id"] < jid < journal_id}
            if window != set(own):
                findings.invalid("BACKFILL_OR_INTERLEAVING", f"{label}: window {sorted(window)} own {sorted(own)}")
            if not (post["journal_max_id"] < journal_id) or any(other < journal_id and other > post["journal_max_id"]
                                                                 for other in write_ids[:index]):
                findings.invalid("REX_ORDER_CONTRADICTION", label)
        else:
            if events:
                findings.invalid("EVENTS_ON_UNCOMMITTED_WRITE", label)
            if pre["edg"] != post["edg"]:
                findings.invalid("UNCOMMITTED_WRITE_CHANGED_STATE", label)
            if previous_psh is None:
                previous_psh = pre["psh"]
            if previous_edg is None:
                previous_edg = pre["edg"]

    for jid, event in economic.items():
        if jid not in referenced:
            findings.invalid("ORPHAN_ECONOMIC_EVENT", f"journal {jid} {event['event_type']}")

    current = edg(conn)
    account, orders, fills = store.load_paper(account_id)
    current_psh = psh(store.paper_state(account, orders, fills))
    if previous_edg is not None and current["edg"] != previous_edg:
        findings.invalid("FINAL_EDG_MISMATCH", f"{current['edg']} != {previous_edg}")
    if previous_psh is not None and current_psh != previous_psh:
        findings.invalid("FINAL_PSH_MISMATCH", f"{current_psh} != {previous_psh}")

    # -- run coverage, linkage, G14 ----------------------------------------------------------------------------------
    by_run = {}
    for journal_id, w in writes:
        by_run.setdefault(w["run_id"], []).append((journal_id, w))
    nwr_days, g14 = set(), []
    identities = set()
    for run_id in sorted(set(started) | set(runs) | set(by_run), key=str):
        own_writes = by_run.get(run_id, [])
        committed = any(w.get("result") == "COMMITTED" for _, w in own_writes)
        if run_id not in runs:
            if own_writes:
                findings.invalid("WRITE_WITHOUT_RUN_RECORD", run_id)
            elif run_id in started:
                nwr_days.add(started[run_id][:10])
            continue
        run_journal, record = runs[run_id]
        if run_id not in started:
            findings.invalid("RUN_RECORD_WITHOUT_RUN", run_id)
        listed = [w.get("rex_journal_id") for w in record.get("writes", [])]
        if listed != [journal_id for journal_id, _ in own_writes]:
            findings.invalid("RUN_WRITE_LINK_MISMATCH", f"{run_id}: {listed}")
        if any(journal_id > run_journal for journal_id, _ in own_writes):
            findings.invalid("REX_ORDER_CONTRADICTION", f"{run_id}: write after its run record")
        identities.add(json.dumps((record.get("identity") or {}).get("rule_identity"), sort_keys=True))
        verdict = rex_oracle.check_run(record, [w for _, w in own_writes])
        g14.append(verdict)
        if verdict["result"] != "PASS":
            if committed:
                findings.invalid("G14_FAIL_WITH_ECONOMIC_WRITE", f"{run_id}: {verdict['failures'][:3]}")
            else:
                findings.fail("G14_FAIL", f"{run_id}: {verdict['failures'][:3]}")
    if len(identities) > 1:
        findings.invalid("RULE_IDENTITY_CHANGED", f"{len(identities)} distinct rule identities in one window")
    if len(nwr_days) > NWR_DAY_LIMIT:
        findings.invalid("NWR_DAYS_ABOVE_LIMIT", sorted(nwr_days))
    if edg_start is None:
        findings.add("NOTE", "CHAIN_START_UNANCHORED", "no edg_start supplied: the first edg_before is not bound")

    invalid = any(f["severity"] == "INVALID" for f in findings.items)
    failed = any(f["severity"] == "FAIL" for f in findings.items)
    sqlite_result = "CERTIFICATION_INVALID" if invalid else "SQLITE_CHECKS_FAIL" if failed else "SQLITE_CHECKS_PASS"
    return {"verifier_version": VERIFIER_VERSION, "account_id": account_id, "since_journal_id": since_journal_id,
            "edg_start": edg_start, "sqlite_result": sqlite_result,
            "classification": "CERTIFICATION_INVALID" if invalid else "NOT VERIFIED",
            "external_evidence": "NOT EVALUATED", "requires_external": list(REQUIRES_EXTERNAL),
            "findings": findings.items, "counts": {
                "rex_writes": len(writes), "committed_writes": committed_count, "rex_runs": len(runs),
                "claimed_runs": len(started), "economic_events": len(economic), "rex_failures": len(failures_logged),
                "nwr_days": sorted(nwr_days)},
            "current": {"edg": current["edg"], "psh": current_psh}, "g14": g14}


def _sha256(path):
    path = Path(path)
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fingerprint(path):
    return {suffix or "db": _sha256(str(path) + suffix) for suffix in ("", "-wal", "-shm")}


def verify(trading_db, *, account_id="paper-main", edg_start=None, since_journal_id=0):
    from storage.database import Store  # read-only use of the private copy
    source = Path(trading_db)
    if not source.is_file():
        raise FileNotFoundError(f"source database not found: {source}")
    before = _fingerprint(source)
    with tempfile.TemporaryDirectory(prefix="rex-chain-") as tmp:
        copy = Path(tmp) / "trading_copy.db"
        src = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            dst = sqlite3.connect(copy)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        conn = sqlite3.connect(copy.resolve().as_uri() + "?mode=ro", uri=True)
        store = Store(copy, readonly=True)
        try:
            report = analyze(conn, store, account_id=account_id, edg_start=edg_start,
                             since_journal_id=since_journal_id)
        finally:
            store.close()
            conn.close()
    report.update(source_hashes=before, source_unchanged=_fingerprint(source) == before, read_only=True,
                  generated_at=datetime.now(timezone.utc).isoformat())
    return report


def _norm(path):
    return os.path.normcase(os.path.realpath(path))


def _targets_source(out, trading_db):
    protected = [str(base) + suffix for base in {Path(trading_db).absolute(), Path(os.path.realpath(trading_db))}
                 for suffix in ("", "-wal", "-shm")]
    if _norm(out) in {_norm(path) for path in protected}:
        return True
    return os.path.exists(out) and any(os.path.exists(p) and os.path.samefile(out, p) for p in protected)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only REX chain verifier (PAPER).")
    parser.add_argument("--trading-db", required=True)
    parser.add_argument("--account", default="paper-main")
    parser.add_argument("--edg-start")
    parser.add_argument("--since-journal-id", type=int, default=0)
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    if args.out and _targets_source(args.out, args.trading_db):
        raise SystemExit("refusing to write the report over the source database or its -wal/-shm files")
    report = verify(args.trading_db, account_id=args.account, edg_start=args.edg_start,
                    since_journal_id=args.since_journal_id)
    text = json.dumps(report, indent=2, sort_keys=True, default=str)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0 if report["source_unchanged"] else 2


if __name__ == "__main__":
    sys.exit(main())
