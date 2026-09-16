def evaluar_entrada(analisis):
	if analisis is None:
		return None

	tendencia = analisis.get("tendencia") or {}
	estado_tendencia = tendencia.get("estado")
	ema_20 = tendencia.get("ema_20")
	ema_50 = tendencia.get("ema_50")
	if estado_tendencia is None or ema_20 is None or ema_50 is None:
		condicion_tendencia = {
			"estado": "no_disponible",
			"valor": estado_tendencia,
			"descripcion": "Faltan estado, EMA 20 o EMA 50 para evaluar la tendencia.",
		}
	elif estado_tendencia == "alcista" and ema_20 > ema_50:
		condicion_tendencia = {
			"estado": "favorable",
			"valor": {"estado": estado_tendencia, "ema_20": ema_20, "ema_50": ema_50},
			"descripcion": "La tendencia es alcista y la EMA 20 supera a la EMA 50.",
		}
	else:
		condicion_tendencia = {
			"estado": "desfavorable",
			"valor": {"estado": estado_tendencia, "ema_20": ema_20, "ema_50": ema_50},
			"descripcion": "La tendencia no es alcista con EMA 20 por encima de EMA 50.",
		}

	momentum = analisis.get("momentum") or {}
	rsi = momentum.get("rsi_14")
	if rsi is None:
		condicion_rsi = {
			"estado": "no_disponible",
			"valor": None,
			"descripcion": "No hay RSI 14 disponible para evaluar el rango 50-70.",
		}
	elif 50 <= rsi <= 70:
		condicion_rsi = {
			"estado": "favorable",
			"valor": rsi,
			"descripcion": "El RSI 14 se encuentra entre 50 y 70, inclusive.",
		}
	else:
		condicion_rsi = {
			"estado": "desfavorable",
			"valor": rsi,
			"descripcion": "El RSI 14 está fuera del rango 50-70.",
		}

	macd = momentum.get("macd") or {}
	macd_valor = macd.get("macd")
	senal = macd.get("senal")
	histograma = macd.get("histograma")
	if macd_valor is None or senal is None or histograma is None:
		condicion_macd = {
			"estado": "no_disponible",
			"valor": macd,
			"descripcion": "Faltan MACD, señal o histograma para evaluar el momentum.",
		}
	elif macd_valor > senal and histograma > 0:
		condicion_macd = {
			"estado": "favorable",
			"valor": macd,
			"descripcion": "El MACD supera a la señal y el histograma es positivo.",
		}
	else:
		condicion_macd = {
			"estado": "desfavorable",
			"valor": macd,
			"descripcion": "El MACD no supera a la señal o el histograma no es positivo.",
		}

	volumen = analisis.get("volumen") or {}
	ratio_volumen = volumen.get("ratio_volumen")
	if ratio_volumen is None:
		condicion_volumen = {
			"estado": "no_disponible",
			"valor": None,
			"descripcion": "No hay ratio de volumen disponible para evaluar el volumen.",
		}
	elif ratio_volumen >= 1.0:
		condicion_volumen = {
			"estado": "favorable",
			"valor": ratio_volumen,
			"descripcion": "El ratio de volumen es igual o superior a 1.0.",
		}
	else:
		condicion_volumen = {
			"estado": "desfavorable",
			"valor": ratio_volumen,
			"descripcion": "El ratio de volumen es inferior a 1.0.",
		}

	estructura = analisis.get("estructura_precio") or {}
	posicion_rango = estructura.get("posicion_rango")
	if posicion_rango is None:
		condicion_estructura = {
			"estado": "no_disponible",
			"valor": None,
			"descripcion": "No hay posición dentro del rango disponible.",
		}
	elif 50 <= posicion_rango <= 90:
		condicion_estructura = {
			"estado": "favorable",
			"valor": posicion_rango,
			"descripcion": "La posición dentro del rango está entre 50 y 90, inclusive.",
		}
	else:
		condicion_estructura = {
			"estado": "desfavorable",
			"valor": posicion_rango,
			"descripcion": "La posición dentro del rango está fuera de 50-90.",
		}

	condiciones = {
		"tendencia": condicion_tendencia,
		"rsi": condicion_rsi,
		"macd": condicion_macd,
		"volumen": condicion_volumen,
		"estructura_precio": condicion_estructura,
	}
	estados = [condicion["estado"] for condicion in condiciones.values()]

	return {
		"condiciones": condiciones,
		"resumen": {
			"favorables": estados.count("favorable"),
			"desfavorables": estados.count("desfavorable"),
			"no_disponibles": estados.count("no_disponible"),
			"total": 5,
		},
	}
