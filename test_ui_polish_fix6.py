"""Spanish operational inventory and responsive detail KPI regressions."""
from pathlib import Path
import unittest

from ui.i18n_es import service_label
from ui.views.system import health_rows


class OperationalPresentationTests(unittest.TestCase):
    def test_labels_values_and_original_evidence(self):
        health = {'supported_symbols': ('XAUUSD', 'NAS100', 'EURUSD'),
                  'scheduler': 'Not started', 'market_provider_mode': 'none',
                  'massive_adapter': 'SUPPORTED / NOT ACTIVE', 'schema_version': 7,
                  'unexpected_contract_field': 'real error detail'}
        original = dict(health)
        rows = dict(health_rows(health))
        self.assertEqual(rows['Símbolos soportados'], 'XAUUSD, NAS100, EURUSD')
        self.assertEqual(rows['Programador'], 'No iniciado')
        self.assertEqual(rows['Modo del proveedor de precios'], 'ninguno')
        self.assertEqual(rows['Adaptador Massive'], 'Compatible · no activo')
        self.assertEqual(rows['Versión del esquema'], 7)
        self.assertEqual(rows['Dato adicional'], 'real error detail')
        self.assertEqual(health, original)
        self.assertEqual(service_label('scheduler_slot'), 'Turno del programador')
        self.assertEqual(service_label('last_provider_status'), 'Último proveedor estado')

    def test_metric_css_wraps_values_and_labels_including_inner_div(self):
        css = Path('ui/static/theme.css').read_text()
        rules = css.split('/* Detail KPIs')[1]
        for selector in ('stMetricValue', 'stMetricLabel', '] > div'):
            self.assertIn(selector, rules)
        for rule in ('white-space:normal!important', 'overflow:visible!important',
                     'text-overflow:clip!important', 'overflow-wrap:anywhere',
                     'word-break:break-word', 'font-size:20px!important'):
            self.assertIn(rule, rules)
