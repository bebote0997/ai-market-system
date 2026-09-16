import unittest

from validador_entrada import evaluar_entrada


class TestEvaluarEntrada(unittest.TestCase):
	def analisis_base(self):
		return {
			"tendencia": {"estado": "alcista", "ema_20": 120.0, "ema_50": 100.0},
			"momentum": {
				"rsi_14": 60.0,
				"macd": {"macd": 2.0, "senal": 1.0, "histograma": 1.0},
			},
			"volumen": {"ratio_volumen": 1.2},
			"estructura_precio": {"posicion_rango": 70.0},
		}

	def test_cinco_condiciones_favorables(self):
		resultado = evaluar_entrada(self.analisis_base())

		self.assertEqual(resultado["resumen"], {
			"favorables": 5,
			"desfavorables": 0,
			"no_disponibles": 0,
			"total": 5,
		})
		self.assertTrue(all(
			condicion["estado"] == "favorable"
			for condicion in resultado["condiciones"].values()
		))

	def test_cinco_condiciones_desfavorables(self):
		analisis = {
			"tendencia": {"estado": "bajista", "ema_20": 90.0, "ema_50": 100.0},
			"momentum": {
				"rsi_14": 80.0,
				"macd": {"macd": 1.0, "senal": 2.0, "histograma": -1.0},
			},
			"volumen": {"ratio_volumen": 0.5},
			"estructura_precio": {"posicion_rango": 95.0},
		}

		resultado = evaluar_entrada(analisis)

		self.assertEqual(resultado["resumen"]["favorables"], 0)
		self.assertEqual(resultado["resumen"]["desfavorables"], 5)
		self.assertEqual(resultado["resumen"]["no_disponibles"], 0)

	def test_mezcla_de_estados_y_contadores(self):
		analisis = self.analisis_base()
		analisis["momentum"]["rsi_14"] = None
		analisis["volumen"]["ratio_volumen"] = 0.8
		analisis["estructura_precio"] = None

		resultado = evaluar_entrada(analisis)

		self.assertEqual(resultado["resumen"], {
			"favorables": 2,
			"desfavorables": 1,
			"no_disponibles": 2,
			"total": 5,
		})

	def test_analisis_none_devuelve_none(self):
		self.assertIsNone(evaluar_entrada(None))

	def test_limites_favorables(self):
		for rsi in [50.0, 70.0]:
			analisis = self.analisis_base()
			analisis["momentum"]["rsi_14"] = rsi
			self.assertEqual(evaluar_entrada(analisis)["condiciones"]["rsi"]["estado"], "favorable")

		for posicion in [50.0, 90.0]:
			analisis = self.analisis_base()
			analisis["estructura_precio"]["posicion_rango"] = posicion
			self.assertEqual(evaluar_entrada(analisis)["condiciones"]["estructura_precio"]["estado"], "favorable")

		analisis = self.analisis_base()
		analisis["volumen"]["ratio_volumen"] = 1.0
		self.assertEqual(evaluar_entrada(analisis)["condiciones"]["volumen"]["estado"], "favorable")

	def test_indicadores_none_son_no_disponibles(self):
		analisis = {
			"tendencia": {"estado": None, "ema_20": None, "ema_50": None},
			"momentum": {"rsi_14": None, "macd": None},
			"volumen": None,
			"estructura_precio": None,
		}

		resultado = evaluar_entrada(analisis)

		self.assertEqual(resultado["resumen"]["no_disponibles"], 5)
		self.assertEqual(resultado["resumen"]["favorables"], 0)
		self.assertEqual(resultado["resumen"]["desfavorables"], 0)
		self.assertTrue(all(
			condicion["estado"] == "no_disponible"
			for condicion in resultado["condiciones"].values()
		))


if __name__ == "__main__":
	unittest.main()
