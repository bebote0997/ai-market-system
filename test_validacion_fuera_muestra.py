import unittest
from unittest.mock import patch

import pandas as pd

from validacion_fuera_muestra import (
	comparar_estadisticas_condiciones,
	crear_resumen_validacion,
	ejecutar_validacion_fuera_muestra,
	evaluar_segmento,
)


class TestValidacionFueraMuestra(unittest.TestCase):
	def datos(self, cantidad=8):
		indice = pd.date_range("2026-01-01", periods=cantidad, freq="D")
		return pd.DataFrame(
			{
				"Open": range(100, 100 + cantidad),
				"High": range(101, 101 + cantidad),
				"Low": range(99, 99 + cantidad),
				"Close": range(100, 100 + cantidad),
				"Volume": [1000] * cantidad,
			},
			index=indice,
		)

	def evaluacion(self, fecha):
		return {
			"fecha": fecha,
			"precio_cierre": 100.0,
			"analisis": {"fecha": fecha},
			"evaluacion": {"resumen": {"favorables": 2}},
		}

	def estadisticas(self, retorno_medio=1.0):
		metrica = {
			"muestras": 2,
			"retorno_medio": retorno_medio,
			"retorno_mediano": 0.5,
			"positivos": 1,
			"negativos": 1,
			"neutros": 0,
			"tasa_positiva": 50.0,
		}
		return {
			"tendencia": {
				"favorable": {"total_evaluaciones": 2, "horizontes": {5: metrica}}
			}
		}

	@patch("validacion_fuera_muestra.calcular_estadisticas_por_condicion", return_value={"condicion": {}})
	@patch("validacion_fuera_muestra.calcular_estadisticas_por_favorables", return_value={"favorables": {}})
	@patch("validacion_fuera_muestra.calcular_resultados_futuros", return_value=[{"resultado": 1}])
	@patch("validacion_fuera_muestra.generar_evaluaciones_historicas")
	def test_evaluar_segmento_reutiliza_pipeline_y_filtra_indices(
		self, generar_mock, futuros_mock, favorables_mock, condiciones_mock
	):
		datos = self.datos(4)
		evaluaciones = [self.evaluacion(datos.index[1]), self.evaluacion(datos.index[2])]
		generar_mock.return_value = evaluaciones

		resultado = evaluar_segmento(datos, 2, (1, 5), {datos.index[2]})

		generar_mock.assert_called_once_with(datos, minimo_historial=2)
		self.assertEqual(resultado["evaluaciones"], [evaluaciones[1]])
		futuros_mock.assert_called_once_with(datos, [evaluaciones[1]], horizontes=(1, 5))
		self.assertEqual(resultado["estadisticas_favorables"], {"favorables": {}})
		self.assertEqual(resultado["estadisticas_condiciones"], {"condicion": {}})

	@patch("validacion_fuera_muestra.calcular_estadisticas_por_condicion", return_value={})
	@patch("validacion_fuera_muestra.calcular_estadisticas_por_favorables", return_value={})
	@patch("validacion_fuera_muestra.calcular_resultados_futuros", return_value=[])
	@patch("validacion_fuera_muestra.generar_evaluaciones_historicas", return_value=[])
	def test_evaluar_segmento_vacio_es_tolerante(self, *mocks):
		resultado = evaluar_segmento(None)
		self.assertEqual(resultado["evaluaciones"], [])
		self.assertEqual(resultado["resultados"], [])

	@patch("validacion_fuera_muestra.preparar_segmento_prueba_con_contexto")
	@patch("validacion_fuera_muestra.evaluar_segmento")
	@patch("validacion_fuera_muestra.dividir_datos_cronologicamente")
	def test_ejecutar_divide_evalua_entrenamiento_y_prueba_con_contexto(
		self, dividir_mock, evaluar_mock, contexto_mock
	):
		datos = self.datos(10)
		entrenamiento = datos.iloc[:7]
		prueba = datos.iloc[7:]
		division = {
			"entrenamiento": entrenamiento,
			"prueba": prueba,
			"total_filas": 10,
			"filas_entrenamiento": 7,
			"filas_prueba": 3,
			"proporcion_entrenamiento": 0.7,
		}
		contexto = {
			"datos_con_contexto": datos,
			"indices_prueba": prueba.index,
			"filas_contexto": 6,
			"filas_prueba": 3,
		}
		division_resultado = {"segmento": "entrenamiento"}
		prueba_resultado = {"segmento": "prueba"}
		dividir_mock.return_value = division
		contexto_mock.return_value = contexto
		evaluar_mock.side_effect = [division_resultado, prueba_resultado]

		resultado = ejecutar_validacion_fuera_muestra(datos, 0.7, 3, (1, 5))

		dividir_mock.assert_called_once_with(datos, 0.7)
		contexto_mock.assert_called_once_with(entrenamiento, prueba, minimo_historial=3)
		pd.testing.assert_frame_equal(evaluar_mock.call_args_list[0].args[0], entrenamiento)
		pd.testing.assert_frame_equal(evaluar_mock.call_args_list[1].args[0], datos)
		self.assertEqual(resultado["entrenamiento"], division_resultado)
		self.assertEqual(resultado["prueba"], prueba_resultado)

	def test_comparacion_calcula_diferencias_y_omite_ausentes(self):
		entrenamiento = self.estadisticas(1.0)
		prueba = self.estadisticas(3.0)
		prueba["tendencia"]["desfavorable"] = {"horizontes": {5: {"muestras": 1}}}

		resultado = comparar_estadisticas_condiciones(entrenamiento, prueba, 5)
		comparacion = resultado["tendencia"]["favorable"]

		self.assertAlmostEqual(comparacion["diferencias"]["retorno_medio"], 2.0)
		self.assertAlmostEqual(comparacion["diferencias"]["retorno_mediano"], 0.0)
		self.assertAlmostEqual(comparacion["diferencias"]["tasa_positiva"], 0.0)
		self.assertNotIn("desfavorable", resultado["tendencia"])

	def test_comparacion_none_produce_diferencia_none(self):
		entrenamiento = self.estadisticas()
		prueba = self.estadisticas()
		prueba["tendencia"]["favorable"]["horizontes"][5]["retorno_medio"] = None

		resultado = comparar_estadisticas_condiciones(entrenamiento, prueba, 5)
		self.assertIsNone(resultado["tendencia"]["favorable"]["diferencias"]["retorno_medio"])

	def test_resumen_cuenta_evaluaciones_y_resultados(self):
		resultado = crear_resumen_validacion({
			"entrenamiento": {"evaluaciones": [1, 2], "resultados": [1]},
			"prueba": {"evaluaciones": [3], "resultados": [2, 3]},
			"estadisticas_condiciones": {},
		}, 5)

		self.assertEqual(resultado["horizonte"], 5)
		self.assertEqual(resultado["entrenamiento"], {"evaluaciones": 2, "resultados": 1})
		self.assertEqual(resultado["prueba"], {"evaluaciones": 1, "resultados": 2})

	def test_entradas_invalidas_no_rompen(self):
		self.assertIsNone(ejecutar_validacion_fuera_muestra(None))
		self.assertEqual(comparar_estadisticas_condiciones(None, None), {})
		self.assertIsNone(crear_resumen_validacion(None))

	def test_originales_no_se_modifican(self):
		datos = self.datos(10)
		original = datos.copy(deep=True)
		indices_prueba = datos.index[7:]
		resultado = ejecutar_validacion_fuera_muestra(datos, 0.7, 3, (1,))
		if resultado is not None:
			pd.testing.assert_frame_equal(datos, original)
		self.assertEqual(list(indices_prueba), list(datos.index[7:]))

	@patch("backtest.evaluar_entrada", return_value={"resumen": {"favorables": 1}})
	@patch("backtest.analizar_mercado", return_value={"analisis": True})
	def test_no_hay_look_ahead_en_las_ventanas_historicas(self, analizar_mock, evaluar_mock):
		datos = self.datos(6)

		evaluar_segmento(datos, minimo_historial=3, horizontes=(1,))

		ventanas = [llamada.args[0] for llamada in analizar_mock.call_args_list]
		self.assertEqual([len(ventana) for ventana in ventanas], [3, 4, 5, 6])
		for ventana in ventanas:
			self.assertEqual(ventana.index[-1], datos.index[len(ventana) - 1])


if __name__ == "__main__":
	unittest.main()
