import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

import pandas as pd

from ejecutar_estrategia import (
    _mostrar_resultados,
    ejecutar_multiples_tickers,
    ejecutar_para_ticker,
)
from riesgo import crear_configuracion_riesgo
from simulador import crear_configuracion_simulacion, simular_operaciones_con_riesgo


class TestEjecutarEstrategia(unittest.TestCase):
    def resultado(self, ticker):
        return {"ticker": ticker, "resultado": {"r": 1}, "comparacion": {}}

    @patch("ejecutar_estrategia.comparar_metricas_estrategia", return_value={"m": 1})
    @patch("ejecutar_estrategia.ejecutar_validacion_estrategia", return_value={"v": 1})
    @patch("ejecutar_estrategia.crear_configuracion_simulacion")
    @patch("ejecutar_estrategia.crear_configuracion_estrategia")
    @patch("ejecutar_estrategia.obtener_datos_historicos")
    def test_ticker_valido_transmite_parametros(self, datos, estrategia, simulacion, validar, comparar):
        datos.return_value = object()
        estrategia.return_value = {"e": 1}
        simulacion.return_value = {"s": 1}
        result = ejecutar_para_ticker("AAPL", periodo="1y", proporcion_entrenamiento=.8, minimo_historial=20, periodo_salida=3)
        self.assertEqual(result["ticker"], "AAPL")
        datos.assert_called_once_with("AAPL", "1y")
        validar.assert_called_once()
        comparar.assert_called_once_with({"v": 1})

    @patch("ejecutar_estrategia.obtener_datos_historicos", return_value=None)
    def test_datos_no_disponibles(self, datos):
        self.assertIsNone(ejecutar_para_ticker("AAPL"))

    @patch("ejecutar_estrategia.ejecutar_para_ticker")
    def test_multiples_normaliza_deduplica_ignora_invalidos_y_aisla_fallo(self, ejecutar):
        ejecutar.side_effect = [self.resultado("AAPL"), None, self.resultado("MSFT")]
        result = ejecutar_multiples_tickers([" aapl ", "AAPL", None, 5, "MSFT"])
        self.assertEqual(set(result), {"AAPL", "MSFT"})
        self.assertEqual(result["AAPL"]["ticker"], "AAPL")
        self.assertEqual(result["MSFT"]["ticker"], "MSFT")
        self.assertEqual(ejecutar.call_count, 2)

    @patch("ejecutar_estrategia.ejecutar_para_ticker", side_effect=Exception("x"))
    def test_fallo_de_ticker_no_detiene(self, ejecutar):
        result = ejecutar_multiples_tickers(["AAPL", "MSFT"])
        self.assertEqual(result["AAPL"]["error"], "no_disponible")
        self.assertEqual(result["MSFT"]["error"], "no_disponible")

    def test_reporte_no_falla_con_posicion_abierta(self):
        metricas = {
            "retorno_total_pct": 1.0,
            "tasa_acierto": 50.0,
            "profit_factor": None,
            "expectativa": 1.0,
            "max_drawdown_pct": -2.0,
            "r_medio": None,
            "stop_count": 0,
            "target_count": 0,
            "tiempo_count": 0,
        }
        simulacion = {
            "capital_inicial": 10000.0,
            "capital_final": 10050.0,
            "capital_realizado": 10000.0,
            "operaciones": [],
            "posicion_abierta": {
                "fecha_entrada": "2026-01-02",
                "cantidad": 10.0,
                "precio_entrada": 100.0,
                "ultimo_precio": 105.0,
            },
        }
        activo = {
            "resultado": {
                "division": {"total_filas": 10, "filas_entrenamiento": 7, "filas_prueba": 3},
                "entrenamiento": {"evaluaciones": [], "eventos": [], "simulacion": simulacion, "metricas": metricas},
                "prueba": {"evaluaciones": [], "eventos": [], "simulacion": simulacion, "metricas": metricas},
            },
            "comparacion": {metrica: {"diferencia": None} for metrica in ["retorno_total_pct", "tasa_acierto", "profit_factor", "expectativa", "max_drawdown_pct"]},
        }
        salida = StringIO()
        with redirect_stdout(salida):
            _mostrar_resultados({"AAPL": activo})
        self.assertIn("último precio=105.000000", salida.getvalue())

    def test_end_to_end_riesgo_posicion_abierta_llega_al_reporte(self):
        indice = pd.date_range("2026-01-01", periods=3, freq="D")
        datos = pd.DataFrame({
            "Open": [100.0, 100.0, 100.0],
            "High": [101.0, 101.0, 101.0],
            "Low": [99.0, 99.0, 99.0],
            "Close": [100.0, 90.0, 90.0],
        }, index=indice)
        simulacion = simular_operaciones_con_riesgo(
            datos,
            [{"fecha": indice[0], "atr_senal": 5.0, "evaluacion": {}}],
            crear_configuracion_simulacion(10000, 25, 5, 0),
            crear_configuracion_riesgo(maximo_capital_pct=25),
        )
        abierta = simulacion["posicion_abierta"]
        self.assertEqual(abierta["ultimo_precio"], 90.0)
        self.assertEqual(abierta["ultima_fecha"], indice[-1])
        self.assertAlmostEqual(abierta["equity_actual"], 9900.0)

        metricas = {
            "retorno_total_pct": 0.0, "tasa_acierto": 0.0,
            "profit_factor": None, "expectativa": 0.0,
            "max_drawdown_pct": 0.0, "r_medio": None,
            "stop_count": 0, "target_count": 0, "tiempo_count": 0,
        }
        activo = {
            "resultado": {
                "division": {"total_filas": 3, "filas_entrenamiento": 2, "filas_prueba": 1},
                "entrenamiento": {"evaluaciones": [], "eventos": [], "simulacion": simulacion, "metricas": metricas},
                "prueba": {"evaluaciones": [], "eventos": [], "simulacion": simulacion, "metricas": metricas},
            },
            "comparacion": {metrica: {"diferencia": None} for metrica in ["retorno_total_pct", "tasa_acierto", "profit_factor", "expectativa", "max_drawdown_pct"]},
        }
        salida = StringIO()
        with redirect_stdout(salida):
            _mostrar_resultados({"AAPL": activo})
        self.assertIn("último precio=90.000000", salida.getvalue())


if __name__ == "__main__":
    unittest.main()
