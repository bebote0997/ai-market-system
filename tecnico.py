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
