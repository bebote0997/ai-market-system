"""Broker-demo routing state (``broker_demo_trades`` + append-only ``broker_demo_events``) in the broker state DB.

Lives in the SAME SQLite file as the account registry (``AI_FLOOR_BROKER_STATE_DB_PATH``, default
``data/runtime/broker_state.db``), never the experiment DB. Tables are created lazily (``IF NOT EXISTS``). Event
details are JSON built by the router from codes, ids and prices only: never tokens, headers or account ids.

``read_router_status`` is the read-only view for the dashboard: ``mode=ro`` (never creates the file) and never raises.
Nothing here touches the network or the paper broker.
"""
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import sqlite3

from execution.broker_config import load_broker_config
from execution.fs_security import private_state_dir, secure_sqlite_files

ROUTING_ENV = "AI_FLOOR_BROKER_DEMO_ROUTING"
DRY_RUN_ENV = "AI_FLOOR_BROKER_DEMO_DRY_RUN"
MAX_UNITS_ENV = "AI_FLOOR_BROKER_DEMO_MAX_UNITS"
DEFAULT_MAX_UNITS = 100000
_ON = {"1", "true", "yes", "on"}
HEARTBEAT_FLAG, MODE_FLAG = "heartbeat", "mode"
HEARTBEAT_MAX_AGE_SECONDS = 20 * 60  # the dashboard shows routing "activo" only with a recent router heartbeat

# Trade row statuses.
PENDING_SEND, OPEN, UNKNOWN, REJECTED = "PENDING_SEND", "OPEN", "UNKNOWN", "REJECTED"
CLOSED, CLOSED_AT_BROKER, DRY_RUN = "CLOSED", "CLOSED_AT_BROKER", "DRY_RUN"
UNRESOLVED_STATUSES = (PENDING_SEND, UNKNOWN)
LIVE_STATUSES = (PENDING_SEND, OPEN, UNKNOWN)  # rows that block a new entry (single net position)
TRADE_COLUMNS = ("client_id", "run_id", "symbol", "side", "units", "entry", "stop", "target", "initial_stop",
                 "trade_id", "fill_price", "opened_at", "stage", "status", "closed_reason", "pending_action",
                 "action_status", "created_at", "updated_at")
SCHEMA = """
CREATE TABLE IF NOT EXISTS broker_demo_trades (
    client_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    units INTEGER NOT NULL,
    entry TEXT NOT NULL,
    stop TEXT NOT NULL,
    target TEXT NOT NULL,
    initial_stop TEXT,
    trade_id TEXT,
    fill_price TEXT,
    opened_at TEXT,
    stage TEXT,
    status TEXT NOT NULL,
    closed_reason TEXT,
    pending_action TEXT,
    action_status TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS broker_demo_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    client_id TEXT,
    detail TEXT
);
CREATE TRIGGER IF NOT EXISTS broker_demo_events_no_update BEFORE UPDATE ON broker_demo_events
BEGIN SELECT RAISE(ABORT, 'broker_demo_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS broker_demo_events_no_delete BEFORE DELETE ON broker_demo_events
BEGIN SELECT RAISE(ABORT, 'broker_demo_events is append-only'); END;
CREATE TABLE IF NOT EXISTS broker_demo_flags (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT NOT NULL
);
"""


def routing_flag(env):
    """Only the exact value ``1`` turns routing on."""
    return env.get(ROUTING_ENV) == "1"


def dry_run_flag(env):
    """Dry-run is opt-in: ``1``/true/yes/on => no POST/PUT. Unset => live routing to the PRACTICE account."""
    return str(env.get(DRY_RUN_ENV) or "").strip().lower() in _ON


def max_units_setting(env):
    """Positive int; anything else -> DEFAULT_MAX_UNITS."""
    raw = str(env.get(MAX_UNITS_ENV) or "").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else DEFAULT_MAX_UNITS


