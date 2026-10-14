"""Read-only adapter for pre-demo candles(symbol, bar_start, close), never OHLC."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import logging
import math
import os
from pathlib import Path
import sqlite3

LOG = logging.getLogger(__name__)
MAX_BARS = 288


def cache_path(environ=None):
    env = os.environ if environ is None else environ
    return Path(env.get('AI_FLOOR_CANDLE_CACHE_PATH') or
                Path(env.get('AI_FLOOR_DB_PATH') or 'data/runtime/trading_floor.db').with_name('candle_cache.db'))


def read_candles(path, symbol, as_of, *, limit=48):
    """Return chronological closed 5m prices at the setup boundary, bounded in SQL.

    Compatible with origin/v2/pre-demo-fixes. Missing/corrupt data yields NO_DATA
    (an empty tuple); never creates files, calls providers or invents OHLC.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or not 0 < limit <= MAX_BARS:
        raise ValueError('limit must be between 1 and 288')
    if not path or not symbol or not as_of:
        return ()
    try:
        at = datetime.fromisoformat(as_of.replace('Z', '+00:00'))
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError('aware setup timestamp required')
        at = at.astimezone(timezone.utc)
        with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
            rows = db.execute('SELECT bar_start,close FROM candles WHERE symbol=? AND bar_start<=? '
                              'ORDER BY bar_start DESC LIMIT ?',
                              (symbol, (at-timedelta(minutes=5)).isoformat(), limit)).fetchall()
        bars = []
        for stamp, price in reversed(rows):
            start = datetime.fromisoformat(stamp)
            if (start.tzinfo is None or start.utcoffset() is None or start+timedelta(minutes=5) > at
                    or isinstance(price, bool) or not isinstance(price, (float, int))
                    or not math.isfinite(price) or price <= 0):
                raise ValueError('invalid cached close')
            bars.append({'timestamp': start.astimezone(timezone.utc).isoformat(),
                         'close': float(price), 'timeframe': '5m', 'source': 'candle_cache'})
        return tuple(bars)
    except (OSError, sqlite3.Error, ValueError, TypeError, OverflowError) as exc:
        LOG.info('component=charts event=SETUP_CACHE_NO_DATA error_type=%s', type(exc).__name__)
        return ()
