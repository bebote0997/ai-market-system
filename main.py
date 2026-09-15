from operaciones import operaciones
from mercado import obtener_precio_actual
from analisis import analizar_operacion


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

		print(f"Simbolo: {operacion['simbolo']}")
		print(f"Mercado: {operacion['mercado']}")
		print(f"Precio de entrada: {operacion['precio_entrada']}")
		print(f"Precio actual: {operacion['precio_actual']}")
		print(f"Ganancia o perdida: ${ganancia_perdida:.2f}")
		print(f"Rentabilidad: {rentabilidad:.2f}%")
		print()


if __name__ == "__main__":
	main()

