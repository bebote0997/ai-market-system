"""Operator actions requested from the dashboard (a separate process from the runner).

Cross-process requests go through the broker state DB (``AI_FLOOR_BROKER_STATE_DB_PATH``): the registry emergency
latch (``AccountRegistry.emergency_stop``) and the ``broker_demo_flags`` requests the router picks up on its next tick
(``flatten_request``, ``drift_clear_request``, ``manual_close_request``). Nothing here touches the network or
places/closes orders itself.

Manual close ("Cerrar operación", Floor page): ``request_manual_close`` asks the router to close ONE routed OPEN trade
through its own close path (the same ``_close`` the emergency flatten uses, reason ``MANUAL_CLOSE``). Refused unless
broker-demo routing is ON, REAL_EXECUTION_ENABLED is False and the configured account is an OANDA practice one. The
request is stored only when none is pending (one transaction, ``DemoStateStore.set_flag_if_absent``): a second click or
a concurrent request never queues a second close; the same ``request_id`` again is reported as a duplicate.

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
DRIFT_EPISODE_FLAG = "drift_episode"
MANUAL_CLOSE_REQUEST_FLAG = "manual_close_request"  # same key as execution/broker_demo_router.py
MANUAL_CLOSE = "MANUAL_CLOSE"
DEMO_SYMBOL = "EURUSD"
BLOCKED_FLAG = "blocked_reason"
DRIFT = "drift"
SOURCE = "dashboard"
MAX_REASON, MAX_ACTOR, MAX_REQUEST_ID = 120, 64, 64


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


def _request(actor, reason, **extra):
    return json.dumps({"actor": _text(actor, MAX_ACTOR), "reason": _text(reason, MAX_REASON), "ts": _now(), **extra},
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
    """Ask the router to clear a drift block; it clears only if its next reconcile verifies broker == local state.
    The request carries the current ``drift_episode``: it can never clear a later drift."""
    env = _env(env)
    episode = _drift_episode(path, env)
    if episode is None:
        return {"ok": False, "reason": "no_drift"}
    actor, reason = _text(actor, MAX_ACTOR) or SOURCE, _text(reason, MAX_REASON)
    try:
        store = _store(_state_path(path, env))
        store.set_flag(DRIFT_CLEAR_REQUEST_FLAG, _request(actor, reason, episode=episode))
    except Exception as exc:  # noqa: BLE001
        LOG.error("component=operator event=DRIFT_CLEAR_REQUEST_FAILED error_type=%s", type(exc).__name__)
        return {"ok": False, "reason": "write_failed"}
    try:
        store.event("DRIFT_CLEAR_REQUESTED", None, {"actor": actor, "reason": reason, "source": SOURCE})
    except Exception as exc:  # noqa: BLE001 - audit best effort; the request itself is stored
        LOG.warning("component=operator event=EVENT_WRITE_FAILED error_type=%s", type(exc).__name__)
    return {"ok": True, "reason": "requested"}


def _read_ro(target, sql, params=()):
    with closing(sqlite3.connect(target.resolve().as_uri() + "?mode=ro", uri=True, timeout=1.0)) as con:
        return con.execute(sql, params).fetchall()


def practice_problem(env=None):
    """None when a manual close may be requested; else a fixed code. Routing ON (exact ``1``), REAL_EXECUTION_ENABLED
    False, provider ``oanda`` (when set), a practice account id (``101-``) and the client's practice-only REST base."""
    env = _env(env)
    try:
        from execution.broker_demo_state import routing_flag
        from execution.dxtrade_broker import real_execution_enabled
        from execution.oanda_broker import ACCOUNT_ENV, PROVIDER
        from execution.oanda_client import PRACTICE_ACCOUNT_RE, OandaClient, practice_url_problem
        if real_execution_enabled() is not False:
            return "real_execution_enabled"
        if not routing_flag(env):
            return "routing_off"
        if env.get("AI_FLOOR_BROKER_DEMO_PROVIDER") not in (None, "", PROVIDER):
            return "not_practice"
        account = env.get(ACCOUNT_ENV)
        if not isinstance(account, str) or not PRACTICE_ACCOUNT_RE.match(account.strip()):
            return "not_practice"
        if practice_url_problem((OandaClient.__init__.__kwdefaults__ or {}).get("base_url")) is not None:
            return "not_practice"
    except Exception:  # noqa: BLE001 - cannot prove practice: refuse
        return "not_practice"
    return None


