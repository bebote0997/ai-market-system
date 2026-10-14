"""Pre-demo: bounded per-cycle 5m close cache for alert charts (CHART_READ_FAILED fix).

Each cycle stores the closed 5m closes of its snapshot in a small SQLite file next to the trading DB
(``data/runtime/candle_cache.db`` by default; ``AI_FLOOR_CANDLE_CACHE_PATH`` overrides it). Only closes are kept and
every symbol is pruned to ``MAX_BARS`` rows, so the file stays a few kilobytes. Observability only: it is never a
trading input, and the alert worker opens it read-only.
"""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import logging
import math
import os
from pathlib import Path
import sqlite3

LOG = logging.getLogger(__name__)
ENV = "AI_FLOOR_CANDLE_CACHE_PATH"
FILENAME = "candle_cache.db"
MAX_BARS = 288  # 24 hours of 5m bars per symbol
FIVE = timedelta(minutes=5)
_SCHEMA = ("CREATE TABLE IF NOT EXISTS candles(symbol TEXT NOT NULL, bar_start TEXT NOT NULL, close REAL NOT NULL,"
           " PRIMARY KEY(symbol, bar_start)) WITHOUT ROWID")


def path_from_env(environ=None):
    """``AI_FLOOR_CANDLE_CACHE_PATH``, else ``candle_cache.db`` beside ``AI_FLOOR_DB_PATH`` (its default)."""
    environ = os.environ if environ is None else environ
    explicit = environ.get(ENV)
    if explicit:
        return Path(explicit)
    return Path(environ.get("AI_FLOOR_DB_PATH") or "data/runtime/trading_floor.db").with_name(FILENAME)


def _utc(value):
    return value.astimezone(timezone.utc).isoformat()


def _positive(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))
            and float(value) > 0)


def record(path, symbol, frame, as_of, *, max_bars=MAX_BARS):
    """Upsert the closed 5m closes of ``frame`` (closed by ``as_of``) and prune ``symbol`` to ``max_bars``.
    Returns the number of bars written. Raises on I/O errors (callers treat it as best effort)."""
    if not isinstance(max_bars, int) or max_bars <= 0:
        raise ValueError("max_bars must be positive")
    if frame is None or getattr(frame, "empty", True):
        return 0
    rows = []
    for stamp, bar in frame.iloc[-max_bars:].iterrows():
        start = stamp.to_pydatetime()
        close = bar.get("Close")
        close = close.item() if hasattr(close, "item") else close
        if (start.tzinfo is None or start + FIVE > as_of or bar.get("is_closed") != True  # noqa: E712 numpy bool
                or not _positive(close)):
            continue
        rows.append((symbol, _utc(start), float(close)))
    if not rows:
        return 0
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=1)) as db:
        db.execute(_SCHEMA)
        with db:
            db.executemany("INSERT OR REPLACE INTO candles VALUES(?,?,?)", rows)
            db.execute("DELETE FROM candles WHERE symbol=? AND bar_start NOT IN (SELECT bar_start FROM candles "
                       "WHERE symbol=? ORDER BY bar_start DESC LIMIT ?)", (symbol, symbol, max_bars))
    return len(rows)


def closes(path, symbol, occurred_at, *, limit=48):
    """The last ``limit`` cached closes of bars closed by ``occurred_at`` (ISO, aware), oldest first. Read-only;
    a missing or unreadable cache yields () and never blocks an alert."""
    if not path or not symbol or not occurred_at:
        return ()
    if not isinstance(limit, int) or limit <= 0:
        raise ValueError("chart bar limit must be positive")
    try:
        at = datetime.fromisoformat(occurred_at)
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("chart event timestamp must be timezone-aware")
        if not Path(path).is_file():
            LOG.info("component=alerts event=CHART_CACHE_EMPTY symbol=%s", symbol)
            return ()
        with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
            rows = db.execute("SELECT close FROM candles WHERE symbol=? AND bar_start<=? ORDER BY bar_start DESC "
                              "LIMIT ?", (symbol, _utc(at - FIVE), limit)).fetchall()
        values = tuple(float(row[0]) for row in reversed(rows))
        if not all(_positive(value) for value in values):
            raise ValueError("chart close must be a positive finite price")
        return values
    except Exception as exc:  # noqa: BLE001 - a chart never blocks an alert
        LOG.warning("component=alerts event=CHART_READ_FAILED source=candle_cache error_type=%s", type(exc).__name__)
        return ()


__all__ = ["ENV", "MAX_BARS", "closes", "path_from_env", "record"]
