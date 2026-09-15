import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

from mercado import obtener_historial_precios, obtener_precio_actual


class TestObtenerPrecioActual(unittest.TestCase):
	@patch("mercado.yf.Ticker")
	def test_devuelve_ultimo_cierre(self, ticker_mock):
		datos = MagicMock()
		datos.empty = False
		cierre = MagicMock()
		cierre.empty = False
		cierre.iloc.__getitem__.return_value = 123.45
		datos.__getitem__.return_value = cierre
		ticker_mock.return_value.history.return_value = datos

		resultado = obtener_precio_actual("AAPL")

		self.assertAlmostEqual(resultado, 123.45)

	@patch("mercado.yf.Ticker")
	def test_devuelve_none_si_los_datos_estan_vacios(self, ticker_mock):
		datos = MagicMock()
		datos.empty = True
		ticker_mock.return_value.history.return_value = datos

		resultado = obtener_precio_actual("AAPL")

		self.assertIsNone(resultado)

	@patch("mercado.yf.Ticker")
	def test_devuelve_none_si_ocurre_una_excepcion(self, ticker_mock):
		ticker_mock.side_effect = Exception("Error simulado")

		resultado = obtener_precio_actual("AAPL")

		self.assertIsNone(resultado)


class TestObtenerHistorialPrecios(unittest.TestCase):
	@patch("mercado.yf.Ticker")
	def test_devuelve_serie_close_y_usa_periodo_predeterminado(self, ticker_mock):
		datos = pd.DataFrame({"Close": [100.0, 101.5, 103.25]})
		ticker_mock.return_value.history.return_value = datos

		resultado = obtener_historial_precios("AAPL")

		pd.testing.assert_series_equal(resultado, datos["Close"])
		self.assertIsNot(resultado, datos["Close"])
		ticker_mock.return_value.history.assert_called_once_with(period="1mo")

	@patch("mercado.yf.Ticker")
	def test_datos_vacios_devuelve_none(self, ticker_mock):
		ticker_mock.return_value.history.return_value = pd.DataFrame()

		resultado = obtener_historial_precios("AAPL")

		self.assertIsNone(resultado)

	@patch("mercado.yf.Ticker")
	def test_falta_columna_close_devuelve_none(self, ticker_mock):
		datos = pd.DataFrame({"Open": [100.0, 101.5]})
		ticker_mock.return_value.history.return_value = datos

		resultado = obtener_historial_precios("AAPL")

		self.assertIsNone(resultado)

	@patch("mercado.yf.Ticker")
	def test_excepcion_en_history_devuelve_none(self, ticker_mock):
		ticker_mock.return_value.history.side_effect = Exception("Error simulado")

		resultado = obtener_historial_precios("AAPL")

		self.assertIsNone(resultado)

	@patch("mercado.yf.Ticker")
	def test_usa_periodo_personalizado(self, ticker_mock):
		datos = pd.DataFrame({"Close": [100.0, 105.0]})
		ticker_mock.return_value.history.return_value = datos

		resultado = obtener_historial_precios("AAPL", periodo="6mo")

		pd.testing.assert_series_equal(resultado, datos["Close"])
		ticker_mock.return_value.history.assert_called_once_with(period="6mo")

if __name__ == "__main__":
	unittest.main()
