"""Replay-only round 2 (replay/round2.py): relaxed JT_SMC equals round 1 without relaxations, no lookahead under every
relaxation, mirrored shorts, R5 limit fills, 25-pip stop model, strict bar, walk-forward folds, step-2 selection."""
import unittest
from collections import Counter
from datetime import datetime, timezone

import pandas as pd

from replay import entry_search as es
from replay import intraday_backtest as bt
from replay import round2 as r2
from test_entry_search import StubContext, fake_trade, mirror, synthetic_frames, toy_market, truncate

UTC = timezone.utc
HALF = 0.75 * bt.PIP


def half(_k):
    return HALF


class Grid(unittest.TestCase):
    def test_grid_is_the_predeclared_twelve_combos(self):
        self.assertEqual([r.name for r in r2.GRID], list(r2.GRID_NAMES))
        self.assertEqual(len(set(r2.GRID)), 12)
        self.assertEqual(r2.Relax.parse("R1+R5"), r2.Relax(r1=True, r5=True))
        self.assertEqual(r2.Relax().name, "base")
        for bad in ("R2+R3", "R7"):
            with self.assertRaises(ValueError):
                r2.Relax.parse(bad)


class RelaxedJT(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frames = synthetic_frames(days=90, seed=11)
        cls.ctx = es.Context(cls.frames)

    def test_no_relaxation_reproduces_round_one(self):
        funnel_es, funnel_r2 = Counter(), Counter()
        for view in self.ctx.views.values():
            old = es.jt_candidates(self.ctx, view, False, {}, funnel_es)
            new = r2.jt_relaxed(self.ctx, view, r2.Relax(), {}, funnel_r2, half)
            self.assertEqual([(c.t, c.side, c.struct, c.idea) for c in new],
                             [(c.t, c.side, c.struct, c.idea) for c in old])
        self.assertTrue(funnel_es["candidate"])
        self.assertEqual(dict(funnel_r2), dict(funnel_es))

    def test_relaxations_open_the_funnel(self):
        base = r2.jt_relaxed_all(self.ctx, r2.Relax(), {}, half)[1]
        h4 = r2.jt_relaxed_all(self.ctx, r2.Relax.parse("R2"), {}, half)[1]
        self.assertEqual(h4["sweep"], base["sweep"])
        self.assertGreaterEqual(h4["trend"], base["trend"])
        r1 = r2.jt_relaxed_all(self.ctx, r2.Relax.parse("R1"), {}, half)[1]
        self.assertGreaterEqual(r1["candidate"], base["candidate"])

    def test_candidates_never_change_when_future_bars_are_removed(self):
        for name in ("R1+R2+R4", "R1+R2+R5", "R1+R2+R4+R5+R6", "R1+R3+R4+R5"):
            relax = r2.Relax.parse(name)
            full = r2.jt_relaxed_all(self.ctx, relax, {}, half)[0]
            self.assertTrue(full, name)
            first = pd.Timestamp(full[0].t, tz=UTC)
            for cut in (first, first + pd.Timedelta("5min"), pd.Timestamp("2026-02-20 10:05", tz=UTC)):
                part = r2.jt_relaxed_all(es.Context(truncate(self.frames, cut)), relax, {}, half)[0]
                # a limit candidate's t is the start of the touching bar: it is known once that bar has ended
                seen = [c for c in full if c.t + (es.FIVE_NS if getattr(c, "limit", None) is not None else 0) <= cut.value]
                self.assertEqual([c for c in part if c.t + (es.FIVE_NS if getattr(c, "limit", None) is not None
                                                                else 0) <= cut.value], seen, (name, cut))

    def test_limit_orders_sit_at_the_ob_edge_and_are_touched(self):
        cands = r2.jt_relaxed_all(self.ctx, r2.Relax.parse("R1+R2+R4+R5"), {}, half)[0]
        self.assertTrue(cands)
        five = self.frames["5m"]
        for c in cands:
            self.assertIsNotNone(c.limit)
            bar = five.loc[pd.Timestamp(c.t, tz=UTC)]
            if c.side == "LONG":
                self.assertLessEqual(bar["Low"] + HALF, c.limit + 1e-12)
                self.assertLess(c.struct, c.limit)
            else:
                self.assertGreaterEqual(bar["High"] - HALF, c.limit - 1e-12)
                self.assertGreater(c.struct, c.limit)

    def test_shorts_are_the_mirrored_longs(self):
        flipped = es.Context(mirror(self.frames))
        relax = r2.Relax.parse("R1+R2+R4+R5+R6")
        shorts = [c for c in r2.jt_relaxed_all(self.ctx, relax, {}, half)[0] if c.side == "SHORT"]
        longs = [c for c in r2.jt_relaxed_all(flipped, relax, {}, half)[0] if c.side == "LONG"]
        self.assertTrue(shorts)
        self.assertEqual([(c.t, -c.struct, -c.limit) for c in longs], [(c.t, c.struct, c.limit) for c in shorts])


class Exits(unittest.TestCase):
    def test_sl15_is_the_round_one_rule_and_sl25_widens(self):
        ctx = StubContext(atr=0.0010)
        for struct in (1.09950, 1.09850, 1.09750, 1.09600):
            cand = es.Candidate(0, "LONG", struct, "E2", "x")
            self.assertEqual(r2.plan_2r(cand, 1.10000, ctx.atr, 15.0),
                             es.plan_levels(cand, "FIX2", 1.10000, ctx.atr, 60.0))
        cand = es.Candidate(0, "SHORT", 1.10100, "E2", "x")  # 10 + 2 pips -> widened to 25
        stop, target, _ = r2.plan_2r(cand, 1.10000, 0.0010, 25.0)
        self.assertAlmostEqual(stop, 1.10250)
        self.assertAlmostEqual(target, 1.09500)
        self.assertEqual(r2.plan_2r(es.Candidate(0, "LONG", 1.09700, "E2", "x"), 1.1, 0.001, 25.0)[2],
                         "sl_wider_than_30_pips")

    def test_limit_fill_at_the_limit_or_a_better_open(self):
        market, _ = toy_market("2026-03-03 13:00", 30, overrides={2: (1.09990, 1.10010, 1.09970, 1.10000),
                                                                 3: (1.09900, 1.09950, 1.09880, 1.09920)})
        self.assertAlmostEqual(r2.limit_fill(market, 2, "LONG", 1.09990), 1.09990)
        self.assertAlmostEqual(r2.limit_fill(market, 3, "LONG", 1.09990), 1.09905)  # opens through it (ask)

    def test_fill_bar_checks_the_stop_but_never_credits_the_target(self):
        tp_bar = {2: (1.10000, 1.10400, 1.09970, 1.10000)}
        market, starts = toy_market("2026-03-03 13:00", 30, price=1.10000, overrides=tp_bar)
        res = r2.simulate_limit(market, 2, "LONG", 1.10000, 1.09850, 1.10300)
        self.assertNotEqual(res["reason"], bt.TP)  # target only reachable from the next bar
        market, starts = toy_market("2026-03-03 13:00", 30, price=1.10000,
                                    overrides={2: (1.10000, 1.10010, 1.09800, 1.10000)})
        res = r2.simulate_limit(market, 2, "LONG", 1.10000, 1.09850, 1.10300)
        self.assertEqual((res["reason"], res["exit_at"]), (bt.SL, starts[3].to_pydatetime()))
        self.assertAlmostEqual(res["r"], (1.09850 - 0.2 * bt.PIP - 1.10000) / 0.0015)
        market, starts = toy_market("2026-03-03 13:00", 30, price=1.10000,
                                    overrides={3: (1.10000, 1.10400, 1.10000, 1.10300)})
        res = r2.simulate_limit(market, 2, "LONG", 1.10000, 1.09850, 1.10300)
        self.assertEqual(res["reason"], bt.TP)
        self.assertAlmostEqual(res["r"], 2.0)

    def test_portfolio_runs_limit_and_market_entries_one_at_a_time(self):
        market, starts = toy_market("2026-03-03 13:00", 60, overrides={3: (1.10000, 1.10010, 1.09950, 1.10000)})
        a = r2.LimitCandidate(starts[3].value, "LONG", 1.09800, "JT_SMC", "a", limit=1.09990)
        b = es.Candidate(starts[5].value, "LONG", 1.09800, "JT_SMC", "b")
        trades, skips = r2.run_2r([a, b], StubContext(), market, "v", 25.0)
        self.assertEqual((len(trades), skips["position_open"]), (1, 1))
        self.assertEqual((trades[0]["entry"], trades[0]["fill"]), ("LIMIT", 1.09990))
        self.assertAlmostEqual(trades[0]["risk_pips"], 25.0)
        self.assertIn("gross_r", trades[0])


class Evaluation(unittest.TestCase):
    def setUp(self):
        self.split = es.Split(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 7, 1, tzinfo=UTC),
                              datetime(2026, 10, 1, tzinfo=UTC))

    def series(self, start, n, r):
        return [fake_trade(start + pd.Timedelta(days=d).to_pytimedelta(), 3, r[d % len(r)]) for d in range(n)]

    def test_strict_bar_needs_both_segments_and_thirty_oos_trades(self):
        good = self.series(datetime(2026, 1, 5, 9, tzinfo=UTC), 150, [1.0, -0.5]) + self.series(
            datetime(2026, 7, 2, 9, tzinfo=UTC), 40, [1.0, -0.5])
        self.assertTrue(r2.strict_pass(*es.summarise(good, self.split)))
        short = good[:150] + good[150:170]  # 20 OOS trades
        self.assertFalse(r2.strict_pass(*es.summarise(short, self.split)))
        self.assertTrue(r2.strict_pass_row({"is_n": 50, "oos_n": 30, "is_r": 0.2, "oos_r": 0.11, "ci_is": 0.01,
                                            "ci_oos": 0.02}))
        self.assertFalse(r2.strict_pass_row({"is_n": 50, "oos_n": 30, "is_r": 0.2, "oos_r": 0.10, "ci_is": 0.01,
                                             "ci_oos": 0.02}))

    def test_three_contiguous_folds_purge_crossers(self):
        edges = r2.folds(self.split)
        self.assertEqual((edges[0][0], edges[-1][1], len(edges)), (self.split.start, self.split.end, 3))
        self.assertTrue(all(edges[q][1] == edges[q + 1][0] for q in range(2)))
        crosser = fake_trade(edges[0][1] - pd.Timedelta(hours=1).to_pytimedelta(), 5, 9.0)
        inside = self.series(datetime(2026, 1, 5, 9, tzinfo=UTC), 10, [0.5])
        m = r2.fold_metrics(inside + [crosser], *edges[0])
        self.assertEqual((m["trades"], m["avg_r"]), (10, 0.5))
        self.assertEqual(r2.fold_metrics([crosser], *edges[1])["trades"], 0)

    def test_step_two_selects_on_in_sample_only(self):
        start = datetime(2026, 1, 5, 9, tzinfo=UTC)
        oos = datetime(2026, 7, 2, 9, tzinfo=UTC)
        combos = [r2.Relax.parse(n) for n in ("R1", "R2", "R4")]
        results = {r2.variant_name(combos[0], "SL25"): (self.series(start, 25, [0.2]) + self.series(oos, 5, [-1]), 0),
                   r2.variant_name(combos[1], "SL25"): (self.series(start, 25, [0.1]) + self.series(oos, 5, [5]), 0),
                   r2.variant_name(combos[2], "SL25"): (self.series(start, 5, [3.0]), 0)}
        self.assertEqual(r2.select_best(results, combos, self.split), [("R1", True), ("R2", True)])
        results.pop(r2.variant_name(combos[1], "SL25"))
        self.assertEqual(r2.select_best(results, [combos[0], combos[2]], self.split), [("R1", True), ("R4", False)])


if __name__ == "__main__":
    unittest.main()
