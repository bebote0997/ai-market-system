import unittest

import pandas as pd

from tecnico import (
	calcular_rsi,
	calcular_media_movil,
	calcular_variacion_periodo,
	calcular_volatilidad,
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


class TestCalcularRsi(unittest.TestCase):
	def test_serie_continuamente_ascendente_devuelve_cien(self):
		precios = list(range(100, 116))
		self.assertAlmostEqual(calcular_rsi(precios), 100.0)

	def test_serie_continuamente_descendente_devuelve_cero(self):
		precios = list(range(115, 99, -1))
		self.assertAlmostEqual(calcular_rsi(precios), 0.0)

	def test_serie_plana_devuelve_cincuenta(self):
		self.assertAlmostEqual(calcular_rsi([100] * 15), 50.0)

	def test_datos_insuficientes_devuelve_none(self):
		self.assertIsNone(calcular_rsi([100] * 14))

	def test_periodo_cero_devuelve_none(self):
		self.assertIsNone(calcular_rsi([100] * 15, periodo=0))


class TestCalcularVolatilidad(unittest.TestCase):
	def test_precios_constantes_producen_volatilidad_cero(self):
		precios = pd.Series([100.0, 100.0, 100.0])
		self.assertAlmostEqual(calcular_volatilidad(precios), 0.0)

	def test_movimientos_variables_producen_volatilidad_positiva(self):
		precios = pd.Series([100.0, 110.0, 100.0, 120.0])
		self.assertGreater(calcular_volatilidad(precios), 0)

	def test_none_devuelve_none(self):
		self.assertIsNone(calcular_volatilidad(None))

	def test_un_solo_precio_devuelve_none(self):
		self.assertIsNone(calcular_volatilidad(pd.Series([100.0])))


if __name__ == "__main__":
	unittest.main()
