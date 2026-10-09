"""V2 Phase 8 / P2a-lib (DEC-8.22-c, restricted scope): READ-ONLY economic digest verifier for Owner-provided copies.

The source trading database is opened with ``mode=ro`` and copied into a private temporary directory through
SQLite's backup API (includes committed WAL content); every computation runs on the copy. The source file and its
``-wal`` / ``-shm`` companions are hashed before and after; ``source_unchanged`` must be true (exit code 2 otherwise).
It never classifies experiment state (DEC-8.18 pending) and never writes anything except the optional report file.

Usage: python -m replay.economic_verifier --trading-db COPY.db [--account paper-main] [--out report.json]
"""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile

from storage.economic_digest import edg, psh

REPORT_VERSION = "V2_P2A_ECONOMIC_VERIFIER_1"


def _sha256(path):
    path = Path(path)
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fingerprint(path):
    return {suffix or "db": _sha256(str(path) + suffix) for suffix in ("", "-wal", "-shm")}


def verify(trading_db, *, account_id="paper-main"):
    from storage.database import Store  # read-only use of the copy
    source = Path(trading_db)
    if not source.is_file():
        raise FileNotFoundError(f"source database not found: {source}")
    before = _fingerprint(source)
    with tempfile.TemporaryDirectory(prefix="economic-verifier-") as tmp:
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
        try:
            schema_version = conn.execute("SELECT version FROM schema_info").fetchone()[0]
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            economic = edg(conn)
        finally:
            conn.close()
        store = Store(copy, readonly=True)
        try:
            account, orders, fills = store.load_paper(account_id)
            state_digest = psh(store.paper_state(account, orders, fills))
        finally:
            store.close()
    after = _fingerprint(source)
    return {"report_version": REPORT_VERSION, "account_id": account_id, "read_only": True,
            "source_unchanged": before == after, "source_hashes": before, "schema_version": schema_version,
            "integrity_check": integrity, "account_present": account is not None, "edg_version": economic["edg_version"],
            "edg": economic["edg"], "tables": economic["tables"], "psh": state_digest,
            "note": "psh covers only the CAS state (no CLOSED positions); edg covers every economic row. Snapshot "
                    "digests detect persistent divergence only, never an alteration reverted between observations."}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only economic digest verifier (PAPER).")
    parser.add_argument("--trading-db", required=True)
    parser.add_argument("--account", default="paper-main")
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    if args.out and Path(args.out).resolve() == Path(args.trading_db).resolve():
        raise SystemExit("refusing to write the report over the source database")
    report = verify(args.trading_db, account_id=args.account)
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0 if report["source_unchanged"] else 2


if __name__ == "__main__":
    sys.exit(main())
