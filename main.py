from operaciones import operaciones
from reporte import mostrar_resultado
from config import capital_inicial
from servicio import procesar_operacion


def main():
	# Recorre cada operacion y analiza sus datos.
	for operacion in operaciones:
		resultado = procesar_operacion(operacion, capital_inicial)

		if resultado is None:
			print(f"Se omite {operacion.get('simbolo', '')}.")
			continue

		mostrar_resultado(
			resultado,
			resultado["ganancia_perdida"],
			resultado["rentabilidad"],
		)


if __name__ == "__main__":
	main()

