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
from runtime.notifications import NotificationEvent, SlackNotificationSink, oanda_demo_summary_text, paper_summary_text
from runtime.trade_alerts import payload_text
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


def closed_row(exited, pnl, r, reason=None):
    return {"exited_at": datetime.fromisoformat(exited).replace(tzinfo=timezone.utc), "net_pnl": pnl,
            "realized_r": r, "realized_r_reason": reason}


SNAPSHOT = {"stats": {"trades": 3, "wins": 2, "total_pnl": 40.0, "today_pnl": -5.5, "avg_r": 0.4567,
                      "avg_r_reason": None},
            "nav": 100040.0, "balance": 100039.98, "currency": "USD", "capital": {"start": 99999.98, "change": 40.02},
            "open_rows": [{"trade_id": "1"}],
            # Trading day 2026-10-20 = [2026-10-19 21:00Z, 2026-10-20 21:00Z) (17:00 -> 17:00 EDT).
            "closed_rows": [closed_row("2026-10-19T20:59:59", 45.5, 1.0),
                            closed_row("2026-10-19T21:00:00", 3.0, 0.5),
                            closed_row("2026-10-20T03:30:00", 1.0, 0.25),  # after UTC midnight
                            closed_row("2026-10-20T20:59:00", -9.5, -1.0),
                            closed_row("2026-10-20T21:00:00", 7.0, 2.0)]}


SENT_AT = datetime(2026, 10, 21, 14, 0, tzinfo=timezone.utc)  # 10:00 EDT, the morning after the summarized day


