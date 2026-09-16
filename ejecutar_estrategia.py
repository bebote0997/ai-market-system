from evaluacion_estrategia import (
    comparar_metricas_estrategia,
    ejecutar_validacion_estrategia,
    ejecutar_validacion_estrategia_con_riesgo,
)
from estrategia import crear_configuracion_estrategia
from mercado import obtener_datos_historicos
from simulador import crear_configuracion_simulacion
from riesgo import crear_configuracion_riesgo


def ejecutar_para_ticker(
    ticker,
    periodo="5y",
    proporcion_entrenamiento=0.70,
    minimo_historial=50,
    minimo_favorables=4,
    maximo_desfavorables=1,
    requeridas=None,
    capital_inicial=10000.0,
    porcentaje_capital_por_operacion=100.0,
    periodo_salida=5,
    coste_porcentual=0.10,
):
    datos = obtener_datos_historicos(ticker, periodo)
    if datos is None:
        return None
    configuracion_estrategia = crear_configuracion_estrategia(
        minimo_favorables, maximo_desfavorables, requeridas
    )
    configuracion_simulacion = crear_configuracion_simulacion(
        capital_inicial,
        porcentaje_capital_por_operacion,
        periodo_salida,
        coste_porcentual,
    )
    if configuracion_estrategia is None or configuracion_simulacion is None:
        return None
    resultado = ejecutar_validacion_estrategia(
        datos,
        configuracion_estrategia,
        configuracion_simulacion,
        proporcion_entrenamiento,
        minimo_historial,
    )
    if resultado is None:
        return None
    return {
        "ticker": ticker,
        "periodo": periodo,
        "resultado": resultado,
        "comparacion": comparar_metricas_estrategia(resultado),
    }


def ejecutar_multiples_tickers(tickers, **configuracion):
    if not tickers:
        return {}
    resultados = {}
    procesados = set()
    for ticker in tickers:
        if not isinstance(ticker, str):
            continue
        ticker_normalizado = ticker.strip().upper()
        if not ticker_normalizado or ticker_normalizado in procesados:
            continue
        procesados.add(ticker_normalizado)
        try:
            resultado = ejecutar_para_ticker(ticker_normalizado, **configuracion)
        except Exception as error:
            resultados[ticker_normalizado] = {
                "ticker": ticker_normalizado, "error": "no_disponible",
                "detalle": str(error),
            }
            continue
        resultados[ticker_normalizado] = (
            resultado
            if resultado is not None
            else {"ticker": ticker_normalizado, "error": "no_disponible"}
        )
    return resultados


def ejecutar_para_ticker_con_riesgo(
    ticker,
    periodo="5y",
    proporcion_entrenamiento=0.70,
    minimo_historial=50,
    minimo_favorables=4,
    maximo_desfavorables=1,
    requeridas=None,
    capital_inicial=10000.0,
    porcentaje_capital_por_operacion=100.0,
    periodo_salida=5,
    coste_porcentual=0.10,
    riesgo_por_operacion_pct=1.0,
    metodo_stop="atr",
    atr_multiplicador=2.0,
    ratio_objetivo=2.0,
    maximo_capital_pct=100.0,
    stop_porcentual_pct=2.0,
):
    datos = obtener_datos_historicos(ticker, periodo)
    if datos is None:
        return None
    estrategia = crear_configuracion_estrategia(minimo_favorables, maximo_desfavorables, requeridas)
    simulacion = crear_configuracion_simulacion(capital_inicial, porcentaje_capital_por_operacion, periodo_salida, coste_porcentual)
    riesgo = crear_configuracion_riesgo(riesgo_por_operacion_pct, metodo_stop, atr_multiplicador, ratio_objetivo, maximo_capital_pct, stop_porcentual_pct)
    if estrategia is None or simulacion is None or riesgo is None:
        return None
    resultado = ejecutar_validacion_estrategia_con_riesgo(datos, estrategia, simulacion, riesgo, proporcion_entrenamiento, minimo_historial)
    if resultado is None:
        return None
    return {"ticker": ticker, "periodo": periodo, "resultado": resultado, "comparacion": comparar_metricas_estrategia(resultado)}


