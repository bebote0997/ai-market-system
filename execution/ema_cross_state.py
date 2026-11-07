"""Durable EMA-cross signal rows in the broker state DB. Created only when the EMA flag is on.

One row per signal id. A restart reads the same PENDING row and will not send a second order for a signal that
was already SUBMITTED, REJECTED, CANCELLED, or EXPIRED. The table is not part of the paper trading schema.
"""
from datetime import datetime, timezone
import sqlite3
from pathlib import Path

from execution.fs_security import private_state_dir, secure_sqlite_files


_TERMINAL = ("CANCELLED", "EXPIRED", "SUBMITTED", "REJECTED")
SCHEMA = """
CREATE TABLE IF NOT EXISTS ema_cross_signals (
    signal_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    confirmed_at TEXT NOT NULL,
    ready_at TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    client_id TEXT,
    updated_at TEXT NOT NULL
);
"""


def _iso(value):
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timezone-aware datetime required")
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    if not isinstance(value, str) or not value:
        raise ValueError("timestamp required")
    return value


def _parse(value):
    if not isinstance(value, str) or not value:
        return None
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


class EmaSignalStore:
    def __init__(self, path):
        self.path = Path(path)

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

    def get(self, signal_id):
        with self._connect() as con:
            row = con.execute("SELECT * FROM ema_cross_signals WHERE signal_id = ?", (signal_id,)).fetchone()
        return None if row is None else dict(row)

    def pending(self, symbol):
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM ema_cross_signals WHERE symbol = ? AND status = 'PENDING' "
                "ORDER BY confirmed_at, signal_id LIMIT 1", (symbol,)).fetchone()
        return None if row is None else dict(row)

    def terminal_ids(self, symbol):
        marks = ",".join("?" for _ in _TERMINAL)
        with self._connect() as con:
            rows = con.execute(
                f"SELECT signal_id FROM ema_cross_signals WHERE symbol = ? AND status IN ({marks})",
                (symbol, *_TERMINAL)).fetchall()
        return {row["signal_id"] for row in rows}

    def save(self, signal, *, now):
        """Insert or replace the signal. A terminal row is not moved back to PENDING."""
        stamp = _iso(now)
        with self._connect() as con:
            current = con.execute("SELECT status FROM ema_cross_signals WHERE signal_id = ?",
                                  (signal.signal_id,)).fetchone()
            if current is not None and current["status"] in _TERMINAL and signal.status == "PENDING":
                return False
            con.execute(
                "INSERT INTO ema_cross_signals(signal_id, symbol, side, confirmed_at, ready_at, status, reason, "
                "client_id, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(signal_id) DO UPDATE SET status = excluded.status, reason = excluded.reason, "
                "client_id = excluded.client_id, updated_at = excluded.updated_at",
                (signal.signal_id, "EURUSD", signal.side, _iso(signal.confirmed_at), _iso(signal.ready_at),
                 signal.status, signal.reason, signal.client_id, stamp))
        return True


def signal_from_row(row):
    """``execution.ema_cross.Signal`` from a stored row, or ``None`` when the row is unusable."""
    from execution.ema_cross import Signal
    if not row or row.get("status") != "PENDING":
        return None
    confirmed, ready = _parse(row.get("confirmed_at")), _parse(row.get("ready_at"))
    if confirmed is None or ready is None or row.get("side") not in ("LONG", "SHORT"):
        return None
    return Signal(row["signal_id"], row["side"], confirmed, ready, client_id=row.get("client_id"))
