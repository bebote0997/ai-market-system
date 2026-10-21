"""Replay-only round 3 (replay/round3.py): no lookahead in every module (M1-M5b), pair/ALL combination and new family
(F1, F1b, F2, F3 VWAP, F4), mirrored shorts, and the fixed rules of each feature."""
import math
import unittest

import numpy as np
import pandas as pd

from replay import entry_search as es
from replay import round3 as r3
from test_entry_search import mirror, synthetic_frames, truncate

UTC = "UTC"


def with_volume(frames, seed=5):
    rng = np.random.default_rng(seed)
    out = dict(frames)
    out["5m"] = frames["5m"].copy()
    out["5m"]["Volume"] = rng.integers(20, 400, len(frames["5m"])).astype(float)
    return out


def truncate_all(frames, cutoff):
    part = truncate(frames, cutoff)
    if "Volume" in frames["5m"]:
        part["5m"] = part["5m"].copy()
        part["5m"]["Volume"] = frames["5m"]["Volume"].loc[part["5m"].index]
    return part


CUTS = ("2026-02-11 09:10", "2026-02-24 14:45", "2026-03-05 04:20", "2026-03-19 10:05", "2026-03-27 13:30")


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frames = with_volume(synthetic_frames(days=90, seed=11))
        cls.ctx = es.Context(cls.frames)
        cls.mods = r3.modules(cls.ctx)


class ModulesNoLookahead(Base):
    def test_module_arrays_never_change_when_future_bars_are_removed(self):
        for cut in CUTS:
            cutoff = pd.Timestamp(cut, tz=UTC)
            part = es.Context(truncate_all(self.frames, cutoff))
            mods = r3.modules(part)
            for name in r3.MODULES:
                for sign in (1, -1):
                    full, small = self.mods[name][sign], mods[name][sign]
                    n = len(small.cond)
                    self.assertGreater(n, 0)
                    np.testing.assert_array_equal(full.cond[:n], small.cond, (name, sign, cut))
                    np.testing.assert_array_equal(full.trig[:n], small.trig, (name, sign, cut))
                    np.testing.assert_array_equal(full.struct[:n], small.struct, (name, sign, cut))
                    self.assertEqual(list(full.idea[:n]), list(small.idea), (name, sign, cut))

    def test_every_module_fires_and_triggers_are_in_force(self):
        for name in r3.MODULES:
            for sign in (1, -1):
                mod = self.mods[name][sign]
                self.assertFalse((mod.trig & ~mod.cond).any(), name)
                self.assertTrue(np.isfinite(mod.struct[mod.trig]).all(), name)
            self.assertTrue(any(self.mods[name][s].trig.any() for s in (1, -1)), name)

    def test_long_trigger_is_a_bullish_close_above_its_structure(self):
        for name in r3.MODULES:
            mod, b = self.mods[name][1], self.ctx.views[1].m5
            k = np.flatnonzero(mod.trig)
            self.assertTrue((b.c[k] > mod.struct[k]).all(), name)
            if name not in ("M1", "M4"):  # M1/M4 trigger on a BOS close, the others on a bullish candle
                self.assertTrue((b.c[k] > b.o[k]).all(), name)

    def test_h4_trend_is_required_where_declared(self):
        for name in ("M2", "M3", "M5", "M5b"):
            for sign, view in self.ctx.views.items():
                self.assertFalse((self.mods[name][sign].cond & ~r3.h4_bull(view)).any(), name)

    def test_m5b_is_a_subset_of_m5(self):
        for sign in (1, -1):
            self.assertFalse((self.mods["M5b"][sign].cond & ~self.mods["M5"][sign].cond).any())

    def test_shorts_are_the_mirrored_longs(self):
        flipped = es.Context(mirror(self.frames))
        mods = r3.modules(flipped)
        for name in r3.MODULES:
            np.testing.assert_array_equal(mods[name][1].trig, self.mods[name][-1].trig, name)
            np.testing.assert_array_equal(mods[name][1].struct, self.mods[name][-1].struct, name)


class Combination(Base):
    def test_single_member_is_the_module_and_pairs_need_both(self):
        for name in r3.MODULES:
            cands = r3.combine(self.ctx, self.mods, (name,), name)
            window = r3.in_window(self.ctx, r3.MOD_WINDOW)
            expected = sum(int((self.mods[name][s].trig & window).sum()) for s in (1, -1))
            self.assertEqual(len(cands), expected, name)
        pair = r3.combine(self.ctx, self.mods, ("M4", "M5"), "M4&M5")
        ends = {int(t): k for k, t in enumerate(self.ctx.m5.end)}
        for c in pair:
            k, sign = ends[c.t], 1 if c.side == "LONG" else -1
            a, b = self.mods["M4"][sign], self.mods["M5"][sign]
            self.assertTrue(a.cond[k] and b.cond[k] and (a.trig[k] or b.trig[k]))
            self.assertTrue(c.idea.startswith("M4&M5:M"))

    def test_combined_candidates_are_prefix_invariant(self):
        members = [("M1", "M5"), ("M2", "M3"), ("M3", "M5b"), ("M1", "M2", "M3", "M4", "M5")]
        full = {m: r3.combine(self.ctx, self.mods, m, "&".join(m)) for m in members}
        for cut in CUTS[1:4]:
            cutoff = pd.Timestamp(cut, tz=UTC)
            part = es.Context(truncate_all(self.frames, cutoff))
            mods = r3.modules(part)
            for m in members:
                self.assertEqual(r3.combine(part, mods, m, "&".join(m)),
                                 [c for c in full[m] if c.t <= cutoff.value], (m, cut))

    def test_decisions_are_bar_closes_inside_the_window(self):
        ends = set(self.ctx.m5.end.tolist())
        for name in r3.MODULES:
            for c in r3.combine(self.ctx, self.mods, (name,), name):
                self.assertIn(c.t, ends)
                start = pd.Timestamp(c.t - es.FIVE_NS, tz=UTC).tz_convert(es.NY)
                self.assertTrue(r3.MOD_WINDOW[0] <= start.hour * 60 + start.minute < r3.MOD_WINDOW[1])


