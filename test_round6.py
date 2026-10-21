"""Replay-only round 6 (replay/round6.py): causal regime router and volatility filter, stop rule, breakeven / trail
ratchets, the COMBO walk (no lookahead, time / Friday exits, daily halt), selection on train only and a holdout runner
that reads only the frozen file."""
import inspect
import json
import math
import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

import numpy as np
import pandas as pd

from replay import entry_search as es
from replay import intraday_backtest as bt
from replay import round4 as r4
from replay import round5 as r5
from replay import round6 as r6
from test_round4 import FRAMES, cut, toy_tape

UTC = timezone.utc
PIP = bt.PIP
FIVE = 5 * es.MINUTE


def ts(text):
    return int(pd.Timestamp(text, tz="UTC").value)


class Regime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.full = r6.Regime(FRAMES)

    def test_prefix_invariance(self):
        """Features at t are identical when no data after t exist (closed D1 bars only)."""
        for end in ("2022-03-09 13:00", "2022-06-01 03:05", "2022-11-18 21:55"):
            t = ts(end)
            part = r6.Regime(cut(FRAMES, pd.Timestamp(end, tz="UTC")))
            for a in (20, 25):
                self.assertEqual(self.full.at(t, a)[:2], part.at(t, a)[:2])
                self.assertAlmostEqual(self.full.at(t, a)[2], part.at(t, a)[2])
                self.assertAlmostEqual(self.full.at(t, a)[3], part.at(t, a)[3])

    def test_uses_only_bars_ended_by_t(self):
        k = 200
        t = int(self.full.avail[k])
        self.assertEqual(self.full.index(t), k)
        self.assertEqual(self.full.index(t - 1), k - 1)

    def test_states(self):
        reg = self.full
        k = 300
        t = int(reg.avail[k])
        reg_adx, reg_slope, reg_close, reg_slow = reg.adx.copy(), reg.slope.copy(), reg.close.copy(), reg.slow.copy()
        reg.adx, reg.slope, reg.close, reg.slow = (x.copy() for x in (reg_adx, reg_slope, reg_close, reg_slow))
        try:
            reg.adx[k] = 19.0
            self.assertEqual(reg.at(t, 20)[0], "RANGE")
            reg.adx[k] = 22.0
            self.assertEqual(reg.at(t, 25)[0], "RANGE")
            reg.slope[k], reg.close[k], reg.slow[k] = 0.001, 1.2, 1.1
            self.assertEqual(reg.at(t, 20)[:2], ("TREND", 1))
            reg.slope[k] = -0.001
            self.assertEqual(reg.at(t, 20)[:2], ("CONFLICT", 0))
            reg.close[k] = 1.0
            self.assertEqual(reg.at(t, 20)[:2], ("TREND", -1))
        finally:
            reg.adx, reg.slope, reg.close, reg.slow = reg_adx, reg_slope, reg_close, reg_slow
        self.assertEqual(reg.at(int(reg.avail[5]), 20)[0], "NO_DATA")  # warm-up

    def test_trailing_percentile(self):
        x = np.array([np.nan, 1.0, 2.0, 3.0, 2.5, 0.5])
        p = r6.trailing_percentile(x, days=3, min_values=2)
        self.assertTrue(math.isnan(p[0]) and math.isnan(p[1]))
        self.assertAlmostEqual(p[2], 1.0)
        self.assertAlmostEqual(p[4], 2 / 3)  # window 2, 3, 2.5
        self.assertAlmostEqual(p[5], 1 / 3)


class StopRule(unittest.TestCase):
    def test_max_of_structure_and_atr_then_clamp(self):
        cfg = r6.config({"a": 20, "k": 1.0, "t": 1.5, "H": 3})
        d, _ = r6.stop_distance(cfg, 1, 1.1000, 1.0990, 0.0005, 0.0050)  # structure 11 pips, 1 x ATR 50 -> 50
        self.assertAlmostEqual(d / PIP, 50.0)
        d, _ = r6.stop_distance(cfg, 1, 1.1000, 1.0990, 0.0005, 0.0030)  # 30 -> clamped up to 40
        self.assertAlmostEqual(d / PIP, 40.0)
        d, _ = r6.stop_distance(cfg, -1, 1.1000, 1.1080, 0.0005, 0.0030)  # structure 81 -> clamped to 60
        self.assertAlmostEqual(d / PIP, 60.0)
        d, _ = r6.stop_distance({**cfg, "clamp": None}, -1, 1.1000, 1.1080, 0.0005, 0.0030)
        self.assertAlmostEqual(d / PIP, 81.0)
        self.assertEqual(r6.stop_distance(cfg, 1, 1.1, 1.1005, 0.0005, 0.005)[1], "structure_beyond_fill")
        self.assertEqual(r6.stop_distance(cfg, 1, 1.1, 1.09, math.nan, 0.005)[1], "atr_unavailable")


