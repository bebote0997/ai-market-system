from validacion_fuera_muestra import comparar_estadisticas_condiciones


def crear_reporte_comparativo(investigaciones, horizonte=5):
	if isinstance(horizonte, bool) or not isinstance(horizonte, int) or horizonte <= 0:
		raise ValueError("El horizonte debe ser un entero positivo.")
	if not investigaciones:
		return {}

	reporte = {}
	for ticker, investigacion in investigaciones.items():
		if not isinstance(investigacion, dict):
			continue
		if "error" in investigacion:
			reporte[ticker] = {
				"ticker": ticker,
				"error": investigacion["error"],
			}
			continue

		validacion = investigacion.get("validacion")
		division = validacion.get("division", {}) if isinstance(validacion, dict) else {}
		entrenamiento = validacion.get("entrenamiento", {}) if isinstance(validacion, dict) else {}
		prueba = validacion.get("prueba", {}) if isinstance(validacion, dict) else {}
		comparacion = comparar_estadisticas_condiciones(
			entrenamiento.get("estadisticas_condiciones", {}),
			prueba.get("estadisticas_condiciones", {}),
			horizonte=horizonte,
		)

		reporte[ticker] = {
			"ticker": ticker,
			"horizonte": horizonte,
			"datos": {
				"filas_historicas": investigacion.get("filas_historicas"),
				"filas_entrenamiento": division.get("filas_entrenamiento"),
				"filas_prueba": division.get("filas_prueba"),
			},
			"muestras": {
				"evaluaciones_entrenamiento": len(entrenamiento.get("evaluaciones", [])),
				"evaluaciones_prueba": len(prueba.get("evaluaciones", [])),
			},
			"condiciones": comparacion,
		}

	return reporte
