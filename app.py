import streamlit as st

from operaciones import operaciones
from config import capital_inicial
from servicio import procesar_cartera


st.set_page_config(
	page_title="AI Market System",
	page_icon="📈",
	layout="wide",
)

st.title("AI Market System")
st.write("Análisis de cartera con datos de mercado")

resultados = procesar_cartera(operaciones, capital_inicial)

if not resultados:
	st.warning("No hay resultados válidos para mostrar.")
else:
	ganancia_perdida_total = sum(
		resultado["ganancia_perdida"] for resultado in resultados
	)

	metrica_capital, metrica_ganancia, metrica_activos = st.columns(3)
	metrica_capital.metric("Capital inicial", f"${capital_inicial:,.2f}")
	metrica_ganancia.metric(
		"Ganancia/Pérdida total",
		f"${ganancia_perdida_total:,.2f}",
	)
	metrica_activos.metric("Número de activos procesados", len(resultados))

	tabla_resultados = [
		{
			"Símbolo": resultado["simbolo"],
			"Mercado": resultado["mercado"],
			"Precio entrada": resultado["precio_entrada"],
			"Precio actual": resultado["precio_actual"],
			"Cantidad": resultado["cantidad"],
			"Ganancia/Pérdida": resultado["ganancia_perdida"],
			"Rentabilidad %": resultado["rentabilidad"],
		}
		for resultado in resultados
	]

	st.dataframe(tabla_resultados, use_container_width=True, hide_index=True)
