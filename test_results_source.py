"""DEC-OANDA-ONLY-1: ``AI_FLOOR_RESULTS_SOURCE=OANDA_DEMO`` shows only OANDA demo results (dashboard, Floor, daily
Slack summary) even with routing OFF; default keeps the current behaviour; RuntimeConfig.fingerprint never changes."""
from contextlib import nullcontext
from datetime import datetime, timezone
import os
import unittest
from unittest.mock import patch

from execution import results_source as rs
from runtime.config import RuntimeConfig
from runtime.notifications import paper_summary_text
import test_demo_from_zero as tz
import test_demo_gates_oanda as tg
from ui.views import dashboard, experiment, luxury

ONLY = {"AI_FLOOR_RESULTS_SOURCE": "OANDA_DEMO"}


class FlagTests(unittest.TestCase):
    def test_exact_value_only(self):
        self.assertTrue(rs.oanda_only(ONLY))
        for value in (None, "", "PAPER", "oanda_demo", " OANDA_DEMO", "1"):
            env = {} if value is None else {"AI_FLOOR_RESULTS_SOURCE": value}
            with self.subTest(value=value):
                self.assertFalse(rs.oanda_only(env))
                self.assertFalse(rs.demo_results(env))
                self.assertTrue(rs.demo_results({**env, "AI_FLOOR_BROKER_DEMO_ROUTING": "1"}))  # unchanged
        self.assertTrue(rs.demo_results(ONLY))  # routing OFF: still OANDA results

    def test_fingerprint_unchanged(self):
        with patch.dict(os.environ, {"AI_FLOOR_RESULTS_SOURCE": ""}):
            os.environ.pop("AI_FLOOR_RESULTS_SOURCE")
            with patch("runtime.env.load_local_env"):
                base = RuntimeConfig.from_env().fingerprint()
        with patch.dict(os.environ, ONLY), patch("runtime.env.load_local_env"):
            self.assertEqual(RuntimeConfig.from_env().fingerprint(), base)

    def test_ui_switches(self):
        with patch.dict(os.environ, ONLY):
            os.environ.pop("AI_FLOOR_BROKER_DEMO_ROUTING", None)
            self.assertIsNotNone(luxury.demo_api())  # Overview / Trades / Decisions / Floor use the demo view
            self.assertTrue(luxury.paper_hidden())
            self.assertTrue(dashboard.paper_hidden())
            with patch("execution.demo_dashboard.demo_progress", return_value=(None, None, 3)):
                self.assertIn("Demo OANDA", experiment.demo_progress_text())
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AI_FLOOR_RESULTS_SOURCE", None)
            os.environ.pop("AI_FLOOR_BROKER_DEMO_ROUTING", None)
            self.assertIsNone(luxury.demo_api())
            self.assertFalse(luxury.paper_hidden())
            self.assertFalse(dashboard.paper_hidden())


class SlackOnlyTests(unittest.TestCase):
    def send(self, env):
        return tg.SlackSummaryTests("test_routing_off_unchanged").send(env)

    def test_oanda_only_with_routing_off_sends_the_demo_summary(self):
        text, _ = self.send({"AI_FLOOR_BROKER_DEMO_ROUTING": "0", **ONLY})
        self.assertTrue(text.startswith("📊 Resumen diario · OANDA Demo"))
        self.assertNotIn("PAPER", text)

    def test_default_routing_off_still_paper(self):
        text, _ = self.send({"AI_FLOOR_BROKER_DEMO_ROUTING": "0", "AI_FLOOR_RESULTS_SOURCE": "PAPER"})
        self.assertEqual(text, paper_summary_text(tg.event().daily_summary))


class DashboardOnlyTests(unittest.TestCase):
    """Composition over test_demo_from_zero.DashboardTests (fixtures only; its own tests are not re-run here)."""

    def setUp(self):
        self.case = tz.DashboardTests("test_routing_off_shows_paper_and_never_reads_the_demo")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.state, self.page = self.case.state, self.case.page

    def only(self):
        os.environ.pop("AI_FLOOR_BROKER_DEMO_ROUTING", None)
        return patch.dict(os.environ, {"AI_FLOOR_BROKER_STATE_DB_PATH": str(self.state), **ONLY})

    def test_overview_routing_off_shows_only_oanda(self):
        with self.only():
            _app, content = self.page("Overview")
        self.assertIn("Equity (NAV) · OANDA demo", content)
        self.assertNotIn("Equity · PAPER", content)
        self.assertNotIn("+$3.00", content)  # the PAPER fixture trade

    def test_command_center_hides_paper_ledger(self):
        with self.only():
            app, content = self.page("OVERVIEW")
            infos = "\n".join(i.value for i in app.info)
            self.assertIn("ledger PAPER es interno", infos)
            self.assertFalse([m for m in app.metric if m.label in ("Equity", "Realized PnL", "Unrealized PnL")])
            app, _ = self.page("POSITIONS")
            self.assertFalse(app.dataframe)

    def test_floor_payload_is_oanda(self):
        from ui.views import floor_view
        st = type("S", (), {"session_state": {"sample_mode": False}})()
        with self.only():
            payload = floor_view.current_payload(st, ("EURUSD",), now=datetime(2026, 10, 21, 14, tzinfo=timezone.utc))
        self.assertEqual(payload["source"], "OANDA_DEMO")

    def test_default_keeps_paper_screens(self):
        os.environ.pop("AI_FLOOR_BROKER_DEMO_ROUTING", None)
        with nullcontext():
            _app, content = self.page("Overview")
        self.assertIn("Equity · PAPER", content)


if __name__ == "__main__":
    unittest.main()
