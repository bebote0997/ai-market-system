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
  it is a G14 failure (not certifiable);
- malformed input (economic payloads that are not valid PAPER JSON, missing fields, unexpected types, malformed REX
  records, non-UTF-8 text, an unprocessable copy) is never an exception: CERTIFICATION_INVALID with a code and a
  structured ``context``.

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

from replay import halt_verifier, rex_oracle
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

    def add(self, severity, code, detail="", context=None):
        item = {"severity": severity, "code": code, "detail": str(detail)}
        if context is not None:
            item["context"] = context
        self.items.append(item)

    def invalid(self, code, detail="", context=None):
        self.add("INVALID", code, detail, context)

    def fail(self, code, detail="", context=None):
        self.add("FAIL", code, detail, context)


MALFORMED = (KeyError, TypeError, ValueError, AttributeError, IndexError)
ECONOMIC_TYPES = {"paper_accounts": "PaperAccount", "paper_orders": "PaperOrder", "paper_fills": "PaperFill",
                  "paper_positions": "PaperPosition", "closed_trades": "ClosedTrade"}
ECONOMIC_KEYS = {"paper_accounts": "account_id", "paper_orders": "order_id", "paper_fills": "fill_id",
                 "paper_positions": "position_id", "closed_trades": "trade_id"}


def _canonical(state):
    return isinstance(state, list) and len(state) == 5 and all(
        isinstance(part, list) and part == sorted(part) for part in state[1:])


def _state_tuple(state):
    return (state[0], *(tuple(part) for part in state[1:]))


def _raw_rows(conn, sql, params=()):
    """Rows with TEXT as raw bytes (a non-UTF-8 value never aborts the read)."""
    previous = conn.text_factory
    conn.text_factory = bytes
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.text_factory = previous


def _text(value):
    return value.decode("utf-8") if isinstance(value, bytes) else value


def _locate_malformed_economic(conn):
    """First economic row that is not a well-formed payload of its type: structured context, or not located."""
    from storage.codec import paper_decode
    for table, key in ECONOMIC_KEYS.items():
        try:
            rows = _raw_rows(conn, f'SELECT "{key}", payload FROM "{table}" ORDER BY "{key}"')
        except sqlite3.Error as exc:
            return {"table": table, "key": None, "check": "table_unreadable", "error_type": type(exc).__name__}
        for raw_key, raw_payload in rows:
            context = {"table": table, "key": raw_key.decode("utf-8", "replace") if isinstance(raw_key, bytes)
                       else raw_key}
            try:
                entity_key, payload = _text(raw_key), _text(raw_payload)
            except UnicodeDecodeError:
                return {**context, "check": "utf8", "error_type": "UnicodeDecodeError"}
            try:
                data = json.loads(payload)
            except (TypeError, ValueError) as exc:
                return {**context, "check": "json", "error_type": type(exc).__name__}
            if not isinstance(data, dict):
                return {**context, "check": "json_object", "error_type": type(data).__name__}
            if data.get(key) != entity_key:
                return {**context, "check": "primary_key_matches_payload", "error_type": "Mismatch"}
            try:
                paper_decode(ECONOMIC_TYPES[table], payload)
            except MALFORMED as exc:
                return {**context, "check": "contract_fields", "error_type": type(exc).__name__}
    return {"table": None, "key": None, "check": "not_located"}