def toy_h1(tape, closes, atr_value):
    """H1 bars closing every 12 5m bars of ``tape`` with the given closes."""
    n = len(closes)
    avail = tape.start[0] + (np.arange(n, dtype=np.int64) + 1) * 12 * FIVE
    c = np.asarray(closes, float)
    return r4.HBars(avail, c, c, c, c), np.full(n, atr_value)


class Ratchets(unittest.TestCase):
    def test_breakeven_then_trail(self):
        # long filled at 1.1000 (bar 0), risk 50 pips; bar 3 high reaches +1R -> stop at the fill from bar 4
        n = 60
        o = np.full(n, 1.1000)
        h, low, c = o + 0.0002, o - 0.0002, o.copy()
        h[3] = 1.1052
        tape = toy_tape(o, h, low, c, spread=0.0)
        h1, atr = toy_h1(tape, [1.1010, 1.1080, 1.1060, 1.1100, 1.1000], 0.0010)
        ev = r6.stop_events(tape, h1, atr, 0, 1, 1.1000, 0.0050, 0.0, int(tape.start[-1]) + FIVE, trail=1.5)
        self.assertEqual(ev[0], (int(tape.start[3]) + FIVE, "stop", 1.1000))
        levels = [e[2] for e in ev[1:]]  # every H1 close: best close since the fill - 1.5 x 10 pips
        np.testing.assert_allclose(levels, [1.0995, 1.1065, 1.1065, 1.1085, 1.1085])
        self.assertTrue(all(e[0] >= ev[0][0] for e in ev))

    def test_no_breakeven_no_events_and_limit_bar_excluded(self):
        n = 30
        o = np.full(n, 1.1000)
        h, low, c = o + 0.0002, o - 0.0002, o.copy()
        tape = toy_tape(o, h, low, c, spread=0.0)
        h1, atr = toy_h1(tape, [1.1, 1.1], 0.001)
        self.assertEqual(r6.stop_events(tape, h1, atr, 0, 1, 1.1, 0.005, 0.0, int(tape.start[-1]), 1.5), [])
        h[0] = 1.1060  # +1R only on the limit fill bar: not counted
        self.assertEqual(r6.stop_events(tape, h1, atr, 0, 1, 1.1, 0.005, 0.0, int(tape.start[-1]), 1.5,
                                        limit_fill=True), [])
        self.assertTrue(r6.stop_events(tape, h1, atr, 0, 1, 1.1, 0.005, 0.0, int(tape.start[-1]), 1.5))


class StubRegime:
    def __init__(self, state, direction, pct=0.5, atr=0.0050):
        self.state, self.direction, self.pct, self.atr = state, direction, pct, atr

    def at(self, t, a):
        return self.state, self.direction, self.atr, self.pct


def market_cands(frames, n=80, seed=11, families=r6.MODULES):
    rng = np.random.default_rng(seed)
    f5 = frames["5m"]
    ny = f5.index.tz_convert(es.NY)
    ok = np.flatnonzero((ny.hour >= 4) & (ny.hour < 11) & (ny.weekday < 5))
    ok = ok[ok > 288 * 30]
    picks = np.sort(rng.choice(ok, n, replace=False))
    out = []
    for q, k in enumerate(picks):
        side = es.LONG if q % 2 else es.SHORT
        s = 1 if side == es.LONG else -1
        struct = float(f5["Open"].iloc[k]) - s * rng.uniform(10, 35) * PIP
        fam = families[q % len(families)]
        out.append((fam, es.Candidate(int(f5.index[k].value), side, struct, fam, f"{fam}:{q}")))
    return out


