"""Operator actions requested from the dashboard (a separate process from the runner).

Cross-process requests go through the broker state DB (``AI_FLOOR_BROKER_STATE_DB_PATH``): the registry emergency
latch (``AccountRegistry.emergency_stop``) and the ``broker_demo_flags`` requests the router picks up on its next tick
(``flatten_request``, ``drift_clear_request``). Nothing here touches the network or places/closes orders itself.

Every function returns a dict and never raises. Errors are reported as fixed codes (never exception text: it may carry
paths); logs carry the exception type only. Clearing the emergency is deliberately NOT offered here.
"""
from contextlib import closing
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sqlite3

LOG = logging.getLogger("ai_floor.broker.operator_actions")
EMERGENCY_BUTTON_ENV = "AI_FLOOR_EMERGENCY_BUTTON"
DRIFT_CLEAR_BUTTON_ENV = "AI_FLOOR_DRIFT_CLEAR_BUTTON"
FLATTEN_REQUEST_FLAG = "flatten_request"
DRIFT_CLEAR_REQUEST_FLAG = "drift_clear_request"
BLOCKED_FLAG = "blocked_reason"
DRIFT = "drift"
SOURCE = "dashboard"
MAX_REASON, MAX_ACTOR = 120, 64


def _env(env):
    return os.environ if env is None else env


def button_flags(env=None):
    """Emergency button ON unless the env value is exactly ``0`` (safety-increasing); drift-clear only with ``1``."""
    env = _env(env)
    return {"emergency": env.get(EMERGENCY_BUTTON_ENV) != "0", "drift_clear": env.get(DRIFT_CLEAR_BUTTON_ENV) == "1"}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _text(value, limit):
    return str(value or "")[:limit]


def _request(actor, reason):
    return json.dumps({"actor": _text(actor, MAX_ACTOR), "reason": _text(reason, MAX_REASON), "ts": _now()},
                      sort_keys=True)


def _state_path(path, env):
    if path is not None:
        return Path(path)
    from execution.broker_config import load_broker_config
    return Path(load_broker_config(env).state_db_path)


def _store(path):
    from execution.broker_demo_state import DemoStateStore
    return DemoStateStore(path)


def request_emergency_stop(actor, reason, *, path=None, env=None):
    """Registry emergency stop (every account OFF, latched in the DB with audit) + a flatten request for the router.

    ``ok`` only when both were persisted. The flatten request is attempted even if the emergency write failed."""
    env = _env(env)
    actor, reason = _text(actor, MAX_ACTOR) or SOURCE, _text(reason, MAX_REASON)
    out = {"ok": False, "persisted": False, "flatten_requested": False}
    try:
        from execution.account_registry import AccountRegistry
        registry = AccountRegistry(path, env=env)
        target = registry.path
    except Exception as exc:  # noqa: BLE001 - e.g. experiment DB path refused
        LOG.error("component=operator event=EMERGENCY_BUTTON_FAILED stage=registry error_type=%s", type(exc).__name__)
        return {**out, "error": "registry_unavailable"}
    try:
        registry.emergency_stop(actor=actor, reason=reason, source=SOURCE)
        out["persisted"] = True
    except Exception as exc:  # noqa: BLE001
        LOG.error("component=operator event=EMERGENCY_BUTTON_FAILED stage=emergency error_type=%s", type(exc).__name__)
    try:
        store = _store(target)
        store.set_flag(FLATTEN_REQUEST_FLAG, _request(actor, reason))
        out["flatten_requested"] = True
        store.event("EMERGENCY_BUTTON", None, {"actor": actor, "reason": reason, "source": SOURCE,
                                               "persisted": out["persisted"]})
    except Exception as exc:  # noqa: BLE001 - the event is best effort once the flag is written
        LOG.error("component=operator event=EMERGENCY_BUTTON_FAILED stage=flatten error_type=%s", type(exc).__name__)
    out["ok"] = out["persisted"] and out["flatten_requested"]
    if not out["ok"]:
        out["error"] = "emergency_not_persisted" if not out["persisted"] else "flatten_request_failed"
    return out


def request_drift_clear(actor, reason, *, path=None, env=None):
    """Ask the router to clear a drift block; it clears only if its next reconcile verifies broker == local state."""
    env = _env(env)
    if operator_state(path, env=env)["blocked_reason"] != DRIFT:
        return {"ok": False, "reason": "no_drift"}
    actor, reason = _text(actor, MAX_ACTOR) or SOURCE, _text(reason, MAX_REASON)
    try:
        store = _store(_state_path(path, env))
        store.set_flag(DRIFT_CLEAR_REQUEST_FLAG, _request(actor, reason))
    except Exception as exc:  # noqa: BLE001
        LOG.error("component=operator event=DRIFT_CLEAR_REQUEST_FAILED error_type=%s", type(exc).__name__)
        return {"ok": False, "reason": "write_failed"}
    try:
        store.event("DRIFT_CLEAR_REQUESTED", None, {"actor": actor, "reason": reason, "source": SOURCE})
    except Exception as exc:  # noqa: BLE001 - audit best effort; the request itself is stored
        LOG.warning("component=operator event=EVENT_WRITE_FAILED error_type=%s", type(exc).__name__)
    return {"ok": True, "reason": "requested"}


def _read_ro(target, sql):
    with closing(sqlite3.connect(target.resolve().as_uri() + "?mode=ro", uri=True, timeout=1.0)) as con:
        return con.execute(sql).fetchall()


def operator_state(path=None, *, env=None):
    """Read-only (``mode=ro``, never creates the DB) view for the UI. Unknown values stay None (shown as unknown;
    the drift section only appears with a readable ``drift`` flag)."""
    out = {"readable": False, "emergency": None, "flatten_pending": None, "drift_clear_pending": None,
           "blocked_reason": None}
    try:
        target = _state_path(path, _env(env))
        if not target.is_file():
            return out
    except Exception:  # noqa: BLE001
        return out
    try:
        row = _read_ro(target, "SELECT active FROM emergency_state WHERE id = 1")
        out["emergency"] = not (row and type(row[0][0]) is int and row[0][0] == 0)
    except Exception:  # noqa: BLE001 - unknown
        pass
    try:
        flags = dict(_read_ro(target, "SELECT key, value FROM broker_demo_flags"))
    except Exception:  # noqa: BLE001 - missing table (router never ran) / locked / corrupt
        return out
    blocked = flags.get(BLOCKED_FLAG)
    return {**out, "readable": True, "flatten_pending": bool(flags.get(FLATTEN_REQUEST_FLAG)),
            "drift_clear_pending": bool(flags.get(DRIFT_CLEAR_REQUEST_FLAG)),
            "blocked_reason": blocked if isinstance(blocked, str) and blocked else None}