def analyze(conn, store, *, account_id="paper-main", edg_start=None, since_journal_id=0):
    """All checks on an open (copy) connection. ``store`` is a read-only ``Store`` on the same copy. Malformed input
    never raises: it is classified CERTIFICATION_INVALID with a code and a structured context."""
    findings = _Findings()
    writes, runs, failures_logged = [], {}, []
    economic = {}
    started = {}
    halts, journal_index = [], []
    for raw in _raw_rows(conn, "SELECT id,timestamp,run_id,symbol,source,event_type,payload FROM journal WHERE id>? "
                               "ORDER BY id", (since_journal_id,)):
        try:
            journal_id, timestamp, run_id, symbol, source, event_type, payload = (_text(v) for v in raw)
        except UnicodeDecodeError:
            findings.invalid("PAYLOAD_NOT_UTF8", f"journal {raw[0]}", {"table": "journal", "journal_id": raw[0]})
            continue
        journal_index.append((journal_id, source, event_type, run_id))
        if source == "halt" and event_type == "HALT_OBSERVED":
            halts.append((journal_id, payload))
            continue
        if source == "rex":
            if event_type == "REX_FAILURE":
                failures_logged.append({"journal_id": journal_id, "run_id": run_id})
                continue
            try:
                body = json.loads(payload)
                text = body["rex"]
                if H(REX_VERSION, text) != body["rex_digest"]:
                    findings.invalid("REX_DIGEST_MISMATCH", f"journal {journal_id}", {"journal_id": journal_id})
                    continue
                record = json.loads(text)
            except MALFORMED as exc:
                findings.invalid("REX_UNPARSEABLE", f"journal {journal_id}",
                                 {"journal_id": journal_id, "error_type": type(exc).__name__})
                continue
            if not isinstance(record, dict):
                findings.invalid("REX_RECORD_MALFORMED", f"journal {journal_id}",
                                 {"journal_id": journal_id, "field": "<record>", "error_type": type(record).__name__})
                continue
            if record.get("run_id") != run_id or record.get("rex_version") != REX_VERSION:
                findings.invalid("REX_IDENTITY_CONTRADICTION", f"journal {journal_id}", {"journal_id": journal_id})
                continue
            if event_type == "REX_WRITE" and record.get("kind") == "WRITE":
                writes.append((journal_id, record))
            elif event_type == "REX_RUN" and record.get("kind") == "RUN":
                if run_id in runs:
                    findings.invalid("REX_RUN_DUPLICATED", run_id, {"journal_id": journal_id})
                runs[run_id] = (journal_id, record)
            else:
                findings.invalid("REX_KIND_CONTRADICTION", f"journal {journal_id}", {"journal_id": journal_id})
        elif event_type in ECONOMIC_EVENTS:
            economic[journal_id] = {"event_type": event_type, "entity_id": source, "run_id": run_id}
        elif event_type == "RUN_STARTED" and source == "runtime":
            started[run_id] = timestamp

    # -- write evidence and the two chains ---------------------------------------------------------------------------
    referenced = {}
    chain = {"edg": edg_start, "psh": None}
    committed_count = 0
    write_ids = [journal_id for journal_id, _ in writes]
    valid_writes = []
    for index, (journal_id, w) in enumerate(writes):
        try:
            committed_count += _check_write(findings, index, journal_id, w, chain, economic, referenced, write_ids)
            valid_writes.append((journal_id, w))
        except MALFORMED as exc:
            findings.invalid("REX_RECORD_MALFORMED", f"REX_WRITE journal {journal_id}",
                             {"journal_id": journal_id, "kind": "REX_WRITE", "error_type": type(exc).__name__,
                              "error": str(exc)[:200]})
            chain["edg"] = chain["psh"] = None  # the chain cannot continue across unreadable evidence

    halt_findings, halt_summary = halt_verifier.check(halts, valid_writes, journal_index, runs)
    for item in halt_findings:
        findings.add(item["severity"], item["code"], item["detail"], item.get("context"))

    for jid, event in economic.items():
        if jid not in referenced:
            findings.invalid("ORPHAN_ECONOMIC_EVENT", f"journal {jid} {event['event_type']}", {"journal_id": jid})

    current_edg = current_psh = None
    try:
        current_edg = edg(conn)["edg"]
        account, orders, fills = store.load_paper(account_id)
        current_psh = psh(store.paper_state(account, orders, fills))
    except (sqlite3.Error, *MALFORMED) as exc:
        findings.invalid("ECONOMIC_PAYLOAD_MALFORMED", "the persisted economic state cannot be read as PAPER payloads",
                         {**_locate_malformed_economic(conn), "stage": "current_state",
                          "raised": type(exc).__name__})
    if current_edg is not None and chain["edg"] is not None and current_edg != chain["edg"]:
        findings.invalid("FINAL_EDG_MISMATCH", f"{current_edg} != {chain['edg']}")
    if current_psh is not None and chain["psh"] is not None and current_psh != chain["psh"]:
        findings.invalid("FINAL_PSH_MISMATCH", f"{current_psh} != {chain['psh']}")

    # -- run coverage, linkage, G14 ----------------------------------------------------------------------------------
    by_run = {}
    for journal_id, w in valid_writes:
        by_run.setdefault(w.get("run_id"), []).append((journal_id, w))
    nwr_days, g14 = set(), []
    identities = set()
    for run_id in sorted(set(started) | set(runs) | set(by_run), key=str):
        try:
            _check_run_record(findings, run_id, by_run, runs, started, nwr_days, identities, g14)
        except MALFORMED as exc:
            findings.invalid("REX_RECORD_MALFORMED", f"REX_RUN {run_id}",
                             {"run_id": run_id, "kind": "REX_RUN", "error_type": type(exc).__name__,
                              "error": str(exc)[:200]})
    if len(identities) > 1:
        findings.invalid("RULE_IDENTITY_CHANGED", f"{len(identities)} distinct rule identities in one window")
    if len(nwr_days) > NWR_DAY_LIMIT:
        findings.invalid("NWR_DAYS_ABOVE_LIMIT", sorted(nwr_days))
    if edg_start is None:
        findings.add("NOTE", "CHAIN_START_UNANCHORED", "no edg_start supplied: the first edg_before is not bound")
    return _report(findings, account_id=account_id, since_journal_id=since_journal_id, edg_start=edg_start,
                   counts={"rex_writes": len(writes), "committed_writes": committed_count, "rex_runs": len(runs),
                           "claimed_runs": len(started), "economic_events": len(economic),
                           "rex_failures": len(failures_logged), "nwr_days": sorted(nwr_days)},
                   current={"edg": current_edg, "psh": current_psh}, g14=g14, halts=halt_summary)


