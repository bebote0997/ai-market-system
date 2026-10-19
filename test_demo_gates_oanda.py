"""DEC-DEMO-GATE-1: while broker-demo routing is ON the PAPER Risk V2 5% drawdown gate uses ONLY the OANDA demo account
and the daily Slack summary reports OANDA demo figures. Routing OFF: unchanged. No network; synthetic ids only."""
import json
import logging
import os
import tempfile
import threading
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from execution import opposite_policy
from execution.contracts import PaperOrder
from execution.paper_broker import PaperBroker
from execution.broker_demo_router import BrokerDemoRouter
from execution.demo_epoch import DEFAULT_DEMO_START_EQUITY, START_EQUITY_ENV, demo_start_equity
from execution.risk_engine_v2 import drawdown_basis_scope, evaluate
from execution.risk_reservation import prepare_reservation
from runtime.config import RuntimeConfig
from runtime.notifications import NotificationEvent, SlackNotificationSink, oanda_demo_summary_text
from runtime.scheduler import slot_key
from runtime.service import OperationalRuntime
from test_dec14_opposite_policy import reports, snapshot
from test_demo_runner import T, macro_fixture
from test_phase5_risk_engine import INS, XAU_LONG, account, f3_plan, position, report

START = Decimal("99999.977")
FLAT = account()  # PAPER flat
PAPER_DOWN = account(9000.0, realized=-1000.0)  # PAPER 10% down


def basis(current, starting=START):
    return {"source": "OANDA_DEMO", "current": current, "starting": starting}


class StartEquityTests(unittest.TestCase):
    def test_default_and_override(self):
        self.assertEqual(DEFAULT_DEMO_START_EQUITY, Decimal("99999.977"))
        self.assertEqual(demo_start_equity({}), DEFAULT_DEMO_START_EQUITY)
        self.assertEqual(demo_start_equity({START_EQUITY_ENV: " 50000.5 "}), Decimal("50000.5"))
        for bad in ("", "  ", "abc", "0", "-1", "NaN", "Infinity", "-Infinity"):
            with self.subTest(bad=bad):
                self.assertEqual(demo_start_equity({START_EQUITY_ENV: bad}), DEFAULT_DEMO_START_EQUITY)

    def test_never_raises(self):
        class Broken:
            def get(self, _key):
                raise RuntimeError("env")
        self.assertEqual(demo_start_equity(Broken()), DEFAULT_DEMO_START_EQUITY)
        with patch.dict(os.environ, {START_EQUITY_ENV: "123.4"}):
            self.assertEqual(demo_start_equity(), Decimal("123.4"))


