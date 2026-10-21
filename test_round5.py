"""Replay-only round 5 (replay/round5.py): constant cost models (spread / commission / slippage, causal USDJPY
commission), commission charged once in R on every runner, the relaxed-rule runner (5-day close, Friday close, limit
fills) and no-lookahead (prefix invariance) of the new runner."""
import math
import unittest
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from replay import entry_search as es
from replay import intraday_backtest as bt
from replay import round2 as r2
from replay import round4 as r4
from replay import round5 as r5
from test_round4 import FRAMES, cut, toy_tape

UTC = timezone.utc
PIP = 0.0001


def ns(text):
    return int(pd.Timestamp(text, tz="UTC").value)


class CostModels(unittest.TestCase):
    def test_eurusd_raw_round_trip_is_1_1_pip_and_tp_0_9(self):
        tape = r5.model_tape(r5.C_RAW, "EURUSD", FRAMES["5m"], PIP, True)
        hs, slip = tape.costs(100)
        fee = tape.commission_at(100)
        self.assertAlmostEqual((2 * hs + 2 * slip + fee) / PIP, 1.1)  # market entry + stop / time exit
        self.assertAlmostEqual((2 * hs + slip + fee) / PIP, 1.0)  # market entry + TP (no exit slippage)
        self.assertAlmostEqual(fee / PIP, 0.7)
        stress = r5.model_tape(r5.C_RAW_STRESS, "EURUSD", FRAMES["5m"], PIP, True)
        self.assertAlmostEqual((2 * sum(stress.costs(5)) + stress.commission_at(5)) / PIP, 1.5)  # 0.4 + 2 x 0.2 + 0.7

    def test_futures_model(self):
        tape = r5.model_tape(r5.C_FUT, "EURUSD", FRAMES["5m"], PIP, True)
        hs, slip = tape.costs(0)
        self.assertAlmostEqual(2 * hs, 0.00005)
        self.assertAlmostEqual(slip, 0.000025)
        self.assertAlmostEqual(tape.commission_at(0) / PIP, 0.36)
        self.assertFalse(r5.C_FUT.covers("GBPUSD"))

    def test_metals_and_indices(self):
        f5 = FRAMES["5m"].iloc[:50]
        xau = r5.model_tape(r5.C_RAW, "XAUUSD", f5, 0.1, False)
        self.assertAlmostEqual(2 * xau.costs(0)[0], 0.10)
        self.assertAlmostEqual(xau.costs(0)[1], 0.01)
        self.assertAlmostEqual(xau.commission_at(0), 0.07)
        nas = r5.model_tape(r5.C_RAW, "NAS100USD", f5, 1.0, False)
        self.assertAlmostEqual(2 * nas.costs(0)[0], 0.8)
        self.assertEqual(nas.commission_at(0), 0.0)

    def test_usdjpy_commission_uses_the_previous_close_only(self):
        f5 = FRAMES["5m"].iloc[:200].copy()
        for col in ("Open", "High", "Low", "Close"):
            f5[col] = f5[col] * 136.0  # ~150 JPY
        fee = r5.commission_array(r5.C_RAW, "USDJPY", f5, 0.01)
        self.assertAlmostEqual(fee[0], 7e-5 * f5["Open"].iloc[0])
        self.assertTrue(np.allclose(fee[1:], 7e-5 * f5["Close"].to_numpy()[:-1]))
        self.assertAlmostEqual(7e-5 * 150.0 / 0.01, 7 * 150 / 1000)  # 1.05 pips at 150
        bumped = f5.copy()
        bumped.iloc[120, bumped.columns.get_loc("Close")] += 5.0  # the fill bar's own close is not known at fill
        again = r5.commission_array(r5.C_RAW, "USDJPY", bumped, 0.01)
        self.assertTrue(np.array_equal(fee[:121], again[:121]))


