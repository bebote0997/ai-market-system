"""V2 Phase 8 / P2b genesis checks for G15 (design P8.6 5.1.3.2 GEN-1..GEN-5, I-G12..I-G14, I-G18). READ-ONLY.

Applies only to a genesis period: one with a supplied OAR-G or a GENESIS_PREPARED row. Findings:
- GEN-1: no OAR-G -> NOT VERIFIED (``edg_start`` unanchored); an OAR-G that does not verify -> NOT VERIFIED; an OAR-G
  whose ``edg_genesis`` differs from the recomputed expected genesis -> INVALID (NG40); a supplied ``edg_start``
  that differs from OAR-G -> INVALID.
- File identity (only when the ORIGINAL file is verified): realpath / st_dev / st_ino differ -> INVALID (NG41).
- GEN-2: (a) an economic event before ``E0`` -> INVALID; (b) a run before ``E0`` with an economic row -> INVALID;
  (c) no PASS GENESIS_SEAL_CHECK before ``E0`` -> NOT VERIFIED; (d) is the EDG chain from ``edg_start`` (G15).
- I-G18: every process start (RECOVERY_STARTED) is followed by a PASS seal check before the next start or economic
  event; missing -> NOT VERIFIED; any FAIL check -> INVALID; a check bound to another OAR-G -> INVALID.
- GENESIS_PREPARED: exactly one, before every other row of the period.
Never detected (stated, NG48): a deletion followed by an identical restoration between observations.
"""
import hashlib
import json
import os

GENESIS_VERIFIER_VERSION = "V2_P2B_GENESIS_VERIFIER/1"
ECONOMIC_EVENTS = frozenset({"ORDER_SUBMITTED", "ORDER_FILLED", "ORDER_REJECTED", "ORDER_CANCELLED",
                             "POSITION_OPENED", "STOP_HIT", "TARGET_HIT", "POSITION_CLOSED"})


def _finding(severity, code, detail, context=None):
    item = {"severity": severity, "code": code, "detail": str(detail)}
    if context is not None:
        item["context"] = context
    return item


def load_oar_g(oar_g_path, allowed_signers):
    """(document dict, sha256) when the OAR-G verifies; (None, reason) otherwise. Uses the runtime verifier."""
    from runtime.genesis import (ANCHOR_NAMESPACE, OAR_G_FIELDS, OAR_G_KIND, GenesisSealError, parse_canonical,
                                 verify_signed_document)
    try:
        _, data = verify_signed_document(oar_g_path, namespace=ANCHOR_NAMESPACE, allowed_signers=allowed_signers)
        return parse_canonical(data, OAR_G_KIND, OAR_G_FIELDS), hashlib.sha256(data).hexdigest()
    except (GenesisSealError, OSError) as exc:
        return None, str(exc)