class RiskEngineBasisTests(unittest.TestCase):
    plan = f3_plan(*XAU_LONG)

    def run_(self, held, dd_basis=None):
        return evaluate(self.plan, held, {}, INS["XAUUSD"], drawdown_basis=dd_basis)

    def test_none_is_unchanged(self):
        for held in (FLAT, PAPER_DOWN, account(9500.01, realized=-499.99)):
            with self.subTest(equity=held.equity):
                plain = evaluate(self.plan, held, {}, INS["XAUUSD"])
                explicit = self.run_(held, None)
                self.assertEqual((plain.decision, plain.record), (explicit.decision, explicit.record))
                self.assertFalse({"drawdown_source", "drawdown_current_equity"} & set(explicit.record))
        self.assertEqual(self.run_(PAPER_DOWN).decision.reason, "ACCOUNT_DRAWDOWN_LIMIT")

    def test_oanda_below_limit_passes_even_when_paper_is_down(self):
        result = self.run_(PAPER_DOWN, basis(Decimal("96000")))
        self.assertEqual(result.decision.status, "APPROVED")
        record = result.record
        self.assertEqual((record["drawdown_source"], record["drawdown_current_equity"],
                          record["drawdown_starting_equity"]), ("OANDA_DEMO", "96000", "99999.977"))
        # Sizing (gates 3/5) still uses the PAPER account.
        self.assertEqual(record["conservative_equity"], "9000")
        self.assertEqual(result.decision.equity_at_decision, 9000.0)
        paper_sized = evaluate(self.plan, account(9000.0, starting=9000.0), {}, INS["XAUUSD"])  # same 9000 basis
        self.assertEqual(result.decision.quantity, paper_sized.decision.quantity)

    def test_oanda_at_or_over_limit_rejects_even_when_paper_is_flat(self):
        limit = START * Decimal("0.95")
        for current in (limit, limit - Decimal("0.001"), Decimal("80000"), Fraction(90000)):
            with self.subTest(current=current):
                result = self.run_(FLAT, basis(current))
                self.assertEqual(result.decision.reason, "ACCOUNT_DRAWDOWN_LIMIT")
                self.assertEqual(result.record["drawdown_source"], "OANDA_DEMO")
        self.assertEqual(self.run_(FLAT, basis(limit + Decimal("0.001"))).decision.status, "APPROVED")
        self.assertEqual(self.run_(FLAT, basis(Decimal("120000"))).decision.status, "APPROVED")  # profit

    def test_error_or_invalid_basis_fails_closed(self):
        for bad in ({"source": "OANDA_DEMO", "error": "nav_unavailable:BrokerUnavailable"},
                    {"source": "OANDA_DEMO", "current": None, "starting": START},
                    {"source": "OANDA_DEMO", "current": 1.5, "starting": START},  # float: not an exact amount
                    {"source": "OANDA_DEMO", "current": Decimal("NaN"), "starting": START},
                    {"source": "OANDA_DEMO", "current": Decimal("1"), "starting": Decimal("0")},
                    {"source": "PAPER", "current": Decimal("1"), "starting": START},
                    {"current": START, "starting": START}, "OANDA_DEMO"):
            with self.subTest(bad=bad):
                result = self.run_(FLAT, bad)
                self.assertEqual((result.decision.status, result.decision.reason),
                                 ("REJECTED", "ACCOUNT_DRAWDOWN_UNAVAILABLE"))
                self.assertIn("drawdown_error", result.record)

    def test_scope_reaches_frozen_prepare_reservation_and_resets(self):
        rejected = prepare_reservation(report(self.plan), INS["XAUUSD"], PAPER_DOWN, {}, {}, as_of=T)
        self.assertEqual(rejected.reason, "ACCOUNT_DRAWDOWN_LIMIT")
        with drawdown_basis_scope(basis(Decimal("99000"))):
            approved = prepare_reservation(report(self.plan), INS["XAUUSD"], account(9000.0, realized=-1000.0), {}, {},
                                           as_of=T)
        self.assertEqual(approved.status, "APPROVED")
        self.assertEqual(approved.record["drawdown_source"], "OANDA_DEMO")
        self.assertEqual(self.run_(PAPER_DOWN).decision.reason, "ACCOUNT_DRAWDOWN_LIMIT")  # scope reset on exit
        with drawdown_basis_scope(None):
            self.assertEqual(self.run_(PAPER_DOWN).decision.reason, "ACCOUNT_DRAWDOWN_LIMIT")

    def test_scope_is_per_thread(self):
        seen = []
        with drawdown_basis_scope(basis(Decimal("99000"))):
            worker = threading.Thread(target=lambda: seen.append(self.run_(PAPER_DOWN).decision.reason))
            worker.start()
            worker.join()
        self.assertEqual(seen, ["ACCOUNT_DRAWDOWN_LIMIT"])


