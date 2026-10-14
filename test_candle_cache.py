"""Pre-demo fix: per-cycle bounded candle cache so alert charts work without the Evidence Store."""
import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from ai.provider import DeterministicAIProvider
from runtime.alert_catalog import alert_event
from runtime.alert_hook import AlertHook
from runtime.config import RuntimeConfig
from runtime.service import OperationalRuntime
from runtime import candle_cache
from test_demo_runner import T, instrument, macro_fixture

FIVE = timedelta(minutes=5)


def five(start, closes, closed=True):
    stamps = pd.date_range(start, periods=len(closes), freq="5min")
    return pd.DataFrame({"Open": closes, "High": closes, "Low": closes, "Close": closes, "symbol": "XAUUSD",
                         "is_closed": closed}, index=stamps)


class Snapshot:
    def load_snapshot(self, symbol, at):
        frame = five(at - 12 * FIVE, [100.0 + i for i in range(12)])
        frame["symbol"] = symbol
        last = frame.iloc[[-1]]
        return {"1h": last, "15m": last, "5m": frame}


class CandleCacheTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="candle-cache-")
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.path = self.dir / "candle_cache.db"

    def test_record_is_bounded_and_keeps_only_closed_bars(self):
        frame = five(T - 20 * FIVE, [100.0 + i for i in range(21)])  # the last bar starts at T: not closed by T
        self.assertEqual(candle_cache.record(self.path, "XAUUSD", frame, T, max_bars=8), 7)
        later = five(T - 4 * FIVE, [200.0, 201.0, 202.0, 203.0, 204.0])
        candle_cache.record(self.path, "XAUUSD", later, T + FIVE, max_bars=8)
        closes = candle_cache.closes(self.path, "XAUUSD", (T + FIVE).isoformat(), limit=100)
        self.assertEqual(len(closes), 8)  # pruned to max_bars
        self.assertEqual(closes[-5:], (200.0, 201.0, 202.0, 203.0, 204.0))  # newer values replace the same bars
        self.assertEqual(candle_cache.closes(self.path, "EURUSD", T.isoformat()), ())
        self.assertEqual(candle_cache.record(self.path, "XAUUSD", five(T - FIVE, [1.0], closed=False), T + FIVE), 0)

    def test_closes_respect_the_event_time_and_never_raise(self):
        candle_cache.record(self.path, "XAUUSD", five(T - 3 * FIVE, [1.0, 2.0, 3.0]), T)
        self.assertEqual(candle_cache.closes(self.path, "XAUUSD", (T - FIVE).isoformat()), (1.0, 2.0))
        self.assertEqual(candle_cache.closes(self.dir / "missing.db", "XAUUSD", T.isoformat()), ())
        (self.dir / "corrupt.db").write_bytes(b"not sqlite")
        with self.assertLogs("runtime.candle_cache", "WARNING"):
            self.assertEqual(candle_cache.closes(self.dir / "corrupt.db", "XAUUSD", T.isoformat()), ())
        self.assertFalse((self.dir / "missing.db").exists())  # the reader never creates a file

    def test_default_path_sits_beside_the_trading_db(self):
        self.assertEqual(candle_cache.path_from_env({"AI_FLOOR_DB_PATH": "/disk/x/trading.db"}),
                         Path("/disk/x/candle_cache.db"))
        self.assertEqual(candle_cache.path_from_env({}), Path("data/runtime/candle_cache.db"))
        self.assertEqual(candle_cache.path_from_env({candle_cache.ENV: "/tmp/c.db"}), Path("/tmp/c.db"))
        with patch.dict(os.environ, {"AI_FLOOR_DB_PATH": str(self.dir / "t.db")}, clear=False):
            os.environ.pop(candle_cache.ENV, None)
            self.assertEqual(RuntimeConfig.from_env().candle_cache_path, self.dir / "candle_cache.db")

    def test_each_cycle_persists_its_candles(self):
        config = RuntimeConfig(db_path=self.dir / "trading_floor.db", enabled_symbols=("XAUUSD", "EURUSD"),
                               candle_cache_path=self.path)
        runtime = OperationalRuntime(config, market_provider=Snapshot(), ai_provider=DeterministicAIProvider(),
                                     macro_provider=macro_fixture(), instruments={"XAUUSD": instrument()},
                                     clock=lambda: T)
        try:
            self.assertNotEqual(runtime.run_cycle("XAUUSD", T), "ERROR")
        finally:
            runtime.close()
        self.assertEqual(candle_cache.closes(self.path, "XAUUSD", T.isoformat()),
                         tuple(100.0 + i for i in range(12)))

    def test_alert_chart_uses_the_cache_without_an_evidence_store(self):
        candle_cache.record(self.path, "XAUUSD", five(T - 3 * FIVE, [1.0, 2.0, 3.0]), T)
        hook = AlertHook(environ={candle_cache.ENV: str(self.path),
                                  "AI_FLOOR_MARKET_EVIDENCE_PATH": str(self.dir / "absent.db")})
        event = alert_event("e-1", "POSITION_OPENED", T.isoformat(), symbol="XAUUSD")
        prepared = hook._prepare_alert(event)
        self.assertEqual(prepared.closes, (1.0, 2.0, 3.0))
        self.assertEqual(dict(prepared.trade_rows())["Chart"], "SPARKLINE (3 closes)")


if __name__ == "__main__":
    unittest.main()
