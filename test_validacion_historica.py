import unittest

import pandas as pd

from validacion_historica import (
	dividir_datos_cronologicamente,
	preparar_segmento_prueba_con_contexto,
)


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

	def test_contexto_usa_todo_el_entrenamiento(self):
		entrenamiento = self.datos(70)
		prueba = self.datos(70).iloc[:10].copy()
		prueba.index = pd.date_range("2026-04-01", periods=10, freq="D")

		resultado = preparar_segmento_prueba_con_contexto(entrenamiento, prueba, 50)

		self.assertEqual(resultado["filas_contexto"], 70)
		self.assertEqual(resultado["filas_prueba"], 10)
		self.assertEqual(list(resultado["datos_con_contexto"].index[:70]), list(entrenamiento.index))
		self.assertEqual(list(resultado["indices_prueba"]), list(prueba.index))

	def test_contexto_usa_todas_las_filas_si_entrenamiento_corto(self):
		entrenamiento = self.datos(3)
		prueba = self.datos(2)
		prueba.index = pd.date_range("2026-02-01", periods=2, freq="D")

		resultado = preparar_segmento_prueba_con_contexto(entrenamiento, prueba, 50)

		self.assertEqual(resultado["filas_contexto"], 3)
		self.assertEqual(len(resultado["datos_con_contexto"]), 5)

	def test_contexto_ordenado_y_sin_solapamiento(self):
		entrenamiento = self.datos(5).iloc[::-1]
		prueba = self.datos(3)
		prueba.index = pd.date_range("2026-02-01", periods=3, freq="D")

		resultado = preparar_segmento_prueba_con_contexto(entrenamiento, prueba, 3)

		self.assertTrue(resultado["datos_con_contexto"].index.is_monotonic_increasing)
		self.assertTrue(set(resultado["datos_con_contexto"].index[:5]).isdisjoint(set(resultado["indices_prueba"])))

	def test_contexto_no_modifica_originales(self):
		entrenamiento = self.datos(5).iloc[::-1]
		prueba = self.datos(3)
		prueba.index = pd.date_range("2026-02-01", periods=3)
		original_entrenamiento = entrenamiento.copy(deep=True)
		original_prueba = prueba.copy(deep=True)

		preparar_segmento_prueba_con_contexto(entrenamiento, prueba, 3)

		pd.testing.assert_frame_equal(entrenamiento, original_entrenamiento)
		pd.testing.assert_frame_equal(prueba, original_prueba)

	def test_contexto_invalidos_devuelven_none(self):
		datos = self.datos(3)
		self.assertIsNone(preparar_segmento_prueba_con_contexto(None, datos))
		self.assertIsNone(preparar_segmento_prueba_con_contexto(datos, None))
		self.assertIsNone(preparar_segmento_prueba_con_contexto(datos, datos, 0))


if __name__ == "__main__":
	unittest.main()
