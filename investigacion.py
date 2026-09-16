from backtest import (
	calcular_estadisticas_por_condicion,
	calcular_estadisticas_por_favorables,
	calcular_resultados_futuros,
	generar_evaluaciones_historicas,
)
from mercado import obtener_datos_historicos
from validacion_fuera_muestra import (
	crear_resumen_validacion,
	ejecutar_validacion_fuera_muestra,
)
from reporte_validacion import crear_reporte_comparativo


def ejecutar_investigacion(
	ticker,
	periodo="2y",
	minimo_historial=50,
	horizontes=(1, 5, 10),
):
	datos = obtener_datos_historicos(ticker, periodo)
	if datos is None:
		return None

	evaluaciones = generar_evaluaciones_historicas(
		datos,
		minimo_historial=minimo_historial,
	)
	resultados = calcular_resultados_futuros(
		datos,
		evaluaciones,
		horizontes=horizontes,
	)
	estadisticas = calcular_estadisticas_por_favorables(
		resultados,
		horizontes=horizontes,
	)
	estadisticas_condiciones = calcular_estadisticas_por_condicion(
		resultados,
		horizontes=horizontes,
	)

	return {
		"ticker": ticker,
		"periodo": periodo,
		"filas_historicas": len(datos),
		"evaluaciones": len(evaluaciones),
		"resultados": resultados,
		"estadisticas": estadisticas,
		"estadisticas_condiciones": estadisticas_condiciones,
	}


def ejecutar_investigacion_multiple(
	tickers,
	periodo="2y",
	minimo_historial=50,
	horizontes=(1, 5, 10),
):
	if not tickers:
		return {}

	resultados = {}
	procesados = set()
	for ticker in tickers:
		if not isinstance(ticker, str):
			continue
		ticker_normalizado = ticker.strip().upper()
		if not ticker_normalizado or ticker_normalizado in procesados:
			continue

		procesados.add(ticker_normalizado)
		try:
			resultado = ejecutar_investigacion(
				ticker_normalizado,
				periodo=periodo,
				minimo_historial=minimo_historial,
				horizontes=horizontes,
			)
		except Exception:
			resultado = None

		if resultado is None:
			resultados[ticker_normalizado] = {
				"ticker": ticker_normalizado,
				"error": "datos_no_disponibles",
			}
		else:
			resultados[ticker_normalizado] = resultado

	return resultados


def ejecutar_investigacion_fuera_muestra(
	ticker,
	periodo="2y",
	proporcion_entrenamiento=0.70,
	minimo_historial=50,
	horizontes=(1, 5, 10),
):
	datos = obtener_datos_historicos(ticker, periodo)
	if datos is None:
		return None

	resultado_validacion = ejecutar_validacion_fuera_muestra(
		datos,
		proporcion_entrenamiento=proporcion_entrenamiento,
		minimo_historial=minimo_historial,
		horizontes=horizontes,
	)
	if resultado_validacion is None:
		return None

	return {
		"ticker": ticker,
		"periodo": periodo,
		"filas_historicas": len(datos),
		"validacion": resultado_validacion,
		"resumen": crear_resumen_validacion(resultado_validacion, horizonte=5),
	}


def ejecutar_investigacion_multiple_fuera_muestra(
	tickers,
	periodo="2y",
	proporcion_entrenamiento=0.70,
	minimo_historial=50,
	horizontes=(1, 5, 10),
):
	if not tickers:
		return {}

	resultados = {}
	procesados = set()
	for ticker in tickers:
		if not isinstance(ticker, str):
			continue
		ticker_normalizado = ticker.strip().upper()
		if not ticker_normalizado or ticker_normalizado in procesados:
			continue
		procesados.add(ticker_normalizado)

		try:
			resultado = ejecutar_investigacion_fuera_muestra(
				ticker_normalizado,
				periodo=periodo,
				proporcion_entrenamiento=proporcion_entrenamiento,
				minimo_historial=minimo_historial,
				horizontes=horizontes,
			)
		except Exception:
			resultado = None

		resultados[ticker_normalizado] = (
			resultado
			if resultado is not None
			else {
				"ticker": ticker_normalizado,
				"error": "datos_no_disponibles",
			}
		)

	return resultados


def _formatear_porcentaje(valor):
	return "N/D" if valor is None else f"{valor:.2f}%"


