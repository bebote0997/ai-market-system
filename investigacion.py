from backtest import (
	calcular_estadisticas_por_condicion,
	calcular_estadisticas_por_favorables,
	calcular_resultados_futuros,
	generar_evaluaciones_historicas,
)
from mercado import obtener_datos_historicos


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


if __name__ == "__main__":
	horizontes = (1, 5, 10)
	investigacion = ejecutar_investigacion(
		"AAPL",
		periodo="2y",
		minimo_historial=50,
		horizontes=horizontes,
	)
	if investigacion is None:
		print("No se pudo obtener información histórica para AAPL.")
	else:
		_mostrar_investigacion(investigacion, horizontes)
