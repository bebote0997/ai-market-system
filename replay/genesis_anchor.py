"""V2 Phase 8 / P2b OAR-G body builder (design P8.6 5.1.3.2 G2-G3). READ-ONLY; never signs.

From a backup-API copy of a freshly prepared genesis database it checks G2 and emits the canonical (``cj``) body of
``V2_OAR_G/1`` for the Owner to sign (``ssh-keygen -Y sign -n v2-genesis-anchor``; operational act OP-7). G2:
- EDG equals the independently recomputed expected genesis for (``account_id``, 10000);
- exactly one ``paper_accounts`` row and no other economic row; exactly one ``GENESIS_PREPARED``;
- ``experiment_started`` absent, zero runs and zero economic journal events.
Any failure refuses (no body). The source file's ``realpath``, ``st_dev`` and ``st_ino`` are taken from the original
path (the anchor binds the file that will be run), everything else from the copy.

Usage: python -m replay.genesis_anchor --trading-db PATH --period-id ID --x-p2 SHA --y-p2 SHA --out BODY.json
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

from runtime.genesis import GENESIS_EQUITY, OAR_G_KIND, PREPARED_EVENT, SHA_PATTERN
from storage.economic_digest import cj, edg, expected_edg_genesis, psh

ECONOMIC_EVENTS = ("ORDER_SUBMITTED", "ORDER_FILLED", "ORDER_REJECTED", "ORDER_CANCELLED", "POSITION_OPENED",
                   "STOP_HIT", "TARGET_HIT", "POSITION_CLOSED")


def build(trading_db, *, period_id, x_p2, y_p2, account_id="paper-main", created_at=None):
    """Returns (report, body_text or None)."""
    from storage.database import Store
    source = Path(trading_db)
    if not source.is_file():
        raise FileNotFoundError(f"source database not found: {source}")
    for name, value in (("x_p2", x_p2), ("y_p2", y_p2)):
        if not isinstance(value, str) or not SHA_PATTERN.match(value):
            raise ValueError(f"{name} must be a 40-hex commit SHA")
    stat = os.stat(source)
    with tempfile.TemporaryDirectory(prefix="genesis-anchor-", ignore_cleanup_errors=True) as tmp:
        copy = Path(tmp) / "genesis_copy.db"
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
            economic = edg(conn)
            try:
                state = psh(store.paper_state(*store.load_paper(account_id)))
            except (KeyError, TypeError, ValueError, AttributeError):
                state = None  # a malformed economic payload: never a genesis (refused below)
            schema_version = conn.execute("SELECT version FROM schema_info").fetchone()[0]
            max_journal_id = conn.execute("SELECT COALESCE(MAX(id),0) FROM journal").fetchone()[0]
            sequence = {name: seq for name, seq in conn.execute("SELECT name, seq FROM sqlite_sequence")}
            runs = conn.execute("SELECT count(*) FROM runs").fetchone()[0]
            started = conn.execute("SELECT value FROM system_state WHERE key='experiment_started'").fetchone()
            events = conn.execute("SELECT count(*) FROM journal WHERE event_type IN (%s)" %
                                  ",".join("?" * len(ECONOMIC_EVENTS)), ECONOMIC_EVENTS).fetchone()[0]
            prepared = conn.execute("SELECT count(*) FROM journal WHERE source='genesis' AND event_type=?",
                                    (PREPARED_EVENT,)).fetchone()[0]
        finally:
            store.close()
            conn.close()
        copy_sha256 = hashlib.sha256(copy.read_bytes()).hexdigest()
    expected = expected_edg_genesis(account_id, float(GENESIS_EQUITY))
    counts = {name: table["count"] for name, table in economic["tables"].items()}
    checks = {"economic_payloads_readable": state is not None,
              "edg_equals_expected_genesis": economic["edg"] == expected["edg"],
              "one_account_no_other_economic_row": counts == {"paper_accounts": 1, "paper_orders": 0,
                                                              "paper_fills": 0, "paper_positions": 0,
                                                              "closed_trades": 0},
              "one_genesis_prepared": prepared == 1, "experiment_not_started": started is None,
              "zero_runs": runs == 0, "zero_economic_events": events == 0}
    report = {"checks": checks, "edg_observed": economic["edg"], "edg_expected": expected["edg"],
              "result": "READY_TO_SIGN" if all(checks.values()) else "REFUSED"}
    if not all(checks.values()):
        return report, None
    body = {"kind": OAR_G_KIND, "period_id_pending": period_id, "account_id": account_id,
            "starting_equity": GENESIS_EQUITY, "db_realpath": os.path.realpath(source), "st_dev": stat.st_dev,
            "st_ino": stat.st_ino, "edg_genesis": economic["edg"], "psh_genesis": state,
            "max_journal_id": max_journal_id, "sqlite_sequence": sequence, "schema_version": schema_version,
            "x_p2": x_p2, "y_p2": y_p2, "copy_sha256": copy_sha256,
            "created_at_utc": (created_at or datetime.now(timezone.utc)).isoformat()}
    return report, cj(body)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build the unsigned OAR-G body (G2-G3). Never signs.")
    parser.add_argument("--trading-db", required=True)
    parser.add_argument("--period-id", required=True)
    parser.add_argument("--x-p2", required=True)
    parser.add_argument("--y-p2", required=True)
    parser.add_argument("--account", default="paper-main")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    protected = {os.path.normcase(os.path.realpath(args.trading_db + suffix)) for suffix in ("", "-wal", "-shm")}
    if os.path.normcase(os.path.realpath(args.out)) in protected:
        raise SystemExit("refusing to write over the source database or its -wal/-shm files")
    report, body = build(args.trading_db, period_id=args.period_id, x_p2=args.x_p2, y_p2=args.y_p2,
                         account_id=args.account)
    print(json.dumps(report, indent=2, sort_keys=True))
    if body is None:
        return 2
    Path(args.out).write_text(body, encoding="utf-8")
    print(f"unsigned OAR-G body written to {args.out}; the Owner signs it with "
          f"ssh-keygen -Y sign -n v2-genesis-anchor (OP-7)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
