"""Replay-only round 7 (replay/round7.py): weekend filter, causal macro availability (COT release lag, shutdown
bounds, rate lag), regime states, T1 Donchian setups, the routed book (routing, one position, T1F direction filter),
cell fitting (min trades, purge, FLAT), selection tie rules, the pass bar and a frozen-file holdout runner that fails
closed."""
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

import numpy as np
import pandas as pd

from replay import entry_search as es
from replay import round4 as r4
from replay import round5 as r5
from replay import round7 as r7
from test_round4 import FRAMES, cut

UTC = timezone.utc


def ny(text):
    return int(pd.Timestamp(text, tz=es.NY).tz_convert(UTC).value)


def dt(text):
    return pd.Timestamp(text, tz="UTC").to_pydatetime()


class Weekend(unittest.TestCase):
    def test_weekend_window_ny(self):
        idx = pd.DatetimeIndex(pd.to_datetime([
            "2011-01-21 21:55", "2011-01-21 22:00",  # Fri 16:55 / 17:00 NY (EST)
            "2011-01-22 12:00",  # Saturday
            "2011-01-23 21:55", "2011-01-23 22:00",  # Sun 16:55 / 17:00 NY
        ], utc=True))
        self.assertEqual(list(r7.is_weekend(idx)), [False, True, True, True, False])


class Macro(unittest.TestCase):
    def test_cot_release_lag(self):
        # as-of Tue 2015-03-10 -> released Fri 03-13 -> used from Mon 03-16 17:00 NY
        self.assertEqual(r7.cot_available(date(2015, 3, 10)), ny("2015-03-16 17:00"))
        # Friday release 2015-12-25 (holiday) -> used from Mon 12-28 17:00 NY (next business day after the Friday)
        self.assertEqual(r7.cot_available(date(2015, 12, 22)), ny("2015-12-28 17:00"))
        # Friday 2015-07-03 is the observed July 4 holiday -> next business day Mon 07-06
        self.assertEqual(r7.cot_available(date(2015, 6, 30)), ny("2015-07-06 17:00"))

    def test_cot_shutdown_bounds(self):
        self.assertEqual(r7.cot_available(date(2013, 10, 8)), ny("2013-12-09 17:00"))
        self.assertEqual(r7.cot_available(date(2019, 1, 8)), ny("2019-03-18 17:00"))
        self.assertEqual(r7.cot_available(date(2019, 3, 12)), ny("2019-03-18 17:00"))  # normal again

    def test_trailing_rank(self):
        x = np.array([1.0, 2.0, 3.0, 0.0, 5.0])
        out = r7.trailing_rank(x, 3, 2)
        self.assertTrue(np.isnan(out[0]))
        self.assertEqual(list(out[1:]), [1.0, 1.0, 1 / 3, 1.0])

    def test_rates_lag_and_trend(self):
        with tempfile.TemporaryDirectory() as d:
            days = pd.bdate_range("2015-01-02", periods=70)
            pd.DataFrame({"Date": days.strftime("%m/%d/%Y"), "2 Yr": np.linspace(0.5, 1.2, 70)}).to_csv(
                os.path.join(d, "ust2015.csv"), index=False)
            pd.DataFrame({"TIME_PERIOD": ["2014-09-10", "2015-02-02"], "OBS_VALUE": [-0.2, -0.3]}).to_csv(
                os.path.join(d, "ecb_dfr.csv"), index=False)
            pd.DataFrame({"TIME_PERIOD": ["2014-12", "2015-01", "2015-02"], "OBS_VALUE": [0.08, 0.06, 0.05]}).to_csv(
                os.path.join(d, "ecb_euribor3m.csv"), index=False)
            rates = r7.load_rates(d)
        first = rates.iloc[0]
        self.assertEqual(first["day"], date(2015, 1, 2))
        self.assertAlmostEqual(first["dfr"], -0.2)
        self.assertEqual(first["avail"], ny("2015-01-05 17:00"))  # Fri value -> next business day Mon 17:00 NY
        self.assertAlmostEqual(rates.loc[rates["day"] == date(2015, 2, 2), "dfr"].iloc[0], -0.3)
        self.assertTrue(np.isnan(rates["trend"].iloc[59]))
        self.assertGreater(rates["trend"].iloc[60], 0)
        # Euribor of Jan 2015 (1-month publication lag) is usable from 2015-03-01: day d's value is used on the next
        # business day, so 02-26 (used 02-27) still sees December, 02-27 (used 03-02) sees January
        self.assertAlmostEqual(rates[rates["day"] == date(2015, 2, 26)].iloc[0]["euribor"], 0.08)
        self.assertAlmostEqual(rates[rates["day"] == date(2015, 2, 27)].iloc[0]["euribor"], 0.06)

    def test_fomc_parser(self):
        page = ("<h5 class='panel-heading'>January 27-28 Meeting - 2015</h5>"
                "<h5>July 31-August  1  Meeting - 2012</h5><h5>April/May 30-1 Meeting - 2013</h5>"
                "<h5>October 16 (unscheduled) - 2013</h5><h5>March 17-18 (cancelled) Meeting - 2020</h5>"
                "<h5>May 9 Conference Call - 2010</h5><h5>March 16 Meeting - 2010</h5>")
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "fomc"))
            with open(os.path.join(d, "fomc", "h2015.htm"), "w") as handle:
                handle.write(page)
            days = r7.load_fomc(d)
        self.assertEqual(days, [date(2010, 3, 16), date(2012, 8, 1), date(2013, 5, 1), date(2015, 1, 28)])


