import copy
import unittest
from unittest.mock import patch

import pandas as pd

from backtest import generar_evaluaciones_historicas, calcular_resultados_futuros
from estrategia import crear_configuracion_estrategia, cumple_estrategia
from evaluacion_estrategia import ejecutar_validacion_estrategia
from metricas_estrategia import calcular_metricas_estrategia
from motor_analisis import analizar_mercado
from mercado import obtener_datos_historicos
from reporte_validacion import crear_reporte_comparativo
from simulador import crear_configuracion_simulacion, simular_operaciones
from validacion import validar_operacion
from validacion_fuera_muestra import ejecutar_validacion_fuera_muestra
from validacion_historica import (
    dividir_datos_cronologicamente, preparar_segmento_prueba_con_contexto,
)


def datos(cierres, aperturas=None):
    return pd.DataFrame({
        "Open": aperturas if aperturas is not None else cierres,
        "High": [x + 5 for x in cierres],
        "Low": [x - 5 for x in cierres],
        "Close": cierres,
        "Volume": [1000.] * len(cierres),
    }, index=pd.date_range("2025-01-01", periods=len(cierres)))


class TestSemanticaV1(unittest.TestCase):
    def simular(self, d, posiciones=(0,), plazo=2, porcentaje=100, coste=0):
        return simular_operaciones(d, [{"fecha": d.index[i]} for i in posiciones],
                                  crear_configuracion_simulacion(10000, porcentaje, plazo, coste))

    def test_ejemplo_senal_10_entrada_11_salida_16(self):
        d = datos([100.] * 17, [200.] * 17)
        r = self.simular(d, (10,), 5)
        op = r["operaciones"][0]
        self.assertEqual(op["fecha_senal"], d.index[10])
        self.assertEqual(op["fecha_entrada"], d.index[11])
        self.assertEqual(op["fecha_salida"], d.index[16])
        self.assertEqual(op["precio_entrada"], 200.)
        self.assertEqual(op["precio_salida"], 100.)
        self.assertEqual(op["resultado_neto"], -5000.)

    def test_open_invalido_no_abre_ni_reintenta(self):
        for valor in [None, float("nan"), float("inf"), -float("inf"), 0, -1, True, "mal"]:
            with self.subTest(valor=valor):
                d = datos([100.] * 4)
                d["Open"] = d["Open"].astype(object)
                d.loc[d.index[1], "Open"] = valor
                r = self.simular(d)
                self.assertEqual(r["operaciones"], [])
                self.assertIsNone(r["posicion_abierta"])
                self.assertEqual(r["capital_final"], 10000.)

    def test_senal_ultima_barra_no_abre(self):
        r = self.simular(datos([100.] * 4), (3,))
        self.assertEqual(r["operaciones"], [])
        self.assertIsNone(r["posicion_abierta"])

    @patch("mercado.yf.Ticker")
    def test_descarga_no_elimina_open_invalido_ni_desplaza_entrada(self, ticker):
        d = datos([100.] * 5)
        d.loc[d.index[1], "Open"] = float("nan")
        ticker.return_value.history.return_value = d
        descargados = obtener_datos_historicos("X")
        self.assertEqual(list(descargados.index), list(d.index))
        r = self.simular(descargados)
        self.assertEqual(r["operaciones"], [])
        self.assertIsNone(r["posicion_abierta"])

    def test_open_booleano_en_columna_booleana_no_abre(self):
        d = datos([100.] * 4); d["Open"] = True
        self.assertIsNone(self.simular(d)["posicion_abierta"])
        self.assertEqual(self.simular(d)["operaciones"], [])

    def test_falta_columna_open_error_explicito(self):
        with self.assertRaisesRegex(ValueError, "Open"):
            self.simular(datos([100.] * 4).drop(columns="Open"))

    def test_final_de_segmento_conserva_posicion_abierta(self):
        d = datos([100., 100., 120.])
        r = self.simular(d, plazo=5)
        self.assertEqual(r["operaciones"], [])
        self.assertEqual(r["posicion_abierta"]["fecha_entrada"], d.index[1])
        self.assertEqual(r["capital_final"], 12000.)
        self.assertEqual(r["capital_realizado"], 10000.)

    def test_prefijo_equity_no_depende_de_barras_futuras(self):
        d = datos([100., 100., 80., 110., 200.])
        corto = self.simular(d.iloc[:3], plazo=2)
        largo = self.simular(d, plazo=2)
        self.assertEqual(corto["curva_capital"], largo["curva_capital"][:4])
        self.assertEqual(corto["posicion_abierta"]["fecha_entrada"], largo["operaciones"][0]["fecha_entrada"])

    def test_close_invalido_durante_posicion_falla_no_borra_entrada(self):
        for posicion in [1, 2, 3]:
            for valor in [float("nan"), float("inf"), -float("inf"), 0, -1]:
                with self.subTest(posicion=posicion, valor=valor):
                    d = datos([100.] * 4)
                    d.loc[d.index[posicion], "Close"] = valor
                    with self.assertRaisesRegex(ValueError, "Close inválido"):
                        self.simular(d)

    def test_senal_en_salida_reentra_siguiente_open_sin_solapar(self):
        d = datos([100.] * 7)
        r = self.simular(d, (0, 1, 2, 3))
        self.assertEqual([o["fecha_entrada"] for o in r["operaciones"]], [d.index[1], d.index[4]])
        self.assertEqual([o["fecha_salida"] for o in r["operaciones"]], [d.index[3], d.index[6]])

    def test_drawdown_50_por_ciento_aunque_recupere(self):
        r = self.simular(datos([100., 100., 50., 100.]))
        self.assertEqual([p["capital"] for p in r["curva_capital"]], [10000., 10000., 10000., 5000., 10000.])
        self.assertEqual(calcular_metricas_estrategia(r)["max_drawdown_pct"], -50.)

    def test_drawdown_capital_parcial_conserva_efectivo(self):
        r = self.simular(datos([100., 100., 50., 100.]), porcentaje=40)
        self.assertEqual(r["curva_capital"][3]["capital"], 8000.)
        self.assertEqual(calcular_metricas_estrategia(r)["max_drawdown_pct"], -20.)

    def test_coste_unico_al_cierre_y_capital_compuesto(self):
        r = self.simular(datos([100.] * 7), (0, 3), porcentaje=50, coste=1)
        self.assertEqual(r["curva_capital"][2]["capital"], 10000.)
        self.assertEqual(r["operaciones"][0]["coste"], 50.)
        self.assertEqual(r["operaciones"][1]["capital_utilizado"], 4975.)
        self.assertEqual(r["capital_final"], 9900.25)
        self.assertEqual(sum(o["coste"] for o in r["operaciones"]), 99.75)

    def test_senal_no_cambia_con_open_de_t_mas_uno(self):
        d = datos([100. + i % 5 for i in range(60)])
        antes = generar_evaluaciones_historicas(d)[0]
        d.loc[d.index[50], "Open"] = 100000.
        despues = generar_evaluaciones_historicas(d)[0]
        self.assertEqual(antes, despues)


