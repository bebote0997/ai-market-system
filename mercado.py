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