class States(unittest.TestCase):
    def test_cell_key(self):
        state = {"Q": "Q2", "VOL": "HI", "RATE": None, "COT": "MID"}
        self.assertEqual(r7.cell_key(state, ()), "ALL")
        self.assertEqual(r7.cell_key(state, ("VOL",)), "VOL=HI")
        self.assertIsNone(r7.cell_key(state, ("VOL", "RATE")))
        self.assertEqual(r7.mapped(("RATE",), {"RATE=UP": "T1"})(state), "FLAT")
        self.assertEqual(r7.n_cells(), 16)

    def test_features_prefix_invariant_and_macro_cutoff(self):
        """Fake macro: states at t are identical with frames / macro cut at t (all values carry their availability)."""
        cot = pd.DataFrame({"asof": [date(2021, 10, 5) + timedelta(days=7 * k) for k in range(60)],
                            "net": np.arange(60.0) % 17})
        cot["avail"] = [r7.cot_available(d) for d in cot["asof"]]
        days = pd.bdate_range("2021-06-01", periods=400)
        rates = pd.DataFrame({"day": days.date, "diff": np.sin(np.arange(400) / 20.0)})
        rates["avail"] = [r7._ns_ny(r7.next_business_day(d), 17) for d in rates["day"]]
        rates["trend"] = rates["diff"] - rates["diff"].shift(r7.RATE_TREND_OBS)
        with mock.patch.object(r7, "load_cot", return_value=cot), \
                mock.patch.object(r7, "load_rates", return_value=rates), \
                mock.patch.object(r7, "COT_MIN_RELEASES", 10):
            full = r7.Features(FRAMES, "/nonexistent", r7.CONTAM_END)
            for end in ("2022-03-09 13:00", "2022-11-18 21:55"):
                t = int(pd.Timestamp(end, tz="UTC").value)
                part = r7.Features(cut(FRAMES, pd.Timestamp(end, tz="UTC")), "/nonexistent", pd.Timestamp(end, tz="UTC"))
                self.assertEqual(full.state(t), part.state(t))
        k = int(np.flatnonzero(rates["avail"].to_numpy() > int(pd.Timestamp("2022-01-10", tz="UTC").value))[0])
        before = full.state(int(rates["avail"].iloc[k]) - 1)["RATE"]
        self.assertEqual(before, "UP" if rates["trend"].iloc[k - 1] > 0 else "DOWN")


class T1(unittest.TestCase):
    def test_setups(self):
        d1 = r4.htf_bars(FRAMES["1h"], "D1")
        setups = r7.t1_setups(d1)
        self.assertTrue(setups)
        a = r4.atr(d1, r7.T1_ATR_N)
        hi, lo = r4.prior_max(d1.h, 20), r4.prior_min(d1.l, 20)
        for s in setups:
            k = int(np.flatnonzero(d1.avail == s.t)[0])
            if s.side > 0:
                self.assertGreater(d1.c[k], hi[k])
                self.assertFalse(d1.c[k - 1] > hi[k - 1])
            else:
                self.assertLess(d1.c[k], lo[k])
            self.assertAlmostEqual(s.stop_dist, 2.0 * a[k])
            if k + 20 < len(d1):
                self.assertEqual(s.deadline, int(d1.avail[k + 20]))
            self.assertTrue(all(e[0] > s.t and e[1] == "stop" for e in s.events))
            self.assertLessEqual(len(s.events), 20)
        self.assertEqual([s.t for s in setups], sorted(s.t for s in setups))

    def test_t1f_direction(self):
        self.assertTrue(r7.t1f_allowed({"RATE": "DOWN"}, 1))
        self.assertFalse(r7.t1f_allowed({"RATE": "UP"}, 1))
        self.assertTrue(r7.t1f_allowed({"RATE": "UP"}, -1))
        self.assertFalse(r7.t1f_allowed({"RATE": None}, -1))


