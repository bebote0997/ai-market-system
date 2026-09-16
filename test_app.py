from pathlib import Path
import unittest
from unittest.mock import patch

import pandas as pd
from streamlit.testing.v1 import AppTest


class TestEstadoValidacionApp(unittest.TestCase):
    def setUp(self):
        self.precios = pd.DataFrame({
            "Open": [100.] * 60, "High": [105.] * 60, "Low": [95.] * 60,
            "Close": [100.] * 60, "Volume": [1000.] * 60,
        }, index=pd.date_range("2025-01-01", periods=60))
        cartera = [{
            "simbolo": ticker, "ticker": ticker, "mercado": "acciones",
            "precio_entrada": 100., "precio_actual": 100., "cantidad": 1.,
            "capital_utilizado": 100., "valor_actual": 100.,
            "ganancia_perdida": 0., "rentabilidad": 0.,
        } for ticker in ["AAPL", "MSFT"]]
        for destino, valor in [
            ("servicio.procesar_cartera", cartera),
            ("mercado.obtener_datos_historicos", self.precios),
            ("investigacion.ejecutar_investigacion_fuera_muestra", {
                "validacion": {"division": {"filas_entrenamiento": 42, "filas_prueba": 18},
                               "entrenamiento": {}, "prueba": {}},
                "filas_historicas": 60,
            }),
        ]:
            parche = patch(destino, return_value=valor)
            mock = parche.start()
            self.addCleanup(parche.stop)
            if destino.startswith("investigacion"):
                self.investigar = mock
        self.app = AppTest.from_file(str(Path(__file__).with_name("app.py")))
        self.app.run()
        self.assertEqual(len(self.app.exception), 0)
        self.investigar.assert_not_called()
        self.app.button[0].click().run()
        self.assertEqual(len(self.app.exception), 0)
        self.assertIsNotNone(self.app.session_state["validacion_historica"])

    def cambiar(self, indice, valor):
        self.app.selectbox[indice].set_value(valor).run()
        self.assertEqual(len(self.app.exception), 0)
        self.assertIsNone(self.app.session_state["validacion_historica"])
        self.investigar.assert_called_once()

    def test_cambio_ticker_invalida_sin_recalcular(self):
        self.cambiar(0, "MSFT")

    def test_cambio_periodo_invalida_sin_recalcular(self):
        self.cambiar(2, "5y")

    def test_cambio_proporcion_invalida_sin_recalcular(self):
        self.cambiar(3, "80 / 20")

    def test_cambio_horizonte_invalida_sin_recalcular(self):
        self.cambiar(4, 10)

    def test_rerun_sin_cambios_conserva_resultado_sin_recalcular(self):
        anterior = self.app.session_state["validacion_historica"]
        self.app.run()
        self.assertEqual(self.app.session_state["validacion_historica"], anterior)
        self.assertEqual(anterior["horizonte"], 5)
        self.assertEqual(anterior["proporcion_entrenamiento"], .7)
        self.investigar.assert_called_once()


if __name__ == "__main__":
    unittest.main()