def ejecutar_multiples_tickers_con_riesgo(tickers, **configuracion):
    if not tickers:
        return {}
    resultados = {}
    procesados = set()
    for ticker in tickers:
        if not isinstance(ticker, str):
            continue
        normalizado = ticker.strip().upper()
        if not normalizado or normalizado in procesados:
            continue
        procesados.add(normalizado)
        try:
            resultado = ejecutar_para_ticker_con_riesgo(normalizado, **configuracion)
        except Exception as error:
            resultado = None
        resultados[normalizado] = resultado if resultado is not None else {"ticker": normalizado, "error": "no_disponible", "detalle": str(error) if 'error' in locals() else None}
    return resultados


def _mostrar_valor(valor, formato="{:.2f}"):
    return "N/D" if valor is None else formato.format(valor)


def _mostrar_resultados(resultados):
    print("AI Market System")
    print("Backtest de estrategia - Validación fuera de muestra")
    print()
    print("Configuración experimental: mínimos favorables=4, máximos desfavorables=1")
    print()
    for ticker, activo in resultados.items():
        print("=" * 40)
        print(f"Ticker: {ticker}")
        if "error" in activo:
            print(f"Error: {activo['error']}")
            if activo.get("detalle"):
                print(f"Detalle: {activo['detalle']}")
            continue
        resultado = activo["resultado"]
        division = resultado["division"]
        print("Datos:")
        print(f"Total: {division['total_filas']}")
        print(f"Entrenamiento: {division['filas_entrenamiento']}")
        print(f"Prueba: {division['filas_prueba']}")
        for nombre in ["entrenamiento", "prueba"]:
            segmento = resultado[nombre]
            metricas = segmento["metricas"]
            simulacion = segmento["simulacion"]
            print(f"\n{nombre.upper()}")
            print(f"Evaluaciones: {len(segmento['evaluaciones'])}")
            print(f"Eventos: {len(segmento['eventos'])}")
            print(f"Operaciones: {len(simulacion['operaciones'])}")
            print(f"Capital inicial: ${simulacion['capital_inicial']:.2f}")
            print(f"Equity final: ${simulacion['capital_final']:.2f}")
            print(f"Capital realizado: ${simulacion['capital_realizado']:.2f}")
            abierta = simulacion["posicion_abierta"]
            print(f"Posiciones abiertas: {int(abierta is not None)}")
            if abierta is not None:
                print(f"Entrada pendiente de cierre: {abierta['fecha_entrada']}; "
                      f"cantidad={abierta['cantidad']:.6f}; "
                      f"precio entrada={abierta['precio_entrada']:.6f}; "
                      f"último precio={abierta['ultimo_precio']:.6f}")
            print(f"Retorno total: {_mostrar_valor(metricas['retorno_total_pct'])}%")
            print(f"Tasa de acierto: {_mostrar_valor(metricas['tasa_acierto'])}%")
            print(f"Profit factor: {_mostrar_valor(metricas['profit_factor'])}")
            print(f"Expectativa: ${_mostrar_valor(metricas['expectativa'])}")
            print(f"Max drawdown: {_mostrar_valor(metricas['max_drawdown_pct'])}%")
            print(f"R medio: {_mostrar_valor(metricas.get('r_medio'))}")
            print(f"Stops: {metricas.get('stop_count', 0)}")
            print(f"Targets: {metricas.get('target_count', 0)}")
            print(f"Salidas temporales: {metricas.get('tiempo_count', 0)}")
            print(f"Posiciones abiertas: {int(simulacion.get('posicion_abierta') is not None)}")
        print("\nDIFERENCIAS TEST - TRAIN")
        for metrica in ["retorno_total_pct", "tasa_acierto", "profit_factor", "expectativa", "max_drawdown_pct"]:
            print(f"{metrica}: {_mostrar_valor(activo['comparacion'][metrica].get('diferencia'))}")


if __name__ == "__main__":
    resultados = ejecutar_multiples_tickers_con_riesgo(
        ["AAPL", "MSFT", "TSLA", "BTC-USD", "EURUSD=X"],
        periodo="5y",
        proporcion_entrenamiento=0.70,
        minimo_historial=50,
        minimo_favorables=4,
        maximo_desfavorables=1,
        requeridas=None,
        capital_inicial=10000.0,
        porcentaje_capital_por_operacion=100.0,
        periodo_salida=5,
        coste_porcentual=0.10,
        riesgo_por_operacion_pct=1.0,
        metodo_stop="atr",
        atr_multiplicador=2.0,
        ratio_objetivo=2.0,
        maximo_capital_pct=100.0,
    )
    _mostrar_resultados(resultados)
