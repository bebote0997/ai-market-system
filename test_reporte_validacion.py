import unittest

from reporte_validacion import crear_reporte_comparativo


class TestCrearReporteComparativo(unittest.TestCase):
	def estadistica(self, retorno_medio=1.0):
		return {
			"muestras": 4,
			"retorno_medio": retorno_medio,
			"retorno_mediano": 0.5,
			"tasa_positiva": 50.0,
		}

	def investigacion(self):
		condicion = {
			"entrenamiento": self.estadistica(1.0),
			"prueba": self.estadistica(2.0),
			"diferencias": {"retorno_medio": 1.0},
		}
		return {
			"ticker": "AAPL",
			"filas_historicas": 100,
			"validacion": {
				"division": {
					"filas_entrenamiento": 70,
					"filas_prueba": 30,
				},
				"entrenamiento": {"evaluaciones": [1, 2], "estadisticas_condiciones": {
                    "rsi": {"favorable": {"horizontes": {5: self.estadistica(1.0)}}}
                }},
                "prueba": {"evaluaciones": [3], "estadisticas_condiciones": {
                    "rsi": {"favorable": {"horizontes": {5: self.estadistica(2.0)}}}
                }},
			},
			"resumen": {
				"comparacion_condiciones": {
					"rsi": {"favorable": condicion},
				}
			},
		}

	def test_investigaciones_none_y_vacias(self):
		self.assertEqual(crear_reporte_comparativo(None), {})
		self.assertEqual(crear_reporte_comparativo({}), {})

	def test_transforma_activo_valido(self):
		reporte = crear_reporte_comparativo({"AAPL": self.investigacion()})
		activo = reporte["AAPL"]
		self.assertEqual(activo["ticker"], "AAPL")
		self.assertEqual(activo["datos"], {
			"filas_historicas": 100,
			"filas_entrenamiento": 70,
			"filas_prueba": 30,
		})
		self.assertEqual(activo["muestras"], {
			"evaluaciones_entrenamiento": 2,
			"evaluaciones_prueba": 1,
		})
		self.assertEqual(activo["condiciones"]["rsi"]["favorable"]["prueba"]["retorno_medio"], 2.0)

	def test_conserva_none_y_errores_y_multiples_activos(self):
		investigaciones = {
			"AAPL": self.investigacion(),
			"MSFT": {"ticker": "MSFT", "error": "datos_no_disponibles"},
		}
		original = repr(investigaciones)
		reporte = crear_reporte_comparativo(investigaciones)
		self.assertEqual(reporte["MSFT"], {
			"ticker": "MSFT",
			"error": "datos_no_disponibles",
		})
		self.assertEqual(repr(investigaciones), original)

	def test_metricas_ausentes_se_conservan_en_horizonte_solicitado(self):
		investigacion = self.investigacion()
		investigacion["validacion"]["entrenamiento"]["estadisticas_condiciones"]["rsi"]["favorable"]["horizontes"][5]["retorno_medio"] = None
		reporte = crear_reporte_comparativo({"AAPL": investigacion})
		self.assertIsNone(
			reporte["AAPL"]["condiciones"]["rsi"]["favorable"]["entrenamiento"]["retorno_medio"]
		)


if __name__ == "__main__":
	unittest.main()
