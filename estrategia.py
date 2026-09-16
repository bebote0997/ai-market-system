CONDICIONES_VALIDAS = (
	"tendencia",
	"rsi",
	"macd",
	"volumen",
	"estructura_precio",
)


def crear_configuracion_estrategia(
	minimo_favorables=4,
	maximo_desfavorables=1,
	requeridas=None,
):
	if (
		isinstance(minimo_favorables, bool)
		or not isinstance(minimo_favorables, int)
		or minimo_favorables < 0
		or minimo_favorables > 5
	):
		return None
	if (
		isinstance(maximo_desfavorables, bool)
		or not isinstance(maximo_desfavorables, int)
		or maximo_desfavorables < 0
		or maximo_desfavorables > 5
	):
		return None

	if requeridas is None:
		requeridas = ()
	elif isinstance(requeridas, str):
		return None
	else:
		if not isinstance(requeridas, (list, tuple, set)):
			return None
		resultado_requeridas = []
		for condicion in requeridas:
			if condicion not in CONDICIONES_VALIDAS:
				return None
			if condicion not in resultado_requeridas:
				resultado_requeridas.append(condicion)
		requeridas = tuple(resultado_requeridas)

	return {
		"minimo_favorables": minimo_favorables,
		"maximo_desfavorables": maximo_desfavorables,
		"requeridas": requeridas,
	}


def cumple_estrategia(evaluacion, configuracion):
	if not isinstance(evaluacion, dict) or not isinstance(configuracion, dict):
		return None
	if not all(
		clave in configuracion
		for clave in ["minimo_favorables", "maximo_desfavorables", "requeridas"]
	):
		return None

	configuracion = crear_configuracion_estrategia(**{
		clave: configuracion[clave]
		for clave in ["minimo_favorables", "maximo_desfavorables", "requeridas"]
	})
	if configuracion is None:
		return None

	resumen = evaluacion.get("resumen")
	condiciones = evaluacion.get("condiciones")
	if not isinstance(resumen, dict) or not isinstance(condiciones, dict):
		return None
	favorables = resumen.get("favorables")
	desfavorables = resumen.get("desfavorables")
	if not isinstance(favorables, int) or isinstance(favorables, bool):
		return None
	if not isinstance(desfavorables, int) or isinstance(desfavorables, bool):
		return None

	if favorables < configuracion["minimo_favorables"]:
		return False
	if desfavorables > configuracion["maximo_desfavorables"]:
		return False

	for condicion in configuracion["requeridas"]:
		if condicion not in condiciones:
			return None
		if not isinstance(condiciones[condicion], dict):
			return None
		if condiciones[condicion].get("estado") != "favorable":
			return False

	return True


def generar_eventos_estrategia(evaluaciones_historicas, configuracion):
	if not isinstance(evaluaciones_historicas, list) or not isinstance(configuracion, dict):
		return []

	eventos = []
	for evaluacion_historica in evaluaciones_historicas:
		if not isinstance(evaluacion_historica, dict):
			continue
		if "fecha" not in evaluacion_historica or "precio_cierre" not in evaluacion_historica:
			continue
		evaluacion = evaluacion_historica.get("evaluacion")
		if cumple_estrategia(evaluacion, configuracion) is not True:
			continue
		eventos.append(
			{
				"fecha": evaluacion_historica["fecha"],
				"precio": evaluacion_historica["precio_cierre"],
				"evaluacion": evaluacion,
				"analisis": evaluacion_historica.get("analisis"),
				"atr_senal": (
					evaluacion_historica.get("analisis", {})
					.get("volatilidad", {})
					.get("atr_14")
					if isinstance(evaluacion_historica.get("analisis"), dict)
					else None
				),
			}
		)

	return sorted(eventos, key=lambda evento: evento["fecha"])
