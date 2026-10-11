"""V2 Phase 4 / P4.1A: offline target-policy lab — lookahead and invariants (synthetic fixtures)."""
import ast
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import floor.orchestrator as floor
from core.contracts import SetupAssessment
from replay import lab
from replay.engine import snapshot_at
from runtime.paper_contracts import paper_instruments

ROOT = Path(__file__).resolve().parent
T = pd.Timestamp("2026-01-15T13:30:00Z")  # Thursday
XAU = paper_instruments()["XAUUSD"]


def frames(highs_1h=None, extra_5m=None):
    """1h bars for the last 3 days (flat 100-101) plus 5m bars; ``highs_1h`` overrides {start: high}."""
    one_idx = pd.date_range((T - pd.Timedelta(days=3)).floor("h"), T + pd.Timedelta(hours=6), freq="1h", inclusive="left")
    one = pd.DataFrame({"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0, "symbol": "XAUUSD",
                        "is_closed": True}, index=one_idx)
    for start, high in (highs_1h or {}).items():
        one.loc[pd.Timestamp(start), "High"] = high
    five_idx = pd.date_range(T - pd.Timedelta(hours=6), T + pd.Timedelta(hours=6), freq="5min", inclusive="left")
    five = pd.DataFrame({"Open": 100.0, "High": 100.5, "Low": 99.5, "Close": 100.0, "symbol": "XAUUSD",
                         "is_closed": True}, index=five_idx)
    for start, high in (extra_5m or {}).items():
        five.loc[pd.Timestamp(start), "High"] = high
    fifteen = five.resample("15min").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"})
    fifteen["symbol"], fifteen["is_closed"] = "XAUUSD", True
    return {"1h": one, "15m": fifteen, "5m": five}


def setup(swings_1h=(), swings_15m=(), stop=90.0):
    def swing(price, pivot, confirmed=None):
        pivot = pd.Timestamp(pivot)
        return {"price": price, "pivot_timestamp": pivot, "timestamp": pd.Timestamp(confirmed) if confirmed else pivot + pd.Timedelta(hours=1)}
    structure = {"1h": {"swings": {"highs": [swing(*s) for s in swings_1h], "lows": []}},
                 "15m": {"swings": {"highs": [swing(*s) for s in swings_15m], "lows": []}}}
    return SetupAssessment("1.0", "run", T.to_pydatetime(), "XAUUSD", "VALID_SETUP", "LONG", ("1h", "15m", "5m"),
                           evidence=(structure, {}), invalidation=stop, explanation={"setup_id": "sid-1"})


def run(variant, s, f, slot=T):
    return lab.evaluate(variant, s, snapshot_at(f, slot), slot, XAU)