class TestSeparacionTemporal(unittest.TestCase):
    def test_duplicados_en_corte_rechazados_por_ambas_validaciones(self):
        d = datos([100.] * 4)
        d.index = pd.to_datetime(["2025-01-01", "2025-01-02", "2025-01-02", "2025-01-03"])
        for funcion in [lambda: dividir_datos_cronologicamente(d, .5),
                        lambda: ejecutar_validacion_fuera_muestra(d, .5),
                        lambda: ejecutar_validacion_estrategia(d, crear_configuracion_estrategia(), crear_configuracion_simulacion(), .5)]:
            with self.assertRaisesRegex(ValueError, "duplicadas"):
                funcion()

    def test_indices_ininterpretables_y_numericos_rechazados(self):
        for indice in [["ayer", "hoy"], [0, 1], [True, False], [pd.NaT, pd.Timestamp("2025-01-01")]]:
            with self.subTest(indice=indice):
                d = datos([100., 100.]); d.index = indice
                with self.assertRaises(ValueError):
                    dividir_datos_cronologicamente(d, .5)

    def test_fechas_texto_se_normalizan_y_ordenan(self):
        d = datos([120., 100., 110.]); d.index = ["2025-01-03", "2025-01-01", "2025-01-02"]
        r = dividir_datos_cronologicamente(d, .7)
        self.assertEqual(list(r["entrenamiento"]["Close"]), [100., 110.])
        self.assertIsInstance(r["prueba"].index, pd.DatetimeIndex)

    def test_contexto_rechaza_fechas_compartidas(self):
        d = datos([100.] * 4)
        with self.assertRaisesRegex(ValueError, "comparten"):
            preparar_segmento_prueba_con_contexto(d.iloc[:3], d.iloc[2:])

    def test_contexto_rechaza_entrenamiento_posterior(self):
        d = datos([100.] * 4)
        with self.assertRaisesRegex(ValueError, "estrictamente anterior"):
            preparar_segmento_prueba_con_contexto(d.iloc[2:], d.iloc[:2])

    def test_contexto_rechaza_duplicados_dentro_de_segmentos(self):
        d = datos([100.] * 4)
        train = d.iloc[:2].copy(); train.index = [d.index[0]] * 2
        with self.assertRaisesRegex(ValueError, "duplicadas"):
            preparar_segmento_prueba_con_contexto(train, d.iloc[2:])

    def test_zonas_horarias_incompatibles_rechazadas(self):
        d = datos([100.] * 4)
        train = d.iloc[:2].tz_localize("UTC")
        with self.assertRaisesRegex(ValueError, "zonas horarias"):
            preparar_segmento_prueba_con_contexto(train, d.iloc[2:])

    def test_contexto_completo_incluso_con_minimo_uno(self):
        d = datos([100.] * 6)
        r = preparar_segmento_prueba_con_contexto(d.iloc[:4], d.iloc[4:], 1)
        self.assertEqual(r["filas_contexto"], 4)
        pd.testing.assert_frame_equal(r["datos_con_contexto"], d)

    def test_indicadores_prueba_iguales_a_historial_completo(self):
        d = datos([100. + .1 * i + 3 * (i % 7) for i in range(100)])
        r = ejecutar_validacion_fuera_muestra(d, .7)
        self.assertEqual(len(r["prueba"]["evaluaciones"]), 30)
        for i, evaluacion in enumerate(r["prueba"]["evaluaciones"][:5], 70):
            self.assertEqual(evaluacion["fecha"], d.index[i])
            self.assertEqual(evaluacion["analisis"], analizar_mercado(d.iloc[:i + 1]))

    def test_prueba_no_altera_entrenamiento_ni_cruza_retornos(self):
        d = datos([100. + i * .1 + (i % 4) for i in range(80)])
        cambiado = d.copy(); cambiado.iloc[56:, :4] *= 10
        r1 = ejecutar_validacion_fuera_muestra(d, .7, horizontes=(1, 5, 10))
        r2 = ejecutar_validacion_fuera_muestra(cambiado, .7, horizontes=(1, 5, 10))
        self.assertEqual(r1["entrenamiento"], r2["entrenamiento"])
        self.assertEqual(r1["entrenamiento"]["resultados"][-1]["retornos_futuros"], {1: None, 5: None, 10: None})
        c = crear_configuracion_estrategia(0, 5)
        s = crear_configuracion_simulacion(periodo_salida=2)
        a = ejecutar_validacion_estrategia(d, c, s, .7)
        b = ejecutar_validacion_estrategia(cambiado, c, s, .7)
        self.assertEqual(a["entrenamiento"], b["entrenamiento"])
        for evaluacion in a["prueba"]["evaluaciones"]:
            self.assertGreaterEqual(evaluacion["fecha"], d.index[56])
        for op in a["prueba"]["simulacion"]["operaciones"]:
            self.assertGreaterEqual(op["fecha_senal"], d.index[56])
            self.assertGreater(op["fecha_entrada"], d.index[56])
        for op in a["entrenamiento"]["simulacion"]["operaciones"]:
            self.assertLess(op["fecha_salida"], d.index[56])


