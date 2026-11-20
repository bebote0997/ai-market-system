"""One-off backup and paper purge. Dry-run unless ``--apply`` and the confirm token match.

Nothing here runs at startup, on deploy, or from the scheduler. The backup is a SQLite online backup
(``Connection.backup``), so the WAL is included. It refuses to run when free disk space is under twice
the database size (main file plus ``-wal``). Then it exports the paper tables, deletes those rows and
runs ``VACUUM``.

Stop the service before ``--apply``, or restart it immediately after. A process that still has the
file open can write into the pre-VACUUM pages.

OANDA and paper modules stay in the tree: the test suite and a non-Exness process still use them.
The live Exness cycle does not write paper state for EURUSD, GBPUSD or USDJPY.
"""
from datetime import datetime, timezone
import argparse
import json
import logging
import os
import shutil
import sqlite3
from pathlib import Path


LOG = logging.getLogger("ai_floor.purge")
CONFIRM = "EXNESS_PURGE_AUTHORIZED"
CONFIRM_ENV = "AI_FLOOR_PURGE_CONFIRM"
PAPER_TABLES = ("paper_orders", "paper_fills", "paper_positions", "closed_trades", "paper_accounts")


def _counts(db):
    found = {}
    for table in PAPER_TABLES:
        row = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        found[table] = 0 if row is None else db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    return found


def plan(db_path):
    """Read-only. No copy, no delete, no VACUUM."""
    path = Path(db_path)
    if not path.is_file():
        return {"dry_run": True, "exists": False, "counts": {}, "backup": None, "vacuum": False}
    db = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        counts = _counts(db)
    finally:
        db.close()
    return {"dry_run": True, "exists": True, "counts": counts, "backup": None, "vacuum": False}


def _db_bytes(path):
    """Main file plus the WAL. A plain copy of the main file would drop uncheckpointed pages."""
    size = path.stat().st_size
    wal = path.with_name(path.name + "-wal")
    if wal.is_file():
        size += wal.stat().st_size
    return size


def _room_for_backup(path, disk_usage):
    usage = disk_usage(path.parent)
    return usage.free >= 2 * _db_bytes(path)


def _online_backup(path, target):
    """Consistent snapshot, WAL included. The result is one file (``journal_mode=DELETE``)."""
    src = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5.0)
    try:
        dst = sqlite3.connect(target, timeout=5.0)
        try:
            src.backup(dst)
            dst.execute("PRAGMA journal_mode=DELETE")
        finally:
            dst.close()
    finally:
        src.close()
    os.chmod(target, 0o600)


def _backup(path, backup_dir):
    folder = Path(backup_dir)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = folder / f"{path.stem}-{stamp}.db"
    _online_backup(path, target)
    db = sqlite3.connect(target)
    try:
        export = {}
        for table in PAPER_TABLES:
            rows = db.execute(f"SELECT * FROM {table}").fetchall() if _counts(db)[table] else []
            export[table] = [list(row) for row in rows]
        payload = folder / f"{path.stem}-{stamp}.paper.json"
        payload.write_text(json.dumps({"tables": export}, sort_keys=True, default=str))
        os.chmod(payload, 0o600)
    finally:
        db.close()
    return str(target)


def apply_purge(db_path, *, confirm, backup_dir, disk_usage=shutil.disk_usage):
    """Back up first, then delete paper rows and VACUUM. Refuses without the exact confirm token.

    Aborts, with nothing deleted, when free space is under twice the database size.
    Stop the service before this, or restart it immediately after.
    """
    if confirm != CONFIRM:
        refused = plan(db_path)
        refused["reason"] = "confirm_required"
        refused["deleted"] = False
        return refused
    path = Path(db_path)
    if not path.is_file():
        return {"dry_run": False, "exists": False, "deleted": False, "reason": "missing"}
    if not _room_for_backup(path, disk_usage):
        LOG.warning("component=purge event=PURGE_ABORTED reason=low_space")
        return {"dry_run": False, "exists": True, "deleted": False, "reason": "low_space",
                "vacuum": False, "backup": None, "restart_required": False}
    backup = _backup(path, backup_dir)
    db = sqlite3.connect(path, isolation_level=None)
    try:
        before = _counts(db)
        db.execute("BEGIN")
        try:
            for table in PAPER_TABLES:
                if before[table]:
                    db.execute(f"DELETE FROM {table}")
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
        db.execute("VACUUM")
        after = _counts(db)
    finally:
        db.close()
    LOG.info("component=purge event=PAPER_PURGED tables=%s", ",".join(PAPER_TABLES))
    return {"dry_run": False, "exists": True, "deleted": True, "backup": backup,
            "counts_before": before, "counts_after": after, "vacuum": True, "reason": "purged",
            "restart_required": True}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Back up and purge paper tables. Dry-run by default.")
    parser.add_argument("--db", default=os.environ.get("AI_FLOOR_DB_PATH") or "data/runtime/trading_floor.db")
    parser.add_argument("--backup-dir", default="data/runtime/purge-backup")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    if not args.apply:
        report = plan(args.db)
        print("dry_run=true deleted=false vacuum=false")
        for table, count in report["counts"].items():
            print(f"{table}={count}")
        return 0
    report = apply_purge(args.db, confirm=os.environ.get(CONFIRM_ENV), backup_dir=args.backup_dir)
    print(f"dry_run=false deleted={str(report.get('deleted')).lower()} reason={report.get('reason')}")
    if report.get("restart_required"):
        print("restart_required=true")
    return 0 if report.get("deleted") else 2


if __name__ == "__main__":
    raise SystemExit(main())
