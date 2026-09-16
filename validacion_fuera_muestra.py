from backtest import (
	calcular_estadisticas_por_condicion,
	calcular_estadisticas_por_favorables,
	calcular_resultados_futuros,
	generar_evaluaciones_historicas,
)
from validacion_historica import (
	dividir_datos_cronologicamente,
	preparar_segmento_prueba_con_contexto,
)


def evaluar_segmento(
	datos,
	minimo_historial=50,
	horizontes=(1, 5, 10),
	indices_objetivo=None,
):
	if datos is None or datos.empty:
		return {
			"evaluaciones": [],
			"resultados": [],
			"estadisticas_favorables": {},
			"estadisticas_condiciones": {},
		}

	evaluaciones = generar_evaluaciones_historicas(
		datos,
		minimo_historial=minimo_historial,
	)
	if indices_objetivo is not None:
		indices_objetivo = set(indices_objetivo)
		evaluaciones = [
			evaluacion
			for evaluacion in evaluaciones
			if evaluacion.get("fecha") in indices_objetivo
		]

	resultados = calcular_resultados_futuros(
		datos,
		evaluaciones,
		horizontes=horizontes,
	)
	return {
		"evaluaciones": evaluaciones,
		"resultados": resultados,
		"estadisticas_favorables": calcular_estadisticas_por_favorables(
			resultados,
			horizontes=horizontes,
		),
		"estadisticas_condiciones": calcular_estadisticas_por_condicion(
			resultados,
			horizontes=horizontes,
		),
	}


def ejecutar_validacion_fuera_muestra(
	datos,
	proporcion_entrenamiento=0.70,
	minimo_historial=50,
	horizontes=(1, 5, 10),
):
	division = dividir_datos_cronologicamente(datos, proporcion_entrenamiento)
	if division is None:
		return None

	entrenamiento = evaluar_segmento(
		division["entrenamiento"],
		minimo_historial=minimo_historial,
		horizontes=horizontes,
	)
	contexto_prueba = preparar_segmento_prueba_con_contexto(
		division["entrenamiento"],
		division["prueba"],
		minimo_historial=minimo_historial,
	)
	if contexto_prueba is None:
		return None

	prueba = evaluar_segmento(
		contexto_prueba["datos_con_contexto"],
		minimo_historial=minimo_historial,
		horizontes=horizontes,
		indices_objetivo=contexto_prueba["indices_prueba"],
	)
	return {
		"division": {
			"total_filas": division["total_filas"],
			"filas_entrenamiento": division["filas_entrenamiento"],
			"filas_prueba": division["filas_prueba"],
			"proporcion_entrenamiento": division["proporcion_entrenamiento"],
		},
		"entrenamiento": entrenamiento,
		"prueba": prueba,
	}


def comparar_estadisticas_condiciones(
	estadisticas_entrenamiento,
	estadisticas_prueba,
	horizonte=5,
):
	if not isinstance(estadisticas_entrenamiento, dict) or not isinstance(estadisticas_prueba, dict):
		return {}
	comparacion = {}
	for condicion in set(estadisticas_entrenamiento) & set(estadisticas_prueba):
		estados_entrenamiento = estadisticas_entrenamiento[condicion]
		estados_prueba = estadisticas_prueba[condicion]
		if not isinstance(estados_entrenamiento, dict) or not isinstance(estados_prueba, dict):
			continue
		for estado in set(estados_entrenamiento) & set(estados_prueba):
			grupo_entrenamiento = estados_entrenamiento[estado]
			grupo_prueba = estados_prueba[estado]
			if not isinstance(grupo_entrenamiento, dict) or not isinstance(grupo_prueba, dict):
				continue
			estadistica_entrenamiento = grupo_entrenamiento.get("horizontes", {}).get(horizonte)
			estadistica_prueba = grupo_prueba.get("horizontes", {}).get(horizonte)
			if not isinstance(estadistica_entrenamiento, dict) or not isinstance(estadistica_prueba, dict):
				continue

			metricas = ["muestras", "retorno_medio", "retorno_mediano", "tasa_positiva"]
			comparacion.setdefault(condicion, {})[estado] = {
				"entrenamiento": {
					metrica: estadistica_entrenamiento.get(metrica)
					for metrica in metricas
				},
				"prueba": {
					metrica: estadistica_prueba.get(metrica)
					for metrica in metricas
				},
				"diferencias": {
					metrica: _diferencia(
						estadistica_prueba.get(metrica),
						estadistica_entrenamiento.get(metrica),
					)
					for metrica in ["retorno_medio", "retorno_mediano", "tasa_positiva"]
				},
			}
	return comparacion


def _diferencia(prueba, entrenamiento):
	if prueba is None or entrenamiento is None:
		return None
	return prueba - entrenamiento


def crear_resumen_validacion(resultado_validacion, horizonte=5):
	if not isinstance(resultado_validacion, dict):
		return None
	entrenamiento = resultado_validacion.get("entrenamiento") or {}
	prueba = resultado_validacion.get("prueba") or {}
	return {
		"horizonte": horizonte,
		"entrenamiento": {
			"evaluaciones": len(entrenamiento.get("evaluaciones", [])),
			"resultados": len(entrenamiento.get("resultados", [])),
		},
		"prueba": {
			"evaluaciones": len(prueba.get("evaluaciones", [])),
			"resultados": len(prueba.get("resultados", [])),
		},
		"comparacion_condiciones": comparar_estadisticas_condiciones(
			entrenamiento.get("estadisticas_condiciones", {}),
			prueba.get("estadisticas_condiciones", {}),
			horizonte=horizonte,
		),
	}