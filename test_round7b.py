"""Replay-only round 7b (replay/round7b.py): data merge at the split, break-hour schedule for D1, rate-driver
availability / staleness, the per-instrument T1F direction, IBKR-like cost tapes, buy-and-hold and the per-instrument
frozen file that fails closed."""
import json
import os
import tempfile
import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest import mock

import numpy as np
import pandas as pd

from replay import round7 as r7
from replay import round7b as r7b

UTC = timezone.utc


def write_csv(path, starts, price=1.0):
    pd.DataFrame({"start": starts, "Open": price, "High": price, "Low": price, "Close": price, "Volume": 1,
                  "BidClose": price, "AskClose": price}).to_csv(path, index=False)


class Merge(unittest.TestCase):
    def test_old_before_split_new_from_split(self):
        with tempfile.TemporaryDirectory() as d:
            for sub in ("old", "new", "out"):
                os.makedirs(os.path.join(d, sub))
            for tf in ("5m", "15m", "1h"):
                write_csv(os.path.join(d, "old", f"GBPUSD_{tf}.csv"),
                          ["2021-09-30T23:55:00Z", "2021-10-01T00:00:00Z"], 1.0)
                write_csv(os.path.join(d, "new", f"GBPUSD_{tf}.csv"),
                          ["2021-10-01T00:00:00Z", "2021-10-01T00:05:00Z"], 2.0)
            r7b.merge_data(os.path.join(d, "old"), os.path.join(d, "new"), os.path.join(d, "out"), "GBPUSD")
            out = pd.read_csv(os.path.join(d, "out", "GBPUSD_5m.csv"))
            self.assertEqual(list(out["start"]), ["2021-09-30T23:55:00Z", "2021-10-01T00:00:00Z",
                                                  "2021-10-01T00:05:00Z"])
            self.assertEqual(list(out["Close"]), [1.0, 2.0, 2.0])


class Schedule(unittest.TestCase):
    def frame(self, with_break):
        # two trading days (Tue 17:00 NY -> Wed 17:00 NY ...), 1h bars
        idx = pd.date_range(pd.Timestamp("2015-03-10 17:00", tz=r7.NY), periods=48, freq="1h").tz_convert(UTC)
        f = pd.DataFrame({"Open": 1.0, "High": 2.0, "Low": 0.5, "Close": 1.5}, index=idx)
        if not with_break:
            f = f[f.index.tz_convert(r7.NY).hour != 17]
        return f

    def test_d1_complete_with_or_without_the_break_hour(self):
        a = r7b.d1_bars(self.frame(True), (17,))
        b = r7b.d1_bars(self.frame(False), (17,))
        self.assertEqual(len(a), 2)
        self.assertEqual(len(b), 2)
        self.assertEqual(list(a.avail), list(b.avail))

    def test_fx_unchanged(self):
        f = self.frame(True)
        self.assertIs(r7b.drop_break(f, ()), f)


class Rates(unittest.TestCase):
    def test_asof_and_staleness(self):
        days = [date(2020, 1, 1), date(2020, 3, 1), date(2020, 7, 1)]
        out = r7b._asof([date(2019, 12, 31), date(2020, 2, 1), date(2020, 7, 2), date(2020, 11, 1)], days,
                        [1.0, 2.0, 3.0], stale=92)
        self.assertTrue(np.isnan(out[0]))
        self.assertEqual(list(out[1:3]), [1.0, 3.0])
        self.assertTrue(np.isnan(out[3]))  # 123 days after the last usable month

    def test_monthly_publication_lag(self):
        with tempfile.TemporaryDirectory() as d:
            pd.DataFrame({"REF_AREA": ["GBR", "GBR"], "TIME_PERIOD": ["2020-01", "2020-02"],
                          "OBS_VALUE": [1.0, 0.5]}).to_csv(os.path.join(d, "oecd_ir3tib.csv"), index=False)
            ust = pd.DataFrame({"day": [date(2020, 2, 28), date(2020, 3, 2), date(2020, 3, 31)], "y2": 2.0})
            with mock.patch.object(r7, "load_ust2y", return_value=ust):
                out = r7b.driver_series(d, "US2Y-UK3M")
        # Jan usable from Mar 1 (next business day of Feb 28 is Mar 2), Feb from Apr 1 (Mar 31 -> Apr 1)
        self.assertEqual(list(out["day"]), [date(2020, 2, 28), date(2020, 3, 2), date(2020, 3, 31)])
        self.assertEqual(list(out["value"]), [1.0, 1.0, 1.5])

    def test_stale_driver_is_unknown(self):
        f = r7b.Features.__new__(r7b.Features)
        f._rate_avail = np.array([0], dtype=np.int64)
        with mock.patch.object(r7.Features, "state", return_value={"RATE": "UP"}):
            self.assertEqual(f.state(5 * 86400 * 10 ** 9)["RATE"], "UP")
            self.assertIsNone(f.state(11 * 86400 * 10 ** 9)["RATE"])


