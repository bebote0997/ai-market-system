import streamlit as st

from operaciones import operaciones
from config import capital_inicial
from servicio import procesar_cartera
from resumen import calcular_resumen_cartera
from mercado import obtener_datos_historicos
from motor_analisis import analizar_mercado
from investigacion import ejecutar_investigacion_fuera_muestra
from reporte_validacion import crear_reporte_comparativo


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

			st.subheader("Validación histórica fuera de muestra")
			periodo_validacion = st.selectbox(
				"Período de validación",
				["1y", "2y", "5y"],
				index=1,
			)
			proporciones = {
				"60 / 40": 0.60,
				"70 / 30": 0.70,
				"80 / 20": 0.80,
			}
			proporcion_etiqueta = st.selectbox(
				"Proporción entrenamiento / prueba",
				list(proporciones),
				index=1,
			)
			horizonte_validacion = st.selectbox(
				"Horizonte de visualización",
				[1, 5, 10],
				index=1,
			)

			if "validacion_historica" not in st.session_state:
				st.session_state.validacion_historica = None

			if st.button("Ejecutar validación histórica"):
				with st.spinner("Ejecutando validación histórica..."):
					resultado_validacion = ejecutar_investigacion_fuera_muestra(
						resultado_seleccionado["ticker"],
						periodo=periodo_validacion,
						proporcion_entrenamiento=proporciones[proporcion_etiqueta],
						minimo_historial=50,
						horizontes=(1, 5, 10),
					)
				if resultado_validacion is None:
					st.session_state.validacion_historica = None
					st.warning("No se pudo ejecutar la validación histórica.")
				else:
					st.session_state.validacion_historica = {
						"ticker": resultado_seleccionado["ticker"],
						"periodo": periodo_validacion,
						"proporcion": proporciones[proporcion_etiqueta],
						"resultado": resultado_validacion,
					}

			guardado = st.session_state.validacion_historica
			if guardado is not None:
				reporte = crear_reporte_comparativo(
					{guardado["ticker"]: guardado["resultado"]},
					horizonte=horizonte_validacion,
				)
				activo = reporte.get(guardado["ticker"])
				if activo is not None and "error" in activo:
					st.warning("No hay datos disponibles para la validación histórica.")
				elif activo is not None:
					division = activo["datos"]
					muestras = activo["muestras"]
					metricas_validacion = st.columns(5)
					metricas_validacion[0].metric("Filas históricas", division["filas_historicas"])
					metricas_validacion[1].metric("Filas entrenamiento", division["filas_entrenamiento"])
					metricas_validacion[2].metric("Filas prueba", division["filas_prueba"])
					metricas_validacion[3].metric("Evaluaciones entrenamiento", muestras["evaluaciones_entrenamiento"])
					metricas_validacion[4].metric("Evaluaciones prueba", muestras["evaluaciones_prueba"])

					st.markdown("#### Comparación por condición")
					st.write(
						"Esta comparación es descriptiva. El segmento de prueba representa "
						"datos cronológicamente posteriores que no se utilizan para ajustar las condiciones."
					)
					for condicion, estados in activo["condiciones"].items():
						st.markdown(f"**{condicion}**")
						for estado, comparacion in estados.items():
							filas = []
							for metrica in ["muestras", "retorno_medio", "retorno_mediano", "tasa_positiva"]:
								entrenamiento = comparacion["entrenamiento"].get(metrica)
								prueba = comparacion["prueba"].get(metrica)
								diferencia = comparacion["diferencias"].get(metrica)
								if metrica != "muestras":
									entrenamiento = "N/D" if entrenamiento is None else f"{entrenamiento:.2f}%"
									prueba = "N/D" if prueba is None else f"{prueba:.2f}%"
									diferencia = "N/D" if diferencia is None else f"{diferencia:.2f}%"
								else:
									entrenamiento = "N/D" if entrenamiento is None else entrenamiento
									prueba = "N/D" if prueba is None else prueba
									diferencia = "N/D"
								filas.append({"Métrica": metrica, "Entrenamiento": entrenamiento, "Prueba": prueba, "Diferencia": diferencia})
							st.markdown(f"Estado: {estado}")
							st.dataframe(filas, width="stretch", hide_index=True)
