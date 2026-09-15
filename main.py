from operaciones import operaciones
from mercado import obtener_precio_actual


def analizar_operacion(capital_inicial, precio_entrada, precio_actual, cantidad_acciones):
	# Calcula el capital utilizado para comprar las acciones.
	capital_utilizado = precio_entrada * cantidad_acciones

	# Calcula el valor actual de la posicion.
	valor_actual = precio_actual * cantidad_acciones

	# Calcula la ganancia o perdida de la operacion.
	ganancia_perdida = valor_actual - capital_utilizado

	# Calcula la rentabilidad porcentual de la operacion.
	rentabilidad = (ganancia_perdida / capital_utilizado) * 100

	# Calcula el capital total despues de la operacion.
	capital_total = capital_inicial + ganancia_perdida

	return capital_utilizado, valor_actual, ganancia_perdida, rentabilidad, capital_total


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

