"""V2 Phase 4 / P4.1: replay foundation (F04-T15) and comparison (F04-T16) on deterministic synthetic fixtures.

Fixtures only — no historical performance is claimed. Proves: separate Replay Store, no trading DB, no
orders, no lookahead, identical evidence for both policies, honest unavailability of probability/net metrics.
"""
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pandas as pd

from core.contracts import AgentMessage
from core.rr_contract import POLICY_V1, POLICY_V2_D
from data.market_evidence import MarketBar
from replay import engine as replay_engine_module
from replay.compare import compare
from replay.engine import replay, simulate
from replay.store import ReplayStoreError, open_replay_store, replay_engine
from storage.evidence_store import EvidenceStoreError

DAY = datetime(2026, 1, 14, tzinfo=timezone.utc)
END = DAY + timedelta(hours=40)
FIVE = timedelta(minutes=5)


def price_at(stamp, rally_from):
    """Flat 2650 until ``rally_from`` then +1.0 per 5 minutes (deterministic fixture)."""
    steps = max(0, int((stamp - rally_from) / FIVE))
    return 2650.0 + steps * 1.0


def build(path, rally_from):
    store = open_replay_store(path)
    engine = replay_engine(store)
    stamps = pd.date_range(DAY, END, freq="5min", inclusive="left")
    five = [(s.to_pydatetime(), price_at(s.to_pydatetime(), rally_from)) for s in stamps]
    for timeframe, step in (("5m", FIVE), ("15m", timedelta(minutes=15)), ("1h", timedelta(hours=1))):
        bars, start = [], DAY
        while start + step <= END:
            inside = [p for t, p in five if start <= t < start + step]
            bars.append(MarketBar("XAUUSD", timeframe, start, inside[0], max(inside), min(inside), inside[-1],
                                  provider="fixture", is_closed=True))
            start += step
        engine.ingest("XAUUSD", timeframe, bars, as_of=END)
    return store, engine


def scouts():
    """Deterministic LONG scouts from the frame they are given: stop = close - 10, first swing high = +24 (2.4R)."""
    def structure(frame, symbol, tf, run_id, at):
        close = float(frame["Close"].iloc[-1])
        payload = {"symbol": symbol, "timeframe": tf, "bias": "bullish", "structure_state": "bullish",
                   "bos": {"type": "BOS", "direction": "bullish", "broken_level": close - 5,
                           "break_timestamp": frame.index[-2]},
                   "retracement": None, "levels": {"support": close - 10, "resistance": close + 24},
                   "swings": {"highs": [{"price": close + 24, "timestamp": frame.index[-1],
                                         "pivot_timestamp": frame.index[-2]}], "lows": []},
                   "evidence": [{"timestamp": frame.index[-1], "type": "closed_bar"}]}
        return AgentMessage("1.0", run_id, at, symbol, tf, "structure", "OK", evidence=(payload,))

    def liquidity(frame, symbol, tf, run_id, at):
        payload = {"sweeps": [{"type": "low"}], "liquidity_above": [], "liquidity_below": [],
                   "evidence": [{"timestamp": frame.index[-1], "closed_bars": len(frame)}]}
        return AgentMessage("1.0", run_id, at, symbol, tf, "liquidity", "OK", evidence=(payload,))
    return patch("floor.orchestrator.analizar_estructura", structure), patch("floor.orchestrator.analizar_liquidez", liquidity)


class ReplayCase(unittest.TestCase):
    def setUp(self):
        self.dir = Path("data/runtime")

    def store(self, rally_from):
        path = self.dir / f"replay-test-{uuid4().hex}.db"
        self.addCleanup(lambda: [path.with_name(path.name + s).unlink(missing_ok=True) for s in ("", "-wal", "-shm")])
        store, engine = build(path, rally_from)
        self.addCleanup(store.close)  # LIFO: closed before its file is removed.
        return engine

    def run_replay(self, engine, start=DAY + timedelta(hours=8), end=DAY + timedelta(hours=10)):
        structure, liquidity = scouts()
        with structure, liquidity:
            return replay(engine, "XAUUSD", start, end)


