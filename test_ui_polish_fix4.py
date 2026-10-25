"""Fix 4 numeric table and generic Spanish display regressions."""
import unittest
from unittest.mock import Mock

from ui.i18n_es import readable
from ui.tables import dataframe


class Fix4Tests(unittest.TestCase):
    def test_visible_numbers_preserve_numeric_sorting_and_raw_data(self):
        for preserve_raw in (False, True):
            st = Mock()
            rows = [{'net_pnl': 2}, {'net_pnl': 10.5}, {'net_pnl': None}]
            dataframe(st, rows, preserve_raw=preserve_raw)
            key = 'display:P&L neto' if preserve_raw else 'P&L neto'
            shown = st.dataframe.call_args.args[0]
            self.assertEqual([r[key] for r in shown], [2, 10.5, None])
            self.assertIsNone(rows[-1]['net_pnl'])

    def test_unknown_keys_and_pending_origin_are_display_only(self):
        self.assertEqual(readable({'new_raw_key': 'fallo real'}), 'Dato adicional: fallo real')
        self.assertNotIn('orden de origen', readable({'origin_order_id': 'pending'}))
        self.assertIn('Compra', readable({'side': 'LONG'}))
