from operaciones import operaciones
from mercado import obtener_precio_actual
from analisis import analizar_operacion
from reporte import mostrar_resultado


def main():
	capital_inicial = 10000

	# Recorre cada operacion y analiza sus datos.
	for operacion in operaciones:
		operacion["precio_actual"] = obtener_precio_actual(operacion["ticker"])

		if operacion["precio_actual"] is None:
			print(f"Se omite {operacion['simbolo']} por falta de precio.")
			continue

		_, _, ganancia_perdida, rentabilidad, _ = analizar_operacion(
			capital_inicial,
			operacion["precio_entrada"],
			operacion["precio_actual"],
			operacion["cantidad"],
		)

		mostrar_resultado(operacion, ganancia_perdida, rentabilidad)


if __name__ == "__main__":
	main()