class TestNumerosYHorizontes(unittest.TestCase):
    def test_operaciones_rechazan_no_finitos_y_bool(self):
        for clave in ["precio_entrada", "cantidad"]:
            for valor in [float("nan"), float("inf"), -float("inf"), True, False]:
                op = {"simbolo": "X", "ticker": "X", "mercado": "acciones", "precio_entrada": 100, "cantidad": 1}
                op[clave] = valor
                self.assertFalse(validar_operacion(op))

    def test_configuracion_simulacion_rechaza_no_finitos_incluso_dict_directo(self):
        for clave in crear_configuracion_simulacion():
            for valor in [float("nan"), float("inf"), -float("inf"), True]:
                c = crear_configuracion_simulacion(); c[clave] = valor
                self.assertIsNone(crear_configuracion_simulacion(**c))
                self.assertIsNone(simular_operaciones(datos([100.] * 4), [], c))

    def test_configuracion_estrategia_rechaza_no_finitos_incluso_dict_directo(self):
        e = {"resumen": {"favorables": 5, "desfavorables": 0}, "condiciones": {}}
        for clave in ["minimo_favorables", "maximo_desfavorables", "requeridas"]:
            for valor in [float("nan"), float("inf"), -float("inf"), True]:
                c = crear_configuracion_estrategia(); c[clave] = valor
                self.assertIsNone(crear_configuracion_estrategia(**c))
                self.assertIsNone(cumple_estrategia(e, c))

    def test_minimo_historial_invalido_no_llega_al_bucle(self):
        for valor in [float("nan"), float("inf"), -float("inf"), True, 1.5]:
            self.assertEqual(generar_evaluaciones_historicas(datos([100.] * 4), valor), [])

    def test_proporcion_no_finita_rechazada(self):
        for valor in [float("nan"), float("inf"), -float("inf"), True]:
            self.assertIsNone(dividir_datos_cronologicamente(datos([100.] * 4), valor))

    def test_retorno_futuro_no_propaga_infinito(self):
        d = datos([100., float("inf")])
        e = [{"fecha": d.index[0], "precio_cierre": 100., "analisis": {}, "evaluacion": {}}]
        self.assertIsNone(calcular_resultados_futuros(d, e, (1,))[0]["retornos_futuros"][1])

    def test_horizontes_1_5_10_seleccionan_fuente_correcta(self):
        segmentos = {}
        for nombre, multiplicador in [("entrenamiento", 1), ("prueba", 2)]:
            segmentos[nombre] = {"estadisticas_condiciones": {"rsi": {"favorable": {
                "horizontes": {h: {"muestras": h, "retorno_medio": h * multiplicador} for h in (1, 5, 10)}
            }}}}
        investigacion = {"validacion": segmentos, "resumen": {"comparacion_condiciones": {"incorrecto": True}}}
        original = copy.deepcopy(investigacion)
        for h in (1, 5, 10):
            reporte = crear_reporte_comparativo({"X": investigacion}, h)["X"]
            self.assertEqual(reporte["horizonte"], h)
            grupo = reporte["condiciones"]["rsi"]["favorable"]
            self.assertEqual(grupo["entrenamiento"]["retorno_medio"], h)
            self.assertEqual(grupo["prueba"]["retorno_medio"], h * 2)
            self.assertEqual(grupo["diferencias"]["retorno_medio"], h)
        self.assertEqual(investigacion, original)

    def test_horizonte_ausente_no_reutiliza_resumen(self):
        inv = {"resumen": {"comparacion_condiciones": {"incorrecto": True}}}
        self.assertEqual(crear_reporte_comparativo({"X": inv}, 10)["X"]["condiciones"], {})

    def test_horizonte_invalido_rechazado(self):
        for h in [0, -1, True, float("nan"), float("inf")]:
            with self.assertRaises(ValueError):
                crear_reporte_comparativo({}, h)


if __name__ == "__main__":
    unittest.main()
