"""V2 Phase 8 / P4a R-HALT verifier (design P8.6 3.8; DEC-8.17b Alternative 1). READ-ONLY, standard library only.

Input: the HALT_OBSERVED rows, the REX_WRITE records (with their ``admission`` evidence) and the journal index of the
window, as parsed by ``replay.rex_chain``. Checks, per halt (one process, identified by ``process_id``):
- evidence: every committed write of that process carries ``admission`` (process id, ``L(W)``, ``read2``,
  ``T_stop``); missing evidence -> INVALID;
- I-R1b: ``l_w_ns < T_h`` and ``read2_ns < T_h`` for every committed write of the process;
- I-R9 / I-R13: at most ONE committed write with ``T_stop > T_h`` (the accepted residual), and ``T_ack >= T_stop``;
  the residual is MEASURED (read2 -> T_h, T_h -> T_stop, T_stop -> T_ack) and reported, never hidden;
- the recorded residual equals the recomputed one;
- post-halt allowlist (journal level): after HALT_OBSERVED and until the next process start (RECOVERY_STARTED) only
  REX evidence rows of the halted run; no write of the halted process after its HALT_OBSERVED.

Not provable here: ``system_state`` history (P keys) and the exact physical instant of each row (checked in-process
by the tests); a process killed before ``T_ack`` leaves no HALT_OBSERVED, so HALT_INTERRUPTED needs an external
``T_h`` (H5) and is never asserted from SQLite alone. Nothing here can yield VERIFIED.
"""
import json

HALT_VERIFIER_VERSION = "V2_P4A_HALT_VERIFIER/1"
ALLOWED_POST_HALT = frozenset({("rex", "REX_RUN")})  # R restricted: the halted run's record only


def _finding(severity, code, detail, context=None):
    item = {"severity": severity, "code": code, "detail": str(detail)}
    if context is not None:
        item["context"] = context
    return item


