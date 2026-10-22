"""Replay-only round 9 (replay/round9.py): fail-closed freeze (committed copy, engine blob, known variants), the
committed freeze itself, quarter bookkeeping, cost R per trade and the strict / round-7 bars."""
import json
import os
import tempfile
import unittest

import numpy as np

from replay import round8 as r8
from replay import round9 as r9

REPO_FROZEN = os.path.join(r8.REPO, "replay", "round9_frozen.json")


def payload(**kw):
    with open(REPO_FROZEN, encoding="utf-8") as handle:
        p = json.load(handle)
    p.update(kw)
    return json.dumps(p, indent=2, sort_keys=True) + "\n"


class FrozenTest(unittest.TestCase):
    def write(self, text):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, r9.FROZEN_NAME), "w", encoding="utf-8") as handle:
            handle.write(text)
        return d

    def test_committed_freeze_is_the_round8b_nas100_trio(self):
        with open(REPO_FROZEN, encoding="utf-8") as handle:
            p = json.load(handle)
        self.assertEqual((p["symbol"], p["primary"], p["secondary"]), ("NAS100USD", "L3:NR4", ["L3:NR7", "S1a:ORB5"]))
        self.assertEqual((p["main_cost"], p["reference_costs"]), ("C_IBKR", ["C_OANDA"]))
        self.assertEqual(p["holdout"], ["2025-01-01", "2026-10-20"])
        self.assertEqual(p["bar_strict"]["tr_mo"], 15.0)
        self.assertEqual(p["bar_info_r7"]["tr_mo"], 3.0)
        self.assertEqual((p["tests_before"], p["tests_after"]), (626, 629))

    def test_loads_when_equal_to_committed_and_engine_matches(self):
        text = payload()
        blob = json.loads(text)["engine_blob"]
        got = r9.load_frozen(self.write(text), committed=text, engine_blob=blob)
        self.assertEqual(got["primary"], "L3:NR4")

    def test_fails_when_file_differs_from_committed(self):
        text = payload()
        with self.assertRaises(RuntimeError):
            r9.load_frozen(self.write(payload(primary="L3:NR7")), committed=text, engine_blob="x")

    def test_fails_when_engine_changed(self):
        text = payload()
        with self.assertRaisesRegex(RuntimeError, "engine"):
            r9.load_frozen(self.write(text), committed=text, engine_blob="deadbeef")

    def test_fails_on_unknown_variant(self):
        text = payload(secondary=["L3:NR9"])
        blob = json.loads(text)["engine_blob"]
        with self.assertRaises(ValueError):
            r9.load_frozen(self.write(text), committed=text, engine_blob=blob)


class MetricsTest(unittest.TestCase):
    BAR = {"avg_r": 0.08, "pf": 1.15, "dd": 12.0, "tr_mo": 15.0, "min_pos_quarters": 5, "quarters": 8}
    INFO = {"avg_r": 0.08, "pf": 1.15, "dd": 12.0, "tr_mo": 3.0}

    def test_holdout_has_eight_quarters_including_partial_2026q4(self):
        qs = r9.quarters("2025-01-01", "2026-10-20")
        self.assertEqual(len(qs), 8)
        self.assertEqual((qs[0], qs[-1]), ("2025Q1", "2026Q4"))

    def test_quarter_r_keeps_empty_quarters_at_zero(self):
        tr = [{"day": "2025-02-03", "r": 1.0}, {"day": "2025-03-03", "r": -0.5}, {"day": "2026-10-19", "r": 2.0}]
        qr = r9.quarter_r(tr, "2025-01-01", "2026-10-20")
        self.assertEqual((qr["2025Q1"], qr["2025Q2"], qr["2026Q4"]), (0.5, 0.0, 2.0))

    def test_cost_r_counts_spread_slip_commission(self):
        fills = r8.Fills(r8.C_IBKR, "NAS100USD", np.zeros(4))
        t_sl = {"k": 0, "j": 2, "reason": "SL", "risk": 10.0}
        t_tp = {"k": 0, "j": 2, "reason": "TP", "risk": 10.0}
        self.assertAlmostEqual(r9.cost_r([t_sl], fills), (0.125 + 0.25 + 0.125 + 0.25 + 0.62) / 10)
        self.assertAlmostEqual(r9.cost_r([t_tp], fills), (0.125 + 0.25 + 0.125 + 0.62) / 10)

    def trades(self, n_per_q, r):
        days = ["2025-02-03", "2025-05-05", "2025-08-04", "2025-11-03", "2026-02-02", "2026-05-04", "2026-08-03",
                "2026-10-05"]
        out, t = [], 0
        for d in days:
            for _ in range(n_per_q):
                out.append({"day": d, "r": r[t % len(r)], "worst": min(r[t % len(r)], 0.0), "reason": "TIME",
                            "k": 0, "j": 1, "risk": 10.0})
                t += 1
        return out

    def test_bars(self):
        fills = r8.Fills(r8.C_IBKR, "NAS100USD", np.zeros(4))
        many = self.trades(50, [1.0, -0.5])  # ~18.6 tr/mo, all quarters positive
        mt = r9.evaluate(many, "2025-01-01", "2026-10-20", self.BAR, self.INFO, fills)
        self.assertTrue(mt["pass_strict"] and mt["pass_r7_info"])
        few = self.trades(12, [1.0, -0.5])  # ~4.5 tr/mo: fails strict frequency only
        mt = r9.evaluate(few, "2025-01-01", "2026-10-20", self.BAR, self.INFO, fills)
        self.assertFalse(mt["pass_strict"])
        self.assertTrue(mt["pass_r7_info"])
        self.assertEqual(mt["pos_quarters"], 8)


if __name__ == "__main__":
    unittest.main()
