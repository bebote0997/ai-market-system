import math
import numbers


def _finito(valor):
	if isinstance(valor, bool) or not isinstance(valor, numbers.Real):
		return False
	return math.isfinite(float(valor))


def crear_configuracion_riesgo(
	riesgo_por_operacion_pct=1.0,
	metodo_stop="atr",
	atr_multiplicador=2.0,
	ratio_objetivo=2.0,
	maximo_capital_pct=100.0,
	stop_porcentual_pct=2.0,
):
	if not _finito(riesgo_por_operacion_pct) or not 0 < riesgo_por_operacion_pct <= 100:
		return None
	if metodo_stop not in ("atr", "porcentual"):
		return None
	if not _finito(atr_multiplicador) or atr_multiplicador <= 0:
		return None
	if not _finito(ratio_objetivo) or ratio_objetivo <= 0:
		return None
	if not _finito(maximo_capital_pct) or not 0 < maximo_capital_pct <= 100:
		return None
	if not _finito(stop_porcentual_pct) or stop_porcentual_pct <= 0:
		return None
	return {
		"riesgo_por_operacion_pct": float(riesgo_por_operacion_pct),
		"metodo_stop": metodo_stop,
		"atr_multiplicador": float(atr_multiplicador),
		"ratio_objetivo": float(ratio_objetivo),
		"maximo_capital_pct": float(maximo_capital_pct),
		"stop_porcentual_pct": float(stop_porcentual_pct),
	}


def calcular_plan_riesgo(capital_actual, precio_entrada, configuracion, atr=None):
	if not _finito(capital_actual) or capital_actual <= 0 or not _finito(precio_entrada) or precio_entrada <= 0:
		return None
	if not isinstance(configuracion, dict):
		return None
	config = crear_configuracion_riesgo(**configuracion)
	if config is None:
		return None
	if config["metodo_stop"] == "atr":
		if not _finito(atr) or atr <= 0:
			return None
		distancia_stop = float(atr) * config["atr_multiplicador"]
	else:
		distancia_stop = precio_entrada * config["stop_porcentual_pct"] / 100
	stop = precio_entrada - distancia_stop
	if stop <= 0:
		return None
	riesgo_unitario = precio_entrada - stop
	riesgo_monetario = capital_actual * config["riesgo_por_operacion_pct"] / 100
	capital_maximo = capital_actual * config["maximo_capital_pct"] / 100
	cantidad_por_riesgo = riesgo_monetario / riesgo_unitario
	cantidad_por_capital = capital_maximo / precio_entrada
	cantidad = min(cantidad_por_riesgo, cantidad_por_capital)
	return {
		"capital_actual": float(capital_actual),
		"precio_entrada": float(precio_entrada),
		"riesgo_monetario_maximo": float(riesgo_monetario),
		"riesgo_unitario": float(riesgo_unitario),
		"cantidad_por_riesgo": float(cantidad_por_riesgo),
		"capital_maximo": float(capital_maximo),
		"cantidad_por_capital": float(cantidad_por_capital),
		"cantidad": float(cantidad),
		"capital_utilizado": float(cantidad * precio_entrada),
		"stop": float(stop),
		"target": float(precio_entrada + riesgo_unitario * config["ratio_objetivo"]),
		"metodo_stop": config["metodo_stop"],
	}
