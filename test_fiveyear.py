"""Replay-only 5-year runner (replay/fiveyear.py): yearly blocks, the pass rule, the false-pass probability, exit
routing / F80 pre-filter equivalence, V2_I2 re-exits and the resumable per-year signal checkpoints."""
import json
import os
import tempfile
import unittest
from collections import Counter
from datetime import datetime, timezone
from unittest.mock import patch

import pandas as pd

from replay import entry_search as es
from replay import fiveyear as fy
from replay import intraday_backtest as bt
from replay import round2 as r2
from test_entry_search import StubContext, fake_trade, toy_market

UTC = timezone.utc


class Years(unittest.TestCase):
    def test_five_consecutive_twelve_month_blocks_clipped_at_the_end(self):
        start = datetime(2021, 10, 29, 21, tzinfo=UTC)
        years = fy.year_blocks(start, datetime(2026, 10, 20, tzinfo=UTC))
        self.assertEqual(len(years), 5)
        self.assertEqual(years[0][0], start)
        self.assertEqual(years[1][0], datetime(2022, 10, 29, 21, tzinfo=UTC))
        self.assertTrue(all(years[q][1] == years[q + 1][0] for q in range(4)))
        self.assertEqual(years[-1][1], datetime(2026, 10, 20, tzinfo=UTC))

    def test_false_pass_probability(self):
        self.assertAlmostEqual(fy.p_false_pass(), 5 * 0.1 ** 4 * 0.9 + 0.1 ** 5)
        self.assertAlmostEqual(round(fy.p_false_pass(), 5), 0.00046)


class PassRule(unittest.TestCase):
    def setUp(self):
        self.years = fy.year_blocks(datetime(2021, 11, 1, tzinfo=UTC), datetime(2026, 11, 1, tzinfo=UTC))

    def series(self, year, n, r, hold=3):
        start = self.years[year][0] + pd.Timedelta(days=3).to_pytimedelta()
        return [fake_trade(start + pd.Timedelta(days=2 * d).to_pytimedelta(), hold, r[d % len(r)]) for d in range(n)]

    def test_four_good_years_pass_and_three_do_not(self):
        good = [t for y in range(5) for t in (self.series(y, 100, [1.0, -0.6]) if y != 2 else
                                              self.series(y, 20, [0.5, -0.6]))]
        s = fy.evaluate(good, self.years)
        self.assertEqual(s["years_ok"], 4)
        self.assertTrue(s["pass"], s["fails"])
        bad = [t for y in range(5) for t in (self.series(y, 100, [1.0, -0.6]) if y < 3 else
                                             self.series(y, 20, [0.5, -0.6]))]
        s = fy.evaluate(bad, self.years)
        self.assertFalse(s["pass"])
        self.assertIn("years 3/5", s["fails"])

    def test_drawdown_and_cost_fail_the_rule(self):
        deep = [t for y in range(5) for t in self.series(y, 100, [1.0, -0.6])]
        deep[50:62] = [dict(t, r=-1.0) for t in deep[50:62]]
        s = fy.evaluate(deep, self.years)
        self.assertGreater(s["dd"], 8)
        self.assertFalse(s["pass"])
        costly = [dict(t, gross_r=t["r"] + 0.2) for y in range(5) for t in self.series(y, 100, [1.0, -0.6])]
        s = fy.evaluate(costly, self.years)
        self.assertIn("cost 0.200R", s["fails"])
        self.assertEqual(fy.evaluate([], self.years)["fails"], ["no trades"])

    def test_yearly_figures_purge_crossers_but_totals_keep_them(self):
        a, b = self.years[0]
        crosser = fake_trade(b - pd.Timedelta(hours=1).to_pytimedelta(), 5, 4.0)
        s = fy.evaluate(self.series(0, 10, [0.5]) + [crosser], self.years)
        self.assertEqual((s["years"][0]["n"], s["years"][1]["n"], s["trades"]), (10, 0, 11))
        self.assertAlmostEqual(s["total_r"], 9.0)