class Walk(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.system = r6.System(FRAMES)
        cls.cands = market_cands(FRAMES)
        cls.system.candidates = lambda model: cls.cands
        cls.cfg = r6.config({"a": 20, "k": 1.0, "t": 1.5, "H": 3})

    def run_with(self, regime, cfg=None):
        real = self.system.regime
        self.system.regime = regime
        try:
            return r6.run_system(self.system, r5.C_RAW, cfg or self.cfg)
        finally:
            self.system.regime = real

    def test_router(self):
        up, _ = self.run_with(StubRegime("TREND", 1))
        self.assertTrue(up)
        self.assertTrue(all(t["module"] in r6.TREND_MODULES and t["side"] == "LONG" for t in up))
        down, _ = self.run_with(StubRegime("TREND", -1))
        self.assertTrue(all(t["module"] in r6.TREND_MODULES and t["side"] == "SHORT" for t in down))
        rng, _ = self.run_with(StubRegime("RANGE", 0))
        self.assertTrue(rng)
        self.assertEqual({t["module"] for t in rng}, {"F2"})
        self.assertEqual({t["side"] for t in rng}, {"LONG", "SHORT"})
        none, sk = self.run_with(StubRegime("CONFLICT", 0))
        self.assertEqual(none, [])
        self.assertEqual(sk["regime_conflict"], len(self.cands))
        hot, sk = self.run_with(StubRegime("RANGE", 0, pct=0.95))
        self.assertEqual(hot, [])
        self.assertGreater(sk["atr_percentile_high"], 0)

    def test_exits_bounded_one_position_costs_once(self):
        trades, _ = self.run_with(StubRegime("RANGE", 0))
        prev_exit = None
        for t in trades:
            cap = min(r5.nth_trading_close(t["fill_at"], 3), bt.friday_close_after(t["fill_at"]))
            self.assertLessEqual(t["exit_at"], cap)
            if t["exit_kind"] in (bt.TIME, bt.FRIDAY):
                self.assertEqual(t["exit_at"], cap)
            self.assertGreaterEqual(t["risk_pips"], 40.0 - 1e-9)
            self.assertLessEqual(t["risk_pips"], 60.0 + 1e-9)
            self.assertAlmostEqual(t["commission_r"], r5.FX_COMMISSION / t["risk_price"])
            self.assertIsNone(t["target"])
            if prev_exit is not None:
                self.assertGreaterEqual(t["signal_at"], prev_exit)
            prev_exit = t["exit_at"]
            if t["exit_kind"] == "BE":
                self.assertAlmostEqual(t["exit"], t["fill"] - (1 if t["side"] == "LONG" else -1) * t["slip"])

    def test_cap_variant_sets_a_4r_target(self):
        trades, _ = self.run_with(StubRegime("RANGE", 0), {**self.cfg, "cap_r": 4.0})
        for t in trades:
            s = 1 if t["side"] == "LONG" else -1
            self.assertAlmostEqual(s * (t["target"] - t["fill"]), 4 * t["risk_price"])

    def test_daily_halt(self):
        cfg = {**self.cfg, "halt_r": 0.5}
        trades, sk = self.run_with(StubRegime("RANGE", 0), cfg)
        self.assertGreater(sk["daily_loss_halt"], 0)
        by_day, halted = {}, set()
        for t in trades:
            self.assertNotIn(bt.day_key(t["signal_at"]), halted)  # no entry decided on a halted trading day
            key = bt.day_key(t["exit_at"])
            by_day[key] = by_day.get(key, 0.0) + t["r"]
            if by_day[key] <= -0.5:
                halted.add(key)

    def test_no_lookahead_prefix_invariance(self):
        """Trades exiting before a cut are identical when the data after the cut do not exist."""
        full, _ = r6.run_system(self.system, r5.C_RAW, self.cfg)
        self.assertTrue(full)
        cut_at = pd.Timestamp("2022-09-15 00:00", tz="UTC")
        part_sys = r6.System(cut(FRAMES, cut_at))
        part_sys.candidates = lambda model: [x for x in self.cands if x[1].t < cut_at.value]
        part, _ = r6.run_system(part_sys, r5.C_RAW, self.cfg)
        done = [t for t in full if t["exit_at"] < cut_at.to_pydatetime() - pd.Timedelta(hours=1)]
        self.assertTrue(done)
        for a, b in zip(done, part):
            self.assertEqual(a["idea"], b["idea"])
            self.assertEqual(a["exit_at"], b["exit_at"])
            self.assertAlmostEqual(a["r"], b["r"])


class Metrics(unittest.TestCase):
    def test_equity_pf_and_bar(self):
        t0 = datetime(2025, 1, 2, tzinfo=UTC)
        trades = [{"exit_at": t0 + pd.Timedelta(days=q), "r": r} for q, r in enumerate((2.0, -1.0, -1.0, 1.0))]
        total, dd = r6.equity_pct(trades)
        self.assertAlmostEqual(total, 100 * (1.01 * 0.995 * 0.995 * 1.005 - 1))
        self.assertAlmostEqual(dd, 100 * (1 - 0.995 * 0.995))
        self.assertAlmostEqual(r6.profit_factor([2.0, -1.0, -1.0, 1.0]), 1.5)
        ok = {"trades": 200, "avg_r": 0.09, "pf": 1.2, "dd": 10.0, "tpm": 7.0}
        self.assertEqual(r6.holdout_pass(ok), (True, []))
        self.assertFalse(r6.holdout_pass({**ok, "avg_r": 0.07})[0])
        self.assertFalse(r6.holdout_pass({**ok, "pf": 1.15})[0])
        self.assertFalse(r6.holdout_pass({**ok, "dd": 12.0})[0])
        self.assertFalse(r6.holdout_pass({**ok, "tpm": 5.9})[0])

    def test_window_purge(self):
        a, b = datetime(2023, 1, 1, tzinfo=UTC), datetime(2023, 7, 1, tzinfo=UTC)
        inside = {"signal_at": datetime(2023, 6, 30, tzinfo=UTC), "exit_at": datetime(2023, 7, 3, tzinfo=UTC)}
        self.assertEqual(r6.window([inside], a, b), [inside])
        self.assertEqual(r6.window([inside], a, b, purge=True), [])


class Protocol(unittest.TestCase):
    def test_grid(self):
        self.assertEqual(len(r6.grid()), 16)
        self.assertEqual(len({r6.combo_name(c) for c in r6.grid()}), 16)
        self.assertEqual(len(r6.neighbours(r6.grid()[0])), 4)
        self.assertEqual(r6.FOLDS[-1][2], r6.TRAIN_END)

    def test_selection_frames_end_before_holdout(self):
        f5 = FRAMES["5m"]
        with mock.patch.object(bt, "load_csv_frames", return_value=FRAMES):
            part = r6.load_frames("unused", end=datetime(2022, 6, 1, tzinfo=UTC))
        for f in part.values():
            self.assertLess(f.index[-1], pd.Timestamp("2022-06-01", tz="UTC"))
        self.assertGreater(f5.index[-1], pd.Timestamp("2022-06-01", tz="UTC"))

    def test_select(self):
        def s(avg, n=40, dd=5.0):
            return {"avg_r": avg, "trades": n, "dd": dd}

        def result(train, test, dd=5.0):
            return {"folds": {f: {"train": s(train[q]), "test": s(test[q])} for q, (f, _, _) in enumerate(r6.FOLDS)},
                    "train": s(0.0, dd=dd)}
        results = {"A": result([0.3, 0.1, 0.1, 0.1], [0.0, 0.0, 0.0, 0.0]),
                   "B": result([0.1, 0.2, 0.1, 0.1], [0.1, 0.1, 0.1, 0.1], dd=9.0),
                   "C": result([0.0, 0.0, 0.0, 0.0], [0.1, 0.1, 0.1, 0.1], dd=4.0)}
        results["A"]["folds"]["F4"]["train"] = s(0.9, n=10)  # too few trades
        per_fold, means, final = r6.select(results)
        self.assertEqual(per_fold, {"F1": "A", "F2": "B", "F3": "A", "F4": "B"})
        self.assertEqual(final, "C")  # tie on mean test avg R, lower DD

    def test_holdout_runner_reads_only_the_frozen_file(self):
        self.assertEqual(list(inspect.signature(r6.holdout_runs).parameters), ["frozen_path", "system", "models"])
        cfg = r6.config({"a": 25, "k": 1.5, "t": 2.5, "H": 5})
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "frozen.json")
            with mock.patch.object(r6, "FROZEN_REPO", os.path.join(tmp, "repo_copy.json")):
                r6.freeze(path, cfg, {"selected": cfg["name"]})
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            seen = []

            def fake_run(system, model, c, name=None):
                seen.append(c)
                return [], {}
            with mock.patch.object(r6, "committed_frozen", return_value=text), \
                    mock.patch.object(r6, "run_system", side_effect=fake_run), \
                    mock.patch.object(r6, "select", side_effect=AssertionError("no selection in holdout")), \
                    mock.patch.object(r6, "grid", side_effect=AssertionError("no grid in holdout")):
                got, runs, info = r6.holdout_runs(path, object())
            self.assertEqual(got, json.loads(text)["config"])
            self.assertEqual(set(runs), set(r6.MODELS))
            self.assertEqual(len(seen), len(r6.MODELS) + 1 + len(r6.INFO_VARIANTS))
            for c in seen[:len(r6.MODELS)]:
                self.assertEqual(c, got)
            for c in seen[len(r6.MODELS):]:  # info variants differ in one declared field only
                diff = {k for k in c if c[k] != got.get(k)}
                self.assertLessEqual(diff, {"friday", "clamp", "cap_r"})
                self.assertEqual(len(diff), 1)
            with mock.patch.object(r6, "committed_frozen", return_value=text.replace('"H": 5', '"H": 3')):
                with self.assertRaises(RuntimeError):
                    r6.load_frozen(path)


if __name__ == "__main__":
    unittest.main()
