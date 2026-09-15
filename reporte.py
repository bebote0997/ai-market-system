def mostrar_resultado(operacion, ganancia_perdida, rentabilidad):
	print(f"Simbolo: {operacion['simbolo']}")
	print(f"Mercado: {operacion['mercado']}")
	print(f"Precio de entrada: {operacion['precio_entrada']}")
	print(f"Precio actual: {operacion['precio_actual']}")
	print(f"Ganancia o perdida: ${ganancia_perdida:.2f}")
	print(f"Rentabilidad: {rentabilidad:.2f}%")
	print()