class SlackSummaryTests(unittest.TestCase):
    def send(self, env, **patches):
        sent = []
        sink = SlackNotificationSink(webhook_url="https://hooks.slack.com/services/T/B/x",
                                     transport=lambda url, payload, timeout: sent.append(payload_text(payload)),
                                     clock=lambda: SENT_AT)
        with patch.dict(os.environ, env), \
                patch("execution.demo_dashboard.demo_snapshot", **patches.get("snapshot", {"return_value": SNAPSHOT})), \
                patch("execution.demo_dashboard.demo_progress",
                      return_value=(None, None, patches.get("day", 2))) as progress:
            sink.deliver(event())
        return sent[0], progress

    def test_routing_off_unchanged(self):
        text, progress = self.send({"AI_FLOOR_BROKER_DEMO_ROUTING": "0"})
        self.assertEqual(text, paper_summary_text(event().daily_summary))
        self.assertNotIn("{", text)  # DEC-ALERTS-REDESIGN-1: never a JSON dump
        progress.assert_not_called()

    def test_routing_on_oanda_figures(self):
        text, _ = self.send({"AI_FLOOR_BROKER_DEMO_ROUTING": "1"})
        self.assertEqual(text.splitlines(), [
            "📊 Resumen diario · OANDA Demo · 2026-10-20",
            "NAV (a las 10:00 ET): 100,040.00 USD", "Balance (a las 10:00 ET): 100,039.98 USD",
            "P&amp;L desde el inicio del demo (a las 10:00 ET): +40.02 USD",
            "P&amp;L del día de trading: -5.50 USD", "Operaciones cerradas en el día: 3 (ganadoras: 2)",
            "R medio del día: -0.08", "Operaciones abiertas (a las 10:00 ET): 1", "OANDA Demo · Día 2 de 30"])
        self.assertNotIn("1234", text)
        self.assertNotIn("paper", text.lower())

    def test_routing_on_read_failure_never_paper(self):
        empty = {"stats": None, "nav": None, "currency": None, "capital": {"start": None, "change": None},
                 "open_rows": []}
        for patches in ({"snapshot": {"return_value": empty}}, {"snapshot": {"side_effect": RuntimeError("x")}}):
            with self.subTest(patches=patches):
                text, _ = self.send({"AI_FLOOR_BROKER_DEMO_ROUTING": "1"}, **patches)
                self.assertEqual(text.splitlines()[1:], ["OANDA demo: sin datos", "OANDA Demo · Día sin datos"])
        nav_only = {**empty, "nav": 99990.0, "currency": "USD"}
        text, _ = self.send({"AI_FLOOR_BROKER_DEMO_ROUTING": "1"}, snapshot={"return_value": nav_only}, day=None)
        self.assertEqual(text.splitlines()[1:], ["Demo OANDA: aún sin operaciones del demo",
                                                 "NAV (a las 10:00 ET): 99,990.00 USD",
                                                 "Balance (a las 10:00 ET): sin datos",
                                                 "P&amp;L desde el inicio del demo (a las 10:00 ET): sin datos",
                                                 "OANDA demo: sin datos de operaciones",
                                                 "OANDA Demo · Día sin datos"])

    def test_send_log_line_names_the_source_never_the_webhook(self):
        for routing, source in (("1", "OANDA_DEMO"), ("0", "PAPER")):
            with self.subTest(routing=routing), self.assertLogs("runtime.notifications", "INFO") as logs:
                self.send({"AI_FLOOR_BROKER_DEMO_ROUTING": routing})
            self.assertEqual([r.getMessage() for r in logs.records],
                             [f"component=notifications event=DAILY_SUMMARY_SENT source={source} date=-"])
            self.assertNotIn("hooks.slack.com", logs.output[0])

    def test_failed_send_is_logged_with_the_safe_code_and_reraised(self):
        from runtime.notifications import SlackNotificationError

        def fail(url, payload, timeout):
            raise SlackNotificationError("RATE_LIMITED")
        sink = SlackNotificationSink(webhook_url="https://hooks.slack.com/services/T/B/secret", transport=fail)
        summary = NotificationEvent("1.0", "evt-1", "daily:2026-10-19", "2026-10-20T00:00:30+00:00", "INFO",
                                    "DAILY_SUMMARY", None, (), (), None, (), (), (),
                                    daily_summary={"date_ny": "2026-10-19"})
        with patch.dict(os.environ, {"AI_FLOOR_BROKER_DEMO_ROUTING": "1"}), \
                patch("execution.demo_dashboard.demo_snapshot", return_value=SNAPSHOT), \
                patch("execution.demo_dashboard.demo_progress", return_value=(None, None, 2)), \
                self.assertLogs("runtime.notifications", "WARNING") as logs, \
                self.assertRaises(SlackNotificationError):
            sink.deliver(summary)
        self.assertEqual(logs.records[0].getMessage(), "component=notifications event=DAILY_SUMMARY_FAILED "
                                                       "source=OANDA_DEMO date=2026-10-19 error_type=RATE_LIMITED")
        self.assertNotIn("secret", logs.output[0])

    def test_multi_day_catch_up_snapshot_at_send_time_and_aggregates_per_trading_day(self):
        # Two missed days sent together the next morning: current figures (NAV, balance, open trades, day counter)
        # are read and labelled at the send time; each day's aggregates keep its own 17:00 -> 17:00 NY window.
        sent = []
        sink = SlackNotificationSink(webhook_url="https://hooks.slack.com/services/T/B/x", clock=lambda: SENT_AT,
                                     transport=lambda url, payload, timeout: sent.append(payload_text(payload)))
        events = [NotificationEvent("1.0", f"evt-{date}", f"daily:{date}", SENT_AT.isoformat(), "INFO",
                                    "DAILY_SUMMARY", None, (), (), None, (), (), (),
                                    daily_summary={"date_ny": date, "close_at_utc": close, "window_start_utc": start})
                  for date, start, close in (("2026-10-19", "2026-10-18T21:00:00+00:00", "2026-10-19T21:00:00+00:00"),
                                             ("2026-10-20", "2026-10-19T21:00:00+00:00", "2026-10-20T21:00:00+00:00"))]
        with patch.dict(os.environ, {"AI_FLOOR_BROKER_DEMO_ROUTING": "1"}), \
                patch("execution.demo_dashboard.demo_snapshot", return_value=SNAPSHOT) as snap, \
                patch("execution.demo_dashboard.demo_progress", return_value=(None, None, 3)) as progress:
            for summary in events:
                sink.deliver(summary)
        self.assertEqual([c.args[0] for c in snap.call_args_list + progress.call_args_list], [SENT_AT] * 4)
        first, second = (text.splitlines() for text in sent)
        for lines in (first, second):
            self.assertEqual(lines[-1], "OANDA Demo · Día 3 de 30")
            self.assertEqual([lines[1], lines[2], lines[-2]], ["NAV (a las 10:00 ET): 100,040.00 USD",
                                                               "Balance (a las 10:00 ET): 100,039.98 USD",
                                                               "Operaciones abiertas (a las 10:00 ET): 1"])
        self.assertEqual((first[0], first[4:6]), ("📊 Resumen diario · OANDA Demo · 2026-10-19",
                                                  ["P&amp;L del día de trading: +45.50 USD",
                                                   "Operaciones cerradas en el día: 1 (ganadoras: 1)"]))
        self.assertEqual((second[0], second[4:6]), ("📊 Resumen diario · OANDA Demo · 2026-10-20",
                                                    ["P&amp;L del día de trading: -5.50 USD",
                                                     "Operaciones cerradas en el día: 3 (ganadoras: 2)"]))

    def test_day_figures_cover_the_window_and_flag_missing_data(self):
        with patch("execution.demo_dashboard.demo_progress", return_value=(None, None, 2)):
            for rows, expected in (
                    ([], ["+0.00 USD", "0 (ganadoras: 0)", "sin datos (sin operaciones cerradas en el día)"]),
                    ([closed_row("2026-10-20T10:00:00", 2.0, None, "sin stop original"),
                      closed_row("2026-10-20T11:00:00", None, 1.0)],
                     ["sin datos", "2 (ganadoras: —)", "sin datos (sin stop original en 1 de 2 operaciones)"]),
                    ([{**closed_row("2026-10-20T10:00:00", 2.0, 1.0), "exited_at": None}],
                     ["sin datos", "sin datos", "sin datos (datos de cierre incompletos en OANDA)"])):
                with self.subTest(rows=len(rows)), \
                        patch("execution.demo_dashboard.demo_snapshot", return_value={**SNAPSHOT, "closed_rows": rows}):
                    lines = oanda_demo_summary_text("2026-10-20T21:00:00+00:00").splitlines()
                self.assertEqual([line.split(": ", 1)[1] for line in lines[6:9]], expected)
        # An explicit window start (from the persisted summary) is honoured.
        with patch("execution.demo_dashboard.demo_snapshot", return_value=SNAPSHOT), \
                patch("execution.demo_dashboard.demo_progress", return_value=(None, None, 2)):
            lines = oanda_demo_summary_text("2026-10-20T21:00:00+00:00", "2026-10-20T00:00:00+00:00").splitlines()
        self.assertEqual(lines[6:8], ["P&L del día de trading: -8.50 USD", "Operaciones cerradas en el día: 2 (ganadoras: 1)"])

    def test_summary_text_reads_the_snapshot_at_send_time(self):
        with patch("execution.demo_dashboard.demo_snapshot", return_value=SNAPSHOT) as snap, \
                patch("execution.demo_dashboard.demo_progress", return_value=(None, None, 1)) as progress:
            lines = oanda_demo_summary_text("2026-10-20T21:00:00Z", None, SENT_AT).splitlines()
        self.assertEqual((snap.call_args.args[0], progress.call_args.args[0]), (SENT_AT, SENT_AT))
        self.assertEqual(lines[2], "Día de trading: 2026-10-20 (17:00 a 17:00 ET)")


def setUpModule():  # DEC-XAU-OFF-1: these tests drive XAUUSD cycles; the pause itself is in test_pause_xauusd
    import unittest.mock
    unittest.enterModuleContext(unittest.mock.patch("runtime.symbol_pause.PAUSED_SYMBOLS", frozenset()))


if __name__ == "__main__":
    unittest.main()
