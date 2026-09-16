import unittest
from datetime import datetime, timezone

import pandas as pd

from core.contracts import InstrumentSpec, MacroEvent
from data.macro_news import InMemoryMacroNewsProvider
from floor.orchestrator import run
from riesgo import crear_configuracion_riesgo_v2


class TestOrchestrator(unittest.TestCase):
    def snapshot(self, bias="bullish"):
        index = pd.date_range("2026-01-01", periods=6, freq="h")
        frames = {}
        for tf in ("1h", "15m", "5m"):
            frames[tf] = pd.DataFrame({"Open": [100, 101, 99, 102, 101, 103], "High": [101, 103, 100, 105, 104, 106], "Low": [99, 98, 98, 100, 99, 101], "Close": [100, 102, 99, 104, 100, 105]}, index=index)
        return frames

    def instrument(self, multiplier=1.0):
        return InstrumentSpec("TEST", "synthetic", "TEST", "UTC", 0.01, 0.01, multiplier, ("1h", "15m", "5m"))

    def test_no_data_snapshot_fails_closed(self):
        report = run({}, datetime(2026, 1, 1, 5, tzinfo=timezone.utc), "TEST", InMemoryMacroNewsProvider(), self.instrument(), crear_configuracion_riesgo_v2())
        self.assertEqual(report.final_status, "NO_SETUP")

    def test_run_id_and_as_of_propagate(self):
        as_of = datetime(2026, 1, 1, 5, tzinfo=timezone.utc)
        report = run(self.snapshot(), as_of, "TEST", InMemoryMacroNewsProvider(), self.instrument(), crear_configuracion_riesgo_v2())
        self.assertEqual(report.as_of, as_of)
        self.assertTrue(all(message.run_id == report.run_id for message in list(report.structure_reports.values()) + list(report.liquidity_reports.values())))

    def test_missing_contract_rejects_if_plan_exists_or_fails_closed(self):
        report = run(self.snapshot(), datetime(2026, 1, 1, 5, tzinfo=timezone.utc), "TEST", InMemoryMacroNewsProvider(), self.instrument(None), crear_configuracion_riesgo_v2())
        self.assertIn(report.final_status, {"NO_SETUP", "WATCH", "RISK_REJECTED"})

    def test_rejected_decision_never_becomes_approved(self):
        report = run(self.snapshot(), datetime(2026, 1, 1, 5, tzinfo=timezone.utc), "TEST", InMemoryMacroNewsProvider(), self.instrument(None), crear_configuracion_riesgo_v2())
        self.assertNotEqual(report.final_status, "APPROVED")

    def test_equity_is_explicit_and_invalid_equity_fails_closed(self):
        as_of = datetime(2026, 1, 1, 5, tzinfo=timezone.utc)
        report = run(self.snapshot(), as_of, "TEST", InMemoryMacroNewsProvider(), self.instrument(), crear_configuracion_riesgo_v2())
        self.assertEqual(report.final_status, "NO_SETUP")
        valid = run(self.snapshot(), as_of, "TEST", InMemoryMacroNewsProvider(), self.instrument(), crear_configuracion_riesgo_v2(), equity=50000.0)
        self.assertIsNotNone(valid)


if __name__ == "__main__":
    unittest.main()
