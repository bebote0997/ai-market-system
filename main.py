from operaciones import operaciones
from reporte import mostrar_resultado
from config import capital_inicial
from servicio import procesar_cartera


def main():
	resultados = procesar_cartera(operaciones, capital_inicial)

	for resultado in resultados:
		mostrar_resultado(
			resultado,
			resultado["ganancia_perdida"],
			resultado["rentabilidad"],
		)


if __name__ == "__main__":
	main()

