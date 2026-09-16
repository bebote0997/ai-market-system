import streamlit as st

from operaciones import operaciones
from config import capital_inicial
from servicio import procesar_cartera
from resumen import calcular_resumen_cartera
from mercado import obtener_historial_precios
from tecnico import (
	calcular_variacion_periodo,
	calcular_media_movil,
	determinar_tendencia,
	calcular_rsi,
	calcular_volatilidad,
)


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

	st.dataframe(tabla_resultados, width="stretch", hide_index=True)

	st.subheader("Rendimiento por activo")
	grafico_resultados = [
		{
			"Símbolo": resultado["simbolo"],
			"Rentabilidad %": resultado["rentabilidad"],
		}
		for resultado in resultados
	]
	st.bar_chart(grafico_resultados, x="Símbolo", y="Rentabilidad %")

	st.subheader("Análisis de mercado")
	simbolos = [resultado["simbolo"] for resultado in resultados]
	simbolo_seleccionado = st.selectbox("Activo", simbolos)
	periodo = st.selectbox("Período", ["1mo", "3mo", "6mo", "1y"])
	resultado_seleccionado = next(
		resultado
		for resultado in resultados
		if resultado["simbolo"] == simbolo_seleccionado
	)
	historial = obtener_historial_precios(
		resultado_seleccionado["ticker"],
		periodo,
	)

	if historial is None:
		st.warning("No se pudo obtener el histórico del activo seleccionado.")
	else:
		variacion = calcular_variacion_periodo(historial)
		media_movil = calcular_media_movil(historial)
		tendencia = determinar_tendencia(historial)
		rsi = calcular_rsi(historial)
		volatilidad = calcular_volatilidad(historial)
		ultimo_precio = float(historial.iloc[-1])

		metrica_ultimo_precio, metrica_variacion, metrica_media_movil = st.columns(3)
		metrica_ultimo_precio.metric("Último precio", f"${ultimo_precio:,.2f}")
		metrica_variacion.metric(
			"Variación del período",
			f"{variacion:.2f}%" if variacion is not None else "N/D",
		)
		metrica_media_movil.metric(
			"Media móvil 20",
			f"${media_movil:,.2f}" if media_movil is not None else "N/D",
		)

		metrica_rsi, metrica_volatilidad, metrica_tendencia = st.columns(3)
		metrica_rsi.metric(
			"RSI 14",
			f"{rsi:.2f}" if rsi is not None else "N/D",
		)
		metrica_volatilidad.metric(
			"Volatilidad",
			f"{volatilidad:.2f}%" if volatilidad is not None else "N/D",
		)
		metrica_tendencia.metric(
			"Tendencia",
			tendencia if tendencia is not None else "N/D",
		)

		st.line_chart(historial)
