import unittest
from datetime import datetime

import pandas as pd

from core.contracts import InstrumentSpec, TradePlan
from core.timeframes import AUTHORIZED_TIMEFRAMES, validar_timeframe
from data.instruments import obtener_instrumento
from riesgo import evaluar_trade_plan, crear_configuracion_riesgo_v2
from simulador import crear_configuracion_simulacion, simular_trade_plan


class TestRiskV2(unittest.TestCase):
    def instrumento(self):
        return InstrumentSpec("TEST", "synthetic", "TEST", "UTC", 0.01, 0.01, 1.0, AUTHORIZED_TIMEFRAMES)

    def plan(self, side="long"):
        return TradePlan("1.0", "TEST", side, "5m", 100.0, 90.0 if side == "long" else 110.0, 130.0 if side == "long" else 70.0, 3.0)

    def test_timeframes_and_specs_are_explicit(self):
        self.assertEqual(AUTHORIZED_TIMEFRAMES, ("1h", "15m", "5m"))
        self.assertTrue(validar_timeframe("15m"))
        self.assertFalse(validar_timeframe("1d"))
        self.assertEqual(obtener_instrumento("xauusd").provider_symbol, None)
        self.assertIsNone(obtener_instrumento("XAUUSD").contract_multiplier)

    def test_approved_long_and_short(self):
        config = crear_configuracion_riesgo_v2()
        for side in ("long", "short"):
            decision = evaluar_trade_plan(self.plan(side), 10000, self.instrumento(), config)
            self.assertEqual(decision.status, "APPROVED")
            self.assertGreater(decision.quantity, 0)

    def test_rejected_for_missing_contract_or_rr(self):
        config = crear_configuracion_riesgo_v2()
        missing = InstrumentSpec("X", "index", None, "UTC", None, None, None, AUTHORIZED_TIMEFRAMES)
        self.assertEqual(evaluar_trade_plan(self.plan(), 10000, missing, config).status, "REJECTED")
        bad_plan = TradePlan("1.0", "TEST", "long", "5m", 100, 90, 100, 1)
        self.assertEqual(evaluar_trade_plan(bad_plan, 10000, self.instrumento(), config).status, "REJECTED")

    def test_approved_trade_plan_reaches_simulation(self):
        dates = pd.date_range("2026-01-01", periods=4)
        datos = pd.DataFrame({"Open": [100, 100, 100, 100], "High": [101, 101, 130, 101], "Low": [99, 99, 99, 99], "Close": [100, 100, 120, 100]}, index=dates)
        config = crear_configuracion_riesgo_v2()
        decision = evaluar_trade_plan(self.plan(), 10000, self.instrumento(), config)
        result = simular_trade_plan(datos, {"fecha": dates[0], "side": "long"}, decision, crear_configuracion_simulacion(periodo_salida=2, coste_porcentual=0))
        self.assertEqual(len(result["operaciones"]), 1)

    def test_rejected_plan_cannot_execute(self):
        dates = pd.date_range("2026-01-01", periods=4)
        datos = pd.DataFrame({"Open": [100] * 4, "High": [101] * 4, "Low": [99] * 4, "Close": [100] * 4}, index=dates)
        rejected = evaluar_trade_plan(self.plan(), 10000, InstrumentSpec("X", "index", None, "UTC", None, None, None, AUTHORIZED_TIMEFRAMES), crear_configuracion_riesgo_v2())
        result = simular_trade_plan(datos, {"fecha": dates[0], "side": "long"}, rejected, crear_configuracion_simulacion())
        self.assertEqual(result["operaciones"], [])


if __name__ == "__main__":
    unittest.main()
