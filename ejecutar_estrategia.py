from evaluacion_estrategia import (
    comparar_metricas_estrategia,
    ejecutar_validacion_estrategia,
)
from estrategia import crear_configuracion_estrategia
from mercado import obtener_datos_historicos
from simulador import crear_configuracion_simulacion


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
        except Exception:
            resultado = None
        resultados[ticker_normalizado] = (
            resultado
            if resultado is not None
            else {"ticker": ticker_normalizado, "error": "no_disponible"}
        )
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
            print(f"Capital final: ${simulacion['capital_final']:.2f}")
            print(f"Retorno total: {_mostrar_valor(metricas['retorno_total_pct'])}%")
            print(f"Tasa de acierto: {_mostrar_valor(metricas['tasa_acierto'])}%")
            print(f"Profit factor: {_mostrar_valor(metricas['profit_factor'])}")
            print(f"Expectativa: ${_mostrar_valor(metricas['expectativa'])}")
            print(f"Max drawdown: {_mostrar_valor(metricas['max_drawdown_pct'])}%")
        print("\nDIFERENCIAS TEST - TRAIN")
        for metrica in ["retorno_total_pct", "tasa_acierto", "profit_factor", "expectativa", "max_drawdown_pct"]:
            print(f"{metrica}: {_mostrar_valor(activo['comparacion'][metrica].get('diferencia'))}")


if __name__ == "__main__":
    resultados = ejecutar_multiples_tickers(
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
    )
    _mostrar_resultados(resultados)