def _check_write(findings, index, journal_id, w, chain, economic, referenced, write_ids):
    """One REX_WRITE; returns 1 if committed. Raises MALFORMED on structurally unreadable evidence."""
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
        chain["edg"] = chain["psh"] = None
        return 0
    for name, state, recorded in (("pre", w["pre_state"], pre["psh"]), ("post", w["post_state"], post["psh"])):
        if not _canonical(state):
            findings.invalid("STATE_NOT_CANONICAL", f"{label} {name}")
        try:
            if psh(_state_tuple(state)) != recorded:
                findings.invalid("PSH_RECOMPUTE_MISMATCH", f"{label} {name} (NG13)")
        except MALFORMED:
            findings.invalid("PSH_RECOMPUTE_MISMATCH", f"{label} {name}")
    if w.get("edg_before") != pre["edg"] or w.get("psh_before") != pre["psh"]:
        findings.invalid("WRITE_FIELDS_CONTRADICTION", label)
    if chain["edg"] is not None and pre["edg"] != chain["edg"]:
        findings.invalid("EDG_CHAIN_BREAK", f"{label}: edg_before {pre['edg']} != {chain['edg']}")
    if chain["psh"] is not None and pre["psh"] != chain["psh"]:
        findings.invalid("PSH_CHAIN_BREAK", f"{label} (NG-B4)")
    events = w.get("journal_events") or []
    if not isinstance(events, list):
        raise TypeError("journal_events must be a list")
    if w.get("result") == "COMMITTED":
        if w.get("edg_after") != post["edg"] or w.get("psh_after") != post["psh"]:
            findings.invalid("WRITE_FIELDS_CONTRADICTION", label)
        chain["edg"], chain["psh"] = post["edg"], post["psh"]
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
        if not (post["journal_max_id"] < journal_id) or any(post["journal_max_id"] < other < journal_id
                                                             for other in write_ids[:index]):
            findings.invalid("REX_ORDER_CONTRADICTION", label)
        return 1
    if events:
        findings.invalid("EVENTS_ON_UNCOMMITTED_WRITE", label)
    if pre["edg"] != post["edg"]:
        findings.invalid("UNCOMMITTED_WRITE_CHANGED_STATE", label)
    if chain["psh"] is None:
        chain["psh"] = pre["psh"]
    if chain["edg"] is None:
        chain["edg"] = pre["edg"]
    return 0


def _check_run_record(findings, run_id, by_run, runs, started, nwr_days, identities, g14):
    own_writes = by_run.get(run_id, [])
    committed = any(w.get("result") == "COMMITTED" for _, w in own_writes)
    if run_id not in runs:
        if own_writes:
            findings.invalid("WRITE_WITHOUT_RUN_RECORD", run_id)
        elif run_id in started:
            nwr_days.add(str(started[run_id])[:10])
        return
    run_journal, record = runs[run_id]
    if run_id not in started:
        findings.invalid("RUN_RECORD_WITHOUT_RUN", run_id)
    listed_writes = record.get("writes", [])
    if not isinstance(listed_writes, list):
        raise TypeError("REX_RUN writes must be a list")
    listed = [w.get("rex_journal_id") for w in listed_writes]
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


def _report(findings, *, account_id, since_journal_id, edg_start, counts, current, g14, halts=()):
    invalid = any(f["severity"] == "INVALID" for f in findings.items)
    failed = any(f["severity"] == "FAIL" for f in findings.items)
    sqlite_result = "CERTIFICATION_INVALID" if invalid else "SQLITE_CHECKS_FAIL" if failed else "SQLITE_CHECKS_PASS"
    return {"verifier_version": VERIFIER_VERSION, "account_id": account_id, "since_journal_id": since_journal_id,
            "edg_start": edg_start, "sqlite_result": sqlite_result,
            "classification": "CERTIFICATION_INVALID" if invalid else "NOT VERIFIED",
            "external_evidence": "NOT EVALUATED", "requires_external": list(REQUIRES_EXTERNAL),
            "findings": findings.items, "counts": counts, "current": current, "g14": g14, "halts": list(halts)}


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
    report = None
    # Cleanup can never mask the classification (Windows keeps a file open while a failing statement is referenced).
    with tempfile.TemporaryDirectory(prefix="rex-chain-", ignore_cleanup_errors=True) as tmp:
        copy = Path(tmp) / "trading_copy.db"
        conn = store = None
        try:
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
            report = analyze(conn, store, account_id=account_id, edg_start=edg_start,
                             since_journal_id=since_journal_id)
        except Exception as exc:  # noqa: BLE001 - fail closed: unprocessable input is never a pass, never a crash
            findings = _Findings()
            findings.invalid("VERIFIER_INPUT_UNPROCESSABLE", "the copy could not be analysed",
                             {"error_type": type(exc).__name__, "error": str(exc)[:200]})
            report = _report(findings, account_id=account_id, since_journal_id=since_journal_id, edg_start=edg_start,
                             counts={}, current={"edg": None, "psh": None}, g14=[])
        finally:
            for handle in (store, conn):
                try:
                    if handle is not None:
                        handle.close()
                except Exception:  # noqa: BLE001
                    pass
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