class CommissionCharging(unittest.TestCase):
    def market(self, commission):
        f = FRAMES
        end = f["5m"].index[-1].to_pydatetime() + bt.FIVE
        m = bt.Market.from_frames(f, 0.2, 0.1, cutoff=end)
        if commission:
            m.commissions = np.full(len(m.starts), commission)
        return m

    def test_simulate_trade_and_limit_charge_commission_once_in_r(self):
        slot = datetime(2022, 3, 9, 13, 0, tzinfo=UTC)
        i = int(np.searchsorted(self.market(0).starts, bt._ns(slot)))
        for fee in (0.0, 7e-5):
            m = self.market(fee)
            o = float(m.open[i])
            res = bt.simulate_trade(m, slot, "LONG", o - 0.0020, o + 0.0040, "I2")
            lim = r2.simulate_limit(m, i, "LONG", o, o - 0.0020, o + 0.0040)
            if fee == 0.0:
                base, base_lim = res, lim
                continue
            risk = res["fill"] - res["stop"]
            self.assertAlmostEqual(base["r"] - res["r"], fee / risk)
            self.assertTrue(np.allclose(base["marks"] - res["marks"], fee / risk))
            self.assertAlmostEqual(base_lim["r"] - lim["r"], fee / (o - (o - 0.0020)))
            for t in (base, res):
                t["side"] = "LONG"
            bt.add_gross([base, res], m)
            self.assertAlmostEqual(base["gross_r"], res["gross_r"])  # gross mid R unchanged -> shows in the cost

    def test_round4_run_charges_tape_commission(self):
        tape = toy_tape([1.0] * 6, [1.0, 1.0, 1.03, 1.0, 1.0, 1.0], [1.0, 0.999, 1.0, 1.0, 1.0, 1.0], [1.0] * 6,
                        spread=0.0)
        st = r4.Setup(int(tape.start[0]), 1, "X", "x", stop_dist=0.01, tp_r=2.0)
        plain, _ = r4.run([st], tape, "x")
        tape.commission = np.full(6, 0.001)
        charged, _ = r4.run([st], tape, "x")
        self.assertEqual(plain[0]["reason"], bt.TP)
        self.assertAlmostEqual(plain[0]["r"] - charged[0]["r"], 0.1)
        self.assertAlmostEqual(plain[0]["gross_r"], charged[0]["gross_r"])
        self.assertAlmostEqual(charged[0]["commission_r"], 0.1)


FIVE_NS = 5 * es.MINUTE


class LimitFillBar(unittest.TestCase):
    def test_target_not_credited_on_the_limit_fill_bar_but_stop_is(self):
        tape = toy_tape([1.0, 1.0, 1.0], [1.05, 1.05, 1.0], [0.999, 1.0, 1.0], [1.0, 1.0, 1.0], spread=0.0)
        x, *_rest, reason, _detail, _free = r4.walk(tape, 0, 1, 1.0, 0.99, 1.02, 0.0, 0.0, [], no_tp_on_fill=True)
        self.assertEqual((x, reason), (1, bt.TP))  # bar 0 reaches the target but it is credited on bar 1
        x, *_rest, reason, _detail, _free = r4.walk(tape, 0, 1, 1.0, 0.99, 1.02, 0.0, 0.0, [])
        self.assertEqual((x, reason), (0, bt.TP))
        stop_tape = toy_tape([1.0, 1.0], [1.05, 1.0], [0.98, 1.0], [1.0, 1.0], spread=0.0)
        x, *_rest, reason, _detail, _free = r4.walk(stop_tape, 0, 1, 1.0, 0.99, 1.02, 0.0, 0.0, [],
                                                    no_tp_on_fill=True)
        self.assertEqual((x, reason), (0, bt.SL))


