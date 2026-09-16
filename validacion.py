import math


def validar_operacion(operacion):
	if not isinstance(operacion, dict):
		return False
	claves_requeridas = [
		"simbolo",
		"ticker",
		"mercado",
		"precio_entrada",
		"cantidad",
	]

	if any(clave not in operacion for clave in claves_requeridas):
		return False

	if not all(
		isinstance(operacion[clave], str) and operacion[clave].strip()
		for clave in ["simbolo", "ticker", "mercado"]
	):
		return False

	for clave in ["precio_entrada", "cantidad"]:
		valor = operacion[clave]
		if isinstance(valor, bool) or not isinstance(valor, (int, float)) or not math.isfinite(valor) or valor <= 0:
			return False

	return True
