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
