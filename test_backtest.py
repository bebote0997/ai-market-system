import unittest
from unittest.mock import patch

import pandas as pd

from backtest import generar_evaluaciones_historicas


class TestGenerarEvaluacionesHistoricas(unittest.TestCase):
	def datos_ohlcv(self, cantidad=6):
		indice = pd.date_range("2026-01-01", periods=cantidad, freq="D")
		return pd.DataFrame(
			{
				"Open": range(100, 100 + cantidad),
				"High": range(101, 101 + cantidad),
				"Low": range(99, 99 + cantidad),
				"Close": range(100, 100 + cantidad),
				"Volume": range(1000, 1000 + cantidad),
			},
			index=indice,
		)

	def test_datos_invalidos_devuelven_lista_vacia(self):
		datos = self.datos_ohlcv()

		self.assertEqual(generar_evaluaciones_historicas(None), [])
		self.assertEqual(generar_evaluaciones_historicas(pd.DataFrame()), [])
		self.assertEqual(generar_evaluaciones_historicas(datos, 0), [])
		self.assertEqual(generar_evaluaciones_historicas(datos, -1), [])
		self.assertEqual(generar_evaluaciones_historicas(datos, 7), [])
		self.assertEqual(
			generar_evaluaciones_historicas(datos.drop(columns="Volume")),
			[],
		)

	@patch("backtest.evaluar_entrada", return_value={"estado": "ok"})
	@patch("backtest.analizar_mercado", return_value={"resultado": "ok"})
	def test_exactamente_minimo_historial_produce_como_maximo_una_evaluacion(
		self, analizar_mock, evaluar_mock
	):
		resultados = generar_evaluaciones_historicas(self.datos_ohlcv(3), 3)

		self.assertLessEqual(len(resultados), 1)
		self.assertEqual(len(resultados), 1)

	@patch("backtest.evaluar_entrada", return_value={"evaluacion": True})
	@patch("backtest.analizar_mercado")
	def test_ventanas_expansivas_sin_look_ahead(self, analizar_mock, evaluar_mock):
		analizar_mock.return_value = {"analisis": True}
		datos = self.datos_ohlcv(6)

		resultados = generar_evaluaciones_historicas(datos, 3)

		self.assertEqual(
			[llamada.args[0].shape[0] for llamada in analizar_mock.call_args_list],
			[3, 4, 5, 6],
		)
		self.assertEqual(len(resultados), 4)

	@patch("backtest.evaluar_entrada", return_value={"evaluacion": True})
	@patch("backtest.analizar_mercado", return_value={"analisis": True})
	def test_fechas_y_precios_corresponden_a_cada_ventana(
		self, analizar_mock, evaluar_mock
	):
		datos = self.datos_ohlcv(4)
		datos.loc[datos.index[2], "Close"] = float("nan")

		resultados = generar_evaluaciones_historicas(datos, 2)

		self.assertEqual(
			[resultado["fecha"] for resultado in resultados],
			list(datos.index[1:]),
		)
		self.assertEqual(
			[resultado["precio_cierre"] for resultado in resultados],
			[101.0, 101.0, 103.0],
		)

	@patch("backtest.evaluar_entrada", return_value={"evaluacion": True})
	@patch("backtest.analizar_mercado", return_value=None)
	def test_analisis_none_omite_fecha(self, analizar_mock, evaluar_mock):
		self.assertEqual(generar_evaluaciones_historicas(self.datos_ohlcv(3), 2), [])
		evaluar_mock.assert_not_called()

	@patch("backtest.evaluar_entrada", return_value=None)
	@patch("backtest.analizar_mercado", return_value={"analisis": True})
	def test_evaluacion_none_omite_fecha(self, analizar_mock, evaluar_mock):
		self.assertEqual(generar_evaluaciones_historicas(self.datos_ohlcv(3), 2), [])

	@patch("backtest.evaluar_entrada", return_value={"evaluacion": True})
	@patch("backtest.analizar_mercado", return_value={"analisis": True})
	def test_no_modifica_dataframe_original(self, analizar_mock, evaluar_mock):
		datos = self.datos_ohlcv(4)
		original = datos.copy(deep=True)

		generar_evaluaciones_historicas(datos, 2)

		pd.testing.assert_frame_equal(datos, original)

	@patch("backtest.evaluar_entrada", return_value={"evaluacion": True})
	@patch("backtest.analizar_mercado", return_value={"analisis": True})
	def test_orden_cronologico_ascendente(self, analizar_mock, evaluar_mock):
		datos = self.datos_ohlcv(3).iloc[::-1]

		resultados = generar_evaluaciones_historicas(datos, 2)

		self.assertEqual(
			[resultado["fecha"] for resultado in resultados],
			list(self.datos_ohlcv(3).index[1:]),
		)


if __name__ == "__main__":
	unittest.main()
