import unittest
from unittest.mock import patch

import pandas as pd

from backtest import (
	calcular_estadisticas_por_condicion,
	calcular_estadisticas_por_favorables,
	calcular_resultados_futuros,
	generar_evaluaciones_historicas,
)


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


class TestCalcularResultadosFuturos(unittest.TestCase):
	def datos(self, cierres=None):
		cierres = cierres or [100.0, 110.0, 90.0, 100.0, 120.0, 130.0]
		indice = pd.date_range("2026-01-01", periods=len(cierres), freq="D")
		return pd.DataFrame(
			{
				"Open": cierres,
				"High": [precio + 2 for precio in cierres],
				"Low": [precio - 2 for precio in cierres],
				"Close": cierres,
				"Volume": [1000] * len(cierres),
			},
			index=indice,
		)

	def evaluaciones(self, datos, posiciones=None):
		posiciones = posiciones or [0]
		return [
			{
				"fecha": datos.index[posicion],
				"precio_cierre": datos["Close"].iloc[posicion],
				"analisis": {"id": posicion},
				"evaluacion": {"estado": "ok"},
			}
			for posicion in posiciones
		]

	def test_retorno_futuro_positivo_negativo_y_cero(self):
		datos = self.datos([100.0, 110.0, 90.0, 100.0])
		resultado = calcular_resultados_futuros(
			datos, self.evaluaciones(datos), horizontes=(1, 2, 3)
		)[0]

		self.assertAlmostEqual(resultado["retornos_futuros"][1], 10.0)
		self.assertAlmostEqual(resultado["retornos_futuros"][2], -10.0)
		self.assertAlmostEqual(resultado["retornos_futuros"][3], 0.0)

	def test_varios_horizontes_y_fuera_del_dataframe(self):
		datos = self.datos([100.0, 110.0, 120.0])
		resultado = calcular_resultados_futuros(
			datos, self.evaluaciones(datos), horizontes=(1, 2, 5)
		)[0]

		self.assertAlmostEqual(resultado["retornos_futuros"][1], 10.0)
		self.assertAlmostEqual(resultado["retornos_futuros"][2], 20.0)
		self.assertIsNone(resultado["retornos_futuros"][5])

	def test_horizontes_invalidos_se_ignoran(self):
		datos = self.datos([100.0, 110.0])
		resultado = calcular_resultados_futuros(
			datos, self.evaluaciones(datos), horizontes=(0, -1, "5", True, 1)
		)[0]

		self.assertEqual(set(resultado["retornos_futuros"]), {1})

	def test_todos_los_horizontes_invalidos_devuelve_lista_vacia(self):
		datos = self.datos([100.0, 110.0])
		self.assertEqual(
			calcular_resultados_futuros(datos, self.evaluaciones(datos), (0, -1)),
			[],
		)

	def test_datos_invalidos_devuelven_lista_vacia(self):
		datos = self.datos([100.0, 110.0])
		evaluaciones = self.evaluaciones(datos)

		self.assertEqual(calcular_resultados_futuros(None, evaluaciones), [])
		self.assertEqual(calcular_resultados_futuros(pd.DataFrame(), evaluaciones), [])
		self.assertEqual(calcular_resultados_futuros(datos, None), [])
		self.assertEqual(calcular_resultados_futuros(datos, []), [])
		self.assertEqual(
			calcular_resultados_futuros(datos.drop(columns="Close"), evaluaciones),
			[],
		)

	def test_precio_base_cero_y_close_futuro_nan_devuelven_none(self):
		datos = self.datos([100.0, float("nan"), 120.0])
		evaluaciones = self.evaluaciones(datos)
		evaluaciones[0]["precio_cierre"] = 0

		resultado = calcular_resultados_futuros(datos, evaluaciones, (1, 2))[0]

		self.assertIsNone(resultado["retornos_futuros"][1])
		self.assertIsNone(resultado["retornos_futuros"][2])

		evaluaciones[0]["precio_cierre"] = 100.0
		resultado = calcular_resultados_futuros(datos, evaluaciones, (1, 2))[0]
		self.assertIsNone(resultado["retornos_futuros"][1])
		self.assertAlmostEqual(resultado["retornos_futuros"][2], 20.0)

	def test_evaluaciones_originales_se_conservan(self):
		datos = self.datos([100.0, 110.0])
		evaluaciones = self.evaluaciones(datos)
		analisis_original = evaluaciones[0]["analisis"]
		evaluacion_original = evaluaciones[0]["evaluacion"]

		resultado = calcular_resultados_futuros(datos, evaluaciones, (1,))

		self.assertIsNot(resultado[0], evaluaciones[0])
		self.assertIs(resultado[0]["analisis"], analisis_original)
		self.assertIs(resultado[0]["evaluacion"], evaluacion_original)
		self.assertNotIn("retornos_futuros", evaluaciones[0])

	@patch("backtest.analizar_mercado")
	@patch("backtest.evaluar_entrada")
	def test_no_llama_al_motor_ni_al_validador(self, evaluar_mock, analizar_mock):
		datos = self.datos([100.0, 110.0])
		calcular_resultados_futuros(datos, self.evaluaciones(datos), (1,))

		analizar_mock.assert_not_called()
		evaluar_mock.assert_not_called()


