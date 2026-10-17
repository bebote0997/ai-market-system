"""Account registry, per-account switches and emergency stop (SKELETON, not wired).

State lives in its OWN SQLite file (``AI_FLOOR_BROKER_STATE_DB_PATH``, default ``data/runtime/broker_state.db``),
never the experiment DB. Everything fails closed: default OFF; missing row, unreadable DB or unknown value -> OFF;
``real`` can never be enabled here. ``read_control_snapshot`` is the read-only view consumed by the UI.

Emergency latch: ``emergency_stop`` first sets a process-local in-memory latch (per state DB path) and only then
writes. Every authorisation check in this process honours the latch, even if the write failed, until an explicit
``clear_emergency`` succeeds. Other processes cannot see the latch: see docs/broker_skeleton.md.
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import sqlite3
from typing import Any, Optional

from execution.broker_config import DEFAULT_DEMO_PROVIDER, emergency_flag, load_broker_config
from execution.broker_port import BrokerGuardError
from execution.fs_security import private_state_dir, secure_sqlite_files


LOG = logging.getLogger("ai_floor.broker.registry")
ACCOUNT_KEYS = ("paper", "broker_demo", "real")
LABELS = {"paper": "PAPER", "broker_demo": "DEMO broker (DXtrade)", "real": "REAL (bloqueada)"}
# broker_demo label per AI_FLOOR_BROKER_DEMO_PROVIDER (default dxtrade keeps LABELS["broker_demo"]).
PROVIDER_LABELS = {"dxtrade": LABELS["broker_demo"], "oanda": "DEMO broker (OANDA practice)"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS broker_accounts (
    account_key TEXT PRIMARY KEY,
    mode TEXT NOT NULL CHECK (mode IN ('paper', 'broker_demo', 'real')),
    enabled INTEGER NOT NULL DEFAULT 0,
    shadow_only INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS broker_switches_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    account_key TEXT NOT NULL,
    field TEXT NOT NULL,
    old TEXT,
    new TEXT,
    actor TEXT NOT NULL,
    source TEXT NOT NULL,
    reason TEXT
);
CREATE TRIGGER IF NOT EXISTS broker_switches_audit_no_update BEFORE UPDATE ON broker_switches_audit
BEGIN SELECT RAISE(ABORT, 'broker_switches_audit is append-only'); END;
CREATE TRIGGER IF NOT EXISTS broker_switches_audit_no_delete BEFORE DELETE ON broker_switches_audit
BEGIN SELECT RAISE(ABORT, 'broker_switches_audit is append-only'); END;
CREATE TABLE IF NOT EXISTS emergency_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    active INTEGER NOT NULL DEFAULT 0,
    reason TEXT,
    updated_at TEXT NOT NULL
);
"""


_EMERGENCY_LATCHES = {}  # resolved state DB path -> reason; process-local, survives registry instances.


def _latch_key(path):
    try:
        return str(Path(path).resolve())
    except Exception:
        return str(path)


def _latched(path):
    try:
        return _latch_key(path) in _EMERGENCY_LATCHES
    except Exception:
        return True


def _now():
    return datetime.now(timezone.utc).isoformat()


def _is_experiment_db(path, config):
    path = Path(path)
    experiment = Path(config.experiment_db_path)
    return path.name == experiment.name or path.resolve() == experiment.resolve()


def _read_state(path):
    """Read-only (``mode=ro``, never creates the file). None = unreadable; callers treat that as OFF/emergency."""
    try:
        path = Path(path)
        if not path.is_file():
            return None
        con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1.0)
        try:
            rows = con.execute("SELECT account_key, mode, enabled, shadow_only FROM broker_accounts").fetchall()
            emergency = con.execute("SELECT active FROM emergency_state WHERE id = 1").fetchone()
        finally:
            con.close()
    except Exception:  # Corrupt/locked/unreadable: fail closed.
        return None
    accounts = {}
    for key, mode, enabled, shadow_only in rows:
        if key in ACCOUNT_KEYS and mode == key:
            accounts[key] = {"enabled": type(enabled) is int and enabled == 1,
                             "shadow_only": not (type(shadow_only) is int and shadow_only == 0)}
    active = emergency[0] if emergency else None
    return {"accounts": accounts, "emergency": not (type(active) is int and active == 0)}


def read_control_snapshot(path=None, *, env=None):
    """Read-only control view for the dashboard. NEVER raises and never creates the DB file."""
    state, env_emergency, provider = None, True, DEFAULT_DEMO_PROVIDER
    try:
        config = load_broker_config(env)
        env_emergency = config.emergency_stop
        provider = config.demo_provider
        target = Path(path) if path is not None else Path(config.state_db_path)
        state = None if _is_experiment_db(target, config) else _read_state(target)
    except Exception:
        state = None
    readable = state is not None
    emergency = (not readable) or env_emergency or state["emergency"] or _latched(target)
    accounts = []
    for key in ACCOUNT_KEYS:
        stored = readable and state["accounts"].get(key, {}).get("enabled", False)
        label = PROVIDER_LABELS.get(provider, LABELS[key]) if key == "broker_demo" else LABELS[key]
        accounts.append({"key": key, "mode": key, "label": label, "locked": key == "real",
                         "enabled": bool(stored and not emergency and key != "real")})
    return {"readable": readable, "emergency_stop": bool(emergency), "connected": False, "accounts": accounts,
            "provider": provider}


