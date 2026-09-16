import pandas as pd
import yfinance as yf


def obtener_precio_actual(simbolo):
	try:
		# Descarga los datos recientes y obtiene el ultimo cierre disponible.
		datos = yf.Ticker(simbolo).history(period="1d")
		if datos.empty or datos["Close"].empty:
			print(f"No se pudo obtener el precio de {simbolo}.")
			return None

		return float(datos["Close"].iloc[-1])
	except Exception as error:
		print(f"Error al obtener el precio de {simbolo}: {error}")
		return None


def obtener_historial_precios(simbolo, periodo="1mo"):
	try:
		ticker = yf.Ticker(simbolo)
		datos = ticker.history(period=periodo)
		if datos.empty or "Close" not in datos.columns:
			return None

		return datos["Close"].copy()
	except Exception:
		return None


def obtener_datos_historicos(simbolo, periodo="1mo"):
	columnas_requeridas = ["Open", "High", "Low", "Close", "Volume"]

	try:
		datos = yf.Ticker(simbolo).history(period=periodo)
		if datos.empty or not all(columna in datos.columns for columna in columnas_requeridas):
			return None

		datos = datos[columnas_requeridas].copy()
		datos = datos.apply(pd.to_numeric, errors="coerce")
		datos = datos.replace([float("inf"), float("-inf")], float("nan"))
		# Conservar las barras: borrarlas desplazaría la entrada t+1 y los plazos.
		# El simulador rechaza Open inválido y falla si no puede valorar un Close.
		if datos["Close"].notna().sum() == 0:
			return None

		return datos
	except Exception:
		return None
