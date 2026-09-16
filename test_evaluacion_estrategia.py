import unittest
from unittest.mock import patch

import pandas as pd

from evaluacion_estrategia import (
    comparar_metricas_estrategia,
    evaluar_estrategia_en_datos,
    ejecutar_validacion_estrategia,
)


class TestEvaluacionEstrategia(unittest.TestCase):
    def datos(self, n=6):
        index = pd.date_range("2026-01-01", periods=n, freq="D")
        return pd.DataFrame({"Close": range(100, 100+n)}, index=index)

    def config(self):
        return {"minimo_favorables": 4, "maximo_desfavorables": 1, "requeridas": ()}

    def sim_config(self):
        return {"capital_inicial": 10000.0, "porcentaje_capital_por_operacion": 100.0, "periodo_salida": 2, "coste_porcentual": 0.1}

    @patch("evaluacion_estrategia.calcular_metricas_estrategia", return_value={"x": 1})
    @patch("evaluacion_estrategia.simular_operaciones", return_value={"sim": 1})
    @patch("evaluacion_estrategia.generar_eventos_estrategia", return_value=[{"fecha": 2}])
    @patch("evaluacion_estrategia.generar_evaluaciones_historicas")
    def test_orquesta_y_filtra_indices(self, gen_mock, eventos_mock, sim_mock, metricas_mock):
        evaluaciones = [{"fecha": 1}, {"fecha": 2}]
        gen_mock.return_value = evaluaciones
        resultado = evaluar_estrategia_en_datos(self.datos(), self.config(), self.sim_config(), 3, {2})
        self.assertEqual(resultado["evaluaciones"], [{"fecha": 2}])
        eventos_mock.assert_called_once_with([{"fecha": 2}], self.config())
        sim_mock.assert_called_once()
        metricas_mock.assert_called_once_with({"sim": 1})

    def test_configuracion_none(self):
        self.assertIsNone(evaluar_estrategia_en_datos(self.datos(), None, self.sim_config()))

    @patch("evaluacion_estrategia.preparar_segmento_prueba_con_contexto")
    @patch("evaluacion_estrategia.evaluar_estrategia_en_datos")
    @patch("evaluacion_estrategia.dividir_datos_cronologicamente")
    def test_validacion_train_test_reutiliza_division_contexto_y_configs(self, dividir, evaluar, preparar):
        datos = self.datos()
        division = {"entrenamiento": datos.iloc[:4], "prueba": datos.iloc[4:], "total_filas": 6, "filas_entrenamiento": 4, "filas_prueba": 2, "proporcion_entrenamiento": .7}
        dividir.return_value = division
        preparar.return_value = {"datos_con_contexto": datos, "indices_prueba": datos.index[4:]}
        evaluar.side_effect = [{"segmento": "train"}, {"segmento": "test"}]
        result = ejecutar_validacion_estrategia(datos, self.config(), self.sim_config(), .7, 3)
        self.assertEqual(result["entrenamiento"], {"segmento": "train"})
        self.assertEqual(result["prueba"], {"segmento": "test"})
        self.assertEqual(result["configuracion_simulacion"], self.sim_config())
        self.assertEqual(
            set(evaluar.call_args_list[1].kwargs["indices_objetivo"]),
            set(datos.index[4:]),
        )

    def metrics(self, n, rate, profit, net, avg, exp, dd):
        return {"numero_operaciones": n, "tasa_acierto": rate, "profit_factor": profit, "resultado_neto_total": net, "retorno_total_pct": avg, "retorno_medio_operacion_pct": 1, "expectativa": exp, "max_drawdown_pct": dd}

    def test_comparacion_metricas_y_none(self):
        result = {"entrenamiento": {"metricas": self.metrics(2, 50, 2, 10, 1, 5, -3)}, "prueba": {"metricas": self.metrics(1, 25, None, 4, None, None, -5)}}
        comparison = comparar_metricas_estrategia(result)
        self.assertEqual(comparison["numero_operaciones"], {"entrenamiento": 2, "prueba": 1})
        self.assertEqual(comparison["tasa_acierto"]["diferencia"], -25)
        self.assertIsNone(comparison["profit_factor"]["diferencia"])
        self.assertEqual(comparison["max_drawdown_pct"]["diferencia"], -2)

    def test_invalid_validation(self):
        self.assertIsNone(ejecutar_validacion_estrategia(None, self.config(), self.sim_config()))
        self.assertIsNone(comparar_metricas_estrategia(None))


if __name__ == "__main__":
    unittest.main()
