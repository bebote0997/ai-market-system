import unittest

from analisis import analizar_operacion


class TestAnalizarOperacion(unittest.TestCase):
	def test_operacion_con_ganancia(self):
		resultado = analizar_operacion(10000, 100, 120, 10)

		self.assertAlmostEqual(resultado[0], 1000)
		self.assertAlmostEqual(resultado[1], 1200)
		self.assertAlmostEqual(resultado[2], 200)
		self.assertAlmostEqual(resultado[3], 20)
		self.assertAlmostEqual(resultado[4], 10200)

	def test_operacion_con_perdida(self):
		resultado = analizar_operacion(10000, 100, 80, 10)

		self.assertAlmostEqual(resultado[0], 1000)
		self.assertAlmostEqual(resultado[1], 800)
		self.assertAlmostEqual(resultado[2], -200)
		self.assertAlmostEqual(resultado[3], -20)
		self.assertAlmostEqual(resultado[4], 9800)


if __name__ == "__main__":
	unittest.main()