class OppositePolicyBasisTests(unittest.TestCase):
    """End to end through the real opposite policy, frozen prepare_reservation and SQLite (no network)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        config = RuntimeConfig(db_path=Path(self.tmp.name) / "paper.db", v2_opposite_setup_policy="STRUCTURE_CHANGE",
                               market_provider_mode="none")
        self.rt = OperationalRuntime(config, instruments=INS, clock=lambda: T, broker_router=None)
        self.addCleanup(self.rt.close)
        self.key = slot_key("XAUUSD", T, 15)
        self.assertTrue(self.rt.store.claim_slot(self.key, "XAUUSD", T, T, run_id="reverse"))

    def seed_paper(self, realized):
        held = account(equity=10000 + realized, realized=realized)
        held.open_positions["XAUUSD"] = replace(position("XAUUSD", "LONG", 2650., 2600., 2900., 1.))
        broker = PaperBroker(held, INS["XAUUSD"])
        broker.orders["ord-XAUUSD"] = PaperOrder("1.0", "ord-XAUUSD", "run-XAUUSD", "XAUUSD", "LONG", 1., 2650.,
                                                 2600., 2900., 1., 10000., 0., T - timedelta(hours=1),
                                                 status="FILLED")
        self.rt.store.save_paper(broker)

    def execute(self, **kwargs):
        return opposite_policy.execute(self.rt, reports(), snapshot(), key=self.key, at=T, ai_allowed=True, **kwargs)

    def risk_rows(self):
        return [json.loads(r[0]) for r in self.rt.store.db.execute(
            "SELECT payload FROM journal WHERE event_type='RISK_V2_DECISION'")]

    def test_paper_down_oanda_ok_submits(self):
        self.seed_paper(-700.)
        outcome = self.execute(drawdown_basis=basis(Decimal("99500")))
        self.assertEqual(outcome.status, "SUBMITTED")
        self.assertEqual(self.risk_rows()[0]["drawdown_source"], "OANDA_DEMO")

    def test_paper_flat_oanda_over_limit_rejects(self):
        self.seed_paper(0.)
        outcome = self.execute(drawdown_basis=basis(Decimal("94000")))
        self.assertEqual(outcome.reason, "RISK_REJECTED_AFTER_CLOSE")
        self.assertEqual(self.risk_rows()[0]["risk_reason"], "ACCOUNT_DRAWDOWN_LIMIT")

    def test_error_basis_rejects_unavailable(self):
        self.seed_paper(0.)
        outcome = self.execute(drawdown_basis={"source": "OANDA_DEMO", "error": "nav_unavailable:X"})
        self.assertEqual(outcome.reason, "RISK_REJECTED_AFTER_CLOSE")
        self.assertEqual(self.risk_rows()[0]["risk_reason"], "ACCOUNT_DRAWDOWN_UNAVAILABLE")

    def test_without_basis_paper_gate_unchanged(self):
        self.seed_paper(-700.)
        self.assertEqual(self.execute().reason, "RISK_REJECTED_AFTER_CLOSE")
        self.assertEqual(self.risk_rows()[0]["risk_reason"], "ACCOUNT_DRAWDOWN_LIMIT")
        self.assertNotIn("drawdown_source", self.risk_rows()[0])


class ServiceWiringTests(unittest.TestCase):
    """The runtime computes the basis once per cycle only with a router and hands it to the opposite policy."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def cycle(self, name, router):
        config = RuntimeConfig(db_path=Path(self.tmp.name) / f"{name}.db", v2_opposite_setup_policy="STRUCTURE_CHANGE",
                               market_provider_mode="none")
        rt = OperationalRuntime(config, instruments=INS, clock=lambda: T, broker_router=router)
        self.addCleanup(rt.close)
        held = account()
        held.open_positions["XAUUSD"] = position("XAUUSD", "LONG", 2650., 2600., 2900., 1.)
        broker = PaperBroker(held, INS["XAUUSD"])
        broker.orders["ord-XAUUSD"] = PaperOrder("1.0", "ord-XAUUSD", "run-XAUUSD", "XAUUSD", "LONG", 1., 2650., 2600.,
                                                 2900., 1., 10000., 0., T - timedelta(hours=1), status="FILLED")
        rt.store.save_paper(broker)
        snap = snapshot()
        rt.macro_provider = macro_fixture()
        rt.market_provider = type("Provider", (), {"load_snapshot": lambda _, symbol, at: snap})()

        def scout(kind):
            def analyze(frame, symbol, tf, run_id, at):
                built = reports(run_id=run_id)
                return (built.structure_reports if kind == "structure" else built.liquidity_reports)[tf]
            return analyze
        with patch("floor.orchestrator.analizar_estructura", scout("structure")), \
                patch("floor.orchestrator.analizar_liquidez", scout("liquidity")), \
                patch("execution.opposite_policy.execute",
                      return_value=opposite_policy.Outcome("SKIPPED", "TEST")) as execute:
            rt.run_cycle("XAUUSD", T)
        return execute

    def test_no_router_no_basis(self):
        execute = self.cycle("off", None)
        execute.assert_called_once()
        self.assertNotIn("drawdown_basis", execute.call_args.kwargs)

    def test_router_basis_passed_once_per_cycle(self):
        given = basis(Decimal("99000"))
        router = SimpleNamespace(drawdown_basis=MagicMock(return_value=given), on_cycle=MagicMock())
        execute = self.cycle("on", router)
        router.drawdown_basis.assert_called_once_with()
        self.assertIs(execute.call_args.kwargs["drawdown_basis"], given)

    def test_raising_router_yields_error_basis(self):
        router = SimpleNamespace(drawdown_basis=MagicMock(side_effect=RuntimeError("down")), on_cycle=MagicMock())
        execute = self.cycle("broken", router)
        self.assertEqual(execute.call_args.kwargs["drawdown_basis"],
                         {"source": "OANDA_DEMO", "error": "basis_failed:RuntimeError"})