class TestCalcularEstadisticasPorFavorables(unittest.TestCase):
	def resultados(self):
		return [
			{
				"evaluacion": {"resumen": {"favorables": 3}},
				"retornos_futuros": {1: 10.0, 5: -5.0, 10: 0.0},
			},
			{
				"evaluacion": {"resumen": {"favorables": 3}},
				"retornos_futuros": {1: 20.0, 5: None, 10: 5.0},
			},
			{
				"evaluacion": {"resumen": {"favorables": 4}},
				"retornos_futuros": {1: -10.0, 5: 5.0, 10: float("nan")},
			},
		]

	def test_agrupa_y_calcula_estadisticas(self):
		resultado = calcular_estadisticas_por_favorables(self.resultados(), (1, 5, 10))

		self.assertEqual(resultado[3]["total_evaluaciones"], 2)
		self.assertEqual(resultado[4]["total_evaluaciones"], 1)
		estadistica = resultado[3]["horizontes"][1]
		self.assertEqual(estadistica["muestras"], 2)
		self.assertAlmostEqual(estadistica["retorno_medio"], 15.0)
		self.assertAlmostEqual(estadistica["retorno_mediano"], 15.0)
		self.assertEqual(estadistica["positivos"], 2)
		self.assertEqual(estadistica["negativos"], 0)
		self.assertEqual(estadistica["neutros"], 0)
		self.assertAlmostEqual(estadistica["tasa_positiva"], 100.0)

		estadistica = resultado[3]["horizontes"][5]
		self.assertEqual(estadistica["muestras"], 1)
		self.assertAlmostEqual(estadistica["retorno_medio"], -5.0)
		self.assertAlmostEqual(estadistica["retorno_mediano"], -5.0)
		self.assertEqual(estadistica["positivos"], 0)
		self.assertEqual(estadistica["negativos"], 1)
		self.assertEqual(estadistica["neutros"], 0)
		self.assertAlmostEqual(estadistica["tasa_positiva"], 0.0)

	def test_horizonte_sin_muestras_devuelve_none_en_metricas(self):
		resultados = [
			{
				"evaluacion": {"resumen": {"favorables": 5}},
				"retornos_futuros": {1: None},
			}
		]

		estadistica = calcular_estadisticas_por_favorables(resultados, (1,))

		self.assertEqual(estadistica[5]["horizontes"][1], {
			"muestras": 0,
			"retorno_medio": None,
			"retorno_mediano": None,
			"positivos": 0,
			"negativos": 0,
			"neutros": 0,
			"tasa_positiva": None,
		})

	def test_horizontes_invalidos_y_entradas_vacias(self):
		resultados = self.resultados()
		self.assertEqual(calcular_estadisticas_por_favorables(None), {})
		self.assertEqual(calcular_estadisticas_por_favorables([]), {})
		self.assertEqual(calcular_estadisticas_por_favorables(resultados, None), {})
		self.assertEqual(calcular_estadisticas_por_favorables(resultados, []), {})
		resultado = calcular_estadisticas_por_favorables(
			resultados, (0, -1, "5", True, 1)
		)
		self.assertEqual(set(resultado[3]["horizontes"]), {1})
		self.assertEqual(
			calcular_estadisticas_por_favorables(resultados, (0, -1)),
			{},
		)

	def test_resultados_malformados_se_omiten(self):
		resultados = self.resultados() + [
			{},
			{"evaluacion": {}},
			{"evaluacion": {"resumen": {"favorables": float("nan")}}},
			{"evaluacion": {"resumen": {"favorables": "3"}}},
		]

		resultado = calcular_estadisticas_por_favorables(resultados, (1,))

		self.assertEqual(set(resultado), {3, 4})
		self.assertEqual(resultado[3]["total_evaluaciones"], 2)

	def test_no_modifica_resultados_originales(self):
		resultados = self.resultados()
		original = [
			{
				"evaluacion": dict(resultado["evaluacion"]),
				"retornos_futuros": dict(resultado["retornos_futuros"]),
			}
			for resultado in resultados
		]

		calcular_estadisticas_por_favorables(resultados, (1, 5))

		self.assertEqual(resultados, original)


