import unittest
from unittest.mock import MagicMock, patch

from mercado import obtener_precio_actual


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


if __name__ == "__main__":
	unittest.main()