class RouterBasisTests(unittest.TestCase):
    class Broker:
        """Only get_balance is allowed: any other attribute (submit/modify/close/...) fails the test."""

        def __init__(self, equity=None, error=None):
            self.equity, self.error, self.calls = equity, error, []

        def get_balance(self):
            self.calls.append("get_balance")
            if self.error is not None:
                raise self.error
            return SimpleNamespace(balance=self.equity, equity=self.equity, last_transaction_id="1")

        def __getattr__(self, name):
            raise AssertionError(f"router.drawdown_basis touched broker.{name}")

    def router(self, broker, env=None):
        store = MagicMock()
        store.flag.return_value = None
        return BrokerDemoRouter(broker, store, registry=MagicMock(), dry_run=False, max_units=1000,
                                env={} if env is None else env), store

    def test_ok_basis_and_log(self):
        broker = self.Broker(Decimal("98000.00"))
        router, store = self.router(broker)
        with self.assertLogs("ai_floor.broker.demo_router", logging.INFO) as logs:
            got = router.drawdown_basis()
        self.assertEqual(got, {"source": "OANDA_DEMO", "current": Decimal("98000.00"), "starting": START})
        self.assertEqual(broker.calls, ["get_balance"])
        self.assertEqual(logs.output, ["INFO:ai_floor.broker.demo_router:component=broker_demo "
                                       "event=PAPER_GATE_BASIS source=OANDA_DEMO drawdown_pct=2.00 status=ok"])
        store.event.assert_not_called()  # nothing written
        override, _ = self.router(self.Broker(Decimal("100")), {START_EQUITY_ENV: "200"})
        self.assertEqual(override.drawdown_basis()["starting"], Decimal("200"))

    def test_never_raises(self):
        for broker, code in ((self.Broker(error=RuntimeError("timeout")), "nav_unavailable:RuntimeError"),
                             (self.Broker(None), "nav_invalid"), (self.Broker("garbage"), "nav_invalid")):
            with self.subTest(code=code), self.assertLogs("ai_floor.broker.demo_router", logging.INFO) as logs:
                router, store = self.router(broker)
                self.assertEqual(router.drawdown_basis(), {"source": "OANDA_DEMO", "error": code})
                self.assertIn("drawdown_pct=NA status=error", logs.output[-1])
                store.event.assert_not_called()


