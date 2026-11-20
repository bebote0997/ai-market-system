"""One-off backup and paper purge. Dry-run unless ``--apply`` and the confirm token match.

Nothing here runs at startup, on deploy, or from the scheduler. It copies the trading database and a
JSON export of the paper tables, then deletes those rows and runs ``VACUUM``. OANDA and paper modules
stay in the tree: the test suite and a non-Exness process still use them. The live Exness cycle does
not write paper state for EURUSD, GBPUSD or USDJPY.
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


def _backup(path, backup_dir):
    folder = Path(backup_dir)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = folder / f"{path.stem}-{stamp}.db"
    shutil.copy2(path, target)
    os.chmod(target, 0o600)
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


def apply_purge(db_path, *, confirm, backup_dir):
    """Copy first, then delete paper rows and VACUUM. Refuses without the exact confirm token."""
    if confirm != CONFIRM:
        refused = plan(db_path)
        refused["reason"] = "confirm_required"
        refused["deleted"] = False
        return refused
    path = Path(db_path)
    if not path.is_file():
        return {"dry_run": False, "exists": False, "deleted": False, "reason": "missing"}
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
            "counts_before": before, "counts_after": after, "vacuum": True, "reason": "purged"}


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
    return 0 if report.get("deleted") else 2


if __name__ == "__main__":
    raise SystemExit(main())
