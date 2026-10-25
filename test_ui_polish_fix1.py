"""Raw table evidence and readable visible columns remain separate."""
import unittest
from unittest.mock import Mock

from ui.tables import dataframe


class EvidenceTableTests(unittest.TestCase):
    def test_raw_contract_and_spanish_projection(self):
        st = Mock()
        identity = "12345678-1234-1234-1234-123456789abc"
        row = {"run_id": identity, "attempts": 3, "recovery": "AMBIGUOUS_RETRY",
               "event_type": "STATE_INCONSISTENCY", "payload": {"detail": "x"}}
        dataframe(st, [row], preserve_raw=True)
        call = st.dataframe.call_args
        data = call.args[0][0]
        self.assertEqual(data["run_id"], identity)
        self.assertEqual(data["attempts"], 3)
        self.assertEqual(data["recovery"], "AMBIGUOUS_RETRY")
        self.assertEqual(data["event_type"], "STATE_INCONSISTENCY")
        self.assertIsNone(call.kwargs["column_config"]["run_id"])
        visible = {call.kwargs["column_config"][key]: data[key]
                   for key in call.kwargs["column_order"]}
        self.assertEqual(visible["Tipo de evento"], "Estado inconsistente")
        self.assertEqual(visible["Detalles"], "Detalle: x")
        self.assertNotIn(identity, str(visible))
        self.assertEqual(row["payload"], {"detail": "x"})
