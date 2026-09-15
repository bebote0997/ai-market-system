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

# Lista de operaciones, donde cada operacion es un diccionario.
operaciones = [
	{
		"simbolo": "AAPL",
		"mercado": "acciones",
		"precio_entrada": 150,
		"precio_actual": 165,
		"cantidad": 10,
	},
	{
		"simbolo": "BTC",
		"mercado": "criptomonedas",
		"precio_entrada": 60000,
		"precio_actual": 63000,
		"cantidad": 0.05,
	},
	{
		"simbolo": "EURUSD",
		"mercado": "forex",
		"precio_entrada": 1.10,
		"precio_actual": 1.12,
		"cantidad": 1000,
	},
	{
		"simbolo": "TSLA",
		"mercado": "acciones",
		"precio_entrada": 250,
		"precio_actual": 225,
		"cantidad": 4,
	},
]

# Recorre cada operacion y analiza sus datos.
for operacion in operaciones:
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

