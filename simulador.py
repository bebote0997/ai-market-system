import math

import pandas as pd

from riesgo import calcular_plan_riesgo
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


def simular_operaciones_con_riesgo(datos, eventos, configuracion, configuracion_riesgo):
    if datos is None or not isinstance(datos, pd.DataFrame) or datos.empty:
        return None
    if not isinstance(eventos, list) or not isinstance(configuracion, dict):
        return None
    if not isinstance(configuracion_riesgo, dict):
        return None
    if not all(columna in datos.columns for columna in ("Open", "High", "Low", "Close")):
        raise ValueError("La simulación con riesgo requiere Open, High, Low y Close.")
    datos_ordenados = ordenar_datos_temporales(datos)
    config = crear_configuracion_simulacion(**{
        clave: configuracion[clave]
        for clave in ("capital_inicial", "porcentaje_capital_por_operacion", "periodo_salida", "coste_porcentual")
    })
    if config is None:
        return None
    capital = config["capital_inicial"]
    operaciones = []
    curva = [{"fecha": None, "capital": capital}]
    abierta = None
    ultima_salida = -1
    eventos_por_fecha = {
        evento.get("fecha"): evento
        for evento in eventos
        if isinstance(evento, dict) and evento.get("fecha") in datos_ordenados.index
    }

    for posicion, fecha in enumerate(datos_ordenados.index):
        if abierta is None and posicion > 0 and posicion - 1 > ultima_salida:
            evento = eventos_por_fecha.get(datos_ordenados.index[posicion - 1])
            if evento is not None:
                precio_entrada = datos_ordenados["Open"].iloc[posicion]
                side = evento.get("side", "long")
                decision = evento.get("risk_decision")
                if isinstance(decision, dict) and decision.get("status") == "APPROVED":
                    plan = {
                        "capital_actual": capital,
                        "precio_entrada": decision["entry"],
                        "riesgo_monetario_maximo": decision["capital_at_risk"],
                        "riesgo_unitario": abs(decision["entry"] - decision["stop"]),
                        "cantidad": decision["quantity"],
                        "capital_utilizado": decision["quantity"] * decision["entry"],
                        "stop": decision["stop"],
                        "target": decision["target"],
                        "metodo_stop": decision.get("method", "plan"),
                        "side": side,
                    }
                else:
                    plan = calcular_plan_riesgo(
                        capital, precio_entrada, configuracion_riesgo,
                        evento.get("atr_senal"), side,
                    )
                if plan is not None:
                    abierta = {
                        **plan,
                        "fecha_senal": datos_ordenados.index[posicion - 1],
                        "fecha_entrada": fecha,
                        "efectivo_no_utilizado": capital - plan["capital_utilizado"],
                        "stop_inicial": plan["stop"],
                        "target_inicial": plan["target"],
                        "posicion_entrada": posicion,
                        "posicion_salida_limite": posicion + config["periodo_salida"],
                        "capital_antes": capital,
                        "atr_senal": evento.get("atr_senal"),
                        "side": side,
                        "ultimo_precio": None,
                        "ultima_fecha": None,
                        "equity_actual": capital - plan["capital_utilizado"],
                    }

        if abierta is not None:
            close = datos_ordenados["Close"].iloc[posicion]
            if _precio_valido(close):
                abierta["ultimo_precio"] = float(close)
                abierta["ultima_fecha"] = fecha
                abierta["equity_actual"] = (
                    abierta["efectivo_no_utilizado"]
                    + abierta["cantidad"] * abierta["ultimo_precio"]
                )
                equity = abierta["equity_actual"]
            else:
                equity = capital
            if posicion > abierta["posicion_entrada"]:
                open_price = datos_ordenados["Open"].iloc[posicion]
                high = datos_ordenados["High"].iloc[posicion]
                low = datos_ordenados["Low"].iloc[posicion]
                exit_price = None
                motivo = None
                salida_gap = False
                if abierta["side"] == "long":
                    gap_stop = _precio_valido(open_price) and float(open_price) <= abierta["stop"]
                    gap_target = _precio_valido(open_price) and float(open_price) >= abierta["target"]
                    stop_hit = _precio_valido(low) and float(low) <= abierta["stop"]
                    target_hit = _precio_valido(high) and float(high) >= abierta["target"]
                else:
                    gap_stop = _precio_valido(open_price) and float(open_price) >= abierta["stop"]
                    gap_target = _precio_valido(open_price) and float(open_price) <= abierta["target"]
                    stop_hit = _precio_valido(high) and float(high) >= abierta["stop"]
                    target_hit = _precio_valido(low) and float(low) <= abierta["target"]
                if gap_stop:
                    exit_price, motivo, salida_gap = float(open_price), "stop", True
                elif gap_target:
                    exit_price, motivo, salida_gap = float(open_price), "target", True
                elif stop_hit:
                    exit_price, motivo = abierta["stop"], "stop"
                elif target_hit:
                    exit_price, motivo = abierta["target"], "target"
                elif posicion >= abierta["posicion_salida_limite"] and _precio_valido(close):
                    exit_price, motivo = float(close), "tiempo"

                if exit_price is not None:
                    bruto = (
                        abierta["cantidad"] * exit_price - abierta["capital_utilizado"]
                        if abierta["side"] == "long"
                        else abierta["cantidad"] * (abierta["precio_entrada"] - exit_price)
                    )
                    coste = abierta["capital_utilizado"] * config["coste_porcentual"] / 100
                    neto = bruto - coste
                    capital = abierta["capital_antes"] + neto
                    operacion = {
                        clave: abierta[clave]
                        for clave in ("fecha_senal", "fecha_entrada", "precio_entrada", "cantidad", "capital_antes", "capital_utilizado", "riesgo_monetario_maximo", "riesgo_unitario", "stop", "target", "metodo_stop", "atr_senal", "side")
                    }
                    operacion.update({
                        "fecha_salida": fecha, "precio_salida": exit_price,
                        "stop_inicial": abierta["stop_inicial"], "target_inicial": abierta["target_inicial"],
                        "riesgo_monetario_planeado": abierta["riesgo_monetario_maximo"],
                        "motivo_salida": motivo, "salida_por_gap": salida_gap,
                        "resultado_bruto": bruto, "coste": coste, "resultado_neto": neto,
                        "retorno_bruto_pct": bruto / abierta["capital_utilizado"] * 100,
                        "retorno_neto_pct": neto / abierta["capital_utilizado"] * 100,
                        "capital_despues": capital,
                    })
                    operaciones.append(operacion)
                    ultima_salida = posicion
                    abierta = None
                    equity = capital
        else:
            equity = capital
        curva.append({"fecha": fecha, "capital": float(equity)})

    if abierta is not None:
        abierta = {
            clave: valor
            for clave, valor in abierta.items()
            if not clave.startswith("posicion_")
        }
    return {
        "capital_inicial": config["capital_inicial"],
        "capital_final": float(curva[-1]["capital"]),
        "capital_realizado": float(capital),
        "operaciones": operaciones,
        "curva_capital": curva,
        "posicion_abierta": abierta,
    }