class TestCalcularEstadisticasPorCondicion(unittest.TestCase):
	def resultados(self):
		return [
			{
				"evaluacion": {
					"condiciones": {
						"tendencia": {"estado": "favorable"},
						"rsi": {"estado": "favorable"},
						"macd": {"estado": "desfavorable"},
						"volumen": {"estado": "no_disponible"},
					}
				},
				"retornos_futuros": {1: 10.0, 5: -5.0},
			},
			{
				"evaluacion": {
					"condiciones": {
						"tendencia": {"estado": "favorable"},
						"rsi": {"estado": "desfavorable"},
						"macd": {"estado": "desfavorable"},
						"volumen": {"estado": "favorable"},
						"estructura_precio": {"estado": "favorable"},
					}
				},
				"retornos_futuros": {1: 20.0, 5: 0.0},
			},
			{
				"evaluacion": {
					"condiciones": {
						"tendencia": {"estado": "no_disponible"},
						"rsi": {"estado": "favorable"},
						"macd": {"estado": "desfavorable"},
						"volumen": {"estado": "no_disponible"},
						"estructura_precio": {"estado": "favorable"},
					}
				},
				"retornos_futuros": {1: float("nan"), 5: 5.0},
			},
		]

	def test_agrupa_condiciones_y_calcula_metricas(self):
		resultado = calcular_estadisticas_por_condicion(self.resultados(), (1, 5))

		tendencia = resultado["tendencia"]["favorable"]
		self.assertEqual(tendencia["total_evaluaciones"], 2)
		self.assertEqual(tendencia["horizontes"][1]["muestras"], 2)
		self.assertAlmostEqual(tendencia["horizontes"][1]["retorno_medio"], 15.0)
		self.assertAlmostEqual(tendencia["horizontes"][1]["retorno_mediano"], 15.0)
		self.assertEqual(tendencia["horizontes"][1]["positivos"], 2)
		self.assertEqual(tendencia["horizontes"][1]["negativos"], 0)
		self.assertEqual(tendencia["horizontes"][1]["neutros"], 0)
		self.assertAlmostEqual(tendencia["horizontes"][1]["tasa_positiva"], 100.0)

		self.assertEqual(resultado["macd"]["desfavorable"]["total_evaluaciones"], 3)
		self.assertEqual(resultado["rsi"]["favorable"]["total_evaluaciones"], 2)
		self.assertEqual(resultado["volumen"]["no_disponible"]["total_evaluaciones"], 2)

	def test_horizontes_independientes_y_none_nan_ignorados(self):
		resultado = calcular_estadisticas_por_condicion(self.resultados(), (1, 5, 10))
		estadistica = resultado["tendencia"]["favorable"]["horizontes"][5]

		self.assertEqual(estadistica["muestras"], 2)
		self.assertAlmostEqual(estadistica["retorno_medio"], -2.5)
		self.assertAlmostEqual(estadistica["retorno_mediano"], -2.5)
		self.assertEqual(estadistica["positivos"], 0)
		self.assertEqual(estadistica["negativos"], 1)
		self.assertEqual(estadistica["neutros"], 1)
		self.assertAlmostEqual(estadistica["tasa_positiva"], 0.0)

		estadistica = resultado["tendencia"]["favorable"]["horizontes"][10]
		self.assertEqual(estadistica["muestras"], 0)
		self.assertIsNone(estadistica["retorno_medio"])
		self.assertIsNone(estadistica["retorno_mediano"])
		self.assertIsNone(estadistica["tasa_positiva"])

	def test_no_disponible_falta_y_estado_invalido_se_omiten(self):
		resultados = self.resultados()
		resultados.append({
			"evaluacion": {
				"condiciones": {
					"rsi": {"estado": "desconocido"},
					"macd": {"estado": "favorable"},
				}
			},
			"retornos_futuros": {1: 30.0},
		})

		resultado = calcular_estadisticas_por_condicion(resultados, (1,))

		self.assertNotIn("desconocido", resultado["rsi"])
		self.assertEqual(resultado["macd"]["favorable"]["total_evaluaciones"], 1)
		self.assertEqual(resultado["estructura_precio"]["favorable"]["total_evaluaciones"], 2)

	def test_entradas_invalidas_y_horizontes_invalidos(self):
		resultados = self.resultados()
		self.assertEqual(calcular_estadisticas_por_condicion(None), {})
		self.assertEqual(calcular_estadisticas_por_condicion([]), {})
		self.assertEqual(calcular_estadisticas_por_condicion(resultados, None), {})
		self.assertEqual(calcular_estadisticas_por_condicion(resultados, []), {})
		resultado = calcular_estadisticas_por_condicion(resultados, (0, -1, "5", True, 1))
		self.assertEqual(set(resultado["tendencia"]["favorable"]["horizontes"]), {1})
		self.assertEqual(calcular_estadisticas_por_condicion(resultados, (0, -1)), {})

	def test_resultados_malformados_no_rompen_y_no_se_modifican(self):
		resultados = self.resultados()
		original = [
			{
				"evaluacion": {
					"condiciones": dict(resultado["evaluacion"]["condiciones"])
				},
				"retornos_futuros": dict(resultado["retornos_futuros"]),
			}
			for resultado in resultados
		]
		resultados.extend([None, {}, {"evaluacion": None}, {"evaluacion": {"condiciones": None}}])

		calcular_estadisticas_por_condicion(resultados, (1,))

		self.assertEqual(resultados[:3], original)


if __name__ == "__main__":
	unittest.main()
