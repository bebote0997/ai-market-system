"""Contract tests: PaperBrokerAdapter is a thin delegate and PaperBroker stays untouched (broker skeleton)."""
from decimal import Decimal
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch

from execution.broker_port import (ACCEPTED, REJECTED, BrokerBalance, BrokerHealth, BrokerPort, BrokerPosition,
                                   OrderPlan, RiskApproval)
from execution.paper_broker import PaperBroker
from execution.paper_broker_adapter import PaperBrokerAdapter
import test_execution

ROOT = Path(__file__).resolve().parent


def order_plan(run_id="run-1", side="LONG", quantity="1.0", entry="100.0", stop="90.0", target="130.0"):
    return OrderPlan("TEST", side, Decimal(quantity), Decimal(entry), Decimal(stop), Decimal(target),
                     client_id=f"cid-{run_id}", source_run_id=run_id)


def approval(plan, status="APPROVED", **changes):
    values = {"status": status, "run_id": plan.source_run_id, "symbol": plan.symbol, "side": plan.side,
              "quantity": plan.quantity, "entry": plan.planned_entry, "stop": plan.stop, "target": plan.target}
    values.update(changes)
    return RiskApproval(**values)


class FakeContext:
    """Paper-only context injected in the adapter constructor (floor reports, clock, prices)."""

    def __init__(self, fx):
        self.fx, self.reports, self.prices, self.now = fx, {}, {}, fx.t(0)

    def floor_report_for(self, plan):
        return self.reports.get(plan.source_run_id)

    def clock(self):
        return self.now

    def price_for(self, symbol):
        return self.prices.get(symbol)