def simular_trade_plan(datos, trade_plan, risk_decision, configuracion):
    if hasattr(trade_plan, "__dict__"):
        trade_plan = trade_plan.__dict__
    if hasattr(risk_decision, "__dict__"):
        risk_decision = risk_decision.__dict__
    if not isinstance(trade_plan, dict) or not isinstance(risk_decision, dict):
        return None
    if risk_decision.get("status") != "APPROVED":
        return {
            "capital_inicial": configuracion.get("capital_inicial") if isinstance(configuracion, dict) else None,
            "capital_final": configuracion.get("capital_inicial") if isinstance(configuracion, dict) else None,
            "capital_realizado": configuracion.get("capital_inicial") if isinstance(configuracion, dict) else None,
            "operaciones": [],
            "curva_capital": [],
            "posicion_abierta": None,
        }
    evento = {
        "fecha": trade_plan.get("fecha"),
        "side": trade_plan.get("side", "long"),
        "atr_senal": trade_plan.get("atr_senal"),
        "risk_decision": risk_decision,
    }
    return simular_operaciones_con_riesgo(
        datos, [evento], configuracion, {"riesgo_por_operacion_pct": 1.0, "metodo_stop": "porcentual", "atr_multiplicador": 1.0, "ratio_objetivo": 3.0, "maximo_capital_pct": 100.0, "stop_porcentual_pct": 2.0},
    )
