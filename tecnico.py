import pandas as pd


def _limpiar_precios(precios):
	if precios is None:
		return None

	serie = pd.Series(precios)
	serie = pd.to_numeric(serie, errors="coerce")
	serie = serie.replace([float("inf"), float("-inf")], float("nan"))
	return serie.dropna()


def _limpiar_columnas(datos, columnas):
	if datos is None or datos.empty or not all(
		columna in datos.columns for columna in columnas
	):
		return None

	limpios = datos[columnas].apply(pd.to_numeric, errors="coerce")
	limpios = limpios.replace([float("inf"), float("-inf")], float("nan"))
	return limpios.dropna(subset=columnas)


def calcular_variacion_periodo(precios):
	precios = _limpiar_precios(precios)
	if precios is None:
		return None

	if precios.empty or precios.iloc[0] == 0:
		return None

	resultado = ((precios.iloc[-1] - precios.iloc[0]) / precios.iloc[0]) * 100
	if pd.isna(resultado) or resultado in [float("inf"), float("-inf")]:
		return None
	return float(resultado)


def calcular_media_movil(precios, ventana=20):
	precios = _limpiar_precios(precios)
	if precios is None or ventana <= 0:
		return None

	if precios.empty or len(precios) < ventana:
		return None

	resultado = precios.iloc[-ventana:].mean()
	return None if pd.isna(resultado) else float(resultado)


def determinar_tendencia(precios, ventana=20):
	precios = _limpiar_precios(precios)
	media_movil = calcular_media_movil(precios, ventana)
	if precios is None or precios.empty or media_movil is None:
		return None

	ultimo_precio = precios.iloc[-1]
	if pd.isna(ultimo_precio):
		return None
	if ultimo_precio > media_movil:
		return "alcista"
	if ultimo_precio < media_movil:
		return "bajista"
	return "neutral"


def calcular_rsi(precios, periodo=14):
	precios = _limpiar_precios(precios)
	if precios is None or periodo <= 0:
		return None

	if len(precios) < periodo + 1:
		return None

	variaciones = [
		precios.iloc[indice] - precios.iloc[indice - 1]
		for indice in range(1, len(precios))
	]
	variaciones = variaciones[-periodo:]

	ganancias = [variacion for variacion in variaciones if variacion > 0]
	perdidas = [-variacion for variacion in variaciones if variacion < 0]
	ganancia_media = sum(ganancias) / periodo
	perdida_media = sum(perdidas) / periodo

	if perdida_media == 0 and ganancia_media > 0:
		return 100.0
	if ganancia_media == 0 and perdida_media > 0:
		return 0.0
	if ganancia_media == 0 and perdida_media == 0:
		return 50.0

	rs = ganancia_media / perdida_media
	resultado = float(100 - (100 / (1 + rs)))
	return None if pd.isna(resultado) else resultado


def calcular_volatilidad(precios):
	precios = _limpiar_precios(precios)
	if precios is None or len(precios) < 2:
		return None

	rendimientos = precios.pct_change()
	rendimientos = rendimientos.replace([float("inf"), float("-inf")], float("nan"))
	rendimientos = rendimientos.dropna()
	if rendimientos.empty:
		return None

	resultado = rendimientos.std() * 100
	return None if pd.isna(resultado) else float(resultado)


def calcular_ema(precios, periodo=20):
	precios = _limpiar_precios(precios)
	if precios is None or periodo <= 0:
		return None

	if precios.empty or len(precios) < periodo:
		return None

	resultado = precios.ewm(span=periodo, adjust=False).mean().iloc[-1]
	return None if pd.isna(resultado) else float(resultado)


def calcular_macd(precios, periodo_rapido=12, periodo_lento=26, periodo_senal=9):
	if precios is None or not all(
		periodo > 0
		for periodo in [periodo_rapido, periodo_lento, periodo_senal]
	):
		return None
	if periodo_rapido >= periodo_lento:
		return None

	precios = _limpiar_precios(precios)
	minimo_datos = periodo_lento + periodo_senal - 1
	if precios.empty or len(precios) < minimo_datos:
		return None

	ema_rapida = precios.ewm(span=periodo_rapido, adjust=False).mean()
	ema_lenta = precios.ewm(span=periodo_lento, adjust=False).mean()
	macd = ema_rapida - ema_lenta
	senal = macd.ewm(span=periodo_senal, adjust=False).mean()
	histograma = macd - senal
	valores = [macd.iloc[-1], senal.iloc[-1], histograma.iloc[-1]]
	if any(pd.isna(valor) for valor in valores):
		return None

	return {
		"macd": float(valores[0]),
		"senal": float(valores[1]),
		"histograma": float(valores[2]),
	}


def calcular_atr(datos, periodo=14):
	if datos is None or periodo <= 0:
		return None

	datos = _limpiar_columnas(datos, ["High", "Low", "Close"])
	if datos is None:
		return None
	if len(datos) < periodo:
		return None

	close_anterior = datos["Close"].shift(1)
	true_range = pd.concat(
		[
			datos["High"] - datos["Low"],
			(datos["High"] - close_anterior).abs(),
			(datos["Low"] - close_anterior).abs(),
		],
		axis=1,
	).max(axis=1)

	resultado = true_range.rolling(window=periodo).mean().iloc[-1]
	return None if pd.isna(resultado) else float(resultado)


def calcular_contexto_volumen(datos, ventana=20):
	if datos is None or datos.empty or ventana <= 0:
		return None

	volumen = _limpiar_columnas(datos, ["Volume"])
	if volumen is None or len(volumen) < ventana:
		return None

	volumen_actual = float(volumen["Volume"].iloc[-1])
	volumen_medio = float(volumen["Volume"].iloc[-ventana:].mean())
	if volumen_medio == 0:
		ratio_volumen = None
	else:
		ratio_volumen = float(volumen_actual / volumen_medio)

	return {
		"volumen_actual": volumen_actual,
		"volumen_medio": volumen_medio,
		"ratio_volumen": ratio_volumen,
	}


def calcular_estructura_precio(datos, ventana=20):
	if datos is None or datos.empty or ventana <= 0:
		return None

	datos = _limpiar_columnas(datos, ["High", "Low", "Close"])
	if datos is None:
		return None
	if len(datos) < ventana:
		return None

	ultimos_datos = datos.iloc[-ventana:]
	maximo_reciente = float(ultimos_datos["High"].max())
	minimo_reciente = float(ultimos_datos["Low"].min())
	precio_actual = float(ultimos_datos["Close"].iloc[-1])

	if maximo_reciente == minimo_reciente:
		posicion_rango = 50.0
	else:
		posicion_rango = (
			(precio_actual - minimo_reciente)
			/ (maximo_reciente - minimo_reciente)
		) * 100

	if any(
		pd.isna(valor)
		for valor in [
			precio_actual,
			maximo_reciente,
			minimo_reciente,
			posicion_rango,
		]
	):
		return None

	return {
		"precio_actual": precio_actual,
		"maximo_reciente": maximo_reciente,
		"minimo_reciente": minimo_reciente,
		"posicion_rango": float(posicion_rango),
	}
