import unittest
from unittest.mock import call, patch

from investigacion import ejecutar_investigacion_multiple


class TestEjecutarInvestigacionMultiple(unittest.TestCase):
	def resultado(self, ticker):
		return {
			"ticker": ticker,
			"filas_historicas": 100,
			"evaluaciones": 50,
			"resultados": [],
			"estadisticas": {},
			"estadisticas_condiciones": {},
		}

	def test_tickers_none_y_lista_vacia_devuelven_dict_vacio(self):
		self.assertEqual(ejecutar_investigacion_multiple(None), {})
		self.assertEqual(ejecutar_investigacion_multiple([]), {})

	@patch("investigacion.ejecutar_investigacion")
	def test_normaliza_y_deduplica_tickers(self, investigar_mock):
		investigar_mock.side_effect = lambda ticker, **kwargs: self.resultado(ticker)

		resultados = ejecutar_investigacion_multiple(
			[" aapl ", "AAPL", " msft"],
			periodo="1y",
			minimo_historial=20,
			horizontes=(1, 5),
		)

		self.assertEqual(set(resultados), {"AAPL", "MSFT"})
		self.assertEqual(investigar_mock.call_count, 2)
		self.assertEqual(
			investigar_mock.call_args_list,
			[
				call("AAPL", periodo="1y", minimo_historial=20, horizontes=(1, 5)),
				call("MSFT", periodo="1y", minimo_historial=20, horizontes=(1, 5)),
			],
		)

	@patch("investigacion.ejecutar_investigacion")
	def test_ignora_tickers_invalidos(self, investigar_mock):
		investigar_mock.side_effect = lambda ticker, **kwargs: self.resultado(ticker)

		resultados = ejecutar_investigacion_multiple([None, 123, "", "   ", "AAPL"])

		self.assertEqual(set(resultados), {"AAPL"})
		investigar_mock.assert_called_once()

	@patch("investigacion.ejecutar_investigacion")
	def test_conserva_resultados_de_multiples_tickers(self, investigar_mock):
		investigar_mock.side_effect = lambda ticker, **kwargs: self.resultado(ticker)

		resultados = ejecutar_investigacion_multiple(["AAPL", "MSFT"])

		self.assertEqual(resultados["AAPL"]["ticker"], "AAPL")
		self.assertEqual(resultados["MSFT"]["ticker"], "MSFT")

	@patch("investigacion.ejecutar_investigacion")
	def test_none_genera_datos_no_disponibles(self, investigar_mock):
		investigar_mock.return_value = None

		resultados = ejecutar_investigacion_multiple(["AAPL"])

		self.assertEqual(resultados["AAPL"], {
			"ticker": "AAPL",
			"error": "datos_no_disponibles",
		})

	@patch("investigacion.ejecutar_investigacion")
	def test_excepcion_de_un_ticker_no_detiene_los_demas(self, investigar_mock):
		investigar_mock.side_effect = [Exception("fallo"), self.resultado("MSFT")]

		resultados = ejecutar_investigacion_multiple(["AAPL", "MSFT"])

		self.assertEqual(resultados["AAPL"]["error"], "datos_no_disponibles")
		self.assertEqual(resultados["MSFT"]["ticker"], "MSFT")


if __name__ == "__main__":
	unittest.main()
