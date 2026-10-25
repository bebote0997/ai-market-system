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


class ProcessHistoryOnlyTests(unittest.TestCase):
    """Process / History / Errors / Floor feed hide PAPER entries, exits and P&L (journal side included); the Process
    entry / exit / outcome steps come from the OANDA demo rows of the same run, or NO_DATA."""

    def setUp(self):
        import json
        import tempfile
        from pathlib import Path
        from storage.database import Store
        import test_floor_view as fv
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db_path, self.state = Path(tmp.name) / "t.db", Path(tmp.name) / "broker_state.db"
        fv.seed(self.db_path)
        store = Store(self.db_path)
        try:
            store.db.execute("INSERT INTO closed_trades VALUES(?,?)", ("trade-e1", json.dumps({
                "trade_id": "trade-e1", "position_id": "pos-open", "run_id": "run-e1", "symbol": "EURUSD",
                "entry_price": 1.1002, "exit_price": 1.1060, "net_pnl": 5.8, "reason": "TARGET"})))
            for event, payload in (("POSITION_CLOSED", {"net_pnl": 5.8, "exit_price": 1.106}),
                                   ("STATE_INCONSISTENCY", {"detail": "x", "equity": 10055.5})):
                store.db.execute("INSERT INTO journal(timestamp,run_id,symbol,source,event_type,severity,payload) "
                                 "VALUES(?,?,?,?,?,?,?)", (fv.T_OPEN, "run-e1", "EURUSD", "paper", event,
                                                           "ERROR", json.dumps(payload)))
        finally:
            store.close()

    def env(self, **extra):
        os.environ.pop("AI_FLOOR_BROKER_DEMO_ROUTING", None)
        return patch.dict(os.environ, {"AI_FLOOR_DB_PATH": str(self.db_path),
                                       "AI_FLOOR_BROKER_STATE_DB_PATH": str(self.state), **extra})

    def evidence(self):
        from ui.state import dashboard_database
        from ui.views.process import cycle_evidence
        with dashboard_database(self.db_path) as db:
            run = dashboard.records(db, "runs", run_id="run-e1")[0]
            return cycle_evidence(db, run)[0]

    def route(self, status="CLOSED"):
        from execution.broker_demo_state import DemoStateStore
        store = DemoStateStore(self.state)
        store.insert_intent({"client_id": "aif-run-e1", "run_id": "run-e1", "symbol": "EURUSD", "side": "LONG",
                             "units": 5000, "entry": "1.1", "stop": "1.098", "target": "1.106",
                             "status": "PENDING_SEND"})
        store.update("aif-run-e1", trade_id="77", fill_price="1.10013", status=status, closed_reason="TP")

    def test_default_process_still_shows_paper(self):
        with self.env(AI_FLOOR_RESULTS_SOURCE="PAPER"):
            evidence = self.evidence()
        self.assertTrue(evidence["Paper Broker entry"])
        self.assertIn({"net_pnl": 5.8}, evidence["Final outcome"])

    def test_process_hides_paper_and_shows_no_data_without_oanda(self):
        with self.env(**ONLY):
            evidence = self.evidence()
        self.assertEqual(evidence["Paper Broker entry"], [])
        self.assertEqual(evidence["Paper Broker exit"], [])
        self.assertIn({"source": "OANDA_DEMO", "outcome": "Sin datos"}, evidence["Final outcome"])
        text = str(evidence)
        for leaked in ("net_pnl", "5.8", "exit_price", "equity", "POSITION_CLOSED", "POSITION_OPENED", "pos-open"):
            self.assertNotIn(leaked, text)

    def test_process_shows_the_oanda_outcome_of_the_same_run(self):
        self.route()
        with self.env(**ONLY):
            evidence = self.evidence()
        self.assertEqual([(r["trade_id"], r["fill_price"]) for r in evidence["Paper Broker entry"]], [("77", "1.10013")])
        self.assertEqual([r["status"] for r in evidence["Paper Broker exit"]], ["CLOSED"])
        self.assertIn({"source": "OANDA_DEMO", "client_id": "aif-run-e1", "status": "CLOSED",
                       "closed_reason": "TP"}, evidence["Final outcome"])
        self.assertNotIn("net_pnl", str(evidence))

    def test_history_and_errors_hide_journal_results(self):
        from streamlit.testing.v1 import AppTest
        def page(name):
            app = AppTest.from_string(
                "import streamlit as st\nfrom ui.views import dashboard\ndashboard.render(st, %r)\n" % name)
            return app.run(timeout=30)
        with self.env(**ONLY):
            history, errors = page("HISTORY"), page("ERRORS")
        for app in (history, errors):
            text = "\n".join(frame.value.to_csv() for frame in app.dataframe)
            for leaked in ("net_pnl", "exit_price", "POSITION_CLOSED", "POSITION_OPENED", "10055.5"):
                self.assertNotIn(leaked, text)
        self.assertIn("STATE_INCONSISTENCY", "\n".join(f.value.to_csv() for f in errors.dataframe))
        with self.env(AI_FLOOR_RESULTS_SOURCE="PAPER"):
            text = "\n".join(frame.value.to_csv() for frame in page("HISTORY").dataframe)
        self.assertIn("POSITION_CLOSED", text)

    def test_floor_feed_drops_paper_events(self):
        from ui.floor_payload import journal_feed
        from ui.state import dashboard_database
        with self.env(**ONLY), dashboard_database(self.db_path) as db:
            lines = journal_feed(db)
        self.assertFalse([line for line in lines or () if "cerrada" in line.lower() or "abierta" in line.lower()])


if __name__ == "__main__":
    unittest.main()
