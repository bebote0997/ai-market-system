import pandas as pd


def ordenar_datos_temporales(datos):
	"""Normaliza fechas sin inventar fechas para índices numéricos ni duplicarlas."""
	indice = datos.index
	if isinstance(indice, pd.MultiIndex) or pd.api.types.is_numeric_dtype(indice.dtype):
		raise ValueError("El índice debe contener fechas interpretables, no posiciones numéricas.")
	try:
		fechas = pd.DatetimeIndex(pd.to_datetime(indice, errors="raise"))
	except (TypeError, ValueError, OverflowError) as error:
		raise ValueError("Índice temporal no interpretable o con zonas horarias incompatibles.") from error
	if fechas.hasnans:
		raise ValueError("El índice temporal contiene fechas ausentes.")
	if fechas.has_duplicates:
		raise ValueError("El índice temporal contiene fechas duplicadas.")
	ordenados = datos.copy()
	ordenados.index = fechas
	return ordenados.sort_index()


def dividir_datos_cronologicamente(datos, proporcion_entrenamiento=0.70):
	if datos is None or datos.empty:
		return None
	if isinstance(proporcion_entrenamiento, bool):
		return None
	if not isinstance(proporcion_entrenamiento, (int, float)):
		return None
	if pd.isna(proporcion_entrenamiento):
		return None
	if proporcion_entrenamiento <= 0 or proporcion_entrenamiento >= 1:
		return None

	datos_ordenados = ordenar_datos_temporales(datos)
	punto_division = int(len(datos_ordenados) * proporcion_entrenamiento)
	entrenamiento = datos_ordenados.iloc[:punto_division].copy()
	prueba = datos_ordenados.iloc[punto_division:].copy()

	if entrenamiento.empty or prueba.empty:
		return None

	return {
		"entrenamiento": entrenamiento,
		"prueba": prueba,
		"total_filas": len(datos_ordenados),
		"filas_entrenamiento": len(entrenamiento),
		"filas_prueba": len(prueba),
		"proporcion_entrenamiento": float(proporcion_entrenamiento),
	}


def preparar_segmento_prueba_con_contexto(
	entrenamiento,
	prueba,
	minimo_historial=50,
):
	if (
		entrenamiento is None
		or prueba is None
		or entrenamiento.empty
		or prueba.empty
		or not isinstance(minimo_historial, int)
		or isinstance(minimo_historial, bool)
		or minimo_historial <= 0
	):
		return None

	entrenamiento_ordenado = ordenar_datos_temporales(entrenamiento)
	prueba_ordenada = ordenar_datos_temporales(prueba)
	if not entrenamiento_ordenado.index.intersection(prueba_ordenada.index).empty:
		raise ValueError("Entrenamiento y prueba comparten fechas.")
	try:
		anterior = entrenamiento_ordenado.index[-1] < prueba_ordenada.index[0]
	except TypeError as error:
		raise ValueError("Entrenamiento y prueba tienen zonas horarias incompatibles.") from error
	if not anterior:
		raise ValueError("Todo el entrenamiento debe ser estrictamente anterior a prueba.")
	contexto = entrenamiento_ordenado
	datos_con_contexto = pd.concat([contexto, prueba_ordenada]).copy()

	return {
		"datos_con_contexto": datos_con_contexto,
		"indices_prueba": prueba_ordenada.index.copy(),
		"filas_contexto": len(contexto),
		"filas_prueba": len(prueba_ordenada),
	}
