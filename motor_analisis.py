import pandas as pd

from tecnico import (
	calcular_atr,
	calcular_contexto_volumen,
	calcular_ema,
	calcular_estructura_precio,
	calcular_macd,
	calcular_media_movil,
	calcular_rsi,
	calcular_variacion_periodo,
	calcular_volatilidad,
	determinar_tendencia,
)


def analizar_mercado(datos):
	if datos is None or datos.empty:
		return None

	columnas_requeridas = ["Open", "High", "Low", "Close", "Volume"]
	if not all(columna in datos.columns for columna in columnas_requeridas):
		return None

	precios = pd.to_numeric(datos["Close"], errors="coerce")
	precios = precios.replace([float("inf"), float("-inf")], float("nan"))
	precios = precios.dropna()
	if precios.empty:
		return None
	variacion_periodo = calcular_variacion_periodo(precios)
	sma_20 = calcular_media_movil(precios, 20)
	ema_20 = calcular_ema(precios, 20)
	ema_50 = calcular_ema(precios, 50)
	tendencia = determinar_tendencia(precios, 20)
	rsi_14 = calcular_rsi(precios, 14)
	volatilidad = calcular_volatilidad(precios)
	macd = calcular_macd(precios)
	atr_14 = calcular_atr(datos, 14)
	volumen = calcular_contexto_volumen(datos, 20)
	estructura = calcular_estructura_precio(datos, 20)

	return {
		"precio_actual": float(precios.iloc[-1]),
		"tendencia": {
			"estado": tendencia,
			"sma_20": sma_20,
			"ema_20": ema_20,
			"ema_50": ema_50,
		},
		"momentum": {
			"rsi_14": rsi_14,
			"macd": macd,
		},
		"volatilidad": {
			"porcentaje": volatilidad,
			"atr_14": atr_14,
		},
		"volumen": volumen,
		"estructura_precio": estructura,
		"rendimiento": {
			"variacion_periodo": variacion_periodo,
		},
	}
