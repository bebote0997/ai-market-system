"""One-shot launch reset. Off unless ``AI_FLOOR_LAUNCH_RESET_TOKEN`` is a short printable token.

Cloud startup calls ``apply_launch_reset``. The same token does nothing the next time. A different token
can run once more. The step first copies the trading DB, the broker-state DB and the Exness state DB
(whichever already exist) to timestamped files under ``launch_backups`` on the same disk. If that copy
fails, nothing else changes and the token is not recorded, so the next start tries again.

After a good copy it does not delete rows. It records the token, sets the Exness session day counter and
trade counter to 0, and sets the launch start to now. Dashboard screens and demo stats treat that start
as a cutoff: older OANDA practice rows and older dashboard rows stay in the file and are not shown.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import logging
import os
from pathlib import Path
import sqlite3


LOG = logging.getLogger("ai_floor.launch_reset")
TOKEN_ENV = "AI_FLOOR_LAUNCH_RESET_TOKEN"
DAY_KEY, TRADE_KEY, START_KEY = "exness_session_day", "exness_trade_count", "exness_launch_start"
_MAX_TOKEN = 128


@dataclass(frozen=True)
class LaunchReset:
    applied: bool
    reason: str
    launch_start: datetime = None
    backups: tuple = ()


def _env(env):
    return os.environ if env is None else env


def _path(env, name, default):
    try:
        raw = _env(env).get(name)
    except Exception:  # noqa: BLE001 - a broken mapping uses the default path
        raw = None
    text = raw.strip() if isinstance(raw, str) else ""
    return Path(text or default)


def state_db_path(env=None):
    return _path(env, "AI_FLOOR_EXNESS_STATE_DB_PATH", "data/runtime/exness_state.db")


def _sources(env):
    return (
        ("trading", _path(env, "AI_FLOOR_DB_PATH", "data/runtime/trading_floor.db")),
        ("broker", _path(env, "AI_FLOOR_BROKER_STATE_DB_PATH", "data/runtime/broker_state.db")),
        ("exness", state_db_path(env)),
    )


def _token(env):
    """The token, ``None`` when unset, or ``False`` when set but not a usable token."""
    try:
        raw = _env(env).get(TOKEN_ENV)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    if len(text) > _MAX_TOKEN or any(ch.isspace() or not ch.isprintable() for ch in text):
        return False
    return text


def _parse_time(value):
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _counts(con):
    names = [row[0] for row in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY 1")]
    counted = []
    for name in names:
        if not name.replace("_", "").isalnum():
            raise sqlite3.DatabaseError("table_name")
        counted.append((name, con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]))
    return tuple(counted)


def _close(con):
    if con is not None:
        try:
            con.close()
        except sqlite3.Error:
            pass


def _discard(tmp):
    if tmp.is_file():
        try:
            tmp.unlink()
        except OSError:
            pass


def _copy_database(source, dest):
    """Full SQLite backup from a read-only source. The source is not deleted. Raises if the copy is not whole."""
    tmp = dest.with_name(dest.name + ".tmp")
    src = dst = None
    try:
        src = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
        dst = sqlite3.connect(tmp, timeout=5)
        src.backup(dst)
        if _counts(src) != _counts(dst):
            raise sqlite3.DatabaseError("count_mismatch")
        check = dst.execute("PRAGMA integrity_check").fetchone()
        if check is None or check[0] != "ok":
            raise sqlite3.DatabaseError("integrity_check")
        _close(dst)
        _close(src)
        dst = src = None
        os.chmod(tmp, 0o600)
        os.replace(tmp, dest)
    except Exception:
        _close(dst)
        _close(src)
        _discard(tmp)
        raise


def _write_empty_marker(dest):
    """A backup file for a start that had no source databases yet. Raises if it cannot be written."""
    tmp = dest.with_name(dest.name + ".tmp")
    con = None
    try:
        con = sqlite3.connect(tmp, timeout=5)
        con.execute("CREATE TABLE backup_manifest(source TEXT NOT NULL, note TEXT NOT NULL)")
        con.execute("INSERT INTO backup_manifest(source, note) VALUES ('none', 'no_source_files')")
        con.commit()
        check = con.execute("PRAGMA integrity_check").fetchone()
        if check is None or check[0] != "ok":
            raise sqlite3.DatabaseError("integrity_check")
        _close(con)
        con = None
        os.chmod(tmp, 0o600)
        os.replace(tmp, dest)
    except Exception:
        _close(con)
        _discard(tmp)
        raise


def _already(path, token):
    if not path.is_file():
        return None
    try:
        con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return None
    try:
        names = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "launch_resets" not in names:
            return None
        row = con.execute("SELECT launch_start FROM launch_resets WHERE token = ?", (token,)).fetchone()
    except sqlite3.Error:
        return None
    finally:
        con.close()
    return None if row is None else _parse_time(row[0])


def _record(path, token, start, names):
    """Insert the token and the zeroed counters. Returns ``(launch_start, newly_applied)``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=5)
    try:
        con.execute("BEGIN IMMEDIATE")
        con.execute("CREATE TABLE IF NOT EXISTS flags (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        con.execute("CREATE TABLE IF NOT EXISTS launch_resets ("
                    "token TEXT PRIMARY KEY, applied_at TEXT NOT NULL, launch_start TEXT NOT NULL, "
                    "backup_name TEXT NOT NULL)")
        existing = con.execute("SELECT launch_start FROM launch_resets WHERE token = ?", (token,)).fetchone()
        if existing:
            con.rollback()
            return existing[0], False
        con.execute("INSERT INTO launch_resets(token, applied_at, launch_start, backup_name) VALUES (?, ?, ?, ?)",
                    (token, start, start, ",".join(names)))
        for key, value in ((DAY_KEY, "0"), (TRADE_KEY, "0"), (START_KEY, start)):
            con.execute("INSERT INTO flags(key, value) VALUES (?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
        con.commit()
    except Exception:
        try:
            con.rollback()
        except sqlite3.Error:
            pass
        raise
    finally:
        con.close()
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return start, True


def _read_flags(env, keys):
    path = state_db_path(env)
    if not path.is_file():
        return None
    try:
        con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)
    except sqlite3.Error:
        return None
    try:
        found = {}
        for key in keys:
            try:
                row = con.execute("SELECT value FROM flags WHERE key = ?", (key,)).fetchone()
            except sqlite3.Error:
                return None
            if row is not None:
                found[key] = row[0]
        return found
    finally:
        con.close()


def launch_cutoff(env=None):
    """Aware UTC launch start after a successful reset, else None. Never creates a file. Never raises."""
    try:
        found = _read_flags(env, (START_KEY,))
    except Exception:  # noqa: BLE001 - a display read must not raise
        return None
    if not found:
        return None
    return _parse_time(found.get(START_KEY))


def _whole(value):
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def session_counters(env=None):
    """``day`` and ``trades`` as ints, and ``launch_start``, or None where no reset has been recorded."""
    blank = {"day": None, "trades": None, "launch_start": None}
    try:
        found = _read_flags(env, (DAY_KEY, TRADE_KEY, START_KEY))
    except Exception:  # noqa: BLE001
        return blank
    if not found:
        return blank
    return {"day": _whole(found.get(DAY_KEY)), "trades": _whole(found.get(TRADE_KEY)),
            "launch_start": _parse_time(found.get(START_KEY))}


def effective_since(since, env=None):
    """The later of ``since`` and the launch cutoff. A missing cutoff leaves ``since`` unchanged.

    ``since`` None means "no demo filter". A cutoff then becomes the filter, so older rows stay stored
    and drop out of the dashboard and the stats.
    """
    cutoff = launch_cutoff(env)
    if cutoff is None:
        return since
    if since is None:
        return cutoff
    try:
        return since if since >= cutoff else cutoff
    except TypeError:
        return cutoff


def _backup(env, now, token):
    """Copy existing source databases. Returns the backup paths. Raises on any failed copy."""
    from execution.fs_security import private_state_dir, secure_sqlite_files
    folder = state_db_path(env).parent / "launch_backups"
    private_state_dir(folder)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]
    seen = set()
    jobs = []
    for label, path in _sources(env):
        if not path.is_file():
            continue
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        jobs.append((label, path))
    created = []
    try:
        if not jobs:
            dest = folder / f"launch_{stamp}_{digest}_empty.db"
            _write_empty_marker(dest)
            secure_sqlite_files(dest)
            created.append(dest)
            return tuple(created)
        for label, path in jobs:
            dest = folder / f"launch_{stamp}_{digest}_{label}.db"
            _copy_database(path, dest)
            secure_sqlite_files(dest)
            created.append(dest)
        return tuple(created)
    except Exception:
        for path in created:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def apply_launch_reset(env=None, *, now=None):
    """Run the reset once for this token. Unset: no effect and no files. Never raises."""
    try:
        return _apply(env, now)
    except Exception as exc:  # noqa: BLE001 - startup must keep running; the token stays unrecorded
        LOG.warning("component=launch_reset event=RESET_ABORTED reason=backup_failed error_type=%s",
                    type(exc).__name__)
        return LaunchReset(False, "backup_failed")