class Direction(unittest.TestCase):
    def test_t1f_per_instrument(self):
        up, down = {"RATE": "UP"}, {"RATE": "DOWN"}
        gbp, jpy = r7b.SPECS["GBPUSD"].rate_side, r7b.SPECS["USDJPY"].rate_side
        self.assertTrue(r7b.t1f_allowed(up, -1, gbp))
        self.assertFalse(r7b.t1f_allowed(up, 1, gbp))
        self.assertTrue(r7b.t1f_allowed(up, 1, jpy))
        self.assertTrue(r7b.t1f_allowed(down, -1, jpy))
        self.assertFalse(r7b.t1f_allowed({"RATE": None}, 1, jpy))
        # EURUSD convention of round 7 is the -1 case
        for state in (up, down):
            for side in (1, -1):
                self.assertEqual(r7b.t1f_allowed(state, side, -1), r7.t1f_allowed(state, side))


class Costs(unittest.TestCase):
    def test_ibkr_tape(self):
        idx = pd.date_range("2018-01-02", periods=3, freq="5min", tz="UTC")
        f5 = pd.DataFrame({"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 1.0}, index=idx)
        tape = r7b.model_tape(r7b.C_IBKR, "NAS100USD", f5, 1.0, False)
        self.assertEqual(list(tape.spread), [0.25] * 3)
        self.assertEqual(list(tape.commission), [0.62] * 3)
        tape = r7b.model_tape(r7b.C_IBKR_STRESS, "XAUUSD", f5, 0.1, False)
        self.assertAlmostEqual(float(tape.spread[0]), 0.2)
        self.assertEqual(r7b.bar_name("XAUUSD"), "C_IBKR")
        self.assertEqual(r7b.bar_name("GBPUSD"), "C_RAW")

    def test_buy_and_hold(self):
        idx = pd.date_range("2018-01-02", periods=4, freq="5min", tz="UTC")
        f5 = pd.DataFrame({"Open": [100.0, 0, 0, 0], "Close": [110.0, 99.0, 120.0, 121.0]}, index=idx)
        bh = r7b.buy_and_hold(f5, datetime(2018, 1, 1, tzinfo=UTC), datetime(2018, 1, 3, tzinfo=UTC))
        self.assertAlmostEqual(bh["ret_pct"], 21.0)
        self.assertAlmostEqual(bh["dd_pct"], 10.0)
        self.assertAlmostEqual(bh["r_equiv"], 42.0)


class Frozen(unittest.TestCase):
    def payload(self, **kw):
        return {"symbol": "GBPUSD", "mapper": "BASE", "keys": [], "mapping": {"ALL": "T1"}, "pool": list(r7.POOL),
                "rule_tp": r7.RELAXED_TP, **kw}

    def write(self, d, payload):
        path = os.path.join(d, "f.json")
        text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        with open(path, "w", encoding="utf-8") as h:
            h.write(text)
        return path, text

    def test_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            path, text = self.write(d, self.payload())
            with mock.patch.object(r7b, "committed_frozen", return_value=None):
                self.assertRaises(RuntimeError, r7b.load_frozen, path, "GBPUSD")
            with mock.patch.object(r7b, "committed_frozen", return_value=text + " "):
                self.assertRaises(RuntimeError, r7b.load_frozen, path, "GBPUSD")
            with mock.patch.object(r7b, "committed_frozen", return_value=text):
                self.assertEqual(r7b.load_frozen(path, "GBPUSD")["mapping"], {"ALL": "T1"})
                self.assertRaises(ValueError, r7b.load_frozen, path, "USDJPY")

    def test_frozen_repo_path(self):
        self.assertTrue(r7b.frozen_repo("XAUUSD").endswith(os.path.join("replay", "round7_frozen_XAUUSD.json")))
        args = SimpleNamespace(root="/x")
        self.assertEqual(r7b.frozen_path(args, "XAUUSD"), "/x/round7_frozen_XAUUSD.json")


if __name__ == "__main__":
    unittest.main()