def event(kind="DAILY_SUMMARY"):
    return NotificationEvent("1.0", "evt-1", "run-1", "2026-10-20T21:00:00+00:00", "INFO", kind, None, (), (), None,
                             (), (), (), daily_summary={"paper_total_pnl": 1234.5, "account": "paper-main"})


SNAPSHOT = {"stats": {"trades": 3, "wins": 2, "total_pnl": 40.0, "today_pnl": -5.5, "avg_r": 0.4567,
                      "avg_r_reason": None},
            "nav": 100040.0, "balance": 100039.98, "currency": "USD", "capital": {"start": 99999.98, "change": 40.02},
            "open_rows": [{"trade_id": "1"}]}


class SlackSummaryTests(unittest.TestCase):
    def send(self, env, **patches):
        sent = []
        sink = SlackNotificationSink(webhook_url="https://hooks.slack.com/services/T/B/x",
                                     transport=lambda url, payload, timeout: sent.append(payload["text"]))
        with patch.dict(os.environ, env), \
                patch("execution.demo_dashboard.demo_snapshot", **patches.get("snapshot", {"return_value": SNAPSHOT})), \
                patch("execution.demo_dashboard.demo_progress",
                      return_value=(None, None, patches.get("day", 2))) as progress:
            sink.deliver(event())
        return sent[0], progress

    def test_routing_off_unchanged(self):
        text, progress = self.send({"AI_FLOOR_BROKER_DEMO_ROUTING": "0"})
        self.assertEqual(text, "AI Market System PAPER DAILY_SUMMARY\n" +
                         json.dumps(event().daily_summary, sort_keys=True, ensure_ascii=False))
        progress.assert_not_called()

    def test_routing_on_oanda_figures(self):
        text, _ = self.send({"AI_FLOOR_BROKER_DEMO_ROUTING": "1"})
        self.assertEqual(text.splitlines(), [
            "AI Market System OANDA DEMO DAILY_SUMMARY", "Día 2 de 30", "NAV: 100,040.00 USD",
            "P&amp;L desde el inicio del demo: +40.02 USD", "Operaciones cerradas: 3 (ganadoras: 2)",
            "R medio: 0.46", "P&amp;L de hoy (ET): -5.50 USD", "Operaciones abiertas: 1"])
        self.assertNotIn("1234", text)
        self.assertNotIn("paper", text.lower())

    def test_routing_on_read_failure_never_paper(self):
        empty = {"stats": None, "nav": None, "currency": None, "capital": {"start": None, "change": None},
                 "open_rows": []}
        for patches in ({"snapshot": {"return_value": empty}}, {"snapshot": {"side_effect": RuntimeError("x")}}):
            with self.subTest(patches=patches):
                text, _ = self.send({"AI_FLOOR_BROKER_DEMO_ROUTING": "1"}, **patches)
                self.assertEqual(text, "AI Market System OANDA DEMO DAILY_SUMMARY\nOANDA demo: sin datos")
        nav_only = {**empty, "nav": 99990.0, "currency": "USD"}
        text, _ = self.send({"AI_FLOOR_BROKER_DEMO_ROUTING": "1"}, snapshot={"return_value": nav_only}, day=None)
        self.assertEqual(text.splitlines()[1:], ["Demo OANDA: aún sin operaciones del demo", "NAV: 99,990.00 USD",
                                                 "P&amp;L desde el inicio del demo: sin datos",
                                                 "OANDA demo: sin datos de operaciones"])

    def test_summary_text_uses_event_time(self):
        with patch("execution.demo_dashboard.demo_snapshot", return_value=SNAPSHOT) as snap, \
                patch("execution.demo_dashboard.demo_progress", return_value=(None, None, 1)):
            oanda_demo_summary_text("2026-10-20T21:00:00Z")
        self.assertEqual(snap.call_args.args[0], datetime(2026, 10, 20, 21, 0, tzinfo=timezone.utc))


if __name__ == "__main__":
    unittest.main()