def _apply(env, now):
    token = _token(env)
    if token is None:
        return LaunchReset(False, "disabled")
    if token is False:
        LOG.warning("component=launch_reset event=RESET_ABORTED reason=invalid_token")
        return LaunchReset(False, "invalid_token")
    if now is None:
        now = datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        LOG.warning("component=launch_reset event=RESET_ABORTED reason=invalid_time")
        return LaunchReset(False, "invalid_time")
    now = now.astimezone(timezone.utc)
    state = state_db_path(env)
    previous = _already(state, token)
    if previous is not None:
        LOG.info("component=launch_reset event=ALREADY_APPLIED")
        return LaunchReset(False, "already_applied", previous, ())
    try:
        backups = _backup(env, now, token)
    except Exception as exc:  # noqa: BLE001 - leave every source row where it is
        LOG.warning("component=launch_reset event=RESET_ABORTED reason=backup_failed error_type=%s",
                    type(exc).__name__)
        return LaunchReset(False, "backup_failed")
    start = now.isoformat()
    try:
        stored, fresh = _record(state, token, start, tuple(path.name for path in backups))
    except Exception as exc:  # noqa: BLE001 - the copies stay; the token is still unrecorded so the next start retries
        LOG.warning("component=launch_reset event=RESET_ABORTED reason=record_failed error_type=%s",
                    type(exc).__name__)
        return LaunchReset(False, "record_failed", None, backups)
    parsed = _parse_time(stored)
    if not fresh:
        LOG.info("component=launch_reset event=ALREADY_APPLIED")
        return LaunchReset(False, "already_applied", parsed, ())
    LOG.info("component=launch_reset event=APPLIED backups=%d", len(backups))
    return LaunchReset(True, "applied", parsed, backups)
