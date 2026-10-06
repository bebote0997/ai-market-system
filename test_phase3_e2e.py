"""V2 Phase 3 / P3.2: end-to-end regression of Setup Validator V2 through the real PAPER runtime.

Market frames -> scouts -> Setup Validator V2 -> AI setup-review request -> Trade Planner -> Risk ->
review / execution record / journal. The explanation and evidence references must stay information only:
the same trading-authority fields, the same order, whatever the metadata says."""
import json
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pandas as pd

import agents.setup_validator as validator
import ai.agents.setup_reviewer_ai as setup_reviewer
from ai.provider import DeterministicAIProvider
from core.contracts import AgentMessage
from runtime.config import RuntimeConfig
from runtime.service import OperationalRuntime
from test_demo_runner import instrument, macro_fixture

SLOT = datetime(2026, 1, 15, 13, 30, tzinfo=timezone.utc)
LAST = {"1h": SLOT - timedelta(minutes=90), "15m": SLOT - timedelta(minutes=15), "5m": SLOT - timedelta(minutes=5)}
STEP = {"1h": timedelta(hours=1), "15m": timedelta(minutes=15), "5m": timedelta(minutes=5)}
ANCHOR = datetime(2026, 1, 15, 12, 45, tzinfo=timezone.utc)  # The 15m break bar of "the" opportunity.


class Market:
    """Aligned, closed bars at every slot: the last closed 1h/15m/5m bar and two before it."""

    def load_snapshot(self, symbol, slot):
        frames = {}
        for tf, step in STEP.items():
            last = slot - step if tf != "1h" else slot.replace(minute=0) - step
            index = pd.DatetimeIndex([last - 2 * step, last - step, last])
            frames[tf] = pd.DataFrame({"Open": 100.0, "High": 100.5, "Low": 99.5, "Close": 100.0,
                                       "symbol": symbol, "is_closed": True}, index=index)
        return frames


def scouts(anchor):
    """Deterministic LONG scouts whose nested evidence is the frame's real last bar."""
    def structure(frame, symbol, tf, run_id, at):
        payload = {"bias": "bullish", "structure_state": "bullish",
                   "bos": {"type": "BOS", "direction": "bullish", "broken_level": 99.0,
                           "break_timestamp": pd.Timestamp(anchor)},
                   "retracement": None, "levels": {"support": 90.0, "resistance": 120.0},
                   "evidence": [{"timestamp": frame.index[-1], "type": "closed_bar"}]}
        return AgentMessage("1.0", run_id, at, symbol, tf, "structure", "OK", evidence=(payload,))

    def liquidity(frame, symbol, tf, run_id, at):
        payload = {"sweeps": [{"type": "low"}], "liquidity_above": [], "liquidity_below": [],
                   "evidence": [{"timestamp": frame.index[-1], "closed_bars": len(frame)}]}
        return AgentMessage("1.0", run_id, at, symbol, tf, "liquidity", "OK", evidence=(payload,))
    return patch("floor.orchestrator.analizar_estructura", structure), patch("floor.orchestrator.analizar_liquidez", liquidity)


