def calcular_variacion_periodo(precios):
	if precios is None:
		return None

	precios = list(precios)
	if not precios or precios[0] == 0:
		return None

	primero = precios[0]
	ultimo = precios[-1]
	return ((ultimo - primero) / primero) * 100


def calcular_media_movil(precios, ventana=20):
	if precios is None or ventana <= 0:
		return None

	precios = list(precios)
	if len(precios) < ventana:
		return None

	return float(sum(precios[-ventana:]) / ventana)


def determinar_tendencia(precios, ventana=20):
	media_movil = calcular_media_movil(precios, ventana)
	if media_movil is None:
		return None

	precios = list(precios)
	ultimo_precio = precios[-1]
	if ultimo_precio > media_movil:
		return "alcista"
	if ultimo_precio < media_movil:
		return "bajista"
	return "neutral"


def calcular_rsi(precios, periodo=14):
	if precios is None or periodo <= 0:
		return None

	precios = list(precios)
	if len(precios) < periodo + 1:
		return None

	variaciones = [
		precios[indice] - precios[indice - 1]
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
	return float(100 - (100 / (1 + rs)))


def calcular_volatilidad(precios):
	if precios is None or len(precios) < 2:
		return None

	rendimientos = precios.pct_change()
	rendimientos = rendimientos.replace([float("inf"), float("-inf")], float("nan"))
	rendimientos = rendimientos.dropna()
	if rendimientos.empty:
		return None

	return float(rendimientos.std() * 100)
