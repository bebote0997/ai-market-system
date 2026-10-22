"""Replay-only round 8 (replay/round8.py): fill rules (stop priority, limit trade-through, no TP on a pending fill
bar, gaps), OCO same-bar tie, stop clamps, cost models, metrics (MTM DD), selection rule and the fail-closed frozen
file."""
import json
import os
import tempfile
import unittest
from datetime import date
from unittest import mock

import numpy as np
import pandas as pd

from replay import round8 as r8


def frame(rows, start="2024-03-05 08:00", spread=0.0001):
    idx = pd.date_range(pd.Timestamp(start, tz=r8.NY), periods=len(rows), freq="1min").tz_convert("UTC")
    o, h, lo, c = zip(*rows)
    df = pd.DataFrame({"Open": o, "High": h, "Low": lo, "Close": c, "Volume": 10.0}, index=idx)
    df["BidClose"] = df["Close"] - spread / 2
    df["AskClose"] = df["Close"] + spread / 2
    df.index = df.index.as_unit("ns")
    return df


ZERO = r8.Cost("ZERO", {"EURUSD": 0.0}, {"EURUSD": 0.0}, {"EURUSD": 0.0})


def mk(rows):
    return r8.Market(frame(rows), "EURUSD")


class Fills(unittest.TestCase):
    def test_stop_wins_same_bar_tie_and_r(self):
        m = mk([(1.1, 1.1, 1.1, 1.1)] + [(1.1, 1.1030, 1.0985, 1.1)] * 3)
        f = r8.Fills(ZERO, "EURUSD", m.spread)
        od = r8.Order(m.days[0], [r8.Leg(+1, "market", 0.0, stop=1.0990, tp_r=1.0)], 1, 2, 3)
        tr = r8.simulate(m, f, od)
        self.assertEqual(tr["reason"], "SL")
        self.assertAlmostEqual(tr["r"], -1.0)

    def test_clamp_widens_to_5_pips_fx(self):
        m = mk([(1.1, 1.1, 1.1, 1.1)] * 3 + [(1.1, 1.1, 1.0990, 1.0990)])
        f = r8.Fills(ZERO, "EURUSD", m.spread)
        od = r8.Order(m.days[0], [r8.Leg(+1, "market", 0.0, stop=1.0999)], 1, 2, 3)
        tr = r8.simulate(m, f, od)
        self.assertAlmostEqual(tr["risk"], 0.0005)
        self.assertEqual(tr["reason"], "SL")

    def test_limit_needs_trade_through_and_no_tp_on_fill_bar(self):
        rows = [(1.1, 1.1, 1.1, 1.1), (1.1, 1.1, 1.0950, 1.0950), (1.0950, 1.1, 1.0949, 1.1),
                (1.1, 1.1, 1.1, 1.1)]
        m = mk(rows)
        f = r8.Fills(ZERO, "EURUSD", m.spread)
        od = r8.Order(m.days[0], [r8.Leg(+1, "limit", 1.0950, stop=1.0900, tp_r=0.5)], 1, 3, 3)
        tr = r8.simulate(m, f, od)
        self.assertEqual(tr["k"], 2)  # bar 1 only touched the limit
        self.assertEqual(tr["reason"], "TP")
        self.assertEqual(tr["j"], 3)  # fill bar 2 reached the TP but cannot take profit

    def test_stop_gap_fills_at_open(self):
        m = mk([(1.1, 1.1, 1.1, 1.1), (1.1, 1.1, 1.1, 1.1), (1.0980, 1.0980, 1.0970, 1.0975)])
        f = r8.Fills(ZERO, "EURUSD", m.spread)
        od = r8.Order(m.days[0], [r8.Leg(+1, "market", 0.0, stop=1.0990)], 1, 2, 2)
        tr = r8.simulate(m, f, od)
        self.assertAlmostEqual(tr["r"], -2.0)

    def test_oco_same_bar_is_no_trade(self):
        m = mk([(1.1, 1.1, 1.1, 1.1), (1.1, 1.1020, 1.0980, 1.1), (1.1, 1.1, 1.1, 1.1)])
        f = r8.Fills(ZERO, "EURUSD", m.spread)
        od = r8.Order(m.days[0], [r8.Leg(+1, "stop", 1.1010, stop=1.0990), r8.Leg(-1, "stop", 1.0990, stop=1.1010)],
                      1, 2, 2)
        self.assertIsNone(r8.simulate(m, f, od))

    def test_c_raw_costs(self):
        m = mk([(1.1, 1.1, 1.1, 1.1)] * 4)
        f = r8.Fills(r8.C_RAW, "EURUSD", m.spread)
        od = r8.Order(m.days[0], [r8.Leg(+1, "market", 0.0, stop=1.0990)], 1, 2, 3)
        tr = r8.simulate(m, f, od)
        # entry 1.1 + 0.1 + 0.1 pip; time exit 1.1 - 0.1 - 0.1 pip; commission 0.7 pip -> -1.1 pip on 10.2 pips risk
        self.assertAlmostEqual(tr["r"], -1.1 / 10.2, places=6)

    def test_oanda_uses_previous_bar_spread(self):
        m = mk([(1.1, 1.1, 1.1, 1.1)] * 3)
        m.spread = np.array([0.0002, 0.0010, 0.0010])
        f = r8.Fills(r8.C_OANDA, "EURUSD", m.spread)
        self.assertAlmostEqual(f.hs[1], 0.0001)
        self.assertAlmostEqual(f.slip[1], 0.00002)

    def test_overlap_skipped(self):
        m = mk([(1.1, 1.1, 1.1, 1.1)] * 6)
        f = r8.Fills(ZERO, "EURUSD", m.spread)
        a = r8.Order(m.days[0], [r8.Leg(+1, "market", 0.0, stop=1.0990)], 1, 2, 4)
        b = r8.Order(m.days[0], [r8.Leg(-1, "market", 0.0, stop=1.1010)], 3, 4, 5)
        self.assertEqual(len(r8.run_orders(m, f, [a, b])), 1)


