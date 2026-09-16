import pandas as pd
from statistics import mean, median

from motor_analisis import analizar_mercado
from validador_entrada import evaluar_entrada


def generar_evaluaciones_historicas(datos, minimo_historial=50):
	columnas_requeridas = ["Open", "High", "Low", "Close", "Volume"]
	if datos is None or datos.empty or minimo_historial <= 0:
		return []
	if not all(columna in datos.columns for columna in columnas_requeridas):
		return []
	if len(datos) < minimo_historial:
		return []

	datos_ordenados = datos.sort_index().copy()
	evaluaciones = []

	for posicion in range(minimo_historial - 1, len(datos_ordenados)):
		datos_hasta_fecha = datos_ordenados.iloc[:posicion + 1].copy()
		close_validos = pd.to_numeric(
			datos_hasta_fecha["Close"], errors="coerce"
		).dropna()
		if close_validos.empty:
			continue

		analisis = analizar_mercado(datos_hasta_fecha)
		if analisis is None:
			continue

		evaluacion = evaluar_entrada(analisis)
		if evaluacion is None:
			continue

		evaluaciones.append(
			{
				"fecha": datos_hasta_fecha.index[-1],
				"precio_cierre": float(close_validos.iloc[-1]),
				"analisis": analisis,
				"evaluacion": evaluacion,
			}
		)

	return evaluaciones


def calcular_resultados_futuros(datos, evaluaciones, horizontes=(1, 5, 10)):
	if datos is None or datos.empty or not evaluaciones:
		return []
	if "Close" not in datos.columns or not horizontes:
		return []

	horizontes_validos = [
		horizonte
		for horizonte in horizontes
		if isinstance(horizonte, int)
		and not isinstance(horizonte, bool)
		and horizonte > 0
	]
	if not horizontes_validos:
		return []

	datos_ordenados = datos.sort_index().copy()
	close = pd.to_numeric(datos_ordenados["Close"], errors="coerce")
	resultados = []

	for evaluacion in evaluaciones:
		fecha = evaluacion["fecha"]
		posiciones = [
			posicion
			for posicion, indice in enumerate(datos_ordenados.index)
			if indice == fecha
		]
		if not posiciones:
			continue

		posicion_actual = posiciones[0]
		precio_base = pd.to_numeric(
			pd.Series([evaluacion.get("precio_cierre")]), errors="coerce"
		).iloc[0]
		if pd.isna(precio_base) or precio_base == 0:
			precio_base = None

		retornos_futuros = {}
		for horizonte in horizontes_validos:
			posicion_futura = posicion_actual + horizonte
			if precio_base is None or posicion_futura >= len(datos_ordenados):
				retornos_futuros[horizonte] = None
				continue

			precio_futuro = close.iloc[posicion_futura]
			if pd.isna(precio_futuro):
				retornos_futuros[horizonte] = None
				continue

			retornos_futuros[horizonte] = float(
				((precio_futuro - precio_base) / precio_base) * 100
			)

		resultados.append(
			{
				"fecha": evaluacion["fecha"],
				"precio_cierre": evaluacion["precio_cierre"],
				"analisis": evaluacion["analisis"],
				"evaluacion": evaluacion["evaluacion"],
				"retornos_futuros": retornos_futuros,
			}
		)

	return resultados