class RelaxedRules(unittest.TestCase):
    def test_nth_trading_close(self):
        mon = datetime(2022, 1, 3, 15, 0, tzinfo=UTC)  # Monday 10:00 NY
        self.assertEqual(r5.nth_trading_close(mon, 5), datetime(2022, 1, 7, 22, 0, tzinfo=UTC))  # Fri 17:00 NY
        wed = datetime(2022, 1, 5, 15, 0, tzinfo=UTC)
        self.assertEqual(r5.nth_trading_close(wed, 5), datetime(2022, 1, 11, 22, 0, tzinfo=UTC))  # next Tue
        sun = datetime(2022, 1, 2, 23, 30, tzinfo=UTC)  # Sunday 18:30 NY = Monday's trading day
        self.assertEqual(r5.nth_trading_close(sun, 1), datetime(2022, 1, 3, 22, 0, tzinfo=UTC))

    def test_rule_levels(self):
        rule = r5.relaxed_rule("TP2")
        stop, target, why = r5.rule_levels(rule, 1, 1.1000, 1.0990, 0.0005, 0.0070, PIP)  # 11 pips -> 15
        self.assertIsNone(why)
        self.assertAlmostEqual((1.1 - stop) / PIP, 15.0)
        self.assertAlmostEqual((target - 1.1) / PIP, 30.0)
        stop, _, why = r5.rule_levels(rule, 1, 1.1, 1.0940, 0.0005, 0.0070, PIP)  # 61 pips: clamped to 50
        self.assertIsNone(why)
        self.assertAlmostEqual((1.1 - stop) / PIP, 50.0)
        self.assertEqual(r5.rule_levels(r5.intraday_rule("FIX2"), 1, 1.1, 1.0960, 0.0005, 0.0070, PIP)[2],
                         "sl_wider_than_hi")  # prior intraday rules keep skipping
        self.assertEqual(r5.rule_levels(rule, -1, 1.1, 1.0990, 0.0005, 0.0070, PIP)[2], "structure_beyond_fill")
        stop, target, _ = r5.rule_levels(r5.relaxed_rule("ADR0.6"), -1, 1.1, 1.1030, 0.0005, 0.0070, PIP)
        self.assertAlmostEqual((stop - 1.1) / PIP, 31.0)  # 30 pips + 0.2 ATR buffer
        self.assertAlmostEqual((1.1 - target) / PIP, 42.0)  # 0.6 x 70 pips, no R clamp
        stop, target, _ = r5.rule_levels(r5.intraday_rule("ADR"), 1, 1.1, math.nan, math.nan, 0.0070, PIP)
        self.assertAlmostEqual((1.1 - stop) / PIP, 21.0)
        self.assertAlmostEqual((target - 1.1) / PIP, min(max(0.58 * 70, 1.5 * 21), 2.0 * 21))
        scale = 0.0070 / 70.0  # other instruments: pip clamps as ADR multiples of a 70-pip reference
        stop, _, _ = r5.rule_levels(rule, 1, 1.1, 1.0999, 0.0, 0.0070, scale)
        self.assertAlmostEqual(1.1 - stop, 15.0 / 70.0 * 0.0070)

    def test_reference_adr_is_causal(self):
        ctx = es.Context(FRAMES)
        ref = r5.ReferenceADR(ctx)
        t = int(pd.Timestamp("2022-06-15 12:00", tz="UTC").value)
        i = ctx.d1.asof(t)
        known = ctx.adr[max(0, i - r5.REF_ADR_DAYS + 1):i + 1]
        known = known[np.isfinite(known)]
        self.assertAlmostEqual(ref(t), float(np.median(known)))
        self.assertTrue(math.isfinite(ref(t)))
        prefix = cut(FRAMES, pd.Timestamp("2022-06-20 00:00", tz="UTC"))
        self.assertAlmostEqual(r5.ReferenceADR(es.Context(prefix))(t), ref(t))  # later data cannot move it


def london_candidates(ctx, frames, n=60, seed=5):
    """Market candidates at London/NY 5m bar starts with a structure 10-35 pips away."""
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
        out.append(es.Candidate(int(f5.index[k].value), side, struct, "T", f"T:{q}"))
    return out


