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
