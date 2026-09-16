import unittest

from riesgo import calcular_plan_riesgo, crear_configuracion_riesgo


class TestRiesgo(unittest.TestCase):
	def test_configuracion_default_y_invalidos(self):
		self.assertEqual(crear_configuracion_riesgo()["metodo_stop"], "atr")
		for valor in [0, -1, 101, True, float("nan"), float("inf")]:
			self.assertIsNone(crear_configuracion_riesgo(riesgo_por_operacion_pct=valor))
		self.assertIsNone(crear_configuracion_riesgo(metodo_stop="otro"))
		self.assertIsNone(crear_configuracion_riesgo(atr_multiplicador=0))
		self.assertIsNone(crear_configuracion_riesgo(ratio_objetivo=0))
		self.assertIsNone(crear_configuracion_riesgo(maximo_capital_pct=101))

	def test_plan_atr_riesgo_y_limite_capital(self):
		config = crear_configuracion_riesgo(1, "atr", 2, 2, 100)
		plan = calcular_plan_riesgo(10000, 100, config, atr=5)
		self.assertAlmostEqual(plan["riesgo_monetario_maximo"], 100)
		self.assertAlmostEqual(plan["riesgo_unitario"], 10)
		self.assertAlmostEqual(plan["cantidad_por_riesgo"], 10)
		self.assertAlmostEqual(plan["cantidad"], 10)
		self.assertAlmostEqual(plan["stop"], 90)
		self.assertAlmostEqual(plan["target"], 120)

	def test_limite_capital_y_fraccionaria(self):
		config = crear_configuracion_riesgo(1, "porcentual", 2, 2, 25, 2)
		plan = calcular_plan_riesgo(10000, 333, config)
		self.assertAlmostEqual(plan["capital_maximo"], 2500)
		self.assertAlmostEqual(plan["cantidad"], 2500 / 333)

	def test_atr_invalido_y_precio_invalido(self):
		config = crear_configuracion_riesgo()
		for atr in [None, 0, -1, float("nan"), float("inf")]:
			self.assertIsNone(calcular_plan_riesgo(10000, 100, config, atr))
		self.assertIsNone(calcular_plan_riesgo(10000, 0, config, 5))


if __name__ == "__main__":
	unittest.main()
