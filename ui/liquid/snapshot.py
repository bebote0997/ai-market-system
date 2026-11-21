"""Latest cycle snapshot already stored by the service. Read-only. No network and no new client."""
import json
import os
from pathlib import Path
import sqlite3


def _db_path(env):
    raw = env.get("AI_FLOOR_DB_PATH") if isinstance(env, dict) else None
    text = raw.strip() if isinstance(raw, str) else ""
    return Path(text or "data/runtime/trading_floor.db")


def _as_of(payload):
    stamp = payload.get("as_of") if isinstance(payload, dict) else None
    return stamp if isinstance(stamp, str) else ""


def latest(env=None):
    """Newest ``ui_snapshots`` payload, or ``{}`` when the file or the table is absent.

    The service writes one snapshot per symbol each cycle. This opens that database
    read-only and does not create it. Account, position and order fields are returned
    only when that payload actually carries them.
    """
    env = os.environ if env is None else env
    path = _db_path(env)
    if not path.is_file():
        return {}
    try:
        con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)
    except (OSError, sqlite3.Error):
        return {}
    try:
        try:
            rows = con.execute("SELECT payload FROM ui_snapshots").fetchall()
        except sqlite3.Error:
            return {}
    finally:
        con.close()
    best, best_at = None, ""
    for (raw,) in rows:
        if not isinstance(raw, str):
            continue
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        stamp = _as_of(payload)
        if best is None or stamp >= best_at:
            best, best_at = payload, stamp
    return {} if best is None else best
