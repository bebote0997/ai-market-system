import unittest

from tecnico import (
	calcular_media_movil,
	calcular_variacion_periodo,
	determinar_tendencia,
)


class TestCalcularVariacionPeriodo(unittest.TestCase):
	def test_variacion_positiva(self):
		self.assertAlmostEqual(calcular_variacion_periodo([100, 110, 120]), 20)

	def test_variacion_negativa(self):
		self.assertAlmostEqual(calcular_variacion_periodo([100, 90, 80]), -20)

	def test_serie_vacia_devuelve_none(self):
		self.assertIsNone(calcular_variacion_periodo([]))

	def test_primer_precio_cero_devuelve_none(self):
		self.assertIsNone(calcular_variacion_periodo([0, 10, 20]))


class TestCalcularMediaMovil(unittest.TestCase):
	def test_media_movil_de_ultimos_precios(self):
		self.assertAlmostEqual(calcular_media_movil([10, 20, 30, 40, 50], 3), 40)

	def test_datos_insuficientes_devuelve_none(self):
		self.assertIsNone(calcular_media_movil([10, 20], 3))

	def test_ventana_cero_devuelve_none(self):
		self.assertIsNone(calcular_media_movil([10, 20], 0))

	def test_serie_vacia_devuelve_none(self):
		self.assertIsNone(calcular_media_movil([], 3))


class TestDeterminarTendencia(unittest.TestCase):
	def test_tendencia_alcista(self):
		self.assertEqual(determinar_tendencia([10, 20, 30], 3), "alcista")

	def test_tendencia_bajista(self):
		self.assertEqual(determinar_tendencia([30, 20, 10], 3), "bajista")

	def test_tendencia_neutral(self):
		self.assertEqual(determinar_tendencia([10, 30, 20], 3), "neutral")

	def test_datos_insuficientes_devuelve_none(self):
		self.assertIsNone(determinar_tendencia([10, 20], 3))


if __name__ == "__main__":
	unittest.main()
