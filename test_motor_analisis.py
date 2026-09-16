import unittest
from unittest.mock import patch

import pandas as pd

from motor_analisis import analizar_mercado


class TestAnalizarMercado(unittest.TestCase):
	def datos_ohlcv(self):
		return pd.DataFrame(
			{
				"Open": [10.0, 11.0, 12.0],
				"High": [12.0, 13.0, 14.0],
				"Low": [9.0, 10.0, 11.0],
				"Close": [11.0, 12.0, 13.0],
				"Volume": [100.0, 110.0, 120.0],
			}
		)

	@patch("motor_analisis.calcular_variacion_periodo", return_value=1.5)
	@patch("motor_analisis.calcular_media_movil", return_value=12.0)
	@patch("motor_analisis.determinar_tendencia", return_value="alcista")
	@patch("motor_analisis.calcular_rsi", return_value=60.0)
	@patch("motor_analisis.calcular_volatilidad", return_value=2.5)
	@patch("motor_analisis.calcular_ema", side_effect=[12.5, 11.5])
	@patch("motor_analisis.calcular_macd", return_value={"macd": 1.0})
	@patch("motor_analisis.calcular_atr", return_value=0.8)
	@patch("motor_analisis.calcular_contexto_volumen", return_value={"ratio_volumen": 1.2})
	@patch("motor_analisis.calcular_estructura_precio", return_value={"posicion_rango": 75.0})
	def test_dataframe_valido_orquesta_indicadores(
		self,
		estructura_mock,
		volumen_mock,
		atr_mock,
		macd_mock,
		ema_mock,
		volatilidad_mock,
		rsi_mock,
		tendencia_mock,
		media_mock,
		variacion_mock,
	):
		datos = self.datos_ohlcv()
		resultado = analizar_mercado(datos)

		self.assertEqual(
			set(resultado),
			{
				"precio_actual",
				"tendencia",
				"momentum",
				"volatilidad",
				"volumen",
				"estructura_precio",
				"rendimiento",
			},
		)
		self.assertAlmostEqual(resultado["precio_actual"], 13.0)
		self.assertEqual(resultado["tendencia"], {"estado": "alcista", "sma_20": 12.0, "ema_20": 12.5, "ema_50": 11.5})
		self.assertEqual(resultado["momentum"], {"rsi_14": 60.0, "macd": {"macd": 1.0}})
		self.assertEqual(resultado["volatilidad"], {"porcentaje": 2.5, "atr_14": 0.8})
		self.assertEqual(resultado["volumen"], {"ratio_volumen": 1.2})
		self.assertEqual(resultado["estructura_precio"], {"posicion_rango": 75.0})
		self.assertEqual(resultado["rendimiento"], {"variacion_periodo": 1.5})
		pd.testing.assert_series_equal(
			variacion_mock.call_args.args[0], datos["Close"]
		)
		pd.testing.assert_series_equal(media_mock.call_args.args[0], datos["Close"])
		pd.testing.assert_series_equal(
			tendencia_mock.call_args.args[0], datos["Close"]
		)
		pd.testing.assert_series_equal(rsi_mock.call_args.args[0], datos["Close"])
		pd.testing.assert_series_equal(
			volatilidad_mock.call_args.args[0], datos["Close"]
		)
		pd.testing.assert_series_equal(macd_mock.call_args.args[0], datos["Close"])
		self.assertEqual(media_mock.call_args.args[1], 20)
		self.assertEqual(tendencia_mock.call_args.args[1], 20)
		self.assertEqual(rsi_mock.call_args.args[1], 14)
		atr_mock.assert_called_once_with(datos, 14)
		volumen_mock.assert_called_once_with(datos, 20)
		estructura_mock.assert_called_once_with(datos, 20)

	def test_datos_none_devuelve_none(self):
		self.assertIsNone(analizar_mercado(None))

	def test_dataframe_vacio_devuelve_none(self):
		self.assertIsNone(analizar_mercado(pd.DataFrame()))

	def test_falta_columna_ohlcv_devuelve_none(self):
		datos = self.datos_ohlcv().drop(columns="Volume")
		self.assertIsNone(analizar_mercado(datos))

	def test_usa_el_ultimo_close_valido(self):
		datos = self.datos_ohlcv()
		datos.loc[2, "Close"] = float("nan")
		datos.loc[1, "Close"] = 12.5

		resultado = analizar_mercado(datos)

		self.assertAlmostEqual(resultado["precio_actual"], 12.5)
		self.assertFalse(pd.isna(resultado["precio_actual"]))

	@patch("motor_analisis.calcular_variacion_periodo", return_value=None)
	@patch("motor_analisis.calcular_media_movil", return_value=None)
	@patch("motor_analisis.determinar_tendencia", return_value=None)
	@patch("motor_analisis.calcular_rsi", return_value=None)
	@patch("motor_analisis.calcular_volatilidad", return_value=None)
	@patch("motor_analisis.calcular_ema", return_value=None)
	@patch("motor_analisis.calcular_macd", return_value=None)
	@patch("motor_analisis.calcular_atr", return_value=None)
	@patch("motor_analisis.calcular_contexto_volumen", return_value=None)
	@patch("motor_analisis.calcular_estructura_precio", return_value=None)
	def test_indicadores_none_no_interrumpen_el_motor(self, *mocks):
		resultado = analizar_mercado(self.datos_ohlcv())

		self.assertAlmostEqual(resultado["precio_actual"], 13.0)
		self.assertIsNone(resultado["tendencia"]["estado"])
		self.assertIsNone(resultado["tendencia"]["sma_20"])
		self.assertIsNone(resultado["momentum"]["rsi_14"])
		self.assertIsNone(resultado["volatilidad"]["atr_14"])
		self.assertIsNone(resultado["volumen"])
		self.assertIsNone(resultado["estructura_precio"])



if __name__ == "__main__":
	unittest.main()
