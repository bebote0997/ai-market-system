import copy
import unittest

from metricas_estrategia import calcular_metricas_estrategia


class TestMetricasEstrategia(unittest.TestCase):
	def resultado(self, operaciones, curva=None):
		return {
			"capital_inicial": 1000.0,
			"capital_final": 1100.0 if operaciones else 1000.0,
			"operaciones": operaciones,
			"curva_capital": curva or [{"fecha": None, "capital": 1000.0}],
		}

	def operacion(self, neto, retorno):
		return {"resultado_neto": neto, "retorno_neto_pct": retorno}

	def test_none_y_cero_operaciones(self):
		self.assertIsNone(calcular_metricas_estrategia(None))
		metricas = calcular_metricas_estrategia(self.resultado([]))
		self.assertEqual(metricas["numero_operaciones"], 0)
		self.assertEqual(metricas["tasa_acierto"], 0.0)
		self.assertIsNone(metricas["profit_factor"])
		self.assertIsNone(metricas["retorno_medio_operacion_pct"])

	def test_conteos_y_metricas_financieras(self):
		operaciones = [self.operacion(100, 10), self.operacion(-50, -5), self.operacion(0, 0)]
		metricas = calcular_metricas_estrategia(self.resultado(operaciones))
		self.assertEqual(metricas["numero_operaciones"], 3)
		self.assertEqual(metricas["ganadoras"], 1)
		self.assertEqual(metricas["perdedoras"], 1)
		self.assertEqual(metricas["neutras"], 1)
		self.assertAlmostEqual(metricas["tasa_acierto"], 100 / 3)
		self.assertAlmostEqual(metricas["ganancia_total"], 100)
		self.assertAlmostEqual(metricas["perdida_total"], 50)
		self.assertAlmostEqual(metricas["profit_factor"], 2)
		self.assertAlmostEqual(metricas["resultado_neto_total"], 50)
		self.assertAlmostEqual(metricas["retorno_total_pct"], 10)
		self.assertAlmostEqual(metricas["retorno_medio_operacion_pct"], 5 / 3)
		self.assertAlmostEqual(metricas["ganancia_media"], 100)
		self.assertAlmostEqual(metricas["perdida_media"], -50)
		self.assertAlmostEqual(metricas["expectativa"], 50 / 3)

	def test_profit_factor_sin_perdidas(self):
		metricas = calcular_metricas_estrategia(self.resultado([self.operacion(10, 1)]))
		self.assertIsNone(metricas["profit_factor"])

	def test_drawdown_cero_y_caida_recuperacion(self):
		curva = [
			{"fecha": None, "capital": 1000},
			{"fecha": "a", "capital": 1100},
			{"fecha": "b", "capital": 1000},
			{"fecha": "c", "capital": 1200},
		]
		metricas = calcular_metricas_estrategia(self.resultado([], curva))
		self.assertAlmostEqual(metricas["max_drawdown_pct"], -100 / 1100 * 100)
		self.assertAlmostEqual(metricas["curva_drawdown"][2]["drawdown_pct"], -100 / 1100 * 100)
		self.assertEqual(metricas["curva_drawdown"][3]["drawdown_pct"], 0.0)

	def test_curva_creciente_tiene_drawdown_cero(self):
		curva = [{"fecha": None, "capital": 1000}, {"fecha": "a", "capital": 1100}]
		metricas = calcular_metricas_estrategia(self.resultado([], curva))
		self.assertEqual(metricas["max_drawdown_pct"], 0.0)

	def test_no_modifica_resultado_original(self):
		resultado = self.resultado([self.operacion(10, 1)])
		original = copy.deepcopy(resultado)
		calcular_metricas_estrategia(resultado)
		self.assertEqual(resultado, original)


if __name__ == "__main__":
	unittest.main()