class Clamp(unittest.TestCase):
    def test_index_clamp_uses_prior_days_median(self):
        m = mk([(1.1, 1.1, 1.1, 1.1)] * 3)
        m.spec = r8.SPECS["NAS100USD"]
        m.atr_med = pd.Series({date(2024, 3, 5): 10.0})
        self.assertEqual(m.clamp(date(2024, 3, 5), 1.0), 15.0)
        self.assertEqual(m.clamp(date(2024, 3, 5), 100.0), 45.0)
        self.assertIsNone(m.clamp(date(2024, 3, 6), 1.0))


class Metrics(unittest.TestCase):
    def test_mtm_dd_includes_open_trade_excursion(self):
        trades = [{"day": "2024-01-02", "r": 1.0, "worst": -0.5, "reason": "TP"},
                  {"day": "2024-01-03", "r": -0.2, "worst": -0.9, "reason": "TIME"}]
        mt = r8.metrics(trades, "2024-01-01", "2024-02-01")
        self.assertAlmostEqual(mt["mtm_dd_r"], 0.9)
        self.assertAlmostEqual(mt["pf"], 5.0)

    def test_select_requires_bar_and_folds(self):
        good = {"trades": 200, "tr_mo": 5, "avg_r": 0.2, "pf": 1.5, "mtm_dd_r": 5}
        fold = {"trades": 20, "avg_r": 0.1}
        res = {"A": {"train": good, "folds": [fold] * 4},
               "B": {"train": {**good, "tr_mo": 2}, "folds": [{"trades": 20, "avg_r": 0.5}] * 4},
               "C": {"train": good, "folds": [fold, fold, {"trades": 20, "avg_r": -0.1}] * 1 + [{"trades": 0,
                                                                                               "avg_r": 0.0}]}}
        best, _ = r8.select(res)
        self.assertEqual(best, "A")

    def test_select_none(self):
        res = {"A": {"train": {"trades": 1, "tr_mo": 0.1, "avg_r": -1, "pf": 0, "mtm_dd_r": 1},
                     "folds": [{"trades": 0, "avg_r": 0.0}] * 4}}
        self.assertIsNone(r8.select(res)[0])


class Frozen(unittest.TestCase):
    def test_fails_closed_when_not_committed_or_different(self):
        with tempfile.TemporaryDirectory() as d:
            payload = {"symbol": "EURUSD", "variant": "L1:OPEN30_MOM"}
            text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
            with open(r8.frozen_path(d, "EURUSD"), "w") as fh:
                fh.write(text)
            with mock.patch.object(r8, "committed_frozen", return_value=None):
                with self.assertRaises(RuntimeError):
                    r8.load_frozen(d, "EURUSD")
            with mock.patch.object(r8, "committed_frozen", return_value=text.replace("MOM", "REV")):
                with self.assertRaises(RuntimeError):
                    r8.load_frozen(d, "EURUSD")
            with mock.patch.object(r8, "committed_frozen", return_value=text):
                self.assertEqual(r8.load_frozen(d, "EURUSD")["variant"], "L1:OPEN30_MOM")

    def test_cutoff_slices_before_build(self):
        with tempfile.TemporaryDirectory() as d:
            df = frame([(1.1, 1.1, 1.1, 1.1)] * 5, start="2024-12-31 23:57")
            out = df.reset_index().rename(columns={"index": "start"})
            out["start"] = out["start"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            path = os.path.join(d, "x.csv")
            out.to_csv(path, index=False)
            sliced = r8.load_m1(path, end="2025-01-01")
            self.assertEqual(len(sliced), 3)
            self.assertLess(sliced.index[-1], pd.Timestamp("2025-01-01", tz=r8.NY))


class Variants(unittest.TestCase):
    def test_declared_sets(self):
        self.assertEqual(len(r8.variants("EURUSD")), 19)
        self.assertEqual(len(r8.variants("GBPUSD")), 19)
        self.assertEqual(len(r8.variants("XAUUSD")), 18)
        self.assertEqual(len(r8.variants("NAS100USD")), 14)


if __name__ == "__main__":
    unittest.main()
