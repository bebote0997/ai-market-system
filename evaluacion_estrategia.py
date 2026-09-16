import copy

from backtest import generar_evaluaciones_historicas
from estrategia import generar_eventos_estrategia
from metricas_estrategia import calcular_metricas_estrategia
from simulador import simular_operaciones
from simulador import simular_operaciones_con_riesgo
from validacion_historica import (
    dividir_datos_cronologicamente,
    preparar_segmento_prueba_con_contexto,
)


def evaluar_estrategia_en_datos(
    datos,
    configuracion_estrategia,
    configuracion_simulacion,
    minimo_historial=50,
    indices_objetivo=None,
):
    if datos is None or configuracion_estrategia is None or configuracion_simulacion is None:
        return None

    evaluaciones = generar_evaluaciones_historicas(datos, minimo_historial=minimo_historial)
    if indices_objetivo is not None:
        indices_objetivo = set(indices_objetivo)
        evaluaciones = [
            evaluacion for evaluacion in evaluaciones
            if evaluacion.get("fecha") in indices_objetivo
        ]

    eventos = generar_eventos_estrategia(
        evaluaciones, configuracion_estrategia
    )
    simulacion = simular_operaciones(
        datos, eventos, configuracion_simulacion
    )
    metricas = calcular_metricas_estrategia(simulacion)
    return {
        "evaluaciones": evaluaciones,
        "eventos": eventos,
        "simulacion": simulacion,
        "metricas": metricas,
    }


def ejecutar_validacion_estrategia(
    datos,
    configuracion_estrategia,
    configuracion_simulacion,
    proporcion_entrenamiento=0.70,
    minimo_historial=50,
):
    if datos is None or configuracion_estrategia is None or configuracion_simulacion is None:
        return None

    division = dividir_datos_cronologicamente(datos, proporcion_entrenamiento)
    if division is None:
        return None

    entrenamiento = evaluar_estrategia_en_datos(
        division["entrenamiento"],
        configuracion_estrategia,
        configuracion_simulacion,
        minimo_historial=minimo_historial,
    )
    preparacion = preparar_segmento_prueba_con_contexto(
        division["entrenamiento"],
        division["prueba"],
        minimo_historial=minimo_historial,
    )
    if preparacion is None:
        return None

    prueba = evaluar_estrategia_en_datos(
        preparacion["datos_con_contexto"],
        configuracion_estrategia,
        configuracion_simulacion,
        minimo_historial=minimo_historial,
        indices_objetivo=preparacion["indices_prueba"],
    )
    return {
        "division": {
            clave: division[clave]
            for clave in [
                "total_filas",
                "filas_entrenamiento",
                "filas_prueba",
                "proporcion_entrenamiento",
            ]
        },
        "configuracion_estrategia": copy.deepcopy(configuracion_estrategia),
        "configuracion_simulacion": copy.deepcopy(configuracion_simulacion),
        "entrenamiento": entrenamiento,
        "prueba": prueba,
    }


def comparar_metricas_estrategia(resultado_validacion):
    if not isinstance(resultado_validacion, dict):
        return None
    entrenamiento = resultado_validacion.get("entrenamiento", {}).get("metricas")
    prueba = resultado_validacion.get("prueba", {}).get("metricas")
    if not isinstance(entrenamiento, dict) or not isinstance(prueba, dict):
        return None

    metricas = [
        "numero_operaciones",
        "tasa_acierto",
        "profit_factor",
        "resultado_neto_total",
        "retorno_total_pct",
        "retorno_medio_operacion_pct",
        "expectativa",
        "max_drawdown_pct",
    ]
    comparacion = {}
    for metrica in metricas:
        valor_entrenamiento = entrenamiento.get(metrica)
        valor_prueba = prueba.get(metrica)
        entrada = {
            "entrenamiento": valor_entrenamiento,
            "prueba": valor_prueba,
        }
        if metrica != "numero_operaciones":
            entrada["diferencia"] = (
                None
                if valor_entrenamiento is None or valor_prueba is None
                else valor_prueba - valor_entrenamiento
            )
        comparacion[metrica] = entrada
    return comparacion


def evaluar_estrategia_en_datos_con_riesgo(
    datos,
    configuracion_estrategia,
    configuracion_simulacion,
    configuracion_riesgo,
    minimo_historial=50,
    indices_objetivo=None,
):
    if datos is None or configuracion_estrategia is None or configuracion_simulacion is None or configuracion_riesgo is None:
        return None
    evaluaciones = generar_evaluaciones_historicas(datos, minimo_historial=minimo_historial)
    if indices_objetivo is not None:
        indices_objetivo = set(indices_objetivo)
        evaluaciones = [evaluacion for evaluacion in evaluaciones if evaluacion.get("fecha") in indices_objetivo]
    eventos = generar_eventos_estrategia(evaluaciones, configuracion_estrategia)
    simulacion = simular_operaciones_con_riesgo(
        datos, eventos, configuracion_simulacion, configuracion_riesgo
    )
    return {
        "evaluaciones": evaluaciones,
        "eventos": eventos,
        "simulacion": simulacion,
        "metricas": calcular_metricas_estrategia(simulacion),
    }


def ejecutar_validacion_estrategia_con_riesgo(
    datos,
    configuracion_estrategia,
    configuracion_simulacion,
    configuracion_riesgo,
    proporcion_entrenamiento=0.70,
    minimo_historial=50,
):
    division = dividir_datos_cronologicamente(datos, proporcion_entrenamiento)
    if division is None:
        return None
    entrenamiento = evaluar_estrategia_en_datos_con_riesgo(
        division["entrenamiento"], configuracion_estrategia,
        configuracion_simulacion, configuracion_riesgo, minimo_historial
    )
    preparacion = preparar_segmento_prueba_con_contexto(
        division["entrenamiento"], division["prueba"], minimo_historial
    )
    if preparacion is None:
        return None
    prueba = evaluar_estrategia_en_datos_con_riesgo(
        preparacion["datos_con_contexto"], configuracion_estrategia,
        configuracion_simulacion, configuracion_riesgo, minimo_historial,
        preparacion["indices_prueba"]
    )
    return {
        "division": {clave: division[clave] for clave in ("total_filas", "filas_entrenamiento", "filas_prueba", "proporcion_entrenamiento")},
        "configuracion_estrategia": copy.deepcopy(configuracion_estrategia),
        "configuracion_simulacion": copy.deepcopy(configuracion_simulacion),
        "configuracion_riesgo": copy.deepcopy(configuracion_riesgo),
        "entrenamiento": entrenamiento,
        "prueba": prueba,
    }
