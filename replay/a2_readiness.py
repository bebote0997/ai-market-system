"""V2 Phase 8 / P4b A2 readiness checks (design P8.6 3.11.1 items 2 and 5; I-C5, I-C7; NC5-NC9, NC11). READ-ONLY.

Checks, all fail-closed (NOT_READY):
- the P2 trading DB path equals the pinned path and is not the P1 path;
- no symlink in any path component (``realpath == abspath``) and ``st_nlink == 1`` for every existing file;
- the P2 trading DB, the P2 Evidence Store and the P1 archive are pairwise distinct by realpath AND (st_dev, st_ino);
  the Evidence Store is neither trading DB (NC9);
- OAR-A verifies (``v2-archive-anchor``) and the archive's SHA-256 still equals its ``backup_sha256`` (I-C7, NC7,
  NC11); the archive's ``-wal`` / ``-shm`` are absent or empty;
- freshness, reconciled with sealed genesis (design 5.1.3.2 supersedes the 3.11.1 "must not exist" wording, because
  the genesis tool creates the P2 file before OAR-G): at the FIRST start the P2 DB must exist with genesis only (one
  GENESIS_PREPARED, no EXPERIMENT_STARTED, no runs); a P2 DB carrying anything else is refused (NC8).
Nothing is created or written; no runtime is constructed.
"""
import hashlib
import json
import os
from pathlib import Path
import sqlite3


def _real(path):
    return os.path.normcase(os.path.realpath(path))


def _identity(path):
    stat = os.stat(path)
    return stat.st_dev, stat.st_ino


def _no_symlink_component(path):
    return _real(path) == os.path.normcase(os.path.abspath(path))


def check(*, p2_db, pinned_p2_db, p1_path, evidence_db, archive, oar_a, allowed_signers, first_start=True,
          account_id="paper-main"):
    from replay.archive_anchor import ARCHIVE_KIND, ARCHIVE_NAMESPACE
    from runtime.genesis import GenesisSealError, verify_signed_document
    paths = {"p2_db": p2_db, "evidence_db": evidence_db, "archive": archive}
    checks = {"p2_db_is_pinned": _real(p2_db) == _real(pinned_p2_db), "p2_db_is_not_p1": _real(p2_db) != _real(p1_path)}
    for name, path in paths.items():
        checks[f"{name}_no_symlink"] = _no_symlink_component(path)
        if Path(path).exists():
            checks[f"{name}_single_link"] = os.stat(path).st_nlink == 1
    existing = {name: path for name, path in paths.items() if Path(path).exists()}
    reals = {name: _real(path) for name, path in paths.items()}
    ids = {name: _identity(path) for name, path in existing.items()}
    checks["pairwise_distinct_realpaths"] = len(set(reals.values())) == len(reals)
    checks["pairwise_distinct_inodes"] = len(set(ids.values())) == len(ids)
    checks["evidence_not_a_trading_db"] = reals["evidence_db"] not in {_real(p2_db), _real(p1_path)} and (
        not Path(evidence_db).exists() or not Path(p1_path).exists() or _identity(evidence_db) != _identity(p1_path))
    try:
        _, data = verify_signed_document(oar_a, namespace=ARCHIVE_NAMESPACE, allowed_signers=allowed_signers)
        body = json.loads(data.decode("utf-8"))
        checks["oar_a_verifies"] = body.get("kind") == ARCHIVE_KIND
    except (GenesisSealError, OSError, ValueError):
        body, checks["oar_a_verifies"] = {}, False
    checks["archive_matches_oar_a"] = Path(archive).is_file() and \
        hashlib.sha256(Path(archive).read_bytes()).hexdigest() == body.get("backup_sha256")
    checks["archive_wal_shm_absent_or_empty"] = all(
        not Path(str(archive) + suffix).exists() or Path(str(archive) + suffix).stat().st_size == 0
        for suffix in ("-wal", "-shm"))
    findings = []
    if first_start:
        findings = genesis_only_findings(p2_db, account_id)
        checks["p2_db_genesis_only"] = not findings
    return {"result": "READY" if all(checks.values()) else "NOT_READY", "checks": checks,
            "genesis_only_findings": findings}


GENESIS_ONLY_ROWS = {("genesis", "GENESIS_PREPARED"), ("genesis", "GENESIS_SEAL_CHECK"),
                     ("runtime", "RECOVERY_STARTED"), ("runtime", "RECOVERY_COMPLETED")}
ECONOMIC_EVENTS = frozenset({"ORDER_SUBMITTED", "ORDER_FILLED", "ORDER_REJECTED", "ORDER_CANCELLED",
                             "POSITION_OPENED", "STOP_HIT", "TARGET_HIT", "POSITION_CLOSED"})


def genesis_only_findings(p2_db, account_id="paper-main"):
    """LOW-1 (P4b audit): the reasons why ``p2_db`` is NOT a genesis-only database ([] = genesis only). Genesis only =
    EDG exactly the expected genesis (one account at 10000 and NO order, fill, position or closed trade), exactly one
    GENESIS_PREPARED, no economic event, no run, no EXPERIMENT_STARTED; the only other rows allowed are the
    non-economic rows of a sealed start that stopped before E0 (RECOVERY_*, a PASS GENESIS_SEAL_CHECK)."""
    from runtime.genesis import GENESIS_EQUITY
    from storage.economic_digest import edg, expected_edg_genesis
    if not Path(p2_db).is_file():
        return ["p2_db_missing"]  # with sealed genesis the P2 file exists (created by the genesis tool) before E0
    conn = sqlite3.connect(Path(p2_db).resolve().as_uri() + "?mode=ro", uri=True)
    reasons = []
    try:
        economic = edg(conn)
        if economic["edg"] != expected_edg_genesis(account_id, float(GENESIS_EQUITY))["edg"]:
            reasons.append("economic_state_is_not_the_expected_genesis")
            reasons += [f"{name}_rows" for name, table in economic["tables"].items()
                        if name != "paper_accounts" and table["count"]]
        rows = conn.execute("SELECT source, event_type, payload FROM journal ORDER BY id").fetchall()
        if sum(1 for source, kind, _ in rows if (source, kind) == ("genesis", "GENESIS_PREPARED")) != 1:
            reasons.append("genesis_prepared_count")
        if any(kind in ECONOMIC_EVENTS for _, kind, _ in rows):
            reasons.append("economic_event_before_e0")
        if any(kind == "EXPERIMENT_STARTED" for _, kind, _ in rows):
            reasons.append("experiment_started")
        unexpected = sorted({kind for source, kind, _ in rows if (source, kind) not in GENESIS_ONLY_ROWS})
        if unexpected:
            reasons.append("unexpected_rows:" + ",".join(unexpected))
        for source, kind, payload in rows:
            if kind == "GENESIS_SEAL_CHECK" and (json.loads(payload) or {}).get("result") != "PASS":
                reasons.append("failed_seal_check")
        if conn.execute("SELECT count(*) FROM runs").fetchone()[0]:
            reasons.append("runs")
    except (sqlite3.Error, ValueError, TypeError, KeyError):
        reasons.append("unreadable")
    finally:
        conn.close()
    return reasons


def _genesis_only(p2_db):
    return not genesis_only_findings(p2_db)


__all__ = ["check"]