def check(halts, writes, journal_index, runs=None):
    """``halts``: [(journal_id, payload_text)]; ``writes``: [(journal_id, REX_WRITE record)];
    ``journal_index``: [(journal_id, source, event_type, run_id)] in id order; ``runs``: {run_id: (journal_id,
    REX_RUN record)}. Returns (findings, summary)."""
    runs = runs or {}
    run_records = {journal_id: record for journal_id, record in runs.values()}
    findings, summary = [], []
    starts = [jid for jid, source, event_type, _ in journal_index
              if source == "runtime" and event_type == "RECOVERY_STARTED"]
    for halt_id, text in halts:
        try:
            payload = json.loads(text)
            process_id = payload["process_id"]
            t_h = int(payload["t_h_monotonic_ns"])
            t_ack = int(payload["t_ack_ns"])
            run_id = payload.get("run_id")
        except (ValueError, TypeError, KeyError) as exc:
            findings.append(_finding("INVALID", "HALT_RECORD_MALFORMED", f"journal {halt_id}",
                                     {"journal_id": halt_id, "error_type": type(exc).__name__}))
            continue
        boundary = max((jid for jid in starts if jid < halt_id), default=0)
        following = min((jid for jid in starts if jid > halt_id), default=None)
        process_writes = []
        for journal_id, w in writes:
            if journal_id > halt_id or w.get("result") != "COMMITTED":
                continue
            admission = w.get("admission")
            context = {"halt_journal_id": halt_id, "rex_journal_id": journal_id}
            mine = isinstance(admission, dict) and admission.get("process_id") == process_id
            if not mine:
                if boundary < journal_id:  # inside the halted process's last start window: evidence required
                    findings.append(_finding("INVALID", "ADMISSION_EVIDENCE_MISSING",
                                             f"write {journal_id} has no admission of the halted process", context))
                continue
            try:
                l_w, read2, t_stop = int(admission["l_w_ns"]), admission.get("read2_ns"), admission.get("t_stop_ns")
            except (KeyError, TypeError, ValueError) as exc:
                findings.append(_finding("INVALID", "ADMISSION_EVIDENCE_MISSING", f"write {journal_id}",
                                         {**context, "error_type": type(exc).__name__}))
                continue
            if read2 is None or t_stop is None:
                findings.append(_finding("INVALID", "ADMISSION_EVIDENCE_MISSING",
                                         f"write {journal_id}: read2 / T_stop not recorded", context))
                continue
            if l_w >= t_h:
                findings.append(_finding("INVALID", "ADMITTED_AFTER_HALT", f"write {journal_id} (I-R1b)", context))
            if int(read2) >= t_h:
                findings.append(_finding("INVALID", "READ2_AFTER_HALT", f"write {journal_id} (I-R1b)", context))
            process_writes.append({"rex_journal_id": journal_id, "seq": admission.get("seq"),
                                   "kind": admission.get("kind"), "l_w_ns": l_w, "read2_ns": int(read2),
                                   "t_stop_ns": int(t_stop)})
        residual = [w for w in process_writes if w["t_stop_ns"] > t_h]
        if len(residual) > 1:
            findings.append(_finding("INVALID", "RESIDUAL_ABOVE_ONE",
                                     f"{len(residual)} committed writes returned after T_h (I-R9 / I-R13)",
                                     {"halt_journal_id": halt_id, "writes": [w["rex_journal_id"] for w in residual]}))
        latest_stop = max((w["t_stop_ns"] for w in process_writes), default=None)
        if latest_stop is not None and t_ack < latest_stop:
            findings.append(_finding("INVALID", "T_ACK_BEFORE_T_STOP", f"halt {halt_id} (I-R13)",
                                     {"halt_journal_id": halt_id}))
        recorded = sorted(item.get("seq") for item in payload.get("residual") or [])
        if recorded != sorted(w["seq"] for w in residual):
            findings.append(_finding("INVALID", "HALT_RECORD_CONTRADICTION", f"halt {halt_id}: residual",
                                     {"halt_journal_id": halt_id, "recorded": recorded}))
        post_runs = 0
        for journal_id, source, event_type, row_run in journal_index:
            if journal_id <= halt_id or (following is not None and journal_id >= following):
                continue
            context = {"halt_journal_id": halt_id, "journal_id": journal_id, "event_type": event_type,
                       "source": source}
            # R after T_h (restricted): one REX_RUN of the halted run, of the halted process; nothing else
            if (source, event_type) not in ALLOWED_POST_HALT or run_id is None or row_run != run_id:
                findings.append(_finding("INVALID", "POST_HALT_WRITE", f"journal {journal_id} after HALT_OBSERVED",
                                         context))
                continue
            post_runs += 1
            record = run_records.get(journal_id) or {}
            if post_runs > 1 or record.get("process_id") != process_id:
                findings.append(_finding("INVALID", "POST_HALT_EVIDENCE_NOT_AUTHORIZED",
                                         f"journal {journal_id}: a duplicate or foreign REX_RUN after HALT_OBSERVED",
                                         {**context, "process_id": record.get("process_id")}))
        for journal_id, w in writes:
            admission = w.get("admission") or {}
            if journal_id > halt_id and admission.get("process_id") == process_id:
                findings.append(_finding("INVALID", "WRITE_AFTER_HALT_RECORD", f"write {journal_id}",
                                         {"halt_journal_id": halt_id, "rex_journal_id": journal_id}))
        summary.append({
            "halt_journal_id": halt_id, "process_id": process_id, "t_h_utc": payload.get("t_h_utc"),
            "refused_kind": payload.get("refused_kind"), "refused_stage": payload.get("refused_stage"),
            "run_id": run_id, "symbol_lock_released": payload.get("symbol_lock_released"),
            "admitted_writes": len(process_writes),
            "residual": [{"rex_journal_id": w["rex_journal_id"], "seq": w["seq"], "kind": w["kind"],
                          "read2_to_t_h_ns": t_h - w["read2_ns"], "t_h_to_t_stop_ns": w["t_stop_ns"] - t_h,
                          "t_stop_to_t_ack_ns": t_ack - w["t_stop_ns"]} for w in residual]})
    if starts and not halts:
        summary.append({"note": "process restarts without HALT_OBSERVED: HALT_INTERRUPTED is not decidable from "
                                "SQLite (needs an external T_h, H5)"})
    return findings, summary


__all__ = ["HALT_VERIFIER_VERSION", "check"]
