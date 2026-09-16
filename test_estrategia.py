import unittest

from estrategia import (
	cumple_estrategia,
	crear_configuracion_estrategia,
	generar_eventos_estrategia,
)


class TestEstrategia(unittest.TestCase):
	def evaluacion(self, favorables=4, desfavorables=1, estado="favorable"):
		return {
			"resumen": {"favorables": favorables, "desfavorables": desfavorables},
			"condiciones": {
				"tendencia": {"estado": estado},
				"rsi": {"estado": estado},
				"macd": {"estado": estado},
				"volumen": {"estado": estado},
				"estructura_precio": {"estado": estado},
			},
		}

	def test_configuracion_default_y_personalizada(self):
		self.assertEqual(crear_configuracion_estrategia(), {
			"minimo_favorables": 4,
			"maximo_desfavorables": 1,
			"requeridas": (),
		})
		self.assertEqual(crear_configuracion_estrategia(2, 3, ["rsi", "rsi", "volumen"])["requeridas"], ("rsi", "volumen"))

	def test_configuracion_invalida(self):
		for valor in [-1, 6, True, 1.5]:
			self.assertIsNone(crear_configuracion_estrategia(valor, 1))
			self.assertIsNone(crear_configuracion_estrategia(1, valor))
		self.assertIsNone(crear_configuracion_estrategia(1, 1, ["otro"]))
		self.assertIsNone(crear_configuracion_estrategia(1, 1, "rsi"))

	def test_cumple_limites_y_requeridas(self):
		config = crear_configuracion_estrategia(4, 1, ["rsi"])
		self.assertTrue(cumple_estrategia(self.evaluacion(), config))
		self.assertTrue(cumple_estrategia(self.evaluacion(4, 1), config))
		self.assertFalse(cumple_estrategia(self.evaluacion(3, 1), config))
		self.assertFalse(cumple_estrategia(self.evaluacion(4, 2), config))
		self.assertFalse(cumple_estrategia(self.evaluacion(4, 1, "desfavorable"), config))

	def test_requerida_no_disponible_falla(self):
		config = crear_configuracion_estrategia(0, 5, ["rsi"])
		analisis = self.evaluacion(0, 0)
		analisis["condiciones"]["rsi"]["estado"] = "no_disponible"
		self.assertFalse(cumple_estrategia(analisis, config))

	def test_evaluacion_invalida_devuelve_none(self):
		self.assertIsNone(cumple_estrategia({}, crear_configuracion_estrategia()))
		self.assertIsNone(cumple_estrategia(self.evaluacion(), None))

	def test_genera_eventos_validos_en_orden_sin_modificar(self):
		config = crear_configuracion_estrategia(4, 1)
		evaluaciones = [
			{"fecha": 2, "precio_cierre": 102, "evaluacion": self.evaluacion()},
			{"fecha": 1, "precio_cierre": 101, "evaluacion": self.evaluacion(3, 1)},
		]
		original = [dict(item) for item in evaluaciones]
		eventos = generar_eventos_estrategia(evaluaciones, config)
		self.assertEqual([evento["fecha"] for evento in eventos], [2])
		self.assertEqual(eventos[0]["precio"], 102)
		self.assertEqual(evaluaciones, original)


if __name__ == "__main__":
	unittest.main()