def ensure_registry_initialized(path=None, *, env=None):
    """Create the state DB with EVERY switch OFF and no emergency when (and only when) it does not exist yet.

    Used by the dashboard so a fresh disk shows "all off" instead of "unreadable". Never touches an existing file,
    never refuses on the experiment DB path (it returns False), never raises. Returns True only if THIS call created it."""
    try:
        config = load_broker_config(env)
        target = Path(path) if path is not None else Path(config.state_db_path)
        if os.path.lexists(target) or _is_experiment_db(target, config):  # lexists: a dangling symlink counts as present
            return False
        created_dir = private_state_dir(target.parent)
        target.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            os.close(os.open(target, flags, 0o600))  # exclusive + owner-only from the first byte; we own this file
        except FileExistsError:
            return False  # someone else created it meanwhile: leave it alone
        try:
            AccountRegistry(target, env=env).initialize()
            secure_sqlite_files(target, created_dir=created_dir)
        except BaseException:
            try:
                if target.stat().st_size == 0:
                    target.unlink()  # our own empty shell only; never remove a populated file
            except OSError:
                pass
            raise
        return True
    except Exception:  # noqa: BLE001 - fail closed: the reader keeps showing "off"
        return False


@dataclass(frozen=True)
class EmergencyResult:
    persisted: bool
    hook_ok: bool


