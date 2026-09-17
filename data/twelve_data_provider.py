"""Twelve Data closed-bar adapter for the existing PAPER market-data boundary."""
from datetime import datetime, timedelta, timezone
import json
import math
import os
import random
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from runtime.retry_after import retry_after_seconds


SYMBOLS = {"XAUUSD": ("XAU/USD", "METAL"), "EURUSD": ("EUR/USD", "FOREX")}
TIMEFRAMES = {"1h": ("1h", timedelta(hours=1)),
              "15m": ("15min", timedelta(minutes=15)),
              "5m": ("5min", timedelta(minutes=5))}


class TwelveDataProviderError(RuntimeError):
    """Safe failure classification without URLs, credentials or server messages."""

    def __init__(self, kind, asset_class, *, http_status=None, retry_after=None):
        self.kind = kind
        self.asset_class = asset_class
        self.http_status = http_status
        self.retry_after = retry_after
        super().__init__(f"{kind}:{asset_class}")


def _http_json(url, timeout):
    with urlopen(Request(url), timeout=timeout) as response:
        return json.load(response)


def _error_kind(code):
    return ("AUTH_ERROR" if code == 401 else
            "ENTITLEMENT_ERROR" if code == 403 else
            "SYMBOL_UNAVAILABLE" if code == 404 else
            "RATE_LIMITED" if code == 429 else "PROVIDER_FAILURE")


