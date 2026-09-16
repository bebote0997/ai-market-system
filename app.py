import streamlit as st

from operaciones import operaciones
from config import capital_inicial
from servicio import procesar_cartera
from resumen import calcular_resumen_cartera
from mercado import obtener_datos_historicos
from motor_analisis import analizar_mercado


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
	datos = obtener_datos_historicos(
		resultado_seleccionado["ticker"],
		periodo,
	)

	if datos is None:
		st.warning("No se pudieron obtener los datos del activo seleccionado.")
	else:
		analisis = analizar_mercado(datos)

		if analisis is None:
			st.warning("No se pudo analizar el activo seleccionado.")
		else:
			tendencia = analisis["tendencia"]
			momentum = analisis["momentum"]
			volatilidad = analisis["volatilidad"]
			volumen = analisis["volumen"]
			estructura = analisis["estructura_precio"]
			macd = momentum["macd"]

			st.subheader("Resumen técnico")
			(
				metrica_ultimo_precio,
				metrica_variacion,
				metrica_tendencia,
				metrica_rsi,
				metrica_volatilidad,
				metrica_atr,
			) = st.columns(6)
			metrica_ultimo_precio.metric(
				"Último precio",
				f"${analisis['precio_actual']:,.2f}"
				if analisis["precio_actual"] is not None
				else "N/D",
			)
			metrica_variacion.metric(
				"Variación del período",
				f"{analisis['rendimiento']['variacion_periodo']:.2f}%"
				if analisis["rendimiento"]["variacion_periodo"] is not None
				else "N/D",
			)
			metrica_tendencia.metric(
				"Tendencia",
				tendencia["estado"] if tendencia["estado"] is not None else "N/D",
			)
			metrica_rsi.metric(
				"RSI 14",
				f"{momentum['rsi_14']:.2f}"
				if momentum["rsi_14"] is not None
				else "N/D",
			)
			metrica_volatilidad.metric(
				"Volatilidad",
				f"{volatilidad['porcentaje']:.2f}%"
				if volatilidad["porcentaje"] is not None
				else "N/D",
			)
			metrica_atr.metric(
				"ATR 14",
				f"${volatilidad['atr_14']:,.2f}"
				if volatilidad["atr_14"] is not None
				else "N/D",
			)

			st.subheader("Tendencia")
			metrica_sma, metrica_ema_20, metrica_ema_50 = st.columns(3)
			metrica_sma.metric(
				"SMA 20",
				f"${tendencia['sma_20']:,.2f}"
				if tendencia["sma_20"] is not None
				else "N/D",
			)
			metrica_ema_20.metric(
				"EMA 20",
				f"${tendencia['ema_20']:,.2f}"
				if tendencia["ema_20"] is not None
				else "N/D",
			)
			metrica_ema_50.metric(
				"EMA 50",
				f"${tendencia['ema_50']:,.2f}"
				if tendencia["ema_50"] is not None
				else "N/D",
			)

			st.subheader("Momentum")
			metrica_rsi_momentum, metrica_macd, metrica_senal, metrica_histograma = st.columns(4)
			metrica_rsi_momentum.metric(
				"RSI 14",
				f"{momentum['rsi_14']:.2f}"
				if momentum["rsi_14"] is not None
				else "N/D",
			)
			metrica_macd.metric(
				"MACD",
				f"{macd['macd']:.4f}" if macd is not None else "N/D",
			)
			metrica_senal.metric(
				"Señal MACD",
				f"{macd['senal']:.4f}" if macd is not None else "N/D",
			)
			metrica_histograma.metric(
				"Histograma MACD",
				f"{macd['histograma']:.4f}" if macd is not None else "N/D",
			)

			st.subheader("Volumen")
			metrica_volumen_actual, metrica_volumen_medio, metrica_ratio = st.columns(3)
			metrica_volumen_actual.metric(
				"Volumen actual",
				f"{volumen['volumen_actual']:,.2f}" if volumen is not None else "N/D",
			)
			metrica_volumen_medio.metric(
				"Volumen medio 20",
				f"{volumen['volumen_medio']:,.2f}" if volumen is not None else "N/D",
			)
			metrica_ratio.metric(
				"Ratio de volumen",
				f"{volumen['ratio_volumen']:.4f}"
				if volumen is not None and volumen["ratio_volumen"] is not None
				else "N/D",
			)

			st.subheader("Estructura de precio")
			metrica_maximo, metrica_precio, metrica_minimo, metrica_posicion = st.columns(4)
			metrica_maximo.metric(
				"Máximo reciente",
				f"${estructura['maximo_reciente']:,.2f}"
				if estructura is not None
				else "N/D",
			)
			metrica_precio.metric(
				"Precio actual",
				f"${estructura['precio_actual']:,.2f}"
				if estructura is not None
				else "N/D",
			)
			metrica_minimo.metric(
				"Mínimo reciente",
				f"${estructura['minimo_reciente']:,.2f}"
				if estructura is not None
				else "N/D",
			)
			metrica_posicion.metric(
				"Posición dentro del rango %",
				f"{estructura['posicion_rango']:.2f}%"
				if estructura is not None
				else "N/D",
			)

			st.line_chart(datos["Close"])
