from validacion import validar_operacion
from mercado import obtener_precio_actual
from analisis import analizar_operacion


def procesar_operacion(operacion, capital_inicial):
	if not validar_operacion(operacion):
		return None

	precio_actual = obtener_precio_actual(operacion["ticker"])
	if precio_actual is None:
		return None

	(
		capital_utilizado,
		valor_actual,
		ganancia_perdida,
		rentabilidad,
		capital_total,
	) = analizar_operacion(
		capital_inicial,
		operacion["precio_entrada"],
		precio_actual,
		operacion["cantidad"],
	)

	return {
		"simbolo": operacion["simbolo"],
		"ticker": operacion["ticker"],
		"mercado": operacion["mercado"],
		"precio_entrada": operacion["precio_entrada"],
		"precio_actual": precio_actual,
		"cantidad": operacion["cantidad"],
		"capital_utilizado": capital_utilizado,
		"valor_actual": valor_actual,
		"ganancia_perdida": ganancia_perdida,
		"rentabilidad": rentabilidad,
		"capital_total": capital_total,
	}


def procesar_cartera(operaciones, capital_inicial):
	resultados = []

	for operacion in operaciones:
		resultado = procesar_operacion(operacion, capital_inicial)
		if resultado is not None:
			resultados.append(resultado)

	return resultados