class PaperBrokerAdapterContractTests(unittest.TestCase):
    def setUp(self):
        self.fx = test_execution.TestExecution()
        self.account = self.fx.account()
        self.paper = PaperBroker(self.account, self.fx.instrument())
        self.context = FakeContext(self.fx)
        self.context.reports["run-1"] = self.fx.report()
        self.adapter = PaperBrokerAdapter(self.paper, floor_report_for=self.context.floor_report_for,
                                          clock=self.context.clock, price_for=self.context.price_for)
        self.port: BrokerPort = self.adapter

    def test_satisfies_the_port_without_exposing_paper_broker(self):
        self.assertIsInstance(self.adapter, BrokerPort)
        self.assertFalse(hasattr(self.adapter, "broker"))
        with self.assertRaises(TypeError):
            PaperBrokerAdapter(self.paper, floor_report_for=None, clock=self.context.clock,
                               price_for=self.context.price_for)

    def test_submit_delegates_to_paper_broker(self):
        report = self.context.reports["run-1"]
        plan = order_plan()
        with patch.object(PaperBroker, "submit_plan", autospec=True, side_effect=PaperBroker.submit_plan) as spy:
            result = self.port.submit_plan(plan, approval(plan))
        spy.assert_called_once_with(self.paper, report, {}, self.fx.t(0))
        self.assertEqual(result.status, ACCEPTED)
        self.assertEqual(result.broker_ref, next(iter(self.paper.orders)))
        again = self.port.submit_plan(plan, approval(plan))
        self.assertEqual(again.broker_ref, result.broker_ref)  # PaperBroker's own run_id idempotency
        self.assertEqual(len(self.paper.orders), 1)

    def test_repeated_run_id_with_different_plan_is_rejected(self):
        plan = order_plan()
        first = self.port.submit_plan(plan, approval(plan))
        self.assertEqual(first.status, ACCEPTED)
        self.context.reports["run-1"].risk_decision.quantity = 2.0
        bigger = order_plan(quantity="2.0")
        conflict = self.port.submit_plan(bigger, approval(bigger))
        self.assertEqual((conflict.status, conflict.reason, conflict.broker_ref),
                         (REJECTED, "paper_order_conflicts_with_plan", None))
        self.assertEqual(len(self.paper.orders), 1)
        self.assertEqual(next(iter(self.paper.orders.values())).quantity, 1.0)

    def test_submit_without_approval_or_report_never_creates_orders(self):
        plan = order_plan()
        with patch.object(PaperBroker, "submit_plan", autospec=True, side_effect=PaperBroker.submit_plan) as spy:
            for bad, reason in ((None, "risk_approval_missing"), (object(), "risk_approval_missing"),
                                (approval(plan, status="REJECTED"), "risk_not_approved"),
                                (approval(plan, stop=Decimal("95")), "risk_approval_mismatch")):
                with self.subTest(reason=reason):
                    self.assertEqual(self.port.submit_plan(plan, bad).reason, reason)
            mismatch = order_plan(stop="95.0")
            self.assertEqual(self.port.submit_plan(mismatch, approval(mismatch)).reason,
                             "plan_does_not_match_risk_decision")
            other = order_plan(run_id="run-2")
            self.assertEqual(self.port.submit_plan(other, approval(other)).reason, "paper_floor_report_unavailable")
            spy.assert_not_called()
            self.context.reports["run-1"].risk_decision.status = "REJECTED"
            refused = self.port.submit_plan(plan, approval(plan))
        self.assertEqual((refused.status, refused.reason), (REJECTED, "paper_broker_refused"))
        self.assertEqual(self.paper.orders, {})

    def test_successful_operations_through_the_port_only(self):
        plan = order_plan()
        self.assertEqual(self.port.submit_plan(plan, approval(plan)).status, ACCEPTED)
        self.paper.process_next_bar(next(iter(self.paper.orders.values())), self.fx.bar(self.fx.t(1)))  # fill
        positions = self.port.get_positions()
        self.assertEqual(positions, [BrokerPosition("TEST", "TEST", "LONG", Decimal("1.0"), Decimal("100"),
                                                    Decimal("90.0"), Decimal("130.0"))])
        balance = self.port.get_balance()
        self.assertIsInstance(balance, BrokerBalance)
        self.assertEqual(balance.equity, Decimal(str(self.account.equity)))
        self.assertEqual(self.port.modify_stop("TEST", Decimal("100"), client_id="m1").status, REJECTED)
        self.assertEqual(self.port.close_market("TEST", reason="X", client_id="c0").reason, "paper_price_unavailable")
        self.context.prices["TEST"], self.context.now = Decimal("105"), self.fx.t(2)
        with patch.object(PaperBroker, "close_market", autospec=True, side_effect=PaperBroker.close_market) as spy:
            closed = self.port.close_market("TEST", reason="MANUAL", client_id="c1")
        spy.assert_called_once_with(self.paper, "TEST", 105.0, self.fx.t(2), exit_reason="MANUAL",
                                    decision_run_id="c1")
        self.assertEqual(closed.status, ACCEPTED)
        self.assertEqual(self.port.get_positions(), [])
        self.assertEqual(self.port.close_market("TEST", reason="X", client_id="c2").reason, "no_open_position")
        health = self.port.health()
        self.assertIsInstance(health, BrokerHealth)
        self.assertEqual((health.mode, health.connected), ("paper", False))

    def test_adapter_is_deletable_nobody_else_imports_it(self):
        offenders = []
        for path in ROOT.rglob("*.py"):
            rel = path.relative_to(ROOT)
            if rel.parts[0] in {".git", ".venv", "venv"} or rel.name in {"paper_broker_adapter.py",
                                                                         "test_broker_paper_adapter.py"}:
                continue
            if "paper_broker_adapter" in path.read_text(encoding="utf-8"):
                offenders.append(rel.as_posix())
        # test_broker_wiring lists it as a forbidden import; test_safety_invariants only allowlists the path string.
        self.assertEqual(sorted(offenders), ["test_broker_wiring.py", "test_safety_invariants.py"])


class PaperBrokerUntouchedTests(unittest.TestCase):
    def test_paper_broker_api_is_unchanged(self):
        for name in ("submit_plan", "close_market", "process_next_bar"):
            self.assertEqual(getattr(PaperBroker, name).__module__, "execution.paper_broker")
        self.assertFalse(hasattr(PaperBroker, "modify_stop"))
        self.assertFalse(issubclass(PaperBrokerAdapter, PaperBroker))

    @unittest.skipUnless(shutil.which("git") and (ROOT / ".git").exists(), "git checkout required")
    def test_paper_broker_file_has_no_working_tree_change(self):
        out = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", "execution/paper_broker.py"], cwd=ROOT,
                             capture_output=True, timeout=60)
        self.assertEqual(out.returncode, 0)


if __name__ == "__main__":
    unittest.main()
