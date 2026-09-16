import unittest

import pandas as pd

from validacion_historica import dividir_datos_cronologicamente


class TestDividirDatosCronologicamente(unittest.TestCase):
	def datos(self, cantidad=10):
		indice = pd.date_range("2026-01-01", periods=cantidad, freq="D")
		return pd.DataFrame({"Close": range(cantidad)}, index=indice)

	def test_datos_none_y_vacios_devuelven_none(self):
		self.assertIsNone(dividir_datos_cronologicamente(None))
		self.assertIsNone(dividir_datos_cronologicamente(pd.DataFrame()))

	def test_proporciones_invalidas_devuelven_none(self):
		datos = self.datos()
		for proporcion in [0, 1, -0.1, 1.1, True, False, "0.7", float("nan")]:
			self.assertIsNone(dividir_datos_cronologicamente(datos, proporcion))

	def test_division_70_30(self):
		resultado = dividir_datos_cronologicamente(self.datos(), 0.70)

		self.assertEqual(resultado["filas_entrenamiento"], 7)
		self.assertEqual(resultado["filas_prueba"], 3)
		self.assertEqual(resultado["total_filas"], 10)
		self.assertAlmostEqual(resultado["proporcion_entrenamiento"], 0.70)

	def test_segmentos_contienen_filas_cronologicas_correctas(self):
		resultado = dividir_datos_cronologicamente(self.datos(), 0.70)

		self.assertEqual(list(resultado["entrenamiento"]["Close"]), list(range(7)))
		self.assertEqual(list(resultado["prueba"]["Close"]), list(range(7, 10)))

	def test_datos_desordenados_se_ordenan_antes_de_dividir(self):
		datos = self.datos().iloc[[4, 2, 8, 0, 9, 1, 7, 3, 6, 5]]
		resultado = dividir_datos_cronologicamente(datos, 0.70)

		self.assertEqual(list(resultado["entrenamiento"]["Close"]), list(range(7)))
		self.assertEqual(list(resultado["prueba"]["Close"]), list(range(7, 10)))

	def test_no_hay_solapamiento_de_indices(self):
		resultado = dividir_datos_cronologicamente(self.datos(), 0.70)
		indices_entrenamiento = set(resultado["entrenamiento"].index)
		indices_prueba = set(resultado["prueba"].index)

		self.assertTrue(indices_entrenamiento.isdisjoint(indices_prueba))

	def test_union_conserva_todas_las_filas_originales(self):
		datos = self.datos()
		resultado = dividir_datos_cronologicamente(datos, 0.70)
		indices_union = set(resultado["entrenamiento"].index) | set(resultado["prueba"].index)

		self.assertEqual(indices_union, set(datos.index))

	def test_no_modifica_dataframe_original(self):
		datos = self.datos().iloc[::-1]
		original = datos.copy(deep=True)

		dividir_datos_cronologicamente(datos, 0.70)

		pd.testing.assert_frame_equal(datos, original)

	def test_dataset_demasiado_pequeno_devuelve_none(self):
		self.assertIsNone(dividir_datos_cronologicamente(self.datos(1), 0.70))


if __name__ == "__main__":
	unittest.main()