def check(rows, *, oar_g=None, oar_g_sha256=None, oar_g_error=None, edg_start=None, source_identity=None):
    """``rows``: journal [(id, source, event_type, run_id, payload)] in id order. Returns (findings, summary)."""
    from storage.economic_digest import expected_edg_genesis
    prepared = [r for r in rows if r[1] == "genesis" and r[2] == "GENESIS_PREPARED"]
    if oar_g is None and oar_g_error is None and not prepared:
        return [], {"status": "NOT APPLICABLE", "note": "no genesis evidence in this window"}
    findings = []
    if oar_g is None:
        findings.append(_finding("NOT_VERIFIED", "OAR_G_ABSENT" if oar_g_error is None else "OAR_G_NOT_VERIFIED",
                                 oar_g_error or "no OAR-G supplied: edg_start is not anchored (GEN-1)"))
    else:
        expected = expected_edg_genesis(oar_g["account_id"], float(oar_g["starting_equity"]))["edg"]
        if oar_g["edg_genesis"] != expected:
            findings.append(_finding("INVALID", "OAR_G_GENESIS_NOT_EXPECTED", "NG40",
                                     {"oar_g": oar_g["edg_genesis"], "expected": expected}))
        if edg_start is not None and edg_start != oar_g["edg_genesis"]:
            findings.append(_finding("INVALID", "EDG_START_NOT_OAR_G", "GEN-1", {"edg_start": edg_start}))
        if source_identity is not None:
            anchored = (os.path.normcase(oar_g["db_realpath"]), oar_g["st_dev"], oar_g["st_ino"])
            if tuple(source_identity) != anchored:
                findings.append(_finding("INVALID", "DB_FILE_SUBSTITUTED", "NG41",
                                         {"observed": list(source_identity), "anchored": list(anchored)}))
    if len(prepared) != 1:
        findings.append(_finding("INVALID", "GENESIS_PREPARED_COUNT", f"{len(prepared)} GENESIS_PREPARED rows"))
    elif any(r[0] < prepared[0][0] for r in rows):
        findings.append(_finding("INVALID", "ROWS_BEFORE_GENESIS", "journal rows precede GENESIS_PREPARED"))
    e0 = next((r[0] for r in rows if r[1] == "experiment" and r[2] == "EXPERIMENT_STARTED"), None)
    seals = []
    for journal_id, source, event_type, _, payload in rows:
        if source == "genesis" and event_type == "GENESIS_SEAL_CHECK":
            try:
                body = json.loads(payload)
            except (TypeError, ValueError):
                body = {}
            seals.append((journal_id, body))
            if body.get("result") != "PASS":
                findings.append(_finding("INVALID", "SEAL_CHECK_FAILED", f"journal {journal_id}",
                                         {"journal_id": journal_id, "reason": body.get("reason")}))
            elif oar_g_sha256 is not None and body.get("oar_g_sha256") != oar_g_sha256:
                findings.append(_finding("INVALID", "SEAL_CHECK_OTHER_ANCHOR", f"journal {journal_id}",
                                         {"journal_id": journal_id}))
    limit = e0 if e0 is not None else float("inf")
    early = [r[0] for r in rows if r[2] in ECONOMIC_EVENTS and r[0] < limit]
    if early:
        findings.append(_finding("INVALID", "ECONOMIC_EVENT_BEFORE_E0", "GEN-2 (a)", {"journal_ids": early}))
    early_runs = {r[3] for r in rows if r[2] == "RUN_STARTED" and r[0] < limit}
    touched = {r[3] for r in rows if (r[2] in ECONOMIC_EVENTS or (r[1] == "rex" and r[2] == "REX_WRITE"))}
    if early_runs & touched:
        findings.append(_finding("INVALID", "RUN_BEFORE_E0_WITH_ECONOMIC_ROW", "GEN-2 (b)",
                                 {"run_ids": sorted(early_runs & touched, key=str)}))
    if e0 is not None and not any(j < e0 and b.get("result") == "PASS" for j, b in seals):
        findings.append(_finding("NOT_VERIFIED", "NO_SEALED_START_BEFORE_E0", "GEN-2 (c)"))
    starts = [r[0] for r in rows if r[1] == "runtime" and r[2] == "RECOVERY_STARTED"]
    for index, start in enumerate(starts):
        boundary = starts[index + 1] if index + 1 < len(starts) else float("inf")
        first_economic = next((r[0] for r in rows if start < r[0] < boundary and r[2] in ECONOMIC_EVENTS), boundary)
        if not any(start < j < first_economic and b.get("result") == "PASS" for j, b in seals):
            findings.append(_finding("NOT_VERIFIED", "PROCESS_START_WITHOUT_SEAL_CHECK", f"journal {start} (I-G18)",
                                     {"recovery_started": start}))
    invalid = any(f["severity"] == "INVALID" for f in findings)
    not_verified = any(f["severity"] == "NOT_VERIFIED" for f in findings)
    status = "INVALID" if invalid else "NOT VERIFIED" if not_verified else "ANCHORED (SQLITE CHECKS PASS)"
    return findings, {"status": status, "e0_journal_id": e0, "seal_checks": len(seals), "process_starts": len(starts),
                      "edg_start": None if oar_g is None else oar_g["edg_genesis"],
                      "limits": "an identical restoration between observations is not detectable (NG48)"}


__all__ = ["GENESIS_VERIFIER_VERSION", "check", "load_oar_g"]
