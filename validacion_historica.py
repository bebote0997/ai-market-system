import pandas as pd


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

	datos_ordenados = datos.sort_index().copy()
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

	entrenamiento_ordenado = entrenamiento.sort_index().copy()
	prueba_ordenada = prueba.sort_index().copy()
	filas_contexto = min(minimo_historial - 1, len(entrenamiento_ordenado))
	contexto = entrenamiento_ordenado.iloc[-filas_contexto:].copy() if filas_contexto else entrenamiento_ordenado.iloc[0:0].copy()
	datos_con_contexto = pd.concat([contexto, prueba_ordenada]).copy()

	return {
		"datos_con_contexto": datos_con_contexto,
		"indices_prueba": prueba_ordenada.index.copy(),
		"filas_contexto": len(contexto),
		"filas_prueba": len(prueba_ordenada),
	}