def manual_close_target(path=None, *, env=None):
    """The routed OPEN EURUSD trade the Floor's "Cerrar operación" acts on (read-only, never creates the DB), newest
    first: ``{client_id, trade_id, symbol, side, units, unresolved}``; None when there is none / unreadable."""
    try:
        target = _state_path(path, _env(env))
        if not target.is_file():
            return None
        rows = _read_ro(target, "SELECT client_id, trade_id, symbol, side, units, action_status FROM broker_demo_trades "
                                "WHERE status = 'OPEN' AND symbol = ? ORDER BY created_at DESC, client_id DESC",
                        (DEMO_SYMBOL,))
    except Exception:  # noqa: BLE001 - router never ran / locked / corrupt: nothing to close from here
        return None
    for client_id, trade_id, symbol, side, units, action_status in rows:
        if isinstance(client_id, str) and client_id.startswith("aif-") and trade_id:
            return {"client_id": client_id, "trade_id": str(trade_id), "symbol": symbol, "side": side, "units": units,
                    "unresolved": action_status == "UNKNOWN"}
    return None


def _pending_manual(store):
    try:
        return json.loads(store.flag(MANUAL_CLOSE_REQUEST_FLAG) or "null")
    except (TypeError, ValueError):
        return None


def request_manual_close(actor, reason, *, client_id, request_id, path=None, env=None):
    """Ask the router to close the routed OPEN trade ``client_id`` (reason ``MANUAL_CLOSE``) on its next tick.

    Returns ``{"ok", "reason"}``: ``requested`` / ``duplicate`` (same ``request_id`` already pending) are ok;
    ``already_pending`` (another close request pending), ``no_open_trade``, ``action_unresolved``, ``bad_request``,
    ``routing_off``, ``not_practice``, ``real_execution_enabled`` and ``write_failed`` are refusals. Never raises."""
    env = _env(env)
    problem = practice_problem(env)
    if problem is not None:
        return {"ok": False, "reason": problem}
    request_id = _text(request_id, MAX_REQUEST_ID)
    if not request_id or not isinstance(client_id, str) or not client_id:
        return {"ok": False, "reason": "bad_request"}
    target = manual_close_target(path, env=env)
    if target is None or target["client_id"] != client_id:
        return {"ok": False, "reason": "no_open_trade"}
    if target["unresolved"]:
        return {"ok": False, "reason": "action_unresolved"}
    actor, reason = _text(actor, MAX_ACTOR) or SOURCE, _text(reason, MAX_REASON)
    value = _request(actor, reason, client_id=client_id, trade_id=target["trade_id"], request_id=request_id)
    detail = {"actor": actor, "reason": MANUAL_CLOSE, "note": reason, "source": SOURCE, "request_id": request_id,
              "trade_id": target["trade_id"]}
    try:
        store = _store(_state_path(path, env))
        if store.set_flag_if_absent(MANUAL_CLOSE_REQUEST_FLAG, value,
                                    event=("MANUAL_CLOSE_REQUESTED", client_id, detail)):
            return {"ok": True, "reason": "requested"}
        pending = _pending_manual(store)
    except Exception as exc:  # noqa: BLE001
        LOG.error("component=operator event=MANUAL_CLOSE_REQUEST_FAILED error_type=%s", type(exc).__name__)
        return {"ok": False, "reason": "write_failed"}
    if isinstance(pending, dict) and pending.get("request_id") == request_id:
        return {"ok": True, "reason": "duplicate"}
    return {"ok": False, "reason": "already_pending"}


def _drift_episode(path, env):
    """The current ``drift_episode`` token while ``blocked_reason == drift`` (read-only), else None."""
    try:
        target = _state_path(path, env)
        if not target.is_file():
            return None
        flags = dict(_read_ro(target, "SELECT key, value FROM broker_demo_flags"))
    except Exception:  # noqa: BLE001 - unreadable: no drift to clear
        return None
    episode = flags.get(DRIFT_EPISODE_FLAG)
    return episode if flags.get(BLOCKED_FLAG) == DRIFT and isinstance(episode, str) and episode else None


def operator_state(path=None, *, env=None):
    """Read-only (``mode=ro``, never creates the DB) view for the UI. Unknown values stay None (shown as unknown;
    the drift section only appears with a readable ``drift`` flag)."""
    out = {"readable": False, "emergency": None, "flatten_pending": None, "drift_clear_pending": None,
           "manual_close_pending": None, "blocked_reason": None}
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
            "manual_close_pending": bool(flags.get(MANUAL_CLOSE_REQUEST_FLAG)),
            "blocked_reason": blocked if isinstance(blocked, str) and blocked else None}
