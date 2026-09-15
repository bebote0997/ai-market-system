import unittest
from unittest.mock import patch

from servicio import procesar_cartera, procesar_operacion


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

	@patch("servicio.procesar_operacion")
	def test_procesar_cartera_devuelve_dos_resultados(self, procesar_mock):
		operaciones = [self.operacion_valida(), self.operacion_valida()]
		primer_resultado = {"simbolo": "TEST1"}
		segundo_resultado = {"simbolo": "TEST2"}
		procesar_mock.side_effect = [primer_resultado, segundo_resultado]

		resultados = procesar_cartera(operaciones, 10000)

		self.assertEqual(resultados, [primer_resultado, segundo_resultado])
		self.assertEqual(procesar_mock.call_count, 2)

	@patch("servicio.procesar_operacion")
	def test_procesar_cartera_omite_resultado_none(self, procesar_mock):
		operaciones = [self.operacion_valida(), self.operacion_valida()]
		resultado_valido = {"simbolo": "TEST"}
		procesar_mock.side_effect = [None, resultado_valido]

		resultados = procesar_cartera(operaciones, 10000)

		self.assertEqual(resultados, [resultado_valido])
		self.assertEqual(procesar_mock.call_count, 2)

	@patch("servicio.procesar_operacion")
	def test_procesar_cartera_vacia_devuelve_lista_vacia(self, procesar_mock):
		resultados = procesar_cartera([], 10000)

		self.assertEqual(resultados, [])
		procesar_mock.assert_not_called()


if __name__ == "__main__":
	unittest.main()
