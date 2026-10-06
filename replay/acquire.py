"""DEC-4.3 offline acquisition tool: page Twelve Data history into a Replay Store. Never imported by the
runtime or the test suite. The API key is read from the environment, kept in memory, never printed.

Usage: python -m replay.acquire <replay_store.db> <symbol> <timeframe> <start ISO> <end ISO>
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import urlopen

from data.market_evidence import TIMEFRAMES, MarketBar
from replay.store import open_replay_store, replay_engine

SYMBOLS = {"XAUUSD": "XAU/USD", "EURUSD": "EUR/USD"}
INTERVALS = {"1h": "1h", "15m": "15min", "5m": "5min"}
PAGE = 5000


def fetch_page(symbol, timeframe, start, end, api_key):
    url = "https://api.twelvedata.com/time_series?" + urlencode({
        "symbol": SYMBOLS[symbol], "interval": INTERVALS[timeframe], "start_date": start.strftime("%Y-%m-%d %H:%M:%S"),
        "end_date": end.strftime("%Y-%m-%d %H:%M:%S"), "outputsize": PAGE, "timezone": "UTC", "order": "ASC",
        "format": "JSON", "apikey": api_key})
    with urlopen(url, timeout=30) as response:
        payload = json.loads(response.read())
    if payload.get("status") == "error":
        raise RuntimeError(f"provider error code={payload.get('code')}")  # Message may echo inputs; not printed.
    return payload.get("values") or []


def to_bars(symbol, timeframe, values, fetched_at):
    bars = []
    for row in values:
        start = datetime.fromisoformat(row["datetime"]).replace(tzinfo=timezone.utc)
        if start + TIMEFRAMES[timeframe] > fetched_at:
            continue  # Only closed bars become evidence.
        bars.append(MarketBar(symbol, timeframe, start, float(row["open"]), float(row["high"]), float(row["low"]),
                              float(row["close"]), provider="twelve_data", is_closed=True))
    return bars


def acquire(path, symbol, timeframe, start, end, api_key, pause=8.0):
    """Page BACKWARD from ``end`` (the API returns the latest page before end_date), collect everything,
    then ingest once in chronological order (exactly once; gaps recorded, nothing invented)."""
    fetched_at = datetime.now(timezone.utc)
    collected, cursor = {}, end
    while cursor > start:
        page = to_bars(symbol, timeframe, fetch_page(symbol, timeframe, start, cursor, api_key), fetched_at)
        fresh = [b for b in page if b.start not in collected and b.start >= start]
        if not fresh:
            break
        collected.update((b.start, b) for b in fresh)
        cursor = min(b.start for b in fresh) - timedelta(seconds=1)
        time.sleep(pause)  # Free-tier rate limit (8 requests/minute).
    store = open_replay_store(path)
    try:
        bars = [collected[k] for k in sorted(collected)]
        if bars:
            replay_engine(store).ingest(symbol, timeframe, bars, as_of=fetched_at)
    finally:
        store.close()
    return len(collected)


if __name__ == "__main__":
    key = os.environ.get("TWELVE_DATA_API_KEY")
    if not key:
        sys.exit("TWELVE_DATA_API_KEY not set")
    db, symbol, timeframe, start, end = sys.argv[1:6]
    count = acquire(db, symbol, timeframe, datetime.fromisoformat(start), datetime.fromisoformat(end), key)
    print(f"{symbol} {timeframe}: {count} closed bars ingested")