class AccountRegistry:
    def __init__(self, path=None, *, env=None, on_emergency=None, clock=None):
        self._env = os.environ if env is None else env
        config = load_broker_config(self._env)
        self.path = Path(path) if path is not None else Path(config.state_db_path)
        if _is_experiment_db(self.path, config):
            raise BrokerGuardError("broker state must not use the experiment DB")
        self._on_emergency = on_emergency or (lambda **_: None)
        self._clock = clock or _now

    # -- reads (fail closed) ---------------------------------------------------------------------------------------
    def _env_emergency(self):
        try:
            return emergency_flag(self._env.get("AI_FLOOR_EMERGENCY_STOP"))
        except Exception:
            return True

    def local_latch_active(self):
        return _latched(self.path)

    def emergency_active(self):
        if self.local_latch_active():
            return True
        state = _read_state(self.path)
        return self._env_emergency() or state is None or state["emergency"]

    def is_allowed(self, account_key):
        if account_key not in ACCOUNT_KEYS or account_key == "real" or self._env_emergency():
            return False
        if self.local_latch_active():
            return False
        state = _read_state(self.path)
        if state is None or state["emergency"]:
            return False
        return state["accounts"].get(account_key, {}).get("enabled", False)

    def audit_log(self):
        try:
            con = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1.0)
            try:
                return con.execute("SELECT ts, account_key, field, old, new, actor, source, reason "
                                   "FROM broker_switches_audit ORDER BY id").fetchall()
            finally:
                con.close()
        except Exception:
            return []

    # -- writes ------------------------------------------------------------------------------------------------------
    def _connect(self):
        created_dir = private_state_dir(self.path.parent)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        try:
            con.executescript(SCHEMA)
            secure_sqlite_files(self.path, created_dir=created_dir)
            now = self._clock()
            con.executemany("INSERT OR IGNORE INTO broker_accounts(account_key, mode, enabled, shadow_only, "
                            "updated_at) VALUES (?, ?, 0, 1, ?)", [(key, key, now) for key in ACCOUNT_KEYS])
            con.execute("INSERT OR IGNORE INTO emergency_state(id, active, reason, updated_at) "
                        "VALUES (1, 0, NULL, ?)", (now,))
        except BaseException:
            con.close()
            raise
        return con

    def initialize(self):
        self._connect().close()

    @staticmethod
    def _audit(con, ts, key, field, old, new, actor, source, reason=None):
        con.execute("INSERT INTO broker_switches_audit(ts, account_key, field, old, new, actor, source, reason) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (ts, key, field, old, new, actor, source, reason))

    def set_enabled(self, account_key, enabled, *, actor, source="code"):
        if account_key not in ACCOUNT_KEYS:
            raise BrokerGuardError("unknown account")
        if type(enabled) is not bool:
            raise BrokerGuardError("enabled must be a bool")
        if account_key == "real" and enabled:
            raise BrokerGuardError("real account cannot be enabled (REAL_EXECUTION_ENABLED=False; out of scope)")
        if not actor:
            raise BrokerGuardError("actor required")
        if enabled and self._env_emergency():
            raise BrokerGuardError("emergency stop active (AI_FLOOR_EMERGENCY_STOP)")
        if enabled and self.local_latch_active():
            raise BrokerGuardError("emergency stop active (local latch)")
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            try:
                active = con.execute("SELECT active FROM emergency_state WHERE id = 1").fetchone()
                if enabled and not (active and type(active[0]) is int and active[0] == 0):
                    raise BrokerGuardError("emergency stop active (registry)")
                old = con.execute("SELECT enabled FROM broker_accounts WHERE account_key = ?", (account_key,)).fetchone()
                ts = self._clock()
                con.execute("UPDATE broker_accounts SET enabled = ?, updated_at = ? WHERE account_key = ?",
                            (int(enabled), ts, account_key))
                self._audit(con, ts, account_key, "enabled", None if old is None else str(old[0]), str(int(enabled)),
                            actor, source)
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
        finally:
            con.close()

    def emergency_stop(self, *, actor, reason="", source="code"):
        """Latch locally, then atomically switch every account OFF and persist emergency_state=ON, audit it, then
        call the hook.

        The in-memory latch is set BEFORE writing, so this process stays stopped even if persisting fails. If
        persisting fails the hook still runs (it is how positions get closed later) and BrokerGuardError is raised;
        other processes only see it if the DB is unreadable to them too (see docs/broker_skeleton.md)."""
        _EMERGENCY_LATCHES[_latch_key(self.path)] = reason
        error = None
        try:
            con = self._connect()
            try:
                con.execute("BEGIN IMMEDIATE")
                try:
                    ts = self._clock()
                    for key, enabled in con.execute("SELECT account_key, enabled FROM broker_accounts").fetchall():
                        if enabled != 0:
                            self._audit(con, ts, key, "enabled", str(enabled), "0", actor, source, reason)
                    con.execute("UPDATE broker_accounts SET enabled = 0, updated_at = ?", (ts,))
                    old = con.execute("SELECT active FROM emergency_state WHERE id = 1").fetchone()
                    con.execute("UPDATE emergency_state SET active = 1, reason = ?, updated_at = ? WHERE id = 1",
                                (reason, ts))
                    self._audit(con, ts, "ALL", "emergency_stop", None if old is None else str(old[0]), "1",
                                actor, source, reason)
                    con.execute("COMMIT")
                except BaseException:
                    con.execute("ROLLBACK")
                    raise
            finally:
                con.close()
        except Exception as exc:
            error = exc
            LOG.critical("emergency stop could not be persisted (local latch ON; stop other processes with "
                         "AI_FLOOR_EMERGENCY_STOP=1): %s", type(exc).__name__)
        hook_ok = True
        try:
            self._on_emergency(reason=reason)
        except Exception as exc:
            hook_ok = False
            LOG.error("on_emergency hook failed: %s", type(exc).__name__)
        if error is not None:
            raise BrokerGuardError("emergency state could not be persisted (readers fail closed)") from error
        return EmergencyResult(True, hook_ok)

    def clear_emergency(self, *, actor, reason="", source="code"):
        """Explicit recovery: persists every switch OFF and the DB flag OFF, then releases the local latch (only if
        the write committed). Nothing is re-enabled; AI_FLOOR_EMERGENCY_STOP is still honoured."""
        if not actor:
            raise BrokerGuardError("actor required")
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            try:
                ts = self._clock()
                for key, enabled in con.execute("SELECT account_key, enabled FROM broker_accounts").fetchall():
                    if enabled != 0:  # e.g. a stop whose write failed: recovery never leaves a switch ON.
                        self._audit(con, ts, key, "enabled", str(enabled), "0", actor, source, reason)
                con.execute("UPDATE broker_accounts SET enabled = 0, updated_at = ?", (ts,))
                old = con.execute("SELECT active FROM emergency_state WHERE id = 1").fetchone()
                con.execute("UPDATE emergency_state SET active = 0, reason = ?, updated_at = ? WHERE id = 1",
                            (reason, ts))
                self._audit(con, ts, "ALL", "emergency_stop", None if old is None else str(old[0]), "0",
                            actor, source, reason)
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
        finally:
            con.close()
        _EMERGENCY_LATCHES.pop(_latch_key(self.path), None)


@dataclass(frozen=True)
class RunOutcome:
    account_key: str
    status: str  # OK | ERROR | SKIPPED
    value: Any = None
    error: Optional[str] = None  # Exception type name only (no message: it could carry broker text).


class AccountRunner:
    """Per-account isolation: an exception in one account never reaches another. Simple circuit breaker: after
    ``failure_threshold`` consecutive failures the account is skipped until ``reset``."""

    def __init__(self, failure_threshold=3):
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        self._threshold = failure_threshold
        self._failures = defaultdict(int)

    def is_open(self, account_key):
        return self._failures[account_key] >= self._threshold

    def reset(self, account_key):
        self._failures[account_key] = 0

    def run(self, account_key, operation, *args, **kwargs):
        if self.is_open(account_key):
            return RunOutcome(account_key, "SKIPPED", error="circuit_open")
        try:
            value = operation(*args, **kwargs)
        except Exception as exc:
            self._failures[account_key] += 1
            LOG.warning("account %s operation failed: %s (%d/%d)", account_key, type(exc).__name__,
                        self._failures[account_key], self._threshold)
            return RunOutcome(account_key, "ERROR", error=type(exc).__name__)
        self._failures[account_key] = 0
        return RunOutcome(account_key, "OK", value=value)

    def run_each(self, operations):
        return {key: self.run(key, operation) for key, operation in operations.items()}