class TwelveDataMarketDataProvider:
    name = "twelve_data"

    def __init__(self, *, api_key=None, timeout=15, retries=1, outputsize=500,
                 transport=None, sleep=time.sleep):
        self.api_key = api_key if api_key is not None else os.environ.get("TWELVE_DATA_API_KEY")
        self.timeout = float(timeout)
        self.retries = int(retries)
        self.outputsize = int(outputsize)
        if self.timeout <= 0 or not 0 <= self.retries <= 2 or not 30 <= self.outputsize <= 5000:
            raise ValueError("invalid Twelve Data provider configuration")
        self._transport = transport or _http_json
        self._sleep = sleep
        self.health_by_asset = {"METAL": "NOT_CONFIGURED", "FOREX": "NOT_CONFIGURED"}
        self._cache = {}

    @property
    def health(self):
        if not self.api_key:
            return "NOT_CONFIGURED"
        values = set(self.health_by_asset.values())
        if "ENTITLEMENT_ERROR" in values:
            return "ENTITLEMENT_ERROR"
        if "ERROR" in values:
            return "ERROR"
        if "STALE" in values:
            return "STALE"
        return "READY" if values == {"READY"} else "DEGRADED"

    def _request(self, symbol, timeframe):
        ticker, asset_class = SYMBOLS[symbol]
        interval, _ = TIMEFRAMES[timeframe]
        if not self.api_key:
            raise TwelveDataProviderError("NOT_CONFIGURED", asset_class)
        # The key is only in memory and is never put in error strings or logs.
        url = "https://api.twelvedata.com/time_series?" + urlencode({
            "symbol": ticker, "interval": interval, "outputsize": self.outputsize,
            "timezone": "UTC", "format": "JSON", "apikey": self.api_key,
        })
        for attempt in range(self.retries + 1):
            status = retry_after = None
            try:
                payload = self._transport(url, self.timeout)
                if not isinstance(payload, dict):
                    raise TwelveDataProviderError("INVALID_RESPONSE", asset_class)
                if payload.get("status") == "error":
                    raw_code = payload.get("code")
                    status = int(raw_code) if str(raw_code).isdigit() else None
                    kind = _error_kind(status)
                else:
                    return payload
            except HTTPError as exc:
                status = exc.code
                retry_after = retry_after_seconds(exc.headers)
                kind = _error_kind(status)
            except (URLError, TimeoutError, ConnectionError):
                kind = "PROVIDER_FAILURE"
            except TwelveDataProviderError as exc:
                kind = exc.kind
            transient = status == 429 or status is not None and 500 <= status <= 599 or (
                status is None and kind == "PROVIDER_FAILURE")
            if not transient or attempt == self.retries or (retry_after is not None and retry_after > 15):
                self.health_by_asset[asset_class] = "ENTITLEMENT_ERROR" if kind == "ENTITLEMENT_ERROR" else "ERROR"
                raise TwelveDataProviderError(kind, asset_class, http_status=status,
                                              retry_after=retry_after) from None
            delay = retry_after if retry_after is not None else min(15.0, 2.0 * 2 ** attempt)
            self._sleep(delay + random.uniform(0, 0.1))
        raise AssertionError("unreachable")

    def _bars_impl(self, symbol, timeframe, as_of):
        ticker, asset_class = SYMBOLS[symbol]
        _, duration = TIMEFRAMES[timeframe]
        as_of = as_of.astimezone(timezone.utc)
        expected_start = datetime.fromtimestamp(
            ((int(as_of.timestamp()) - int(duration.total_seconds())) // int(duration.total_seconds()))
            * int(duration.total_seconds()), timezone.utc)
        cache_key = (symbol, timeframe, expected_start)
        if cache_key in self._cache:
            return self._cache[cache_key].copy(deep=True)
        payload = self._request(symbol, timeframe)
        meta = payload.get("meta")
        if (payload.get("status") != "ok" or not isinstance(meta, dict) or
                meta.get("symbol") != ticker or meta.get("interval") != TIMEFRAMES[timeframe][0]):
            self.health_by_asset[asset_class] = "ERROR"
            raise TwelveDataProviderError("INVALID_RESPONSE", asset_class)
        values = payload.get("values")
        if not isinstance(values, list):
            self.health_by_asset[asset_class] = "ERROR"
            raise TwelveDataProviderError("INVALID_RESPONSE", asset_class)
        rows = []
        seen = set()
        for item in values:
            if not isinstance(item, dict) or not isinstance(item.get("datetime"), str):
                raise TwelveDataProviderError("INVALID_BAR", asset_class)
            try:
                stamp = datetime.fromisoformat(item["datetime"])
                stamp = stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)
                prices = [float(item[k]) for k in ("open", "high", "low", "close")]
            except (ValueError, TypeError, KeyError, OverflowError):
                raise TwelveDataProviderError("INVALID_BAR", asset_class) from None
            if stamp in seen or any(not math.isfinite(v) or v <= 0 for v in prices):
                raise TwelveDataProviderError("INVALID_BAR", asset_class)
            seen.add(stamp)
            o, h, l, c = prices
            if h < max(o, c) or l > min(o, c) or h < l:
                raise TwelveDataProviderError("INVALID_BAR", asset_class)
            if stamp + duration > as_of:
                continue
            volume = item.get("volume")
            if volume is not None:
                try:
                    volume = float(volume)
                except (ValueError, TypeError, OverflowError):
                    raise TwelveDataProviderError("INVALID_BAR", asset_class) from None
                if not math.isfinite(volume) or volume < 0:
                    raise TwelveDataProviderError("INVALID_BAR", asset_class)
            rows.append({"timestamp": stamp, "Open": o, "High": h, "Low": l, "Close": c,
                         "volume": volume, "symbol": symbol, "timeframe": timeframe,
                         "provider": self.name, "provider_symbol": ticker, "is_closed": True})
        frame = pd.DataFrame(rows)
        if frame.empty:
            self.health_by_asset[asset_class] = "ERROR"
            return pd.DataFrame(index=pd.DatetimeIndex([], tz="UTC"))
        frame = frame.set_index("timestamp").sort_index()
        latest = frame.index.max().to_pydatetime()
        self.health_by_asset[asset_class] = "READY" if latest >= expected_start else "STALE"
        if latest >= expected_start:
            self._cache[cache_key] = frame.copy(deep=True)
        return frame

    def _bars(self, symbol, timeframe, as_of):
        try:
            return self._bars_impl(symbol, timeframe, as_of)
        except TwelveDataProviderError as exc:
            self.health_by_asset[exc.asset_class] = (
                "ENTITLEMENT_ERROR" if exc.kind == "ENTITLEMENT_ERROR" else "ERROR")
            raise

    def load_snapshot(self, symbol, as_of):
        if symbol not in SYMBOLS:
            raise ValueError("unsupported Twelve Data symbol")
        if not isinstance(as_of, datetime) or as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")
        return {tf: self._bars(symbol, tf, as_of) for tf in TIMEFRAMES}
