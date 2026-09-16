import math

import pandas as pd


def crear_configuracion_simulacion(
	capital_inicial=10000.0,
	porcentaje_capital_por_operacion=100.0,
	periodo_salida=5,
	coste_porcentual=0.10,
):
	if (
		isinstance(capital_inicial, bool)
		or not isinstance(capital_inicial, (int, float))
		or not math.isfinite(capital_inicial)
		or capital_inicial <= 0
	):
		return None
	if (
		isinstance(porcentaje_capital_por_operacion, bool)
		or not isinstance(porcentaje_capital_por_operacion, (int, float))
		or not math.isfinite(porcentaje_capital_por_operacion)
		or porcentaje_capital_por_operacion <= 0
		or porcentaje_capital_por_operacion > 100
	):
		return None
	if (
		isinstance(periodo_salida, bool)
		or not isinstance(periodo_salida, int)
		or periodo_salida <= 0
	):
		return None
	if (
		isinstance(coste_porcentual, bool)
		or not isinstance(coste_porcentual, (int, float))
		or not math.isfinite(coste_porcentual)
		or coste_porcentual < 0
	):
		return None

	return {
		"capital_inicial": float(capital_inicial),
		"porcentaje_capital_por_operacion": float(porcentaje_capital_por_operacion),
		"periodo_salida": periodo_salida,
		"coste_porcentual": float(coste_porcentual),
	}


def _precio_valido(valor):
	if valor is None or isinstance(valor, bool):
		return False
	try:
		valor = float(valor)
	except (TypeError, ValueError):
		return False
	return math.isfinite(valor) and valor > 0


def simular_operaciones(datos, eventos, configuracion):
	if datos is None or not isinstance(datos, pd.DataFrame) or datos.empty:
		return None
	if not isinstance(eventos, list) or not isinstance(configuracion, dict):
		return None
	claves = [
		"capital_inicial",
		"porcentaje_capital_por_operacion",
		"periodo_salida",
		"coste_porcentual",
	]
	if not all(clave in configuracion for clave in claves):
		return None

	datos_ordenados = datos.sort_index().copy()
	if "Close" not in datos_ordenados.columns:
		return None
	close = pd.to_numeric(datos_ordenados["Close"], errors="coerce")
	capital_actual = float(configuracion["capital_inicial"])
	operaciones = []
	curva_capital = [{"fecha": None, "capital": capital_actual}]
	ultima_salida = -1
	fechas_validas = set(datos_ordenados.index)
	eventos_validos = [
		evento
		for evento in eventos
		if isinstance(evento, dict)
		and evento.get("fecha") in fechas_validas
	]

	for evento in sorted(
		eventos_validos,
		key=lambda evento: evento.get("fecha"),
	):
		fecha_entrada = evento.get("fecha")
		posiciones = [
			posicion
			for posicion, indice in enumerate(datos_ordenados.index)
			if indice == fecha_entrada
		]
		if not posiciones:
			continue
		posicion_entrada = posiciones[0]
		if posicion_entrada <= ultima_salida:
			continue
		posicion_salida = posicion_entrada + configuracion["periodo_salida"]
		if posicion_salida >= len(datos_ordenados):
			continue

		precio_entrada = close.iloc[posicion_entrada]
		precio_salida = close.iloc[posicion_salida]
		if not _precio_valido(precio_entrada) or not _precio_valido(precio_salida):
			continue

		precio_entrada = float(precio_entrada)
		precio_salida = float(precio_salida)
		capital_antes = capital_actual
		capital_utilizado = capital_actual * configuracion["porcentaje_capital_por_operacion"] / 100
		cantidad = capital_utilizado / precio_entrada
		valor_salida_bruto = cantidad * precio_salida
		resultado_bruto = valor_salida_bruto - capital_utilizado
		coste = capital_utilizado * configuracion["coste_porcentual"] / 100
		resultado_neto = resultado_bruto - coste
		capital_despues = capital_actual + resultado_neto
		operacion = {
			"fecha_entrada": fecha_entrada,
			"fecha_salida": datos_ordenados.index[posicion_salida],
			"precio_entrada": precio_entrada,
			"precio_salida": precio_salida,
			"cantidad": float(cantidad),
			"capital_antes": float(capital_antes),
			"capital_utilizado": float(capital_utilizado),
			"resultado_bruto": float(resultado_bruto),
			"coste": float(coste),
			"resultado_neto": float(resultado_neto),
			"retorno_bruto_pct": float(resultado_bruto / capital_utilizado * 100),
			"retorno_neto_pct": float(resultado_neto / capital_utilizado * 100),
			"capital_despues": float(capital_despues),
		}
		operaciones.append(operacion)
		curva_capital.append({
			"fecha": operacion["fecha_salida"],
			"capital": operacion["capital_despues"],
		})
		capital_actual = capital_despues
		ultima_salida = posicion_salida

	return {
		"capital_inicial": float(configuracion["capital_inicial"]),
		"capital_final": float(capital_actual),
		"operaciones": operaciones,
		"curva_capital": curva_capital,
	}
