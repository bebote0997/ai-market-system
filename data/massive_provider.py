"""Massive OHLC adapter for closed, timestamped PAPER market snapshots."""
from datetime import datetime, timedelta, timezone
import json
import math
import os
import random
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import pandas as pd
from runtime.retry_after import retry_after_seconds


SYMBOLS = {
    "XAUUSD": ("C:XAUUSD", "METAL"),
    "EURUSD": ("C:EURUSD", "FOREX"),
    "NAS100": ("I:NDX", "INDEX"),
}
TIMEFRAMES = {"1h": (1, "hour", timedelta(hours=1)),
              "15m": (15, "minute", timedelta(minutes=15)),
              "5m": (5, "minute", timedelta(minutes=5))}


class MassiveProviderError(RuntimeError):
    """Safe classification. The server body and request headers stay private."""

    def __init__(self, kind, asset_class, *, http_status=None, retry_after=None):
        self.kind = kind
        self.asset_class = asset_class
        self.http_status = http_status
        self.retry_after = retry_after
        super().__init__(f"{kind}:{asset_class}")


def _http_json(url, api_key, timeout):
    request = Request(url, headers={"Authorization": "Bearer " + api_key})
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


class MassiveMarketDataProvider:
    name = "massive"

    def __init__(self, *, api_key=None, timeout=15, retries=2, history_days=14,
                 transport=None, sleep=time.sleep):
        self.api_key = api_key if api_key is not None else os.environ.get("MASSIVE_API_KEY")
        self.timeout = float(timeout)
        self.retries = int(retries)
        self.history_days = int(history_days)
        if self.timeout <= 0 or not 0 <= self.retries <= 5 or not 1 <= self.history_days <= 90:
            raise ValueError("invalid Massive provider configuration")
        self._transport = transport or _http_json
        self._sleep = sleep
        self.health_by_asset = {"METAL": "NOT_CONFIGURED", "FOREX": "NOT_CONFIGURED",
                                "INDEX": "NOT_CONFIGURED"}
        self.delayed_symbols = set()

    @property
    def health(self):
        values = set(self.health_by_asset.values())
        if not self.api_key:
            return "NOT_CONFIGURED"
        if "ENTITLEMENT_ERROR" in values:
            return "ENTITLEMENT_ERROR"
        if "ERROR" in values:
            return "ERROR"
        if "STALE" in values:
            return "STALE"
        return "READY" if values == {"READY"} else "DEGRADED"

    def _get(self, url, asset_class):
        if not self.api_key:
            raise MassiveProviderError("NOT_CONFIGURED", asset_class)
        for attempt in range(self.retries + 1):
            http_status = retry_after = None
            try:
                return self._transport(url, self.api_key, self.timeout)
            except HTTPError as exc:
                http_status = exc.code
                retry_after = retry_after_seconds(exc.headers)
                transient = exc.code in {408, 429} or 500 <= exc.code <= 599
                kind = ("AUTH_ERROR" if exc.code == 401 else
                        "ENTITLEMENT_ERROR" if exc.code == 403 else
                        "RATE_LIMITED" if exc.code == 429 else "PROVIDER_ERROR")
            except (URLError, TimeoutError, ConnectionError):
                transient, kind = True, "PROVIDER_ERROR"
            if not transient or attempt == self.retries or (retry_after is not None and retry_after > 15):
                self.health_by_asset[asset_class] = "ENTITLEMENT_ERROR" if kind == "ENTITLEMENT_ERROR" else "ERROR"
                raise MassiveProviderError(kind, asset_class, http_status=http_status,
                                           retry_after=retry_after) from None
            delay = retry_after if retry_after is not None else (
                min(15.0, 2.0 * 2 ** attempt) if kind == "RATE_LIMITED"
                else min(4.0, 0.4 * 2 ** attempt))
            self._sleep(delay + random.uniform(0, 0.1))
        raise AssertionError("unreachable")

    def _bars(self, symbol, timeframe, as_of):
        provider_symbol, asset_class = SYMBOLS[symbol]
        multiplier, timespan, duration = TIMEFRAMES[timeframe]
        start = as_of - timedelta(days=self.history_days)
        path = (f"https://api.massive.com/v2/aggs/ticker/{quote(provider_symbol, safe=':')}"
                f"/range/{multiplier}/{timespan}/{int(start.timestamp() * 1000)}"
                f"/{int(as_of.timestamp() * 1000)}?sort=asc&limit=50000")
        payload = self._get(path, asset_class)
        if not isinstance(payload, dict) or payload.get("ticker") != provider_symbol:
            self.health_by_asset[asset_class] = "ERROR"
            raise MassiveProviderError("INVALID_RESPONSE", asset_class)
        status = payload.get("status")
        if status not in {"OK", "DELAYED"}:
            self.health_by_asset[asset_class] = "ENTITLEMENT_ERROR" if status in {"NOT_AUTHORIZED", "DENIED"} else "ERROR"
            raise MassiveProviderError(self.health_by_asset[asset_class], asset_class)
        raw = payload.get("results", [])
        if not isinstance(raw, list):
            raise MassiveProviderError("INVALID_RESPONSE", asset_class)
        rows = []
        previous = None
        for item in raw:
            if not isinstance(item, dict) or not isinstance(item.get("t"), int) or isinstance(item["t"], bool):
                raise MassiveProviderError("INVALID_BAR", asset_class)
            stamp = datetime.fromtimestamp(item["t"] / 1000, timezone.utc)
            if previous is not None and stamp <= previous:
                raise MassiveProviderError("DUPLICATE_OR_UNSORTED", asset_class)
            previous = stamp
            values = [item.get(k) for k in ("o", "h", "l", "c")]
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or
                   not math.isfinite(v) or v <= 0 for v in values):
                raise MassiveProviderError("INVALID_BAR", asset_class)
            o, h, l, c = values
            if h < max(o, c) or l > min(o, c) or h < l:
                raise MassiveProviderError("INVALID_BAR", asset_class)
            # Massive t is the aggregate-window START. A forming bar is never
            # supplied to deterministic analysis or the paper broker.
            if stamp + duration > as_of:
                continue
            if "v" in item and (isinstance(item["v"], bool) or not isinstance(item["v"], (int, float)) or
                                not math.isfinite(item["v"]) or item["v"] < 0):
                raise MassiveProviderError("INVALID_BAR", asset_class)
            rows.append({"timestamp": stamp, "Open": o, "High": h, "Low": l, "Close": c,
                         "volume": item.get("v"), "symbol": symbol, "timeframe": timeframe,
                         "provider": "massive", "provider_symbol": provider_symbol,
                         "is_closed": True})
        frame = pd.DataFrame(rows)
        if frame.empty:
            return pd.DataFrame(index=pd.DatetimeIndex([], tz="UTC"))
        frame = frame.set_index("timestamp")
        if status == "DELAYED":
            self.delayed_symbols.add(symbol)
            self.health_by_asset[asset_class] = "STALE"
        elif symbol not in self.delayed_symbols:
            self.health_by_asset[asset_class] = "READY"
        return frame

    def load_snapshot(self, symbol, as_of):
        if symbol not in SYMBOLS:
            raise ValueError("unsupported symbol")
        if not isinstance(as_of, datetime) or as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")
        as_of = as_of.astimezone(timezone.utc)
        return {timeframe: self._bars(symbol, timeframe, as_of) for timeframe in TIMEFRAMES}
