import unittest

import pandas as pd

from tecnico import (
	calcular_atr,
	calcular_contexto_volumen,
	calcular_ema,
	calcular_estructura_precio,
	calcular_macd,
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


class TestCalcularEma(unittest.TestCase):
	def test_serie_valida_devuelve_ultimo_valor_ema(self):
		self.assertAlmostEqual(calcular_ema([10, 20, 30, 40, 50], 3), 40.625)

	def test_datos_insuficientes_devuelve_none(self):
		self.assertIsNone(calcular_ema([10, 20], 3))

	def test_periodo_cero_devuelve_none(self):
		self.assertIsNone(calcular_ema([10, 20, 30], 0))


class TestCalcularMacd(unittest.TestCase):
	def test_serie_valida_devuelve_resultado_numerico(self):
		precios = pd.Series(range(1, 41), dtype=float)
		resultado = calcular_macd(
			precios,
			periodo_rapido=3,
			periodo_lento=5,
			periodo_senal=2,
		)
		ema_rapida = precios.ewm(span=3, adjust=False).mean()
		ema_lenta = precios.ewm(span=5, adjust=False).mean()
		macd = ema_rapida - ema_lenta
		senal = macd.ewm(span=2, adjust=False).mean()

		self.assertEqual(set(resultado), {"macd", "senal", "histograma"})
		self.assertAlmostEqual(resultado["macd"], macd.iloc[-1])
		self.assertAlmostEqual(resultado["senal"], senal.iloc[-1])
		self.assertAlmostEqual(resultado["histograma"], macd.iloc[-1] - senal.iloc[-1])

	def test_datos_insuficientes_devuelve_none(self):
		self.assertIsNone(calcular_macd([1, 2, 3], 3, 5, 2))

	def test_periodos_invalidos_devuelve_none(self):
		self.assertIsNone(calcular_macd([1] * 10, 0, 5, 2))
		self.assertIsNone(calcular_macd([1] * 10, 5, 5, 2))


class TestCalcularAtr(unittest.TestCase):
	def test_dataframe_ohlc_devuelve_atr(self):
		datos = pd.DataFrame(
			{
				"High": [12.0, 14.0, 13.0],
				"Low": [9.0, 10.0, 11.0],
				"Close": [10.0, 12.0, 12.0],
			}
		)

		self.assertAlmostEqual(calcular_atr(datos, 3), 3.0)

	def test_datos_insuficientes_devuelve_none(self):
		datos = pd.DataFrame({"High": [12.0], "Low": [9.0], "Close": [10.0]})
		self.assertIsNone(calcular_atr(datos, 2))

	def test_falta_columna_requerida_devuelve_none(self):
		for columna in ["High", "Low", "Close"]:
			datos = pd.DataFrame(
				{
					"High": [12.0, 14.0],
					"Low": [9.0, 10.0],
					"Close": [10.0, 12.0],
				}
			).drop(columns=columna)
			self.assertIsNone(calcular_atr(datos, 2))

	def test_periodo_cero_devuelve_none(self):
		datos = pd.DataFrame({"High": [12.0], "Low": [9.0], "Close": [10.0]})
		self.assertIsNone(calcular_atr(datos, 0))


class TestCalcularContextoVolumen(unittest.TestCase):
	def test_datos_validos_producen_valores_esperados(self):
		datos = pd.DataFrame({"Volume": [100.0, 200.0, 300.0]})

		resultado = calcular_contexto_volumen(datos, 2)

		self.assertAlmostEqual(resultado["volumen_actual"], 300.0)
		self.assertAlmostEqual(resultado["volumen_medio"], 250.0)
		self.assertAlmostEqual(resultado["ratio_volumen"], 1.2)

	def test_volumen_medio_cero_produce_ratio_none(self):
		datos = pd.DataFrame({"Volume": [0.0, 0.0]})

		resultado = calcular_contexto_volumen(datos, 2)

		self.assertIsNone(resultado["ratio_volumen"])

	def test_datos_insuficientes_devuelve_none(self):
		datos = pd.DataFrame({"Volume": [100.0]})
		self.assertIsNone(calcular_contexto_volumen(datos, 2))

	def test_falta_volume_devuelve_none(self):
		datos = pd.DataFrame({"Close": [100.0, 101.0]})
		self.assertIsNone(calcular_contexto_volumen(datos, 2))

	def test_ventana_cero_devuelve_none(self):
		datos = pd.DataFrame({"Volume": [100.0]})
		self.assertIsNone(calcular_contexto_volumen(datos, 0))


class TestCalcularEstructuraPrecio(unittest.TestCase):
	def test_datos_validos_producen_estructura_esperada(self):
		datos = pd.DataFrame(
			{
				"High": [110.0, 130.0, 120.0],
				"Low": [90.0, 100.0, 80.0],
				"Close": [100.0, 120.0, 110.0],
			}
		)

		resultado = calcular_estructura_precio(datos, 3)

		self.assertAlmostEqual(resultado["precio_actual"], 110.0)
		self.assertAlmostEqual(resultado["maximo_reciente"], 130.0)
		self.assertAlmostEqual(resultado["minimo_reciente"], 80.0)
		self.assertAlmostEqual(resultado["posicion_rango"], 60.0)

	def test_rango_constante_produce_posicion_cincuenta(self):
		datos = pd.DataFrame(
			{
				"High": [100.0, 100.0],
				"Low": [100.0, 100.0],
				"Close": [100.0, 100.0],
			}
		)

		resultado = calcular_estructura_precio(datos, 2)

		self.assertAlmostEqual(resultado["posicion_rango"], 50.0)

	def test_datos_insuficientes_devuelve_none(self):
		datos = pd.DataFrame(
			{"High": [110.0], "Low": [90.0], "Close": [100.0]}
		)
		self.assertIsNone(calcular_estructura_precio(datos, 2))

	def test_falta_columna_requerida_devuelve_none(self):
		for columna in ["High", "Low", "Close"]:
			datos = pd.DataFrame(
				{
					"High": [110.0, 120.0],
					"Low": [90.0, 80.0],
					"Close": [100.0, 110.0],
				}
			).drop(columns=columna)
			self.assertIsNone(calcular_estructura_precio(datos, 2))

	def test_ventana_cero_devuelve_none(self):
		datos = pd.DataFrame(
			{"High": [110.0], "Low": [90.0], "Close": [100.0]}
		)
		self.assertIsNone(calcular_estructura_precio(datos, 0))


if __name__ == "__main__":
	unittest.main()
