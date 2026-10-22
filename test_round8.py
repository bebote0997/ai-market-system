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


SPREAD = r8.Cost("SPREAD", {"EURUSD": 0.0002}, {"EURUSD": 0.0}, {"EURUSD": 0.0})  # half spread 1 pip, no slip


def segments(*parts):
    """Concatenated 1-min frames: each part is (NY start, rows)."""
    return pd.concat([frame(rows, start=start) for start, rows in parts])


def flat(n, px=1.1):
    return [(px, px, px, px)] * n


class BidAsk(unittest.TestCase):
    """Codex finding (a): every touch on the side that fills it (half spread 1 pip)."""

    def test_buy_limit_needs_the_ask_through_the_level(self):
        # mid low 1.0948 -> ask low 1.0949: not through 1.0949; next bar mid low 1.0947 -> ask 1.0948 < 1.0949
        m = mk([(1.1, 1.1, 1.1, 1.1), (1.0960, 1.0960, 1.0948, 1.0955), (1.0955, 1.0955, 1.0947, 1.0955),
                (1.0955, 1.0955, 1.0955, 1.0955)])
        f = r8.Fills(SPREAD, "EURUSD", m.spread)
        od = r8.Order(m.days[0], [r8.Leg(+1, "limit", 1.0949, stop=1.0939)], 1, 3, 3)
        tr = r8.simulate(m, f, od)
        self.assertEqual(tr["k"], 2)
        self.assertAlmostEqual(tr["risk"], 0.0010)  # filled at the limit itself, never above it

    def test_buy_stop_triggers_on_the_ask(self):
        # mid high 1.10095 never reaches 1.1010, but the ask (1.10105) does
        m = mk([(1.1, 1.1, 1.1, 1.1), (1.1, 1.10095, 1.1, 1.1), (1.1, 1.1, 1.1, 1.1)])
        f = r8.Fills(SPREAD, "EURUSD", m.spread)
        od = r8.Order(m.days[0], [r8.Leg(+1, "stop", 1.1010, stop=1.1000)], 1, 2, 2)
        self.assertEqual(r8.simulate(m, f, od)["k"], 1)
        f0 = r8.Fills(ZERO, "EURUSD", m.spread)
        self.assertIsNone(r8.simulate(m, f0, od))

    def test_long_stop_hit_on_the_bid_when_mid_stays_above(self):
        # entry ask 1.1001, stop 1.0990 (mid open - 10 pips); mid low 1.09905 -> bid 1.09895 <= stop
        m = mk([(1.1, 1.1, 1.1, 1.1), (1.1, 1.1, 1.1, 1.1), (1.1, 1.1, 1.09905, 1.1), (1.1, 1.1, 1.1, 1.1)])
        f = r8.Fills(SPREAD, "EURUSD", m.spread)
        od = r8.Order(m.days[0], [r8.Leg(+1, "market", 0.0, stop=1.0990)], 1, 2, 3)
        tr = r8.simulate(m, f, od)
        self.assertEqual((tr["reason"], tr["j"]), ("SL", 2))
        self.assertAlmostEqual(tr["r"], -1.0)  # risk = ask entry - stop; exit at the stop on the bid

    def test_long_tp_needs_the_bid_through_the_target(self):
        # TP 1R at mid 1.1010; mid high 1.1011 -> bid 1.1010 (touch, no fill); next bar bid high 1.10105 > TP
        m = mk([(1.1, 1.1, 1.1, 1.1), (1.1, 1.1, 1.1, 1.1), (1.1, 1.1011, 1.1, 1.1), (1.1, 1.10115, 1.1, 1.1)])
        f = r8.Fills(SPREAD, "EURUSD", m.spread)
        od = r8.Order(m.days[0], [r8.Leg(+1, "market", 0.0, stop=1.0990, tp_r=1.0)], 1, 2, 3)
        tr = r8.simulate(m, f, od)
        self.assertEqual((tr["reason"], tr["j"]), ("TP", 3))

    def test_short_stop_hit_on_the_ask(self):
        m = mk([(1.1, 1.1, 1.1, 1.1), (1.1, 1.1, 1.1, 1.1), (1.1, 1.10095, 1.1, 1.1), (1.1, 1.1, 1.1, 1.1)])
        f = r8.Fills(SPREAD, "EURUSD", m.spread)
        od = r8.Order(m.days[0], [r8.Leg(-1, "market", 0.0, stop=1.1010)], 1, 2, 3)
        tr = r8.simulate(m, f, od)
        self.assertEqual((tr["reason"], tr["j"]), ("SL", 2))