class ReplayTests(ReplayCase):
    def test_both_policies_see_identical_setups_and_produce_their_own_plans(self):
        decisions = self.run_replay(self.store(rally_from=DAY + timedelta(hours=12)))
        report = compare(decisions)
        self.assertTrue(report["identical_setups_across_policies"])
        v1, d = report["policies"][POLICY_V1], report["policies"][POLICY_V2_D]
        self.assertGreater(v1["plans_ready"], 0)
        self.assertEqual(v1["plans_ready"], d["plans_ready"])
        self.assertEqual(d["rr_bands"], {"2_TO_3": d["plans_ready"]})
        self.assertEqual(set(d["target_sources"]), {"swing_high_15m"})
        self.assertEqual(set(v1["target_sources"]), {"manufactured_3r"})
        self.assertEqual(d["historical_probability"], "UNAVAILABLE / INSUFFICIENT_EVIDENCE")
        self.assertEqual((d["net_pnl"], d["effective_rr"], d["cost_model"]), ("UNAVAILABLE", "UNAVAILABLE", "NO_COST_MODEL"))
        self.assertIn("not a historical performance", report["claim"])

    def test_outcomes_follow_the_policy_target(self):
        decisions = self.run_replay(self.store(rally_from=DAY + timedelta(hours=10, minutes=30)))
        first = {d.policy: d for d in decisions if d.final_status == "PLAN_READY"}
        self.assertEqual(first[POLICY_V2_D].outcome, "TARGET")
        self.assertAlmostEqual(first[POLICY_V2_D].r_multiple, 2.4)
        self.assertEqual(first[POLICY_V1].outcome, "TARGET")
        self.assertAlmostEqual(first[POLICY_V1].r_multiple, 3.0)
        self.assertLess(first[POLICY_V2_D].exit_at, first[POLICY_V1].exit_at)  # nearer genuine target first

    def test_no_lookahead_decisions_ignore_future_bars(self):
        # The rally starts exactly at the 09:00 slot: the bar starting at 09:00 is still forming at decision
        # time, so a decision at or before 09:00 must not see it (only bars with start + duration <= slot).
        start, end = DAY + timedelta(hours=8), DAY + timedelta(hours=9)
        quiet = self.run_replay(self.store(rally_from=DAY + timedelta(hours=100)), start, end)
        rally = self.run_replay(self.store(rally_from=end), start, end)
        decision_fields = lambda ds: [(d.policy, d.slot, d.setup_id, d.plan_status, d.entry, d.stop, d.target, d.rr)
                                      for d in ds]
        self.assertTrue(quiet)
        self.assertEqual(decision_fields(quiet), decision_fields(rally))  # Same decisions; only outcomes differ.
        self.assertNotEqual([d.outcome for d in quiet], [d.outcome for d in rally])
        snapshot = replay_engine_module.snapshot_at(replay_engine_module.load_frames(self.store(DAY), "XAUUSD"), end)
        for timeframe, frame in snapshot.items():
            self.assertTrue((frame.index + replay_engine_module.TIMEFRAMES[timeframe] <= end).all())

    def test_replay_never_touches_trading_db_or_orders(self):
        engine = self.store(rally_from=DAY + timedelta(hours=12))
        with patch("storage.database.Store.__init__", side_effect=AssertionError("trading DB opened")), \
                patch("execution.paper_broker.PaperBroker.submit_plan", side_effect=AssertionError("order")), \
                patch("execution.paper_broker.PaperBroker.process_next_bar", side_effect=AssertionError("fill")):
            self.assertTrue(self.run_replay(engine))

    def test_replay_store_is_separate(self):
        with self.assertRaises(ReplayStoreError):
            open_replay_store(self.dir / "market_evidence.db")
        with self.assertRaises((ReplayStoreError, EvidenceStoreError)):
            open_replay_store(self.dir / "trading_floor.db")

    def test_simulate_fill_gates_and_stop(self):
        stamps = pd.DatetimeIndex([DAY + i * FIVE for i in range(3)])
        frame = pd.DataFrame({"Open": [2652.0, 2650.0, 2640.0], "High": [2652.0, 2650.0, 2650.0],
                              "Low": [2652.0, 2650.0, 2638.0], "Close": [2652.0, 2650.0, 2639.0]}, index=stamps)
        self.assertEqual(simulate(POLICY_V2_D, frame, DAY, "LONG", 2640.0, 2674.0)[0], "FILL_REJECTED")  # 1.83R
        fill, price, outcome, _, r = simulate(POLICY_V2_D, frame, DAY + FIVE, "LONG", 2640.0, 2674.0)
        self.assertEqual((fill, price, outcome, r), ("FILLED", 2650.0, "STOP", -1.0))
        self.assertEqual(simulate(POLICY_V1, frame, DAY + FIVE, "LONG", 2640.0, 2674.0)[0], "FILL_REJECTED")

    def test_real_scouts_run_end_to_end(self):
        engine = self.store(rally_from=DAY + timedelta(hours=12))
        decisions = replay(engine, "XAUUSD", DAY + timedelta(hours=9), DAY + timedelta(hours=10))
        self.assertTrue(decisions)
        self.assertTrue(compare(decisions)["identical_setups_across_policies"])


if __name__ == "__main__":
    unittest.main()
