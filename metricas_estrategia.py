import math
from statistics import mean


def _numero_valido(valor):
	return (
		isinstance(valor, (int, float))
		and not isinstance(valor, bool)
		and math.isfinite(float(valor))
	)


def calcular_metricas_estrategia(resultado_simulacion):
	if resultado_simulacion is None or not isinstance(resultado_simulacion, dict):
		return None

	operaciones = resultado_simulacion.get("operaciones", [])
	if not isinstance(operaciones, list):
		return None
	validas = [
		operacion
		for operacion in operaciones
		if isinstance(operacion, dict)
		and _numero_valido(operacion.get("resultado_neto"))
		and _numero_valido(operacion.get("retorno_neto_pct"))
	]
	ganadoras = [operacion for operacion in validas if operacion["resultado_neto"] > 0]
	perdedoras = [operacion for operacion in validas if operacion["resultado_neto"] < 0]
	neutras = [operacion for operacion in validas if operacion["resultado_neto"] == 0]
	numero_operaciones = len(validas)
	ganancia_total = sum(operacion["resultado_neto"] for operacion in ganadoras)
	perdida_total = abs(sum(operacion["resultado_neto"] for operacion in perdedoras))
	if perdida_total == 0:
		profit_factor = None
	else:
		profit_factor = ganancia_total / perdida_total

	capital_inicial = resultado_simulacion.get("capital_inicial")
	capital_final = resultado_simulacion.get("capital_final")
	if _numero_valido(capital_inicial) and _numero_valido(capital_final) and capital_inicial != 0:
		retorno_total_pct = (capital_final - capital_inicial) / capital_inicial * 100
	else:
		retorno_total_pct = None

	curva_capital = resultado_simulacion.get("curva_capital", [])
	curva_drawdown = []
	maximo_previo = None
	for punto in curva_capital if isinstance(curva_capital, list) else []:
		if not isinstance(punto, dict) or not _numero_valido(punto.get("capital")):
			continue
		capital = float(punto["capital"])
		if maximo_previo is None and capital <= 0:
			return None
		if maximo_previo is None or capital > maximo_previo:
			maximo_previo = capital
		drawdown_pct = (capital - maximo_previo) / maximo_previo * 100
		curva_drawdown.append({
			"fecha": punto.get("fecha"),
			"capital": capital,
			"drawdown_pct": float(drawdown_pct),
		})
	max_drawdown_pct = min(
		(punto["drawdown_pct"] for punto in curva_drawdown),
		default=0.0,
	)

	return {
		"numero_operaciones": numero_operaciones,
		"ganadoras": len(ganadoras),
		"perdedoras": len(perdedoras),
		"neutras": len(neutras),
		"tasa_acierto": (len(ganadoras) / numero_operaciones * 100) if numero_operaciones else 0.0,
		"ganancia_total": float(ganancia_total),
		"perdida_total": float(perdida_total),
		"profit_factor": float(profit_factor) if profit_factor is not None else None,
		"resultado_neto_total": float(sum(operacion["resultado_neto"] for operacion in validas)),
		"retorno_total_pct": float(retorno_total_pct) if retorno_total_pct is not None else None,
		"retorno_medio_operacion_pct": float(mean(operacion["retorno_neto_pct"] for operacion in validas)) if validas else None,
		"ganancia_media": float(mean(operacion["resultado_neto"] for operacion in ganadoras)) if ganadoras else None,
		"perdida_media": float(mean(operacion["resultado_neto"] for operacion in perdedoras)) if perdedoras else None,
		"expectativa": float(mean(operacion["resultado_neto"] for operacion in validas)) if validas else None,
		"max_drawdown_pct": float(max_drawdown_pct),
		"curva_drawdown": curva_drawdown,
	}