def _json_default(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _now():
    return datetime.now(timezone.utc).isoformat()


_EVENT_SQL = "INSERT INTO broker_demo_events(ts, kind, client_id, detail) VALUES (?, ?, ?, ?)"
_SET_FLAG_SQL = ("INSERT INTO broker_demo_flags(key, value, updated_at) VALUES (?, ?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at")
_CLEAR_FLAG_SQL = "DELETE FROM broker_demo_flags WHERE key = ? AND (? IS NULL OR value = ?)"


def _detail(detail):
    return None if detail is None else json.dumps(detail, sort_keys=True, default=_json_default)


class DemoStateStore:
    """Short-lived connections per call; every write commits immediately (write-ahead for the router)."""

    def __init__(self, path, *, clock=None):
        self.path = Path(path)
        self._clock = clock or _now

    def _connect(self):
        created_dir = private_state_dir(self.path.parent)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        con.row_factory = sqlite3.Row
        try:
            con.executescript(SCHEMA)
            secure_sqlite_files(self.path, created_dir=created_dir)
        except BaseException:
            con.close()
            raise
        return con

    def initialize(self):
        self._connect().close()

    # -- trades ----------------------------------------------------------------------------------------------------
    def insert_intent(self, row):
        now = self._clock()
        values = {column: row.get(column) for column in TRADE_COLUMNS}
        values["created_at"] = values["updated_at"] = now
        with closing(self._connect()) as con:
            con.execute(f"INSERT INTO broker_demo_trades({', '.join(TRADE_COLUMNS)}) "
                        f"VALUES ({', '.join('?' for _ in TRADE_COLUMNS)})",
                        [None if v is None else str(v) if isinstance(v, Decimal) else v
                         for v in (values[c] for c in TRADE_COLUMNS)])

    def update(self, client_id, **fields):
        unknown = set(fields) - set(TRADE_COLUMNS)
        if unknown or "client_id" in fields:
            raise ValueError("unknown trade columns")
        fields["updated_at"] = self._clock()
        names = sorted(fields)
        with closing(self._connect()) as con:
            con.execute(f"UPDATE broker_demo_trades SET {', '.join(f'{n} = ?' for n in names)} WHERE client_id = ?",
                        [*(None if fields[n] is None else str(fields[n]) if isinstance(fields[n], (Decimal, datetime))
                           else fields[n] for n in names), client_id])

    def trade(self, client_id):
        with closing(self._connect()) as con:
            row = con.execute("SELECT * FROM broker_demo_trades WHERE client_id = ?", (client_id,)).fetchone()
        return None if row is None else dict(row)

    def trades(self, statuses=None):
        with closing(self._connect()) as con:
            if statuses is None:
                rows = con.execute("SELECT * FROM broker_demo_trades ORDER BY created_at, client_id").fetchall()
            else:
                rows = con.execute(f"SELECT * FROM broker_demo_trades WHERE status IN "
                                   f"({', '.join('?' for _ in statuses)}) ORDER BY created_at, client_id",
                                   tuple(statuses)).fetchall()
        return [dict(row) for row in rows]

    # -- events / flags ----------------------------------------------------------------------------------------------
    def event(self, kind, client_id=None, detail=None):
        with closing(self._connect()) as con:
            con.execute(_EVENT_SQL, (self._clock(), kind, client_id, _detail(detail)))

    def apply(self, *, set_flags=None, delete_flags=None, event=None):
        """ONE transaction (all or nothing): upsert ``set_flags`` ({key: value}), delete ``delete_flags`` ({key: value
        or None}, same value guard as ``clear_flag``) and append ``event`` = ``(kind, client_id, detail)``."""
        now = self._clock()
        with closing(self._connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                for key, value in (set_flags or {}).items():
                    con.execute(_SET_FLAG_SQL, (key, value, now))
                for key, value in (delete_flags or {}).items():
                    con.execute(_CLEAR_FLAG_SQL, (key, value, value))
                if event is not None:
                    kind, client_id, detail = event
                    con.execute(_EVENT_SQL, (now, kind, client_id, _detail(detail)))
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise

    def set_flag_if_absent(self, key, value, *, event=None):
        """ONE transaction: insert ``key`` only when it holds no (non-empty) value, plus ``event`` = ``(kind,
        client_id, detail)`` when inserted. True when this call stored it; False when a request is already pending
        (a concurrent / repeated request never overwrites it)."""
        now = self._clock()
        with closing(self._connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                row = con.execute("SELECT value FROM broker_demo_flags WHERE key = ?", (key,)).fetchone()
                inserted = row is None or not row[0]
                if inserted:
                    con.execute(_SET_FLAG_SQL, (key, value, now))
                    if event is not None:
                        kind, client_id, detail = event
                        con.execute(_EVENT_SQL, (now, kind, client_id, _detail(detail)))
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
        return inserted

    def events(self, limit=20):
        with closing(self._connect()) as con:
            rows = con.execute("SELECT ts, kind, client_id, detail FROM broker_demo_events ORDER BY id DESC LIMIT ?",
                               (int(limit),)).fetchall()
        return [dict(row) for row in rows]

    def flag(self, key):
        with closing(self._connect()) as con:
            row = con.execute("SELECT value FROM broker_demo_flags WHERE key = ?", (key,)).fetchone()
        return None if row is None else row[0]

    def set_flag(self, key, value):
        with closing(self._connect()) as con:
            con.execute(_SET_FLAG_SQL, (key, value, self._clock()))

    def clear_flag(self, key, value=None):
        """Delete ``key`` (only while it still holds ``value`` when given, so a newer request is never lost)."""
        with closing(self._connect()) as con:
            con.execute(_CLEAR_FLAG_SQL, (key, value, value))


def _counts(rows):
    counts = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return counts


def summarize_status(rows, events, *, routing, dry_run, blocked_reason, max_units, extra=None):
    """Status dict shared by ``BrokerDemoRouter.status()`` and ``read_router_status``. No secrets: ids, codes, prices.
    ``extra`` (the loss-limit view) is added under ``loss_limit`` only when given."""
    open_rows = [r for r in rows if r["status"] in LIVE_STATUSES]
    current = open_rows[-1] if open_rows else None
    open_trade = None if current is None else {
        key: current.get(key) for key in ("client_id", "symbol", "side", "units", "entry", "stop", "target",
                                          "trade_id", "opened_at", "stage", "status")}
    out = {"routing": bool(routing), "dry_run": bool(dry_run), "blocked_reason": blocked_reason,
           "max_units": max_units, "counts": _counts(rows), "open_trade": open_trade,
           "last_events": [{"ts": e["ts"], "kind": e["kind"], "client_id": e["client_id"]} for e in events]}
    return out if extra is None else {**out, "loss_limit": extra}


def _recent(value, now):
    try:
        seen = datetime.fromisoformat(str(value))
        return seen.tzinfo is not None and 0 <= (now - seen).total_seconds() <= HEARTBEAT_MAX_AGE_SECONDS
    except (TypeError, ValueError):
        return False


def read_router_status(path=None, *, env=None, limit=5, now=None):
    """Read-only dashboard view (the dashboard may run in another process, so routing is "on" only when the router
    left a recent heartbeat in the state DB). Unreadable/missing DB -> ``readable False``; never raises, never
    creates the DB."""
    env = os.environ if env is None else env
    base = {"readable": False, "routing": False, "dry_run": False, "blocked_reason": None, "max_units": None,
            "counts": {}, "open_trade": None, "last_events": []}
    try:
        now = now or datetime.now(timezone.utc)
        target = Path(path) if path is not None else Path(load_broker_config(env).state_db_path)
        if not target.is_file():
            return base
        with closing(sqlite3.connect(target.resolve().as_uri() + "?mode=ro", uri=True, timeout=1.0)) as con:
            con.row_factory = sqlite3.Row
            rows = [dict(r) for r in con.execute("SELECT * FROM broker_demo_trades ORDER BY created_at, client_id")]
            events = [dict(r) for r in con.execute(
                "SELECT ts, kind, client_id FROM broker_demo_events ORDER BY id DESC LIMIT ?", (int(limit),))]
            flags = {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM broker_demo_flags")}
    except Exception:  # Missing tables (router never ran), locked or corrupt: nothing trustworthy to show.
        return base
    routing = _recent(flags.get(HEARTBEAT_FLAG), now)
    return {"readable": True, **summarize_status(rows, events, routing=routing,
                                                 dry_run=routing and flags.get(MODE_FLAG) == "dry_run",
                                                 blocked_reason=flags.get("blocked_reason"), max_units=None)}