class Causality(unittest.TestCase):
    def l7_market(self, session_rows):
        prev = [(1.11 - 0.0001 * q / 1.19, 1.11 - 0.0001 * q / 1.19, 1.11 - 0.0001 * (q + 1) / 1.19,
                 1.11 - 0.0001 * (q + 1) / 1.19) for q in range(119)]  # opens at the high, closes at the low
        return r8.Market(segments(("2024-03-04 08:00", prev), ("2024-03-05 03:00", session_rows)), "EURUSD")

    def test_l7_order_placed_after_breach_with_known_stop(self):
        """Codex finding (b): the stop order starts at kb + 1 and its stop ignores lower lows after kb."""
        lo = 1.11 - 0.0001 * 119 / 1.19  # previous D1 low = the level
        rows = [(lo + 0.0005,) * 4, (lo + 0.0005, lo + 0.0005, lo - 0.0010, lo - 0.0008),
                (lo - 0.0008, lo - 0.0008, lo - 0.0020, lo - 0.0015), (lo - 0.0015, lo + 0.0005, lo - 0.0015, lo)]
        m = self.l7_market(rows + flat(480, lo))
        day = date(2024, 3, 5)
        (od,) = r8.f_l7(m, day)
        kb = int(np.searchsorted(m.t, m.ts(day, (3, 0)))) + 1
        self.assertEqual(od.start, kb + 1)
        self.assertEqual(od.legs[0].kind, "stop")
        self.assertAlmostEqual(od.legs[0].stop, lo - 0.0010)  # not the later low lo - 0.0020

    def test_silver_bullet_ignores_fvgs_after_the_mss(self):
        """Codex finding (c): an FVG completed at or before the MSS bar wins over a later one."""
        m = mk(flat(20))
        m.h, m.l = np.full(20, 1.1), np.full(20, 1.099)
        ks, j = 2, 8
        m.l[3], m.h[5] = 1.0985, 1.0980  # bearish FVG completed at bar 5 (h[5] < l[3])
        m.l[8], m.h[10] = 1.0970, 1.0960  # another one completing at bar 10, after the MSS at bar 8
        self.assertEqual(r8.sb_fvg_bar(m, -1, ks, j, 20), 5)
        m.h[5] = 1.1
        self.assertEqual(r8.sb_fvg_bar(m, -1, ks, j, 20), 10)  # only then a later (forward-scanned) FVG
        self.assertIsNone(r8.sb_fvg_bar(m, -1, ks, j, 11))  # it would complete on the window's last bar

    def l1_market(self, before, entry_open):
        rows = flat(120) + flat(29) + [(1.1, 1.1, before, before)] + [(entry_open,) * 4] + flat(449, entry_open)
        return r8.Market(frame(rows, start="2024-03-05 01:00"), "EURUSD")

    def test_l1_direction_from_closed_bars_entry_next_open(self):
        """Codex finding (d): sign from the close of the last bar before +30 min, not the entry bar's open."""
        day = date(2024, 3, 5)
        m = self.l1_market(before=1.0990, entry_open=1.1020)  # down in the first 30 min, gap up at the entry open
        (od,) = r8.f_l1(+1)(m, day)
        self.assertEqual(od.legs[0].side, -1)
        self.assertEqual(m.t[od.start], m.ts(day, (3, 30)))

    def test_l1b_direction_from_closed_bars(self):
        day = date(2024, 3, 5)
        rows = flat(359) + [(1.1, 1.1, 1.0990, 1.0990)] + [(1.1020,) * 4] * 31  # down bar at 15:59
        m = r8.Market(segments(("2024-03-04 11:00", flat(361)), ("2024-03-05 10:00", rows)), "EURUSD")
        (od,) = r8.f_l1b(m, day)
        self.assertEqual(od.legs[0].side, -1)
        self.assertEqual(m.t[od.start], m.ts(day, (16, 0)))


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