def stub_system(states):
    d1 = r4.htf_bars(FRAMES["1h"], "D1")
    tape = r5.model_tape(r5.C_RAW, "EURUSD", FRAMES["5m"], r7.PIP, True)
    items = []
    for fam in r7.SWING:
        items += [(s.t, fam, s) for s in r7.t1_setups(d1, fam)]
    items.sort(key=lambda x: (x[0], r7.POOL.index(x[1])))
    return SimpleNamespace(tape=lambda m: tape, ctx=None, items=lambda m: items, state=states, rule=None)


class Book(unittest.TestCase):
    A, B = dt("2021-10-01"), dt("2023-02-01")

    def test_routing_and_one_position(self):
        sysm = stub_system(lambda t: {"RATE": "UP"})
        trades, _ = r7.run_book(sysm, r5.C_RAW, r7.fixed("T1"), self.A, self.B)
        self.assertTrue(trades)
        self.assertEqual({t["module"] for t in trades}, {"T1"})
        for prev, nxt in zip(trades, trades[1:]):
            self.assertGreaterEqual(nxt["signal_at"], prev["exit_at"])
        for t in trades:
            self.assertAlmostEqual(t["commission_r"], r5.FX_COMMISSION / t["risk_price"])
        flat, _ = r7.run_book(sysm, r5.C_RAW, r7.fixed("FLAT"), self.A, self.B)
        self.assertEqual(flat, [])

    def test_t1f_only_with_rate_direction(self):
        sysm = stub_system(lambda t: {"RATE": "UP"})
        trades, sk = r7.run_book(sysm, r5.C_RAW, r7.fixed("T1F"), self.A, self.B)
        self.assertTrue(all(t["side"] == "SHORT" for t in trades))
        self.assertGreater(sk["t1f_against_rate_trend"], 0)

    def test_window(self):
        sysm = stub_system(lambda t: {})
        mid = dt("2022-06-01")
        trades, _ = r7.run_book(sysm, r5.C_RAW, r7.fixed("T1"), mid, self.B)
        self.assertTrue(all(mid <= t["signal_at"] < self.B for t in trades))

    def test_state_dependent_route(self):
        cutover = int(pd.Timestamp("2022-06-01", tz="UTC").value)
        sysm = stub_system(lambda t: {"VOL": "HI" if t >= cutover else "LO"})
        trades, _ = r7.run_book(sysm, r5.C_RAW, r7.mapped(("VOL",), {"VOL=HI": "T1"}), self.A, self.B)
        self.assertTrue(trades)
        self.assertTrue(all(t["signal_at"] >= dt("2022-06-01") for t in trades))


def fake_trades(fam, n, r, state, start="2011-01-03"):
    t0 = pd.Timestamp(start, tz="UTC")
    return [{"signal_at": (t0 + pd.Timedelta(days=3 * k)).to_pydatetime(),
             "exit_at": (t0 + pd.Timedelta(days=3 * k + 1)).to_pydatetime(), "r": r, "state": state, "module": fam}
            for k in range(n)]


