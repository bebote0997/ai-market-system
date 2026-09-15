import unittest

from validacion import validar_operacion


class TestValidarOperacion(unittest.TestCase):
	def operacion_valida(self):
		return {
			"simbolo": "AAPL",
			"ticker": "AAPL",
			"mercado": "acciones",
			"precio_entrada": 150,
			"cantidad": 10,
		}

	def test_operacion_valida_devuelve_true(self):
		self.assertTrue(validar_operacion(self.operacion_valida()))

	def test_cantidad_igual_a_cero_devuelve_false(self):
		operacion = self.operacion_valida()
		operacion["cantidad"] = 0
		self.assertFalse(validar_operacion(operacion))

	def test_precio_entrada_negativo_devuelve_false(self):
		operacion = self.operacion_valida()
		operacion["precio_entrada"] = -150
		self.assertFalse(validar_operacion(operacion))

	def test_ticker_vacio_devuelve_false(self):
		operacion = self.operacion_valida()
		operacion["ticker"] = ""
		self.assertFalse(validar_operacion(operacion))

	def test_falta_clave_obligatoria_devuelve_false(self):
		operacion = self.operacion_valida()
		del operacion["mercado"]
		self.assertFalse(validar_operacion(operacion))


if __name__ == "__main__":
	unittest.main()
