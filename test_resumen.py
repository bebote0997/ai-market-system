import unittest

from resumen import calcular_resumen_cartera


class TestCalcularResumenCartera(unittest.TestCase):
	def test_cartera_con_resultados_balanceados(self):
		resultados = [
			{
				"capital_utilizado": 1000,
				"valor_actual": 1200,
				"ganancia_perdida": 200,
			},
			{
				"capital_utilizado": 2000,
				"valor_actual": 1800,
				"ganancia_perdida": -200,
			},
		]

		resumen = calcular_resumen_cartera(resultados, 10000)

		self.assertEqual(resumen["capital_inicial"], 10000)
		self.assertAlmostEqual(resumen["capital_utilizado_total"], 3000)
		self.assertAlmostEqual(resumen["valor_actual_total"], 3000)
		self.assertAlmostEqual(resumen["ganancia_perdida_total"], 0)
		self.assertAlmostEqual(resumen["rentabilidad_cartera"], 0)
		self.assertEqual(resumen["numero_activos"], 2)

	def test_cartera_con_ganancia_calcula_rentabilidad(self):
		resultados = [
			{
				"capital_utilizado": 1000,
				"valor_actual": 1250,
				"ganancia_perdida": 250,
			}
		]

		resumen = calcular_resumen_cartera(resultados, 10000)

		self.assertAlmostEqual(resumen["ganancia_perdida_total"], 250)
		self.assertAlmostEqual(resumen["rentabilidad_cartera"], 25)

	def test_cartera_vacia_devuelve_totales_cero(self):
		resumen = calcular_resumen_cartera([], 7500)

		self.assertEqual(resumen["capital_inicial"], 7500)
		self.assertAlmostEqual(resumen["capital_utilizado_total"], 0)
		self.assertAlmostEqual(resumen["valor_actual_total"], 0)
		self.assertAlmostEqual(resumen["ganancia_perdida_total"], 0)
		self.assertAlmostEqual(resumen["rentabilidad_cartera"], 0)
		self.assertEqual(resumen["numero_activos"], 0)


if __name__ == "__main__":
	unittest.main()