class FamiliesNoLookahead(Base):
    def test_family_candidates_never_change_when_future_bars_are_removed(self):
        full = {f: r3.family_candidates(self.ctx, f) for f in r3.FAMILIES}
        self.assertTrue(all(full.values()), {f: len(c) for f, c in full.items()})
        for cut in CUTS:
            cutoff = pd.Timestamp(cut, tz=UTC)
            part = es.Context(truncate_all(self.frames, cutoff))
            for f in r3.FAMILIES:
                self.assertEqual(r3.family_candidates(part, f), [c for c in full[f] if c.t <= cutoff.value],
                                 (f, cut))

    def test_shorts_are_the_mirrored_longs(self):
        flipped = es.Context(mirror(self.frames))
        for f in r3.FAMILIES:
            shorts = [c for c in r3.family_candidates(self.ctx, f) if c.side == "SHORT"]
            longs = [c for c in r3.family_candidates(flipped, f) if c.side == "LONG"]
            self.assertEqual([(c.t, -c.struct) for c in longs], [(c.t, c.struct) for c in shorts], f)

    def test_orb_needs_the_full_range_and_breaks_after_it(self):
        view, b = self.ctx.views[1], self.ctx.views[1].m5
        for c in [c for c in r3.family_candidates(self.ctx, "F1") if c.side == "LONG"]:
            k = view.m5.asof(c.t)
            local = pd.Timestamp(int(b.start[k]), tz=UTC).tz_convert(es.NY)
            day = int(self.ctx.et_day[k])
            rng = self.ctx.window(day, *r3.F1_LONDON[0])
            self.assertEqual(len(rng), r3.ORB_BARS)
            self.assertGreaterEqual(local.hour * 60 + local.minute, r3.F1_LONDON[1][0])
            self.assertGreater(b.c[k], b.h[rng].max())
            self.assertAlmostEqual(c.struct, b.l[rng].min())
        frames = dict(self.frames)
        f5 = self.frames["5m"]
        local = f5.index.tz_convert(es.NY)
        drop = (local.hour == 3) & (local.minute == 10)  # one range bar missing every day -> no ORB London
        frames["5m"] = f5[~drop]
        self.assertEqual(r3.family_candidates(es.Context(frames), "F1"), [])

    def test_f2_needs_an_excursion_of_a_tenth_of_adr_beyond_pdl(self):
        view, b = self.ctx.views[1], self.ctx.views[1].m5
        longs = [c for c in r3.family_candidates(self.ctx, "F2") if c.side == "LONG"]
        self.assertTrue(longs)
        for c in longs:
            k = b.asof(c.t)
            day = int(self.ctx.et_day[k])
            win = self.ctx.window(day, *r3.F2_WINDOW)
            t0 = int(b.start[win[0]])
            pdl = view.d1.l[view.prior_day(t0)]
            self.assertGreater(b.c[k], pdl)
            self.assertLess(c.struct, pdl - r3.F2_BEYOND_ADR * self.ctx.adr_at(t0) * es.PIP + 1e-12)

    def test_vwap_uses_only_bars_of_its_session_up_to_the_bar(self):
        vwap, label = r3.session_vwap(self.ctx)
        f = self.frames["5m"]
        typical = ((f["High"] + f["Low"] + f["Close"]) / 3).to_numpy()
        vol = f["Volume"].to_numpy()
        k = int(np.flatnonzero(label >= 0)[40])
        same = np.flatnonzero(label[:k + 1] == label[k])
        self.assertAlmostEqual(vwap[k], (typical[same] * vol[same]).sum() / vol[same].sum())
        future = self.frames["5m"].copy()
        future.iloc[k + 1:, future.columns.get_loc("Volume")] *= 7  # future volume never changes past VWAP
        frames = dict(self.frames, **{"5m": future})
        self.assertAlmostEqual(r3.session_vwap(es.Context(frames))[0][k], vwap[k])
        local = f.index.tz_convert(es.NY)
        self.assertTrue((label[(local.hour < 3) | (local.hour >= 12)] == -1).all())
        self.assertNotEqual(label[(local.hour == 7) & (local.minute == 55)][0],
                            label[(local.hour == 8) & (local.minute == 0)][0])  # NY re-anchors at 08:00

    def test_f4_uses_the_atr_before_the_candle_and_its_midpoint(self):
        view = self.ctx.views[1]
        h = view.h1
        cands = [c for c in r3.family_candidates(self.ctx, "F4") if c.side == "LONG"]
        self.assertTrue(cands)
        for c in cands:
            m = int(np.flatnonzero(h.end == c.t)[0])
            self.assertGreaterEqual(h.c[m] - h.o[m], r3.F4_BODY_ATR * self.ctx.atr1[m - 1])
            self.assertAlmostEqual(c.struct, (h.h[m] + h.l[m]) / 2)
        self.assertTrue(all(math.isfinite(self.ctx.atr1[int(np.flatnonzero(h.end == c.t)[0]) - 1]) for c in cands))


if __name__ == "__main__":
    unittest.main()
