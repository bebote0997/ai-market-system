import copy
import unittest

import pandas as pd

from estrategia import crear_configuracion_estrategia, generar_eventos_estrategia
from metricas_estrategia import calcular_metricas_estrategia
from riesgo import crear_configuracion_riesgo
from simulador import (
	crear_configuracion_simulacion,
	simular_operaciones,
	simular_operaciones_con_riesgo,
)


class TestSimulador(unittest.TestCase):
	def datos(self, cierres=None):
		cierres = cierres or [100.0, 110.0, 120.0, 100.0, 80.0, 100.0, 100.0]
		cierres = [999.0] + list(cierres)
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

	def test_no_superposicion_y_senal_al_cierre_de_salida_entra_al_dia_siguiente(self):
		datos = self.datos([100] * 12)
		eventos = [self.evento(datos, 0), self.evento(datos, 5), self.evento(datos, 6)]
		resultado = simular_operaciones(datos, eventos, crear_configuracion_simulacion(periodo_salida=5, coste_porcentual=0))
		self.assertEqual([op["fecha_entrada"] for op in resultado["operaciones"]], [datos.index[1], datos.index[7]])

	def test_datos_invalidos_no_abren(self):
		datos = self.datos([100] * 7)
		config = crear_configuracion_simulacion(periodo_salida=5)
		eventos = [{"fecha": "no existe"}, self.evento(datos, 0)]
		datos_nan = datos.copy()
		datos_nan.loc[datos.index[1], "Open"] = float("nan")
		self.assertEqual(len(simular_operaciones(datos_nan, eventos, config)["operaciones"]), 0)
		self.assertEqual(len(simular_operaciones(datos, eventos, config)["operaciones"]), 1)
		pendiente = simular_operaciones(datos, [self.evento(datos, 6)], config)
		self.assertEqual(len(pendiente["operaciones"]), 0)
		self.assertEqual(pendiente["posicion_abierta"]["fecha_entrada"], datos.index[7])

	def test_orden_curva_y_originales(self):
		datos = self.datos([100] * 7)
		evento = self.evento(datos, 0)
		datos = datos.iloc[::-1]
		original = datos.copy(deep=True)
		resultado = simular_operaciones(datos, [evento], crear_configuracion_simulacion(periodo_salida=2, coste_porcentual=0))
		self.assertIsNone(resultado["curva_capital"][0]["fecha"])
		self.assertEqual(len(resultado["curva_capital"]), len(datos) + 1)
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

	def datos_riesgo(self, opens, highs, lows, closes):
		indice = pd.date_range("2026-02-01", periods=len(opens), freq="D")
		return pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes}, index=indice)

	def evento_riesgo(self, datos, posicion=0, atr=5.0):
		return {"fecha": datos.index[posicion], "precio": 999, "atr_senal": atr, "evaluacion": {}}

	def riesgo_config(self, metodo="atr", coste=0.0, maximo=100.0):
		return crear_configuracion_riesgo(1.0, metodo, 2.0, 2.0, maximo, 2.0)

	def test_riesgo_entra_open_y_stop_target_temporal(self):
		datos = self.datos_riesgo([100, 100, 100, 100], [101, 101, 103, 101], [99, 99, 85, 99], [100, 100, 102, 100])
		resultado = simular_operaciones_con_riesgo(datos, [self.evento_riesgo(datos)], crear_configuracion_simulacion(10000, 100, 2, 0), self.riesgo_config())
		self.assertEqual(resultado["operaciones"][0]["fecha_entrada"], datos.index[1])
		self.assertEqual(resultado["operaciones"][0]["motivo_salida"], "stop")
		self.assertAlmostEqual(resultado["operaciones"][0]["precio_salida"], 90)

		datos_target = self.datos_riesgo([100, 100, 100, 100], [101, 101, 120, 101], [99, 99, 99, 99], [100, 100, 110, 100])
		resultado = simular_operaciones_con_riesgo(datos_target, [self.evento_riesgo(datos_target)], crear_configuracion_simulacion(10000, 100, 2, 0), self.riesgo_config())
		self.assertEqual(resultado["operaciones"][0]["motivo_salida"], "target")

	def test_ambiguous_bar_stop_first_and_gaps(self):
		datos = self.datos_riesgo([100, 100, 100], [101, 101, 120], [99, 99, 80], [100, 100, 100])
		resultado = simular_operaciones_con_riesgo(datos, [self.evento_riesgo(datos)], crear_configuracion_simulacion(10000, 100, 2, 0), self.riesgo_config())
		self.assertEqual(resultado["operaciones"][0]["motivo_salida"], "stop")
		self.assertFalse(resultado["operaciones"][0]["salida_por_gap"])

		gap = self.datos_riesgo([100, 100, 85], [101, 101, 90], [99, 99, 80], [100, 100, 85])
		resultado = simular_operaciones_con_riesgo(gap, [self.evento_riesgo(gap)], crear_configuracion_simulacion(10000, 100, 2, 0), self.riesgo_config())
		self.assertTrue(resultado["operaciones"][0]["salida_por_gap"])
		self.assertAlmostEqual(resultado["operaciones"][0]["precio_salida"], 85)

	def test_riesgo_capital_parcial_y_posicion_abierta(self):
		datos = self.datos_riesgo([100, 100, 100], [101, 101, 101], [99, 99, 99], [100, 90, 90])
		config = crear_configuracion_simulacion(10000, 25, 5, 0)
		resultado = simular_operaciones_con_riesgo(datos, [self.evento_riesgo(datos)], config, self.riesgo_config(maximo=25))
		self.assertIsNotNone(resultado["posicion_abierta"])
		self.assertLess(resultado["capital_final"], 10000)
		self.assertLess(min(punto["capital"] for punto in resultado["curva_capital"]), 10000)

	def test_riesgo_temporal_y_invalido_no_abre(self):
		datos = self.datos_riesgo([100, 100, 100, 100], [101]*4, [99]*4, [100]*4)
		resultado = simular_operaciones_con_riesgo(datos, [self.evento_riesgo(datos)], crear_configuracion_simulacion(10000, 100, 2, 0), self.riesgo_config())
		self.assertEqual(resultado["operaciones"][0]["motivo_salida"], "tiempo")
		bad = self.datos_riesgo([100, 100, 100], [101]*3, [99]*3, [100, 100, 100])
		self.assertEqual(simular_operaciones_con_riesgo(bad, [self.evento_riesgo(bad, atr=None)], crear_configuracion_simulacion(), self.riesgo_config())["operaciones"], [])

	def test_atr_senal_no_usa_datos_de_entrada_o_futuros(self):
		base = self.datos_riesgo([100, 100, 100, 100], [101, 101, 101, 101], [99, 99, 99, 99], [100, 100, 100, 100])
		alterado = base.copy()
		alterado.loc[alterado.index[1], ["High", "Low", "Close"]] = [999, 1, 500]
		config = crear_configuracion_simulacion(10000, 100, 2, 0)
		resultado_base = simular_operaciones_con_riesgo(base, [self.evento_riesgo(base)], config, self.riesgo_config())
		resultado_alterado = simular_operaciones_con_riesgo(alterado, [self.evento_riesgo(alterado)], config, self.riesgo_config())
		self.assertEqual(resultado_base["operaciones"][0]["stop_inicial"], resultado_alterado["operaciones"][0]["stop_inicial"])

	def test_posicion_abierta_tiene_contrato_estable_y_equity_correcta(self):
		datos = self.datos_riesgo(
			[100, 100, 100], [101, 101, 101], [99, 99, 99], [100, 90, 90]
		)
		resultado = simular_operaciones_con_riesgo(
			datos,
			[self.evento_riesgo(datos)],
			crear_configuracion_simulacion(10000, 25, 5, 0),
			self.riesgo_config(maximo=25),
		)
		abierta = resultado["posicion_abierta"]
		campos = {
			"fecha_senal", "fecha_entrada", "precio_entrada", "cantidad",
			"capital_utilizado", "efectivo_no_utilizado", "stop_inicial",
			"target_inicial", "metodo_stop", "atr_senal", "ultimo_precio",
			"ultima_fecha", "equity_actual",
		}
		self.assertTrue(campos.issubset(abierta))
		self.assertAlmostEqual(
			abierta["equity_actual"],
			abierta["efectivo_no_utilizado"] + abierta["cantidad"] * abierta["ultimo_precio"],
		)


if __name__ == "__main__":
	unittest.main()
