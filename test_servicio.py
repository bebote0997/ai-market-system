import unittest
from unittest.mock import patch

from servicio import procesar_operacion


class TestProcesarOperacion(unittest.TestCase):
	def operacion_valida(self):
		return {
			"simbolo": "TEST",
			"ticker": "TEST",
			"mercado": "acciones",
			"precio_entrada": 100,
			"cantidad": 10,
		}

	@patch("servicio.obtener_precio_actual", return_value=120)
	def test_operacion_valida_devuelve_resultado_estructurado(self, precio_mock):
		resultado = procesar_operacion(self.operacion_valida(), 10000)

		self.assertEqual(resultado["simbolo"], "TEST")
		self.assertEqual(resultado["ticker"], "TEST")
		self.assertEqual(resultado["mercado"], "acciones")
		self.assertEqual(resultado["precio_entrada"], 100)
		self.assertEqual(resultado["precio_actual"], 120)
		self.assertEqual(resultado["cantidad"], 10)
		self.assertAlmostEqual(resultado["capital_utilizado"], 1000)
		self.assertAlmostEqual(resultado["valor_actual"], 1200)
		self.assertAlmostEqual(resultado["ganancia_perdida"], 200)
		self.assertAlmostEqual(resultado["rentabilidad"], 20)
		self.assertAlmostEqual(resultado["capital_total"], 10200)
		precio_mock.assert_called_once_with("TEST")

	@patch("servicio.validar_operacion", return_value=False)
	def test_operacion_invalida_devuelve_none(self, validacion_mock):
		resultado = procesar_operacion({}, 10000)

		self.assertIsNone(resultado)
		validacion_mock.assert_called_once_with({})

	@patch("servicio.obtener_precio_actual", return_value=None)
	def test_precio_no_disponible_devuelve_none(self, precio_mock):
		resultado = procesar_operacion(self.operacion_valida(), 10000)

		self.assertIsNone(resultado)
		precio_mock.assert_called_once_with("TEST")


if __name__ == "__main__":
	unittest.main()