class LabLookaheadTests(unittest.TestCase):
    def test_future_1h_swing_cannot_become_target(self):
        f = frames()
        future = setup(swings_1h=[(130.0, T - pd.Timedelta(hours=1), T + pd.Timedelta(hours=1))])
        self.assertEqual(run("D1", future, f)["status"], "NO_VALID_TARGET")
        known = setup(swings_1h=[(130.0, T - pd.Timedelta(hours=3))])
        self.assertEqual(run("D1", known, f)["status"], "TRADEABLE")

    def test_incomplete_current_day_is_not_a_prior_day(self):
        f = frames(highs_1h={T.normalize() + pd.Timedelta(hours=9): 140.0})  # today's high, day not complete
        day, high, _ = lab.prior_day(snapshot_at(f, T), T)
        self.assertEqual((day, high), (T.normalize() - pd.Timedelta(days=1), 101.0))

    def test_future_daily_high_cannot_leak(self):
        later = T.normalize() - pd.Timedelta(days=1) + pd.Timedelta(hours=20)
        decided_before = later.normalize() + pd.Timedelta(hours=10)  # slot on the prior day itself
        f = frames(highs_1h={later: 150.0})
        found = lab.prior_day(snapshot_at(f, decided_before), decided_before)
        self.assertNotEqual(found[1], 150.0)  # a bar of a day not yet complete at the slot never counts
        f2 = frames(highs_1h={T + pd.Timedelta(hours=2): 150.0})  # after the slot entirely
        self.assertEqual(lab.prior_day(snapshot_at(f2, T), T)[1], 101.0)

    def test_weekend_quotes_are_never_a_prior_day(self):
        monday = pd.Timestamp("2026-01-19T10:00:00Z")
        f = frames()
        one = pd.DataFrame({"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0},
                           index=pd.date_range("2026-01-16T00:00Z", "2026-01-19T09:00Z", freq="1h"))
        one.loc[one.index.dayofweek >= 5, "High"] = 120.0
        one.loc[one.index.normalize() == pd.Timestamp("2026-01-16T00:00Z"), "High"] = 105.0
        found = lab.prior_day({"1h": one, "5m": f["5m"].iloc[:0]}, monday)
        self.assertEqual((found[0].date().isoformat(), found[1]), ("2026-01-16", 105.0))


class LabSelectionTests(unittest.TestCase):
    def test_internal_15m_swing_is_context_not_d1_target(self):
        s = setup(swings_15m=[(102.0, T - pd.Timedelta(hours=2))], swings_1h=[(130.0, T - pd.Timedelta(hours=3))])
        r = run("D1", s, frames())
        self.assertEqual((r["status"], r["target"]), ("TRADEABLE", "130.00"))

    def test_d1_and_d3_take_the_nearest_never_the_best_ratio(self):
        s = setup(swings_1h=[(115.0, T - pd.Timedelta(hours=4)), (130.0, T - pd.Timedelta(hours=3))])
        r = run("D1", s, frames())
        self.assertEqual((r["status"], r["target"]), ("RR_BELOW_FLOOR", "115.00"))  # 1.5R nearest, not 3R
        prior_high = frames(highs_1h={T.normalize() - pd.Timedelta(hours=5): 125.0})
        r = run("D3", setup(swings_1h=[(130.0, T - pd.Timedelta(hours=3))]), prior_high)
        self.assertEqual((r["target"], r["source"]), ("125.00", "prior_day_high"))  # 2.5R nearer than 3R

    def test_swept_levels_are_not_external(self):
        s = setup(swings_1h=[(120.0, T - pd.Timedelta(hours=5))])
        swept = frames(extra_5m={T - pd.Timedelta(minutes=20): 121.0})
        self.assertEqual(run("D1", s, swept)["status"], "NO_VALID_TARGET")
        self.assertEqual(run("D1", s, frames())["status"], "TRADEABLE")
        swept_prior = frames(highs_1h={T.normalize() - pd.Timedelta(hours=5): 125.0, T - pd.Timedelta(hours=3): 126.0})
        self.assertEqual(run("D2", setup(), swept_prior)["status"], "NO_VALID_TARGET")

    def test_bands_stop_and_setup_identity(self):
        for price, status in ((115.0, "RR_BELOW_FLOOR"), (120.0, "TRADEABLE"), (150.0, "TRADEABLE"),
                              (150.01, "OUT_OF_POLICY_EXTENDED_TARGET")):
            s = setup(swings_1h=[(price, T - pd.Timedelta(hours=3))])
            r = run("D1", s, frames())
            self.assertEqual((r["status"], r["stop"], r["entry"]), (status, "90.00", "100.00"))
            self.assertEqual((s.invalidation, s.explanation["setup_id"]), (90.0, "sid-1"))

    def test_lab_never_touches_production_paths(self):
        with patch("storage.database.Store.__init__", side_effect=AssertionError("db")), \
                patch("execution.paper_broker.PaperBroker.submit_plan", side_effect=AssertionError("order")):
            run("D3", setup(swings_1h=[(130.0, T - pd.Timedelta(hours=3))]), frames())
        with self.assertRaises(ValueError):
            floor.run({}, T, "XAUUSD", None, None, {}, equity=1.0, planner_policy="D1")
        for path in list((ROOT / "runtime").glob("*.py")) + [ROOT / "floor" / "orchestrator.py", ROOT / "riesgo.py",
                                                              ROOT / "execution" / "paper_broker.py"]:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            modules = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
            self.assertNotIn("replay.lab", modules, path.name)
            self.assertNotIn("replay", modules, path.name)


if __name__ == "__main__":
    unittest.main()
