"""V2 Phase 8 / P4b G-8.INT measurement matrix (design P8.6 section 5, DEC-8.17; Owner integration checklist).
READ-ONLY: every input is read from copies (``replay.rex_chain.verify`` makes its own backup-API copy).

Each criterion is classified PASS, FAIL, NOT VERIFIED or BLOCKED, with its measured values:
- PASS only on positive evidence that meets the threshold; NOT VERIFIED when the evidence is missing, incomplete or
  below a threshold whose window may still be open; FAIL on a measured violation; BLOCKED when it depends on an act or
  review outside this tool (live A2 period, independent review, external anchors, Owner decisions).
- Absence of errors is never PASS. Synthetic test periods never close HIGH-8.1 or M-5 (both stay BLOCKED here).
The global Phase 8 verdict is BLOCKED unless every criterion is PASS; this tool cannot certify Phase 8.

Usage: python -m replay.g8int --trading-db COPY.db [--evidence-db EV.db] [--oar-g F --allowed-signers F]
       [--expected-commit Y_P2] [--out report.json]
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import sys

from replay.rex_chain import verify

G8INT_VERSION = "V2_P4B_G8INT/1"
PASS, FAIL, NOT_VERIFIED, BLOCKED = "PASS", "FAIL", "NOT VERIFIED", "BLOCKED"
SAFE_SCHEMA = 3


def _item(status, measured=None, note=""):
    return {"status": status, "measured": measured, "note": note}


def _rows(db, sql, params=()):
    conn = sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _rex_records(db, kind):
    out = []
    for run_id, payload in _rows(db, "SELECT run_id, payload FROM journal WHERE source='rex' AND event_type=? "
                                     "ORDER BY id", (kind,)):
        try:
            out.append(json.loads(json.loads(payload)["rex"]))
        except (ValueError, KeyError, TypeError):
            continue
    return out


def _counted_days(db, runs, scope_by_run, halt_days, sessions):
    """P8.6F2 counted session days per symbol (weekday; >= 95 % of the scheduled slots COMPLETED non-ERROR; 0 ERROR;
    symbol in scope; no halt on the day)."""
    from runtime.scheduler import session_names
    by = defaultdict(list)
    for slot_key, run_id, symbol, as_of, status, final in runs:
        by[(symbol, as_of[:10])].append((run_id, status, final))
    counted = defaultdict(list)
    for (symbol, day), items in sorted(by.items()):
        date = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
        if date.weekday() >= 5 or day in halt_days:
            continue
        slots = [date + timedelta(minutes=15 * i) for i in range(96)]
        scheduled = [t for t in slots if set(session_names(t)) & set(sessions)]
        good = sum(1 for _, status, final in items if status == "COMPLETED" and final != "ERROR")
        errors = sum(1 for _, _, final in items if final == "ERROR")
        in_scope = all(symbol in scope_by_run.get(run_id, ()) for run_id, _, _ in items)
        if scheduled and good >= 0.95 * len(scheduled) and errors == 0 and in_scope:
            counted[symbol].append(day)
    return counted


def measure(trading_db, *, evidence_db=None, oar_g_path=None, allowed_signers=None, expected_commit=None):
    chain = verify(trading_db, oar_g_path=oar_g_path, allowed_signers=allowed_signers)
    runs_rex = _rex_records(trading_db, "REX_RUN")
    writes = _rex_records(trading_db, "REX_WRITE")
    runs = _rows(trading_db, "SELECT slot_key, run_id, symbol, as_of, status, final_status FROM runs")
    enabled = sorted({s for r in runs_rex for s in (r.get("identity") or {}).get("enabled_symbols", [])})
    sessions = next(((r.get("identity") or {}).get("sessions") for r in runs_rex), None) or ["LONDON", "NEW_YORK"]
    scope_by_run = {r["run_id"]: tuple((r.get("identity") or {}).get("catch_up_scope", [])) for r in runs_rex}
    halts = _rows(trading_db, "SELECT timestamp FROM journal WHERE event_type='HALT_OBSERVED'")
    halt_days = {t[:10] for (t,) in halts}
    g14 = {v["run_id"]: v["result"] for v in chain["g14"]}
    period = chain["genesis"]["status"] not in ("NOT APPLICABLE",)
    items = {}

    # G1 scope: alternative A, every enabled symbol in scope for every run
    full = bool(runs_rex) and all(set(scope_by_run.get(r["run_id"], ())) == set(enabled) for r in runs_rex)
    items["G1_scope"] = _item(PASS if full else (FAIL if runs_rex else NOT_VERIFIED),
                              {"enabled": enabled, "runs": len(runs_rex)})
    items["G2_simulation_on_copies"] = _item(BLOCKED, note="section 6 rehearsal on Owner copies; tests are not it")
    counted = _counted_days(trading_db, runs, scope_by_run, halt_days, sessions)
    days = {s: len(counted.get(s, [])) for s in enabled}
    items["G3_counted_session_days"] = _item(
        PASS if enabled and all(n >= 10 for n in days.values()) else (BLOCKED if not period else NOT_VERIFIED), days,
        "threshold 10 per symbol in one segment")
    cycles = Counter(symbol for _, _, symbol, as_of, _, _ in runs if as_of[:10] in set(counted.get(symbol, [])))
    managed = {s: any(w.get("symbol") == s and (w.get("context") or {}).get("stage") == "ST2C" for w in writes)
               for s in enabled}
    items["G4_managed_exposure"] = _item(
        PASS if enabled and all(cycles[s] >= 200 and managed[s] for s in enabled) else
        (BLOCKED if not period else NOT_VERIFIED), {"cycles": dict(cycles), "open_position_under_catch_up": managed})
    closes = _close_reconciliation(trading_db, evidence_db, writes, g14, enabled)
    if evidence_db is None:
        g5 = _item(NOT_VERIFIED, closes, "committed evidence not supplied: closes are not reconciled")
    elif closes["unreconciled"]:
        g5 = _item(FAIL, closes, "a close differs from the committed evidence or its G14 reproduction")
    elif enabled and all(closes["reconciled"].get(s, 0) >= 5 for s in enabled):
        g5 = _item(PASS, closes)
    elif any(days.get(s, 0) >= 20 for s in enabled):
        g5 = _item(FAIL, closes, "fewer than 5 reconciled closes after 20 counted days (NOT MET)")
    else:
        g5 = _item(NOT_VERIFIED if period else BLOCKED, closes, "fewer than 5 reconciled closes per symbol so far")
    items["G5_close_reconciliation"] = g5
    duplicates = {
        "fills": sum(n - 1 for n in Counter(json.loads(p)["order_id"] for (p,) in
                                             _rows(trading_db, "SELECT payload FROM paper_fills")).values() if n > 1),
        "closes": sum(n - 1 for n in Counter(json.loads(p)["position_id"] for (p,) in
                                              _rows(trading_db, "SELECT payload FROM closed_trades")).values() if n > 1),
        "claims": len(runs) - len({k for k, *_ in runs})}
    items["G6_duplicates"] = _item(FAIL if any(duplicates.values()) else (PASS if runs else NOT_VERIFIED), duplicates)
    unavailable = _rows(trading_db, "SELECT count(*) FROM journal WHERE event_type='EVIDENCE_UNAVAILABLE'")[0][0]
    in_scope_cycles = sum(1 for r in runs_rex if r.get("symbol") in scope_by_run.get(r["run_id"], ()))
    rate = None if not in_scope_cycles else unavailable / in_scope_cycles
    items["G7_evidence_availability"] = _item(
        NOT_VERIFIED if rate is None else (PASS if rate <= 0.01 else FAIL),
        {"evidence_unavailable": unavailable, "in_scope_cycles": in_scope_cycles, "rate": rate})
    if evidence_db is None:
        items["G8_evidence_gaps"] = _item(NOT_VERIFIED, note="Evidence Store not supplied")
        items["G9_revision_gate"] = _item(NOT_VERIFIED, note="Evidence Store not supplied")
    else:
        gaps = _rows(evidence_db, "SELECT count(*) FROM evidence_anomalies WHERE kind='GAP'")[0][0]
        items["G8_evidence_gaps"] = _item(PASS if gaps == 0 else NOT_VERIFIED, {"gap_anomalies": gaps},
                                          "any GAP needs a recorded explanation (not decidable here)")
        from runtime.revision_review import demo_gate
        gate = demo_gate(trading_db, evidence_db, enabled_symbols=enabled or None)
        items["G9_revision_gate"] = _item(PASS if gate["status"] == "CLEAR" else FAIL, {"status": gate["status"],
                                                                                         "blockers": gate["blockers"]})
    error_runs = sum(1 for *_, final in runs if final == "ERROR")
    items["G10_runtime_health"] = _item(FAIL if error_runs or halts else (PASS if runs else NOT_VERIFIED),
                                        {"error_runs": error_runs, "halts": len(halts)})
    items["G11_chain_and_external_anchors"] = _item(BLOCKED, note="chain-head anchors (OP-6) and OAR attestations")
    from runtime.demo_runner import REAL_EXECUTION_ENABLED
    schema = _rows(trading_db, "SELECT version FROM schema_info")[0][0]
    symbols = {symbol for _, _, symbol, *_ in runs}
    safe = not REAL_EXECUTION_ENABLED and "NAS100" not in symbols and schema == SAFE_SCHEMA
    items["G12_safety"] = _item(FAIL if not safe else (PASS if runs else NOT_VERIFIED),
                                {"real_execution_enabled": REAL_EXECUTION_ENABLED,
                                                         "symbols": sorted(symbols), "schema": schema})
    items["G13_independent_review"] = _item(BLOCKED, note="independent reviewer and Owner")
    g14_values = set(g14.values())
    items["G14_decision_reproduction"] = _item(
        FAIL if "FAIL" in g14_values else (PASS if g14_values == {"PASS"} else NOT_VERIFIED),
        {"runs": len(g14), "failed": sorted(k for k, v in g14.items() if v != "PASS")})
    items["G15_whole_period_chain"] = _item(
        FAIL if chain["classification"] == "CERTIFICATION_INVALID" else NOT_VERIFIED,
        {"sqlite_result": chain["sqlite_result"], "classification": chain["classification"]},
        "only VERIFIED passes; it needs the external evidence (single-writer attestation, OAR-G, anchors)")

    integration = _integration(chain, runs_rex, writes, run_meta=_rows(
        trading_db, "SELECT DISTINCT git_commit, config_fingerprint FROM run_metadata"),
        expected_commit=expected_commit, oar_g_path=oar_g_path, enabled=enabled, items=items)
    all_items = {**items, **integration}
    verdict = PASS if all(i["status"] == PASS for i in all_items.values()) else BLOCKED
    return {"g8int_version": G8INT_VERSION, "phase8_verdict": verdict, "criteria": items, "integration": integration,
            "summary": dict(Counter(i["status"] for i in all_items.values())),
            "gates": {"HIGH-8.1": "OPEN (requires a live two-symbol A2 period; synthetic evidence never closes it)",
                      "M-5": "OPEN (independent certification required)",
                      "B-STRICT": "PROVISIONAL (external anchors and attestations required)"},
            "chain": {"sqlite_result": chain["sqlite_result"], "classification": chain["classification"],
                      "genesis": chain["genesis"], "halts": chain["halts"]}}


def _close_reconciliation(trading_db, evidence_db, writes, g14, enabled):
    reconciled, unreconciled = Counter(), []
    committed = {}
    if evidence_db is not None:
        committed = {(s, b): d for s, b, d in _rows(evidence_db, "SELECT symbol, bar_start, digest FROM "
                                                                  "market_evidence WHERE timeframe='5m'")}
    for w in writes:
        context = w.get("context") or {}
        events = [e["event_type"] for e in (w.get("journal_events") or [])]
        if w.get("result") != "COMMITTED" or "POSITION_CLOSED" not in events:
            continue
        symbol = w.get("symbol")
        ok = g14.get(w.get("run_id")) == "PASS"
        if context.get("stage") == "ST2C" and evidence_db is not None:
            ok = ok and committed.get((symbol, context.get("bar_start"))) == context.get("digest")
        elif evidence_db is not None:
            ok = False  # a close outside catch-up has no committed-evidence binding (legacy newest bar)
        if ok:
            reconciled[symbol] += 1
        else:
            unreconciled.append({"run_id": w.get("run_id"), "symbol": symbol, "stage": context.get("stage")})
    return {"reconciled": {s: reconciled[s] for s in enabled}, "unreconciled": unreconciled}


def _integration(chain, runs_rex, writes, *, run_meta, expected_commit, oar_g_path, enabled, items):
    rule_ids = {json.dumps((r.get("identity") or {}).get("rule_identity"), sort_keys=True) for r in runs_rex}
    commits = {c for c, _ in run_meta}
    if len(rule_ids) > 1 or len(commits) > 1:
        identity = _item(FAIL, {"rule_identities": len(rule_ids), "commits": sorted(map(str, commits))})
    elif expected_commit is None or commits != {expected_commit}:
        identity = _item(NOT_VERIFIED, {"commits": sorted(map(str, commits))}, "expected deployed commit not proven")
    else:
        identity = _item(PASS, {"commits": sorted(commits)})
    genesis = chain["genesis"]["status"]
    invalid_codes = {f["code"] for f in chain["findings"] if f["severity"] == "INVALID"}
    halt_codes = {c for c in invalid_codes if "HALT" in c or c in {"ADMITTED_AFTER_HALT", "READ2_AFTER_HALT",
                                                                    "RESIDUAL_ABOVE_ONE", "T_ACK_BEFORE_T_STOP",
                                                                    "ADMISSION_EVIDENCE_MISSING"}}
    stages = {s: {(w.get("context") or {}).get("stage") for w in writes if w.get("symbol") == s} for s in enabled}
    return {
        "I1_code_identity_and_baseline": identity,
        "I2_genesis_integrity": _item(
            FAIL if genesis == "INVALID" else (PASS if genesis.startswith("ANCHORED") and oar_g_path else
                                               NOT_VERIFIED), {"genesis": genesis}),
        "I3_economic_integrity_edg_psh": _item(
            FAIL if chain["sqlite_result"] == "CERTIFICATION_INVALID" else
            (PASS if chain["sqlite_result"] == "SQLITE_CHECKS_PASS" and chain["counts"].get("committed_writes")
             else NOT_VERIFIED),
            {"sqlite_result": chain["sqlite_result"], "committed_writes": chain["counts"].get("committed_writes")},
            "SQLite-provable scope only, over evidenced writes (none -> NOT VERIFIED); external anchors in G11/G15"),
        "I4_rex_g14_g15": _item(_rex_status(items["G14_decision_reproduction"]["status"], chain["sqlite_result"]),
                                {"g14": items["G14_decision_reproduction"]["measured"],
                                 "sqlite_result": chain["sqlite_result"]}),
        "I5_rhalt_and_recovery": _item(FAIL if halt_codes else BLOCKED, {"halts": chain["halts"]},
                                       "mechanism evidence only; M-5 closure needs its independent certification"),
        "I6_catch_up_per_symbol": _item(BLOCKED, {s: sorted(filter(None, v)) for s, v in stages.items()},
                                        "HIGH-8.1 closure needs a live two-symbol A2 period"),
        "I7_conflict_engine_compatibility": _item(NOT_VERIFIED, note="established by the test suite and inventory "
                                                                    "tests on the SHA (CI), not by a DB copy"),
        "I8_risk_sizing_sltp_broker": _item(items["G14_decision_reproduction"]["status"],
                                            note="V1 sizing, adapter and fill gate reproduced by G14"),
        "I9_rollback_and_restart": _item(
            FAIL if "SEAL_CHECK_FAILED" in invalid_codes else
            (PASS if genesis.startswith("ANCHORED") else NOT_VERIFIED), {"genesis": genesis},
            "every process start has a PASS seal check (I-G18)"),
        "I10_tests_ci_external_evidence": _item(BLOCKED, note="CI on the exact SHA and the external evidence are "
                                                              "checked by the reviewer, not by this tool"),
    }


def _rex_status(g14_status, sqlite_result):
    if g14_status == FAIL or sqlite_result == "CERTIFICATION_INVALID":
        return FAIL
    if g14_status == PASS and sqlite_result == "SQLITE_CHECKS_PASS":
        return PASS
    return NOT_VERIFIED


def main(argv=None):
    parser = argparse.ArgumentParser(description="G-8.INT measurement matrix (read-only).")
    parser.add_argument("--trading-db", required=True)
    parser.add_argument("--evidence-db")
    parser.add_argument("--oar-g")
    parser.add_argument("--allowed-signers")
    parser.add_argument("--expected-commit")
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    report = measure(args.trading_db, evidence_db=args.evidence_db, oar_g_path=args.oar_g,
                     allowed_signers=args.allowed_signers, expected_commit=args.expected_commit)
    text = json.dumps(report, indent=2, sort_keys=True, default=str)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