def calcular_estadisticas_por_favorables(resultados, horizontes=(1, 5, 10)):
	if not resultados or not horizontes:
		return {}

	horizontes_validos = [
		horizonte
		for horizonte in horizontes
		if isinstance(horizonte, int)
		and not isinstance(horizonte, bool)
		and horizonte > 0
	]
	if not horizontes_validos:
		return {}

	grupos = {}
	for resultado in resultados:
		if not isinstance(resultado, dict):
			continue
		evaluacion = resultado.get("evaluacion")
		if not isinstance(evaluacion, dict):
			continue
		resumen = evaluacion.get("resumen")
		if not isinstance(resumen, dict):
			continue
		favorables = resumen.get("favorables")
		if isinstance(favorables, bool) or not isinstance(favorables, (int, float)):
			continue
		if pd.isna(favorables) or not pd.api.types.is_number(favorables):
			continue

		grupos.setdefault(favorables, []).append(resultado)

	estadisticas = {}
	for favorables, grupo in grupos.items():
		estadisticas_horizontes = {}
		for horizonte in horizontes_validos:
			retornos = []
			for resultado in grupo:
				retornos_futuros = resultado.get("retornos_futuros")
				if not isinstance(retornos_futuros, dict):
					continue
				retorno = retornos_futuros.get(horizonte)
				if isinstance(retorno, bool) or not isinstance(retorno, (int, float)):
					continue
				if pd.isna(retorno) or not pd.api.types.is_number(retorno):
					continue
				retornos.append(float(retorno))

			muestras = len(retornos)
			if muestras == 0:
				estadisticas_horizontes[horizonte] = {
					"muestras": 0,
					"retorno_medio": None,
					"retorno_mediano": None,
					"positivos": 0,
					"negativos": 0,
					"neutros": 0,
					"tasa_positiva": None,
				}
				continue

			positivos = sum(retorno > 0 for retorno in retornos)
			negativos = sum(retorno < 0 for retorno in retornos)
			neutros = sum(retorno == 0 for retorno in retornos)
			estadisticas_horizontes[horizonte] = {
				"muestras": muestras,
				"retorno_medio": float(mean(retornos)),
				"retorno_mediano": float(median(retornos)),
				"positivos": positivos,
				"negativos": negativos,
				"neutros": neutros,
				"tasa_positiva": (positivos / muestras) * 100,
			}

		estadisticas[favorables] = {
			"total_evaluaciones": len(grupo),
			"horizontes": estadisticas_horizontes,
		}

	return estadisticas


def calcular_estadisticas_por_condicion(resultados, horizontes=(1, 5, 10)):
	if not resultados or not horizontes:
		return {}

	horizontes_validos = [
		horizonte
		for horizonte in horizontes
		if isinstance(horizonte, int)
		and not isinstance(horizonte, bool)
		and horizonte > 0
	]
	if not horizontes_validos:
		return {}

	condiciones = ["tendencia", "rsi", "macd", "volumen", "estructura_precio"]
	estados_validos = {"favorable", "desfavorable", "no_disponible"}
	grupos = {condicion: {} for condicion in condiciones}

	for resultado in resultados:
		if not isinstance(resultado, dict):
			continue
		evaluacion = resultado.get("evaluacion")
		if not isinstance(evaluacion, dict):
			continue
		condiciones_resultado = evaluacion.get("condiciones")
		if not isinstance(condiciones_resultado, dict):
			continue

		for condicion in condiciones:
			condicion_resultado = condiciones_resultado.get(condicion)
			if not isinstance(condicion_resultado, dict):
				continue
			estado = condicion_resultado.get("estado")
			if estado not in estados_validos:
				continue
			grupos[condicion].setdefault(estado, []).append(resultado)

	estadisticas = {}
	for condicion, estados in grupos.items():
		if not estados:
			continue
		estadisticas[condicion] = {}
		for estado, grupo in estados.items():
			estadisticas_horizontes = {}
			for horizonte in horizontes_validos:
				retornos = []
				for resultado in grupo:
					retornos_futuros = resultado.get("retornos_futuros")
					if not isinstance(retornos_futuros, dict):
						continue
					retorno = retornos_futuros.get(horizonte)
					if isinstance(retorno, bool) or not isinstance(retorno, (int, float)):
						continue
					if pd.isna(retorno) or not pd.api.types.is_number(retorno):
						continue
					retornos.append(float(retorno))

				muestras = len(retornos)
				if muestras == 0:
					estadisticas_horizontes[horizonte] = {
						"muestras": 0,
						"retorno_medio": None,
						"retorno_mediano": None,
						"positivos": 0,
						"negativos": 0,
						"neutros": 0,
						"tasa_positiva": None,
					}
					continue

				positivos = sum(retorno > 0 for retorno in retornos)
				negativos = sum(retorno < 0 for retorno in retornos)
				neutros = sum(retorno == 0 for retorno in retornos)
				estadisticas_horizontes[horizonte] = {
					"muestras": muestras,
					"retorno_medio": float(mean(retornos)),
					"retorno_mediano": float(median(retornos)),
					"positivos": positivos,
					"negativos": negativos,
					"neutros": neutros,
					"tasa_positiva": (positivos / muestras) * 100,
				}

			estadisticas[condicion][estado] = {
				"total_evaluaciones": len(grupo),
				"horizontes": estadisticas_horizontes,
			}

	return estadisticas
