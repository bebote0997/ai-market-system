def calcular_resumen_cartera(resultados, capital_inicial):
	capital_utilizado_total = sum(
		resultado["capital_utilizado"] for resultado in resultados
	)
	valor_actual_total = sum(resultado["valor_actual"] for resultado in resultados)
	ganancia_perdida_total = sum(
		resultado["ganancia_perdida"] for resultado in resultados
	)

	if capital_utilizado_total == 0:
		rentabilidad_cartera = 0
	else:
		rentabilidad_cartera = (
			ganancia_perdida_total / capital_utilizado_total
		) * 100

	return {
		"capital_inicial": capital_inicial,
		"capital_utilizado_total": capital_utilizado_total,
		"valor_actual_total": valor_actual_total,
		"ganancia_perdida_total": ganancia_perdida_total,
		"rentabilidad_cartera": rentabilidad_cartera,
		"numero_activos": len(resultados),
	}