class RunRule(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = es.Context(FRAMES)
        cls.cands = london_candidates(cls.ctx, FRAMES)
        cls.end = FRAMES["5m"].index[-1].to_pydatetime() + bt.FIVE

    def test_intraday_rule_reproduces_round2_run_2r(self):
        market = r5.model_market(r5.C_RAW, FRAMES, self.end)
        tape = r5.model_tape(r5.C_RAW, "EURUSD", FRAMES["5m"], PIP, True)
        for exit_model, min_sl in (("FIX2", 15.0), ("SL25", 25.0)):
            ref, _ = r2.run_2r(self.cands, self.ctx, market, "x", min_sl)
            got, _ = r5.run_rule(self.cands, self.ctx, tape, "x", r5.intraday_rule(exit_model))
            self.assertGreater(len(ref), 10)
            self.assertEqual([t["idea"] for t in ref], [t["idea"] for t in got])
            for a, b in zip(ref, got):
                self.assertEqual(a["exit_at"], b["exit_at"])
                self.assertEqual(a["reason"], b["reason"])
                self.assertAlmostEqual(a["r"], b["r"])
                self.assertAlmostEqual(a["gross_r"], b["gross_r"])

    def test_relaxed_exits_by_friday_or_the_fifth_day(self):
        tape = r5.model_tape(r5.C_RAW, "EURUSD", FRAMES["5m"], PIP, True)
        trades, _ = r5.run_rule(self.cands, self.ctx, tape, "x", r5.relaxed_rule("TP3"))
        self.assertTrue(trades)
        for t in trades:
            cap = min(r5.nth_trading_close(t["fill_at"], 5), bt.friday_close_after(t["fill_at"]))
            self.assertLessEqual(t["exit_at"], cap)
            self.assertAlmostEqual(t["commission_r"], 7e-5 / t["risk_price"])
            if t["reason"] in (bt.TIME, bt.FRIDAY):
                self.assertEqual(t["exit_at"], cap)

    def test_limit_candidates_fill_at_the_limit_without_slippage(self):
        tape = r5.model_tape(r5.C_RAW, "EURUSD", FRAMES["5m"], PIP, True)
        lims = []
        for c in self.cands[:20]:
            i = int(np.searchsorted(tape.start, c.t))
            s = 1 if c.side == es.LONG else -1
            lims.append(r2.LimitCandidate(c.t, c.side, c.struct, c.family, c.idea,
                                          limit=float(tape.l[i] if s > 0 else tape.h[i]) + s * 0.00002))
        trades, _ = r5.run_rule(lims, self.ctx, tape, "x", r5.relaxed_rule("TP2"))
        self.assertTrue(trades)
        for t, c in zip(trades, (c for c in lims if c.idea in {t["idea"] for t in trades})):
            self.assertEqual(t["entry"], "LIMIT")
            i = int(np.searchsorted(tape.start, c.t))
            s = 1 if c.side == es.LONG else -1
            at_open = float(tape.o[i]) + s * tape.costs(i)[0]
            self.assertAlmostEqual(t["fill"], at_open if s * (at_open - c.limit) <= 0 else c.limit)
            if t["reason"] == bt.TP:
                self.assertGreater(t["exit_at"], t["fill_at"] + bt.FIVE)  # never on the fill bar

    def test_no_lookahead_prefix_invariance(self):
        """Trades exiting before a cut are identical when the data after the cut does not exist."""
        full_tape = r5.model_tape(r5.C_RAW, "EURUSD", FRAMES["5m"], PIP, True)
        full, _ = r5.run_rule(self.cands, self.ctx, full_tape, "x", r5.relaxed_rule("TP2"))
        cut_at = pd.Timestamp("2022-06-15 00:00", tz="UTC")
        part = cut(FRAMES, cut_at)
        ctx = es.Context(part)
        tape = r5.model_tape(r5.C_RAW, "EURUSD", part["5m"], PIP, True)
        trimmed, _ = r5.run_rule([c for c in self.cands if c.t < cut_at.value], ctx, tape, "x",
                                 r5.relaxed_rule("TP2"))
        before = [t for t in full if t["exit_at"] < cut_at.to_pydatetime()]
        self.assertGreater(len(before), 5)
        self.assertEqual([(t["idea"], t["exit_at"], round(t["r"], 12)) for t in before],
                         [(t["idea"], t["exit_at"], round(t["r"], 12)) for t in trimmed[:len(before)]])


class Selection(unittest.TestCase):
    def test_prior_grid_and_declared_counts(self):
        self.assertEqual(len(r5.PRIOR), 81)
        counts = r5.declared_counts(list(r4.INSTRUMENTS))
        self.assertEqual(counts, {"prior C_RAW": 81, "C_FUT": 5, "relaxed C_RAW": 48, "relaxed C_OANDA": 48,
                                  "others C_RAW": 35})

    def test_top_variants_one_per_family_min_trades_and_transferable(self):
        def row(avg, n=40):
            return {"avg_r": avg, "trades": n}
        rows = {"V2_I2:FIX2": row(0.5), "E3:FIX2": row(0.4), "E3:SL25": row(0.39), "A:SW:TP3": row(0.3),
                "A:SW:TP2": row(0.35), "M2:ADR": row(0.9, 10), "RX:M3:TP2": row(0.2), "RX:M3:TP3": row(0.25),
                "JT[R1+R5]:SL25": row(0.1)}
        self.assertEqual(r5.top_variants(rows, 3), ["V2_I2:FIX2", "E3:FIX2", "A:SW:TP2"])
        self.assertEqual(r5.top_variants(rows, 4, r5.transferable), ["E3:FIX2", "A:SW:TP2", "RX:M3:TP3",
                                                                    "JT[R1+R5]:SL25"])
        self.assertEqual(r5.best_relaxed_tp(rows, "M3"), "RX:M3:TP3")

    def test_neighbour_rules(self):
        labels = [label for label, _ in r5.neighbour_rules("RX:E3:TP2")]
        self.assertEqual(labels, ["lo=11.25", "lo=18.75", "hi=37.5", "hi=62.5", "tp=1.5", "tp=2.5", "days=4",
                                  "days=6"])
        self.assertIn("sl_adr=0.225", [label for label, _ in r5.neighbour_rules("E1:ADR")])


if __name__ == "__main__":
    unittest.main()