class Exits(unittest.TestCase):
    def test_routing_and_prefilter_equivalence(self):
        market, starts = toy_market("2026-03-03 13:00", 80)
        cands = [es.Candidate(starts[2].value, "LONG", 1.09900, "E2", "a"),
                 es.Candidate(starts[30].value, "LONG", 1.09900, "E2", "b")]
        trades, _ = fy.run_exit(cands, StubContext(), market, "E2:SL25", "SL25")
        self.assertAlmostEqual(trades[0]["risk_pips"], 25.0)
        fix, _ = fy.run_exit(cands, StubContext(), market, "E2:FIX2", "FIX2")
        ref, _ = es.run_variant(cands, StubContext(), market, es.Variant("E2:FIX2", "E2", "FIX2", False))
        self.assertEqual([t["r"] for t in fix], [t["r"] for t in ref])
        for ctx in (StubContext(day_range=50.0), StubContext(day_range=10.0)):
            pre, _ = fy.run_exit(cands, ctx, market, "E2:FIX2:F80", "FIX2", f80=True)
            loop, _ = es.run_variant(cands, ctx, market, es.Variant("E2:FIX2:F80", "E2", "FIX2", True))
            self.assertEqual([t["r"] for t in pre], [t["r"] for t in loop])
        limit = [r2.LimitCandidate(starts[3].value, "LONG", 1.09800, "JT_SMC", "c", limit=1.09990)]
        with self.assertRaises(ValueError):
            fy.run_exit(limit, StubContext(), market, "x", "ADR")


class V2Records(unittest.TestCase):
    def test_sl25_and_adr_re_exits_of_the_i2_plan(self):
        ctx = StubContext(adr=80.0)
        ctx.adr_at = lambda t: 80.0
        rec = {"slot": "2026-03-03T13:00:00+00:00", "status": "VALID_SETUP", "side": "SHORT",
               "V2_I2": {"entry": 1.10000, "stop": 1.10180, "target": 1.09640}}
        wide = dict(rec, V2_I2={"entry": 1.10000, "stop": 1.10350, "target": 1.09300})
        out = fy.v2_records([rec, wide, {"slot": rec["slot"], "status": "NO_SETUP"}], ctx)
        self.assertAlmostEqual(out[0]["V2_I2_SL25"]["stop"], 1.10250)
        self.assertAlmostEqual(out[0]["V2_I2_SL25"]["target"], 1.09500)
        self.assertAlmostEqual(out[0]["V2_I2_ADR"]["stop"], 1.10240)  # 0.3 x 80 = 24 pips
        self.assertAlmostEqual(out[0]["V2_I2_ADR"]["target"], 1.09536)  # 0.58 x 80 = 46.4 (<= 2R = 48)
        self.assertEqual(out[1]["V2_I2_SL25_skip"], "sl_wider_than_30_pips")
        self.assertNotIn("V2_I2_SL25", out[2])
        self.assertNotIn("V2_I2_SL25", rec)  # input records untouched


class Checkpoints(unittest.TestCase):
    def test_years_are_checkpointed_and_reused(self):
        slots = [datetime(2024, 12, 31, 15, tzinfo=UTC), datetime(2025, 1, 2, 9, tzinfo=UTC),
                 datetime(2025, 1, 2, 9, 15, tzinfo=UTC)]
        calls = Counter()

        def fake(data_dir, chunk, workers, log):
            calls[chunk[0].year] += 1
            return [{"slot": s.isoformat(), "status": "NO_SETUP"} for s in chunk]
        with tempfile.TemporaryDirectory() as out, patch.object(bt, "generate_signals", fake):
            first = bt.signals_by_year("d", out, slots, 1, lambda m: None)
            self.assertEqual(sorted(os.listdir(out)), ["signals_2024.jsonl", "signals_2025.jsonl"])
            again = bt.signals_by_year("d", out, slots, 1, lambda m: None)
            self.assertEqual(first, again)
            self.assertEqual(dict(calls), {2024: 1, 2025: 1})
            with open(os.path.join(out, "signals_2025.jsonl"), "w", encoding="utf-8") as handle:
                handle.write(json.dumps({"slot": slots[1].isoformat()}) + "\n")  # interrupted / stale year
            bt.signals_by_year("d", out, slots, 1, lambda m: None)
            self.assertEqual(dict(calls), {2024: 1, 2025: 2})


class Pairs(unittest.TestCase):
    def test_pair_members_never_take_m5_and_m5b(self):
        rows = {f"{m}:FIX2": {"avg_r": r} for m, r in
                (("M1", -0.2), ("M2", 0.0), ("M3", -0.1), ("M4", -0.3), ("M5", 0.2), ("M5b", 0.3))}
        chosen, _ = fy.pair_members(rows)
        self.assertEqual(chosen, ["M5b", "M2", "M3", "M1"])


if __name__ == "__main__":
    unittest.main()
