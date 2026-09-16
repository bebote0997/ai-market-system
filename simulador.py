import math

import pandas as pd

from validacion_historica import ordenar_datos_temporales


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
	if valor is None or pd.api.types.is_bool(valor):
		return False
	try:
		valor = float(valor)
	except (TypeError, ValueError):
		return False
	return math.isfinite(valor) and valor > 0


def simular_operaciones(datos, eventos, configuracion):
    """V1: señal al cierre t, compra Open[t+1], venta Close[t+1+plazo].

    Equity al cierre de cada barra; coste único cobrado al salir. Una posición
    pendiente al final se conserva y valora, sin consultar barras inexistentes.
    Un cierre inválido durante la posición impide valorar: error explícito.
    """
    if datos is None or not isinstance(datos, pd.DataFrame) or datos.empty:
        return None
    if not isinstance(eventos, list) or not isinstance(configuracion, dict):
        return None
    claves = ("capital_inicial", "porcentaje_capital_por_operacion",
              "periodo_salida", "coste_porcentual")
    if not all(clave in configuracion for clave in claves):
        return None
    config = crear_configuracion_simulacion(**{k: configuracion[k] for k in claves})
    if config is None:
        return None
    if not all(columna in datos.columns for columna in ("Open", "Close")):
        raise ValueError("La simulación V1 requiere Open y Close.")
    datos = ordenar_datos_temporales(datos)
    # No coercionar bool a 1: conservar el valor para _precio_valido.
    fechas_eventos = {
        evento["fecha"] for evento in eventos
        if isinstance(evento, dict) and evento.get("fecha") in datos.index
    }
    capital = config["capital_inicial"]
    operaciones = []
    curva = [{"fecha": None, "capital": capital}]
    abierta = None
    for posicion, fecha in enumerate(datos.index):
        # Sólo una señal de la barra anterior puede provocar una entrada.
        if (abierta is None and capital > 0 and posicion > 0
                and datos.index[posicion - 1] in fechas_eventos):
            precio = datos["Open"].iloc[posicion]
            if _precio_valido(precio):
                precio = float(precio)
                utilizado = capital * config["porcentaje_capital_por_operacion"] / 100
                abierta = {
                    "fecha_senal": datos.index[posicion - 1],
                    "fecha_entrada": fecha,
                    "precio_entrada": precio,
                    "cantidad": utilizado / precio,
                    "capital_antes": capital,
                    "capital_utilizado": utilizado,
                    "capital_no_utilizado": capital - utilizado,
                    "posicion_salida": posicion + config["periodo_salida"],
                    "coste": utilizado * config["coste_porcentual"] / 100,
                }
        equity = capital
        if abierta is not None:
            cierre = datos["Close"].iloc[posicion]
            if not _precio_valido(cierre):
                raise ValueError(f"Close inválido en {fecha}: no se puede valorar/cerrar la posición.")
            cierre = float(cierre)
            equity = abierta["capital_no_utilizado"] + abierta["cantidad"] * cierre
            if posicion == abierta["posicion_salida"]:
                bruto = abierta["cantidad"] * cierre - abierta["capital_utilizado"]
                neto = bruto - abierta["coste"]
                capital = abierta["capital_antes"] + neto
                operacion = {
                    k: v for k, v in abierta.items()
                    if k not in ("posicion_salida", "capital_no_utilizado", "ultimo_precio", "valor_actual")
                }
                operacion.update({
                    "fecha_salida": fecha,
                    "precio_salida": cierre,
                    "resultado_bruto": bruto,
                    "resultado_neto": neto,
                    "retorno_bruto_pct": bruto / abierta["capital_utilizado"] * 100,
                    "retorno_neto_pct": neto / abierta["capital_utilizado"] * 100,
                    "capital_despues": capital,
                })
                operaciones.append(operacion)
                abierta = None
                equity = capital
            else:
                abierta["ultimo_precio"] = cierre
                abierta["valor_actual"] = abierta["cantidad"] * cierre
        curva.append({"fecha": fecha, "capital": float(equity)})

    return {
        "capital_inicial": config["capital_inicial"],
        "capital_final": curva[-1]["capital"],
        "capital_realizado": float(capital),
        "posicion_abierta": abierta,
        "operaciones": operaciones,
        "curva_capital": curva,
    }
