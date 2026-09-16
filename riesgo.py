import math
import numbers

from core.contracts import RiskDecision, TradePlan
from core.timeframes import validar_timeframe


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


def calcular_plan_riesgo(capital_actual, precio_entrada, configuracion, atr=None, side="long"):
	if not _finito(capital_actual) or capital_actual <= 0 or not _finito(precio_entrada) or precio_entrada <= 0:
		return None
	if not isinstance(configuracion, dict):
		return None
	if side not in ("long", "short"):
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
	stop = precio_entrada - distancia_stop if side == "long" else precio_entrada + distancia_stop
	if stop <= 0:
		return None
	riesgo_unitario = abs(precio_entrada - stop)
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
		"target": float(
			precio_entrada + riesgo_unitario * config["ratio_objetivo"]
			if side == "long"
			else precio_entrada - riesgo_unitario * config["ratio_objetivo"]
		),
		"metodo_stop": config["metodo_stop"],
		"side": side,
	}


def crear_configuracion_riesgo_v2(
	riesgo_por_operacion_pct=1.0,
	metodo_stop="atr",
	atr_multiplicador=2.0,
	ratio_minimo=3.0,
	maximo_capital_pct=100.0,
	stop_porcentual_pct=2.0,
):
	config = crear_configuracion_riesgo(
		riesgo_por_operacion_pct,
		metodo_stop,
		atr_multiplicador,
		ratio_minimo,
		maximo_capital_pct,
		stop_porcentual_pct,
	)
	if config is None:
		return None
	config["ratio_minimo"] = config.pop("ratio_objetivo")
	return config


def evaluar_trade_plan(plan, capital_actual, instrumento, configuracion, atr=None):
	if isinstance(plan, TradePlan):
		plan_data = plan.__dict__
	else:
		plan_data = plan if isinstance(plan, dict) else None
	if plan_data is None or instrumento is None or not isinstance(configuracion, dict):
		return RiskDecision("1.0", "REJECTED", "", "", None, None, 0.0, 0.0, 0.0, "invalid_input")
	try:
		side = plan_data["side"]
		entry = float(plan_data["entry"])
		stop = float(plan_data["stop"])
		target = float(plan_data["target"])
		rr = float(plan_data["risk_reward"])
	except (KeyError, TypeError, ValueError):
		return RiskDecision("1.0", "REJECTED", plan_data.get("symbol", ""), plan_data.get("side", ""), None, None, 0.0, 0.0, 0.0, "invalid_plan")
	if side not in ("long", "short") or not validar_timeframe(plan_data.get("timeframe")):
		return RiskDecision("1.0", "REJECTED", plan_data.get("symbol", ""), side, None, None, entry, stop, target, "invalid_direction_or_timeframe")
	if not all(_finito(valor) and valor > 0 for valor in (entry, stop, target, capital_actual)):
		return RiskDecision("1.0", "REJECTED", plan_data.get("symbol", ""), side, None, None, entry, stop, target, "invalid_numeric_value")
	if instrumento.contract_multiplier is None or not _finito(instrumento.contract_multiplier) or instrumento.contract_multiplier <= 0:
		return RiskDecision("1.0", "REJECTED", plan_data.get("symbol", ""), side, None, None, entry, stop, target, "instrument_contract_data_unavailable")
	if (side == "long" and not stop < entry < target) or (side == "short" and not target < entry < stop):
		return RiskDecision("1.0", "REJECTED", plan_data["symbol"], side, None, None, entry, stop, target, "invalid_level_order")
	if not _finito(rr) or rr < configuracion["ratio_minimo"]:
		return RiskDecision("1.0", "REJECTED", plan_data["symbol"], side, None, None, entry, stop, target, "rr_below_minimum")
	risk_unitario = abs(entry - stop) * instrumento.contract_multiplier
	risk_money = float(capital_actual) * configuracion["riesgo_por_operacion_pct"] / 100
	capital_max = float(capital_actual) * configuracion["maximo_capital_pct"] / 100
	quantity_risk = risk_money / risk_unitario
	quantity_capital = capital_max / (entry * instrumento.contract_multiplier)
	quantity = min(quantity_risk, quantity_capital)
	if quantity <= 0 or not _finito(quantity):
		return RiskDecision("1.0", "REJECTED", plan_data["symbol"], side, None, None, entry, stop, target, "invalid_quantity")
	return RiskDecision("1.0", "APPROVED", plan_data["symbol"], side, float(quantity), float(quantity * risk_unitario), entry, stop, target, "approved")