def _mostrar_investigacion(investigacion, horizontes):
	print("AI Market System - Investigación histórica")
	print(f"Ticker: {investigacion['ticker']}")
	print(f"Período: {investigacion['periodo']}")
	print(f"Filas históricas: {investigacion['filas_historicas']}")
	print(f"Evaluaciones: {investigacion['evaluaciones']}")
	print()

	for favorables, grupo in investigacion["estadisticas"].items():
		print(f"Condiciones favorables: {favorables}")
		print(f"Total evaluaciones: {grupo['total_evaluaciones']}")

		for horizonte in horizontes:
			estadistica = grupo["horizontes"].get(horizonte)
			if estadistica is None:
				continue

			print(f"\nHorizonte {horizonte}:")
			print(f"Muestras: {estadistica['muestras']}")
			print(
				f"Retorno medio: "
				f"{_formatear_porcentaje(estadistica['retorno_medio'])}"
			)
			print(
				f"Retorno mediano: "
				f"{_formatear_porcentaje(estadistica['retorno_mediano'])}"
			)
			print(
				f"Tasa positiva: "
				f"{_formatear_porcentaje(estadistica['tasa_positiva'])}"
			)
		print()

	print("ESTADÍSTICAS POR CONDICIÓN")
	nombres_condiciones = {
		"tendencia": "Tendencia",
		"rsi": "RSI",
		"macd": "MACD",
		"volumen": "Volumen",
		"estructura_precio": "Estructura de precio",
	}
	for condicion, estados in investigacion["estadisticas_condiciones"].items():
		for estado, grupo in estados.items():
			print(f"Condición: {nombres_condiciones.get(condicion, condicion)}")
			print(f"Estado: {estado}")
			print(f"Total evaluaciones: {grupo['total_evaluaciones']}")

			for horizonte in horizontes:
				estadistica = grupo["horizontes"].get(horizonte)
				if estadistica is None:
					continue

				print(f"\nHorizonte {horizonte}:")
				print(f"Muestras: {estadistica['muestras']}")
				print(
					f"Retorno medio: "
					f"{_formatear_porcentaje(estadistica['retorno_medio'])}"
				)
				print(
					f"Retorno mediano: "
					f"{_formatear_porcentaje(estadistica['retorno_mediano'])}"
				)
				print(
					f"Tasa positiva: "
					f"{_formatear_porcentaje(estadistica['tasa_positiva'])}"
				)
			print()


def _mostrar_investigaciones_multiples(investigaciones):
	nombres_condiciones = {
		"tendencia": "Tendencia",
		"rsi": "RSI",
		"macd": "MACD",
		"volumen": "Volumen",
		"estructura_precio": "Estructura de precio",
	}

	for ticker, investigacion in investigaciones.items():
		print("=" * 40)
		print(f"Ticker: {ticker}")
		if "error" in investigacion:
			print(f"Error: {investigacion['error']}")
			print()
			continue

		print(f"Filas históricas: {investigacion['filas_historicas']}")
		print(f"Evaluaciones: {investigacion['evaluaciones']}")
		print("\nRESUMEN POR CONDICIÓN")

		for condicion, estados in investigacion["estadisticas_condiciones"].items():
			print(f"\n{nombres_condiciones.get(condicion, condicion)}")
			for estado, grupo in estados.items():
				estadistica = grupo["horizontes"].get(5)
				if estadistica is None:
					continue

				print(f"Estado: {estado}")
				print(f"Muestras: {estadistica['muestras']}")
				print(
					f"Retorno medio 5: "
					f"{_formatear_porcentaje(estadistica['retorno_medio'])}"
				)
				print(
					f"Retorno mediano 5: "
					f"{_formatear_porcentaje(estadistica['retorno_mediano'])}"
				)
				print(
					f"Tasa positiva 5: "
					f"{_formatear_porcentaje(estadistica['tasa_positiva'])}"
				)


def _mostrar_reporte_fuera_muestra(reporte, horizonte=5):
	for ticker, activo in reporte.items():
		print("=" * 40)
		print(f"Ticker: {ticker}")
		if "error" in activo:
			print(f"Error: {activo['error']}")
			continue

		print(f"Filas históricas: {activo['datos']['filas_historicas']}")
		print(f"Entrenamiento: {activo['datos']['filas_entrenamiento']} filas")
		print(f"Prueba: {activo['datos']['filas_prueba']} filas")
		print(
			"Evaluaciones entrenamiento: "
			f"{activo['muestras']['evaluaciones_entrenamiento']}"
		)
		print(f"Evaluaciones prueba: {activo['muestras']['evaluaciones_prueba']}")
		print(f"\nVALIDACIÓN FUERA DE MUESTRA — HORIZONTE {horizonte}")

		for condicion, estados in activo["condiciones"].items():
			for estado, comparacion in estados.items():
				print(f"\nCondición: {condicion}")
				print(f"Estado: {estado}")
				for segmento in ["entrenamiento", "prueba"]:
					metricas = comparacion[segmento]
					print(f"\n{segmento.capitalize()}:")
					print(f"Muestras: {metricas.get('muestras')}")
					print(
						f"Retorno medio: "
						f"{_formatear_porcentaje(metricas.get('retorno_medio'))}"
					)
					print(
						f"Retorno mediano: "
						f"{_formatear_porcentaje(metricas.get('retorno_mediano'))}"
					)
					print(
						f"Tasa positiva: "
						f"{_formatear_porcentaje(metricas.get('tasa_positiva'))}"
					)
				diferencias = comparacion["diferencias"]
				print("\nDiferencia prueba - entrenamiento:")
				print(
					f"Retorno medio: "
					f"{_formatear_porcentaje(diferencias.get('retorno_medio'))}"
				)
				print(
					f"Retorno mediano: "
					f"{_formatear_porcentaje(diferencias.get('retorno_mediano'))}"
				)
				print(
					f"Tasa positiva: "
					f"{_formatear_porcentaje(diferencias.get('tasa_positiva'))}"
				)


if __name__ == "__main__":
	tickers = ["AAPL", "MSFT", "TSLA", "BTC-USD", "EURUSD=X"]
	horizontes = (1, 5, 10)
	investigaciones = ejecutar_investigacion_multiple_fuera_muestra(
		tickers,
		periodo="2y",
		proporcion_entrenamiento=0.70,
		minimo_historial=50,
		horizontes=horizontes,
	)
	_reporte = crear_reporte_comparativo(investigaciones, horizonte=5)
	_mostrar_reporte_fuera_muestra(_reporte, horizonte=5)