class Fitting(unittest.TestCase):
    def test_fit_min_trades_positive_and_flat(self):
        hi, lo = {"VOL": "HI"}, {"VOL": "LO"}
        standalone = {"E3": fake_trades("E3", 25, 0.1, hi) + fake_trades("E3", 25, -0.1, lo),
                      "F2": fake_trades("F2", 19, 0.5, hi) + fake_trades("F2", 30, 0.05, lo),
                      "T1": fake_trades("T1", 30, 0.3, hi)}
        mapping, stats = r7.fit(("VOL",), standalone, r7.SEL_START, r7.SEL_END)
        self.assertEqual(mapping, {"VOL=HI": "T1", "VOL=LO": "F2"})  # F2 HI has 19 < 20 trades
        self.assertEqual(stats["VOL=HI"]["F2"], (19, 0.5))
        bad = {"E3": fake_trades("E3", 25, -0.1, hi)}
        self.assertEqual(r7.fit(("VOL",), bad, r7.SEL_START, r7.SEL_END)[0], {"VOL=HI": "FLAT"})
        self.assertEqual(r7.fit((), standalone, r7.SEL_START, r7.SEL_END)[0], {"ALL": "T1"})

    def test_fit_purges_at_window_end(self):
        trades = fake_trades("E3", 25, 0.1, {}, start="2012-10-01")
        end = trades[20]["exit_at"]
        _, stats = r7.fit((), {"E3": trades}, r7.SEL_START, end)
        self.assertEqual(stats["ALL"]["E3"][0], 21)

    def test_choose_and_score(self):
        self.assertEqual(r7.choose({"A": 0.1, "B": 0.1, "C": 0.05}, {"A": {1: 1, 2: 2}, "B": {1: 1}, "C": {}}), "B")
        wf = {2013: {"trades": [{"r": 1.0}, {"r": 0.0}]}, 2014: {"trades": []}}
        self.assertAlmostEqual(r7.wf_score(wf), 0.25)

    def test_pass_bar_needs_three_positive_years(self):
        s = {"trades": 200, "avg_r": 0.1, "pf": 1.3, "dd": 5.0, "tpm": 4.0}
        years = {y: {"total_r": v} for y, v in zip("abcd", (1.0, 2.0, -1.0, 0.5))}
        self.assertEqual(r7.holdout_pass(s, years), (True, []))
        years["d"]["total_r"] = -0.5
        ok, fails = r7.holdout_pass(s, years)
        self.assertFalse(ok)
        self.assertIn("positive years 2/4 < 3", fails)
        ok, fails = r7.holdout_pass({**s, "tpm": 2.9}, {y: {"total_r": 1.0} for y in "abcd"})
        self.assertFalse(ok)


class Frozen(unittest.TestCase):
    PAYLOAD = '{"keys": ["VOL"], "mapper": "M_vol", "mapping": {"VOL=HI": "T1"}, "pool": %s, "rule_tp": "TP2"}\n'

    def write(self, d, text):
        path = os.path.join(d, "f.json")
        with open(path, "w") as handle:
            handle.write(text)
        return path

    def test_fails_closed(self):
        text = self.PAYLOAD % str(list(r7.POOL)).replace("'", '"')
        with tempfile.TemporaryDirectory() as d:
            path = self.write(d, text)
            with mock.patch.object(r7, "committed_frozen", return_value=None):
                with self.assertRaises(RuntimeError):
                    r7.load_frozen(path)
            with mock.patch.object(r7, "committed_frozen", return_value=text.replace("T1", "E3")):
                with self.assertRaises(RuntimeError):
                    r7.load_frozen(path)
            with mock.patch.object(r7, "committed_frozen", return_value=text):
                self.assertEqual(r7.load_frozen(path)["mapping"], {"VOL=HI": "T1"})
            bad = self.PAYLOAD % '["E3"]'
            path = self.write(d, bad)
            with mock.patch.object(r7, "committed_frozen", return_value=bad):
                with self.assertRaises(ValueError):
                    r7.load_frozen(path)

    def test_git_unavailable(self):
        with mock.patch.object(r7.subprocess, "run", side_effect=OSError):
            self.assertIsNone(r7.committed_frozen())

    def test_holdout_frames_sliced_before_system(self):
        """run_frozen slices frames at the cutoff before building the System and loads the frozen file first."""
        seen = {}

        class Boom(Exception):
            pass

        def fake_system(frames, macro, cutoff):
            seen["last"] = frames["5m"].index[-1]
            raise Boom

        args = SimpleNamespace(data="x", macro="m", frozen="f")
        with mock.patch.object(r7, "load_frames", side_effect=lambda data, end: cut(FRAMES, pd.Timestamp(end))), \
                mock.patch.object(r7, "load_frozen", return_value={"keys": [], "mapping": {}, "mapper": "BASE"}), \
                mock.patch.object(r7, "System", side_effect=fake_system):
            with self.assertRaises(Boom):
                r7.run_frozen(args, datetime(2022, 6, 1, tzinfo=UTC), [], print)
        self.assertLess(seen["last"], pd.Timestamp("2022-06-01", tz="UTC"))
        with mock.patch.object(r7, "load_frames", side_effect=lambda data, end: cut(FRAMES, pd.Timestamp(end))), \
                mock.patch.object(r7, "load_frozen", side_effect=RuntimeError("not committed")), \
                mock.patch.object(r7, "System", side_effect=AssertionError("must not run")):
            with self.assertRaises(RuntimeError):
                r7.run_frozen(args, datetime(2022, 6, 1, tzinfo=UTC), [], print)


if __name__ == "__main__":
    unittest.main()
