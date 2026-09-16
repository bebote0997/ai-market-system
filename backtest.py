import pandas as pd

from motor_analisis import analizar_mercado
from validador_entrada import evaluar_entrada


def generar_evaluaciones_historicas(datos, minimo_historial=50):
	columnas_requeridas = ["Open", "High", "Low", "Close", "Volume"]
	if datos is None or datos.empty or minimo_historial <= 0:
		return []
	if not all(columna in datos.columns for columna in columnas_requeridas):
		return []
	if len(datos) < minimo_historial:
		return []

	datos_ordenados = datos.sort_index().copy()
	evaluaciones = []

	for posicion in range(minimo_historial - 1, len(datos_ordenados)):
		datos_hasta_fecha = datos_ordenados.iloc[:posicion + 1].copy()
		close_validos = pd.to_numeric(
			datos_hasta_fecha["Close"], errors="coerce"
		).dropna()
		if close_validos.empty:
			continue

		analisis = analizar_mercado(datos_hasta_fecha)
		if analisis is None:
			continue

		evaluacion = evaluar_entrada(analisis)
		if evaluacion is None:
			continue

		evaluaciones.append(
			{
				"fecha": datos_hasta_fecha.index[-1],
				"precio_cierre": float(close_validos.iloc[-1]),
				"analisis": analisis,
				"evaluacion": evaluacion,
			}
		)

	return evaluaciones
