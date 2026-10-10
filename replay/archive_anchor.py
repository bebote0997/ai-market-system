"""V2 Phase 8 / P4b P1 archive and OAR-A body (design P8.6 3.11.1 items 4 and 6). Never signs; never touches the source.

The archive is a consistent copy made with SQLite's backup API from a ``mode=ro`` connection (committed WAL content
included; never a raw file copy, never a checkpoint on the source). On the copy: ``integrity_check`` = ok, schema 3,
row counts per table, ``MAX(journal.id)``, ``sqlite_sequence``, the hash and equity of the final account payload and the
FLAT proof (0 OPEN positions, 0 PENDING orders, 0 RUNNING runs; otherwise the archive is refused, NC12). The canonical
``TRADING_DB_ARCHIVE`` body is emitted for the Owner to sign (``ssh-keygen -Y sign -n v2-archive-anchor``); the H5
attestation is required as input (its hash is bound), never produced here.

Usage: python -m replay.archive_anchor --trading-db P1.db --archive OUT.db --h5-attestation H5 --out OAR_A.json
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys

from storage.economic_digest import cj

ARCHIVE_KIND = "TRADING_DB_ARCHIVE"
ARCHIVE_NAMESPACE = "v2-archive-anchor"


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build(trading_db, archive, *, h5_attestation, archive_time=None):
    """Writes the archive copy and returns (report, body_text or None). A refused archive copy is removed."""
    source, archive = Path(trading_db), Path(archive)
    if not source.is_file():
        raise FileNotFoundError(f"source database not found: {source}")
    if h5_attestation is None or not Path(h5_attestation).is_file():
        raise ValueError("an H5 attestation file is required (EX = STOPPED before the copy)")
    protected = {os.path.normcase(os.path.realpath(str(source) + suffix)) for suffix in ("", "-wal", "-shm")}
    if archive.exists() or os.path.normcase(os.path.realpath(archive)) in protected:
        raise ValueError("the archive target must be a new file distinct from the source")
    stat = os.stat(source)
    src = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(archive)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    conn = sqlite3.connect(archive.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        schema = conn.execute("SELECT version FROM schema_info").fetchone()[0]
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE "
                                             "'sqlite_%' ORDER BY name")]
        counts = {name: conn.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0] for name in tables}
        max_journal_id = conn.execute("SELECT COALESCE(MAX(id),0) FROM journal").fetchone()[0]
        sequence = {name: seq for name, seq in conn.execute("SELECT name, seq FROM sqlite_sequence")}
        open_positions = conn.execute("SELECT count(*) FROM paper_positions WHERE "
                                      "json_extract(payload,'$.status')='OPEN'").fetchone()[0]
        pending_orders = conn.execute("SELECT count(*) FROM paper_orders WHERE "
                                      "json_extract(payload,'$.status')='PENDING'").fetchone()[0]
        running_runs = conn.execute("SELECT count(*) FROM runs WHERE status='RUNNING'").fetchone()[0]
        accounts = conn.execute("SELECT payload FROM paper_accounts ORDER BY account_id").fetchall()
        state = dict(conn.execute("SELECT key, value FROM system_state WHERE key IN ('experiment_started_at_utc',"
                                  "'experiment_baseline_sha','experiment_freeze_sha')").fetchall())
    finally:
        conn.close()
    checks = {"integrity_ok": integrity == "ok", "schema_3": schema == 3, "one_account": len(accounts) == 1,
              "flat_no_open_positions": open_positions == 0, "flat_no_pending_orders": pending_orders == 0,
              "flat_no_running_runs": running_runs == 0}
    report = {"checks": checks, "result": "READY_TO_SIGN" if all(checks.values()) else "REFUSED"}
    if not all(checks.values()):
        archive.unlink()  # our own refused copy; the source is untouched
        return report, None
    account_payload = accounts[0][0]
    body = {"kind": ARCHIVE_KIND,
            "p1_identity": {"experiment_started_at_utc": state.get("experiment_started_at_utc", "NOT_STARTED"),
                            "baseline_sha": state.get("experiment_baseline_sha", "NOT_STARTED"),
                            "freeze_sha": state.get("experiment_freeze_sha", "NOT_STARTED")},
            "backup_sha256": _sha256(archive), "integrity_check": integrity, "schema_version": schema,
            "row_counts": counts, "max_journal_id": max_journal_id, "sqlite_sequence": sequence,
            "final_account_payload_sha256": hashlib.sha256(account_payload.encode("utf-8")).hexdigest(),
            "final_account_equity_text": str(json.loads(account_payload)["equity"]),
            "open_positions": 0, "pending_orders": 0, "running_runs": 0,
            "source_realpath": os.path.realpath(source), "source_st_dev": stat.st_dev, "source_st_ino": stat.st_ino,
            "archive_time_utc": (archive_time or datetime.now(timezone.utc)).isoformat(),
            "h5_attestation_sha256": _sha256(h5_attestation)}
    return report, cj(body)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Archive the P1 trading DB and build the unsigned OAR-A body.")
    parser.add_argument("--trading-db", required=True)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--h5-attestation", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    report, body = build(args.trading_db, args.archive, h5_attestation=args.h5_attestation)
    print(json.dumps(report, indent=2, sort_keys=True))
    if body is None:
        return 2
    Path(args.out).write_text(body, encoding="utf-8")
    print(f"unsigned OAR-A body written to {args.out}; the Owner signs it (ssh-keygen -Y sign -n {ARCHIVE_NAMESPACE})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
