"""Disk-full handling and retention for the trading SQLite file.

A full disk must not crash-loop the service. Callers catch ``database or disk is full``,
log ``DISK_FULL``, and continue. Retention deletes old REX journal rows and old archive
copies. It never runs a purge and never deletes the newest archive.
"""
from datetime import datetime, timedelta, timezone
import logging
import os
import shutil
import sqlite3
import time

from storage.codec import utc


LOG = logging.getLogger("ai_floor.disk")
DISK_WARN_RATIO = 0.80
REX_KEEP_DAYS = 14
ARCHIVE_KEEP_DAYS = 14


def is_disk_full(exc):
    return isinstance(exc, sqlite3.OperationalError) and "disk is full" in str(exc).lower()


def log_disk_full(where):
    LOG.error("component=storage event=DISK_FULL where=%s", where)


def disk_soft(action, where, *, fallback=False):
    """Run ``action``. A full disk is logged and becomes ``fallback`` instead of escaping.

    ``storage/database.py`` stays byte-identical. The runtime installs this around ``save_paper`` and ``recover``.
    """
    try:
        return action()
    except sqlite3.OperationalError as exc:
        if not is_disk_full(exc):
            raise
        log_disk_full(where)
        return fallback


def usage_ratio(path):
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    if usage.total <= 0:
        return None
    return (usage.total - usage.free) / usage.total


_warned = False


def warn_if_tight(path, *, threshold=DISK_WARN_RATIO, ratio=None):
    """Log ``DISK_USAGE_WARNING`` once per process when used space is at or above ``threshold``.

    Returns the warning dict, or None when the disk is under the threshold or cannot be read.
    """
    global _warned
    if ratio is None:
        ratio = usage_ratio(path)
    if ratio is None or ratio < threshold:
        return None
    detail = {"event": "DISK_USAGE_WARNING", "used_pct": int(ratio * 100),
              "threshold_pct": int(threshold * 100), "severity": "WARNING"}
    if not _warned:
        _warned = True
        LOG.warning("component=disk event=DISK_USAGE_WARNING used_pct=%s threshold_pct=%s",
                    detail["used_pct"], detail["threshold_pct"])
    return detail


def reset_disk_warning():
    """Test hook. Production calls this never."""
    global _warned
    _warned = False


def _sealed_genesis_on(env):
    """True when ``AI_FLOOR_V2_SEALED_GENESIS`` is set to anything other than empty or ``0``."""
    try:
        raw = (os.environ if env is None else env).get("AI_FLOOR_V2_SEALED_GENESIS", "")
    except Exception:  # noqa: BLE001
        return False
    return str(raw).strip() not in ("", "0")


def prune_rex_journal(store, *, keep_days=REX_KEEP_DAYS, now=None, env=None):
    """Delete REX journal rows older than ``keep_days``. Returns the number removed. Other rows stay.

    A sealed genesis treats the REX journal as part of the chain. Pruning is skipped entirely.
    """
    if _sealed_genesis_on(env):
        LOG.info("component=storage event=REX_PRUNE_SKIPPED reason=sealed_genesis")
        return 0
    now = now or datetime.now(timezone.utc)
    cutoff = utc(now - timedelta(days=keep_days))
    with store.transaction():
        cursor = store.db.execute("DELETE FROM journal WHERE source=? AND timestamp < ?", ("rex", cutoff))
        removed = cursor.rowcount
    if removed:
        LOG.info("component=storage event=REX_PRUNED removed=%s keep_days=%s", removed, keep_days)
    return removed


def prune_old_archives(db_path, *, keep_days=ARCHIVE_KEEP_DAYS, now=None):
    """Delete archive ``*.db`` files older than ``keep_days``, except the newest one. Returns how many went."""
    folder = db_path.parent / "archive" if hasattr(db_path, "parent") else None
    if folder is None or not folder.is_dir():
        return 0
    files = sorted((path for path in folder.iterdir() if path.is_file() and path.suffix == ".db"),
                   key=lambda path: path.stat().st_mtime)
    if len(files) <= 1:
        return 0
    now_s = time.time() if now is None else now.timestamp()
    cutoff = now_s - keep_days * 86400
    removed = 0
    for path in files[:-1]:
        if path.stat().st_mtime >= cutoff:
            continue
        path.unlink()
        removed += 1
    if removed:
        LOG.info("component=storage event=ARCHIVE_PRUNED removed=%s", removed)
    return removed


def maintain_disk(store):
    """Best-effort retention. Never raises. Does not vacuum and does not delete paper rows."""
    try:
        prune_rex_journal(store)
        prune_old_archives(store.path)
        warn_if_tight(store.path.parent)
    except Exception as exc:  # noqa: BLE001 - maintenance never stops the service
        if is_disk_full(exc):
            log_disk_full("maintain_disk")
        else:
            LOG.warning("component=storage event=MAINTAIN_FAILED error_type=%s", type(exc).__name__)


__all__ = ["ARCHIVE_KEEP_DAYS", "DISK_WARN_RATIO", "REX_KEEP_DAYS", "is_disk_full", "log_disk_full",
           "maintain_disk", "prune_old_archives", "prune_rex_journal", "reset_disk_warning", "usage_ratio",
           "warn_if_tight"]
