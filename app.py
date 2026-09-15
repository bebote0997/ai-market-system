import streamlit as st

from operaciones import operaciones
from config import capital_inicial
from servicio import procesar_cartera
from resumen import calcular_resumen_cartera


st.set_page_config(
	page_title="AI Market System",
	page_icon="📈",
	layout="wide",
)

st.title("AI Market System")
st.write("Análisis de cartera con datos de mercado")

resultados = procesar_cartera(operaciones, capital_inicial)
resumen = calcular_resumen_cartera(resultados, capital_inicial)

if not resultados:
	st.warning("No hay resultados válidos para mostrar.")
else:
	(
		metrica_capital_inicial,
		metrica_capital_utilizado,
		metrica_valor_actual,
		metrica_ganancia,
		metrica_rentabilidad,
	) = st.columns(5)
	metrica_capital_inicial.metric(
		"Capital inicial",
		f"${resumen['capital_inicial']:,.2f}",
	)
	metrica_capital_utilizado.metric(
		"Capital utilizado",
		f"${resumen['capital_utilizado_total']:,.2f}",
	)
	metrica_valor_actual.metric(
		"Valor actual",
		f"${resumen['valor_actual_total']:,.2f}",
	)
	metrica_ganancia.metric(
		"Ganancia/Pérdida total",
		f"${resumen['ganancia_perdida_total']:,.2f}",
		delta=f"${resumen['ganancia_perdida_total']:,.2f}",
	)
	metrica_rentabilidad.metric(
		"Rentabilidad cartera",
		f"{resumen['rentabilidad_cartera']:.2f}%",
	)

	tabla_resultados = [
		{
			"Símbolo": resultado["simbolo"],
			"Mercado": resultado["mercado"],
			"Precio entrada": f"${resultado['precio_entrada']:,.2f}",
			"Precio actual": f"${resultado['precio_actual']:,.2f}",
			"Cantidad": f"{resultado['cantidad']:.4f}".rstrip("0").rstrip("."),
			"Ganancia/Pérdida": f"${resultado['ganancia_perdida']:,.2f}",
			"Rentabilidad %": f"{resultado['rentabilidad']:.2f}%",
		}
		for resultado in resultados
	]

	st.dataframe(tabla_resultados, use_container_width=True, hide_index=True)

	st.subheader("Rendimiento por activo")
	grafico_resultados = [
		{
			"Símbolo": resultado["simbolo"],
			"Rentabilidad %": resultado["rentabilidad"],
		}
		for resultado in resultados
	]
	st.bar_chart(grafico_resultados, x="Símbolo", y="Rentabilidad %")
