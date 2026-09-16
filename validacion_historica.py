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
