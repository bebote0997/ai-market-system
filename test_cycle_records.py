"""Cycle audit is bounded, durable, and never changes execution decisions."""
from contextlib import closing
from datetime import timedelta
import io
import json
import logging
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from runtime import cloud_runner, cycle_records
from runtime.config import RuntimeConfig
from runtime.demo_runner import DemoRunner
from test_demo_runner import Data, T, instrument, macro_fixture, patched_scouts
from ai.provider import DeterministicAIProvider


class CycleRecordTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.trading = Path(self.temp.name) / "trading.db"
        self.config = RuntimeConfig(db_path=self.trading)

    def runner(self, data=None):
        runner = DemoRunner(self.config, market_provider=data or Data(),
                            ai_provider=DeterministicAIProvider(), macro_provider=macro_fixture(),
                            instruments={"XAUUSD": instrument()}, clock=lambda: T)
        self.addCleanup(runner.close)
        return runner

    def rows(self):
        with closing(sqlite3.connect(cycle_records.path_for(self.trading))) as db:
            return [(symbol, slot, run_id, json.loads(payload)) for symbol, slot, run_id, payload in
                    db.execute("SELECT * FROM cycles ORDER BY slot_time,symbol")]

    def test_successful_cycle_records_existing_gates_and_ai_statuses(self):
        with patched_scouts("LONG"):
            runner = self.runner()
            result = runner.run_once("XAUUSD", T)
        row = self.rows()[0]
        payload = row[3]
        self.assertEqual(row[:3], ("XAUUSD", T.isoformat(), result["run_id"]))
        self.assertEqual(payload["setup_status"], "VALID_SETUP")
        self.assertEqual(payload["gates"]["timeframe_alignment"], "PASSED")
        self.assertEqual(payload["timeframes"], {
            "1h": {"status": "OK", "bias": "bullish"},
            "15m": {"status": "OK", "structure_state": "bullish"},
            "5m": {"status": "OK", "structure_state": "bullish"}})
        review = runner.store.review_report(result["run_id"])
        self.assertEqual(payload["execution_reason"], review["execution"]["execution_reason"])
        self.assertTrue(all(value == "OK" for value in payload["ai_reviews"].values()))
        self.assertEqual(runner.run_once("XAUUSD", T)["status"], "DUPLICATE")
        self.assertEqual(self.rows(), [row])

    def test_alignment_rejection_is_copied_without_changing_filter(self):
        from test_demo_runner import scout
        structure = scout("LONG")
        structure.kind = "structure"
        bearish = scout("SHORT")
        bearish.kind = "structure"

        def mixed(frame, symbol, tf, run_id, at):
            return (bearish if tf == "5m" else structure)(frame, symbol, tf, run_id, at)

        with patched_scouts("LONG"), patch("floor.orchestrator.analizar_estructura", mixed):
            result = self.runner().run_once("XAUUSD", T)
        payload = self.rows()[0][3]
        self.assertEqual(result["status"], "WATCH")
        self.assertEqual(payload["gates"]["timeframe_alignment"], "FAILED")
        self.assertEqual(payload["timeframes"]["5m"]["structure_state"], "bearish")
        self.assertEqual(payload["rejection_reason"], "timeframe_incompatibility")
        self.assertEqual(payload["gates"]["structure_confirmation"], "NOT_EVALUATED")
        self.assertEqual(payload["ai_reviews"]["ai_trade_review"], "NOT_REQUIRED")

    def test_early_stale_cycle_is_recorded_with_unevaluated_gates(self):
        result = self.runner(Data(age=1000)).run_once("XAUUSD", T)
        payload = self.rows()[0][3]
        self.assertEqual(payload["status"], result["status"])
        self.assertEqual(payload["gates"]["fresh"], "FAILED")
        self.assertEqual(payload["gates"]["timeframe_alignment"], "NOT_EVALUATED")
        self.assertEqual(payload["timeframes"]["1h"]["bias"], "NO_DATA")
        self.assertEqual(payload["execution_reason"], "STALE_DATA")

    def test_record_failure_does_not_change_cycle(self):
        with patched_scouts("LONG"), patch("runtime.cycle_records.record", side_effect=OSError("disk")):
            runner = self.runner()
            with self.assertLogs("ai_floor.runtime", level="WARNING") as logs:
                result = runner.run_once("XAUUSD", T)
        self.assertEqual(result["status"], "PLAN_READY")
        self.assertTrue(any("RECORD_FAILED" in line for line in logs.output))

    def test_pruning_is_global_bounded_and_survives_restart(self):
        def record(slot, symbol="XAUUSD"):
            cycle_records.record(self.trading, {"slot": slot, "symbol": symbol, "run_id": "fixture"},
                                 "NO_DATA", paper_enabled=False)
        record(T - timedelta(days=31))
        record(T - timedelta(days=30), "EURUSD")
        record(T)
        self.assertEqual(len(self.rows()), 2)
        record(T - timedelta(days=40))  # an old replay cannot move the retention watermark backwards
        self.assertEqual(len(self.rows()), 2)
        with patch.object(cycle_records, "MAX_RECORDS", 2):
            record(T + timedelta(minutes=15))
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual(self.rows()[-1][1], (T + timedelta(minutes=15)).isoformat())

    def test_locked_database_fails_without_breaking_cycle(self):
        cycle_records.record(self.trading, {"slot": T - timedelta(minutes=15), "symbol": "EURUSD",
                                           "run_id": "fixture"}, "NO_DATA", paper_enabled=False)
        with closing(sqlite3.connect(cycle_records.path_for(self.trading))) as db:
            db.execute("BEGIN EXCLUSIVE")
            with patched_scouts("LONG"):
                result = self.runner().run_once("XAUUSD", T)
            db.rollback()
        self.assertEqual(result["status"], "PLAN_READY")
        self.assertEqual(len(self.rows()), 1)

    def test_session_skip_is_recorded(self):
        result = self.runner().run_once("XAUUSD", T.replace(hour=23))
        payload = self.rows()[0][3]
        self.assertEqual(result["status"], "SESSION_SKIPPED")
        self.assertEqual(payload["gates"]["session"], "FAILED")
        self.assertEqual(payload["execution_reason"], "SESSION_SKIPPED")

    def test_error_cycle_is_recorded_without_inventing_ai_results(self):
        data = Data(failure=RuntimeError("fixture"))
        result = self.runner(data).run_once("XAUUSD", T)
        payload = self.rows()[0][3]
        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(payload["stage"], "market_data")
        self.assertEqual(payload["execution_reason"], "ERROR")
        self.assertTrue(all(value == "NOT_EVALUATED" for value in payload["ai_reviews"].values()))

    def test_halt_suppresses_cycle_record_write(self):
        from runtime.halt import HaltGate
        runner = self.runner()
        runner.runtime.halt_gate = HaltGate()
        runner.runtime.halt_gate.request()
        with patch("runtime.cycle_records.record") as record:
            self.assertEqual(runner.runtime.run_cycle("XAUUSD", T), "HALT_REFUSED")
        record.assert_not_called()
        self.assertFalse(cycle_records.path_for(self.trading).exists())


class CloudLoggingTests(unittest.TestCase):
    def test_main_configures_info_to_stdout_even_with_existing_handlers(self):
        root = logging.getLogger()
        handlers, level = root.handlers[:], root.level
        output = io.StringIO()
        try:
            with patch("sys.stdout", output), patch.dict("os.environ", {"RENDER": "false"}):
                with self.assertRaisesRegex(RuntimeError, "not activated"):
                    cloud_runner.main()
                logging.getLogger("ai_floor.runtime").info("event=RUN_COMPLETED status=WATCH")
            self.assertIn("event=RUN_COMPLETED", output.getvalue())
        finally:
            for handler in root.handlers:
                handler.close()
            root.handlers = handlers
            root.setLevel(level)


def setUpModule():  # DEC-XAU-OFF-1: these tests drive XAUUSD cycles; the pause itself is in test_pause_xauusd
    import unittest.mock
    unittest.enterModuleContext(unittest.mock.patch("runtime.symbol_pause.PAUSED_SYMBOLS", frozenset()))


if __name__ == "__main__":
    unittest.main()
