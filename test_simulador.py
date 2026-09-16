import copy
import unittest

import pandas as pd

from estrategia import crear_configuracion_estrategia, generar_eventos_estrategia
from metricas_estrategia import calcular_metricas_estrategia
from simulador import crear_configuracion_simulacion, simular_operaciones


class TestSimulador(unittest.TestCase):
	def datos(self, cierres=None):
		cierres = cierres or [100.0, 110.0, 120.0, 100.0, 80.0, 100.0, 100.0]
		indice = pd.date_range("2026-01-01", periods=len(cierres), freq="D")
		return pd.DataFrame({
			"Open": cierres,
			"High": [precio + 2 for precio in cierres],
			"Low": [precio - 2 for precio in cierres],
			"Close": cierres,
			"Volume": [1000] * len(cierres),
		}, index=indice)

	def evento(self, datos, posicion):
		return {"fecha": datos.index[posicion], "precio": 999, "evaluacion": {"ok": True}}

	def test_configuracion_default_y_invalidas(self):
		self.assertEqual(crear_configuracion_simulacion(), {
			"capital_inicial": 10000.0,
			"porcentaje_capital_por_operacion": 100.0,
			"periodo_salida": 5,
			"coste_porcentual": 0.1,
		})
		self.assertIsNone(crear_configuracion_simulacion(0))
		self.assertIsNone(crear_configuracion_simulacion(10000, 0))
		self.assertIsNone(crear_configuracion_simulacion(10000, 101))
		self.assertIsNone(crear_configuracion_simulacion(10000, 100, 0))
		self.assertIsNone(crear_configuracion_simulacion(10000, 100, True))
		self.assertIsNone(crear_configuracion_simulacion(10000, 100, 5, -1))
		self.assertIsNone(crear_configuracion_simulacion(10000, True))

	def test_operacion_positiva_cantidad_retorno_y_coste_unico(self):
		datos = self.datos([100, 100, 100, 100, 100, 110])
		config = crear_configuracion_simulacion(10000, 100, 5, 0.1)
		resultado = simular_operaciones(datos, [self.evento(datos, 0)], config)
		operacion = resultado["operaciones"][0]
		self.assertAlmostEqual(operacion["cantidad"], 100)
		self.assertAlmostEqual(operacion["resultado_bruto"], 1000)
		self.assertAlmostEqual(operacion["coste"], 10)
		self.assertAlmostEqual(operacion["resultado_neto"], 990)
		self.assertAlmostEqual(operacion["retorno_bruto_pct"], 10)
		self.assertAlmostEqual(operacion["retorno_neto_pct"], 9.9)
		self.assertAlmostEqual(resultado["capital_final"], 10990)

	def test_operacion_negativa_neutra_y_coste_convierte_neutra(self):
		datos = self.datos([100, 100, 100, 100, 100, 90])
		resultado = simular_operaciones(datos, [self.evento(datos, 0)], crear_configuracion_simulacion(10000, 100, 5, 0))
		self.assertAlmostEqual(resultado["operaciones"][0]["resultado_neto"], -1000)
		datos_neutros = self.datos([100, 100, 100, 100, 100, 100])
		resultado = simular_operaciones(datos_neutros, [self.evento(datos_neutros, 0)], crear_configuracion_simulacion(10000, 100, 5, 0.1))
		self.assertAlmostEqual(resultado["operaciones"][0]["resultado_neto"], -10)

	def test_capital_parcial_y_compuesto(self):
		datos = self.datos([100, 100, 100, 100, 100, 110, 110])
		config = crear_configuracion_simulacion(10000, 50, 5, 0)
		resultado = simular_operaciones(datos, [self.evento(datos, 0), self.evento(datos, 1)], config)
		self.assertEqual(len(resultado["operaciones"]), 1)
		self.assertAlmostEqual(resultado["operaciones"][0]["capital_utilizado"], 5000)

	def test_no_superposicion_evento_en_salida_se_ignora_y_posterior_se_acepta(self):
		datos = self.datos([100] * 12)
		eventos = [self.evento(datos, 0), self.evento(datos, 5), self.evento(datos, 6)]
		resultado = simular_operaciones(datos, eventos, crear_configuracion_simulacion(periodo_salida=5, coste_porcentual=0))
		self.assertEqual([op["fecha_entrada"] for op in resultado["operaciones"]], [datos.index[0], datos.index[6]])

	def test_datos_invalidos_no_abren(self):
		datos = self.datos([100] * 7)
		config = crear_configuracion_simulacion(periodo_salida=5)
		eventos = [{"fecha": "no existe"}, self.evento(datos, 0)]
		datos_nan = datos.copy()
		datos_nan.loc[datos.index[0], "Close"] = float("nan")
		self.assertEqual(len(simular_operaciones(datos_nan, [eventos[0]], config)["operaciones"]), 0)
		self.assertEqual(len(simular_operaciones(datos, eventos, config)["operaciones"]), 1)
		self.assertEqual(len(simular_operaciones(datos, [self.evento(datos, 5)], config)["operaciones"]), 0)

	def test_orden_curva_y_originales(self):
		datos = self.datos([100] * 7)
		evento = self.evento(datos, 0)
		datos = datos.iloc[::-1]
		original = datos.copy(deep=True)
		resultado = simular_operaciones(datos, [evento], crear_configuracion_simulacion(periodo_salida=2, coste_porcentual=0))
		self.assertIsNone(resultado["curva_capital"][0]["fecha"])
		self.assertEqual(len(resultado["curva_capital"]), 2)
		pd.testing.assert_frame_equal(datos, original)

	def test_integracion_eventos_simulacion_metricas(self):
		datos = self.datos([100, 100, 100, 100, 100, 110])
		evaluacion = {
			"fecha": datos.index[0],
			"precio_cierre": 100,
			"evaluacion": {
				"resumen": {"favorables": 5, "desfavorables": 0},
				"condiciones": {},
			},
		}
		eventos = generar_eventos_estrategia([evaluacion], crear_configuracion_estrategia())
		resultado = simular_operaciones(datos, eventos, crear_configuracion_simulacion(coste_porcentual=0))
		metricas = calcular_metricas_estrategia(resultado)
		self.assertEqual(len(eventos), 1)
		self.assertEqual(len(resultado["operaciones"]), 1)
		self.assertAlmostEqual(metricas["resultado_neto_total"], 1000)


if __name__ == "__main__":
	unittest.main()