class Phase3E2E(unittest.TestCase):
    def setUp(self):
        self.path = Path("data/runtime") / f"p3-e2e-{uuid4().hex}.db"
        self.addCleanup(lambda: [self.path.with_name(self.path.name + s).unlink(missing_ok=True)
                                 for s in ("", "-wal", "-shm")])

    def runtime(self, slot, path=None):
        runtime = OperationalRuntime(RuntimeConfig(db_path=path or self.path, enabled_symbols=("XAUUSD",)),
                                     market_provider=Market(), ai_provider=DeterministicAIProvider(),
                                     macro_provider=macro_fixture(), instruments={"XAUUSD": instrument()},
                                     clock=lambda: slot)
        self.addCleanup(runtime.close)
        return runtime

    def cycle(self, slot, anchor=ANCHOR, path=None, extra=()):
        runtime = self.runtime(slot, path)
        structure, liquidity = scouts(anchor)
        requests = []
        original = setup_reviewer.build_request

        def spy(*args, **kwargs):
            requests.append(original(*args, **kwargs))
            return requests[-1]
        with ExitStack() as stack:
            for context in (structure, liquidity, patch.object(setup_reviewer, "build_request", spy), *extra):
                stack.enter_context(context)
            status = runtime.run_cycle("XAUUSD", slot)
        run = runtime.store.latest_run("XAUUSD")
        review = runtime.store.review_report(run["run_id"])
        account, orders, _ = runtime.store.load_paper("paper-main")
        return status, review, orders, requests, runtime

    def test_setup_validator_v2_end_to_end(self):
        # Cycle 1: a VALID_SETUP LONG becomes a plan, an approved risk decision and a PAPER order.
        status, review, orders, requests, runtime = self.cycle(SLOT)
        setup, execution = review["setup"], review["execution"]
        self.assertEqual((status, review["setup_status"], setup["status"], setup["side"]),
                         ("PLAN_READY", "VALID_SETUP", "VALID_SETUP", "LONG"))  # 1.
        self.assertEqual((setup["decision_reason"], setup["failed_checks"], setup["missing_checks"],
                          setup["not_evaluated_checks"]), ("all_checks_passed", [], [], []))  # 2.
        self.assertEqual(setup["check_details"]["invalidation_level"], {"level": 90.0, "source": "15m support"})
        bars = [(r["source"], r["timeframe"], r["ref_state"], r["bar_start"]) for r in setup["evidence_refs"][:6]]
        self.assertEqual(bars, [(s, tf, "VALID", LAST[tf].isoformat())
                                for s in ("structure", "liquidity") for tf in ("1h", "15m", "5m")])  # 3.
        setup_id = setup["setup_id"]
        self.assertEqual((execution["execution_status"], execution["setup_id"]), ("SUBMITTED", setup_id))  # 11.
        order = orders[execution["order_id"]]
        self.assertEqual((order.run_id, order.side, order.planned_entry, order.stop, order.target, order.quantity),
                         (review["run_id"], "LONG", 100.0, 90.0, 130.0, 10.0))  # 6.
        self.assertEqual(review["risk_decision"]["status"], "APPROVED")
        decision = runtime.store.journal(run_id=review["run_id"], event="EXECUTION_DECISION")
        self.assertEqual([json.loads(d["payload"])["setup_id"] for d in decision], [setup_id])
        self.assertEqual(requests[0].deterministic_evidence[0],
                         {"evidence_id": "setup_status", "status": "VALID_SETUP", "side": "LONG"})  # AI authority.

        # Cycle 2 (retry/repeated cycle of the same opportunity): same setup_id; no second order.
        _, review2, orders2, _, _ = self.cycle(SLOT + timedelta(minutes=15))
        self.assertEqual((review2["setup"]["setup_id"], review2["execution"]["setup_id"]), (setup_id, setup_id))  # 9.
        self.assertEqual(len(orders2), 1)

        # Cycle 3: a materially new opportunity (new 15m break bar) gets a new identity.
        _, review3, _, _, _ = self.cycle(SLOT + timedelta(minutes=30), anchor=ANCHOR + timedelta(minutes=30))
        self.assertNotEqual(review3["setup"]["setup_id"], setup_id)  # 10.
        self.assertIsNotNone(review3["setup"]["setup_id"])

    def test_metadata_cannot_alter_the_trade(self):
        """7./8. A contradictory explanation and all-INVALID references change nothing that trades."""
        _, base, base_orders, base_requests, _ = self.cycle(SLOT)
        other = Path("data/runtime") / f"p3-e2e-{uuid4().hex}.db"
        self.addCleanup(lambda: [other.with_name(other.name + s).unlink(missing_ok=True) for s in ("", "-wal", "-shm")])
        lie = {"status": "NO_SETUP", "decision_reason": "fabricated", "failed_checks": validator.CHECKS,
               "setup_id": "not-an-id"}
        refs = (tuple({"source": "structure", "ref_state": "INVALID"} for _ in range(6)),)
        _, odd, odd_orders, odd_requests, _ = self.cycle(SLOT, path=other, extra=(
            patch.object(validator, "explain", lambda *a: dict(lie)),
            patch.object(validator, "_evidence_refs", lambda *a: refs[0])))
        self.assertEqual(odd["setup"]["decision_reason"], "fabricated")  # The metadata really changed...
        strip = lambda o: (o.side, o.quantity, o.planned_entry, o.stop, o.target, o.status)
        self.assertEqual([strip(o) for o in odd_orders.values()], [strip(o) for o in base_orders.values()])
        self.assertEqual((odd["setup_status"], odd["final_status"], odd["risk_decision"]),
                         (base["setup_status"], base["final_status"], base["risk_decision"]))
        self.assertEqual(odd["execution"]["execution_status"], base["execution"]["execution_status"])
        # ...and the audit setup_id (execution identity) is still the real one, not the explanation's.
        self.assertEqual(odd["execution"]["setup_id"], base["execution"]["setup_id"])
        self.assertEqual(odd_requests[0].deterministic_evidence, base_requests[0].deterministic_evidence)

    def test_scope_real_disabled(self):  # 12.
        config = RuntimeConfig()
        self.assertIs(config.v2_position_catch_up, False)
        self.assertNotIn("NAS100", config.enabled_symbols)
        self.assertIn('"paper_mode": True', (Path(__file__).resolve().parent / "runtime" / "config.py")
                      .read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
