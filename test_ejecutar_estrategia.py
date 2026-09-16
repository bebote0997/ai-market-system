import unittest
from unittest.mock import patch

from ejecutar_estrategia import ejecutar_multiples_tickers, ejecutar_para_ticker


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


if __name__ == "__main__":
    unittest.main()
