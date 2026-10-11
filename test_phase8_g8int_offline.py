"""G-8.INT offline evidence; synthetic fixtures never certify a live period."""
from pathlib import Path
import json
import tempfile
import unittest

from replay.g8int import main as g8int_main
from replay.status_verifier import sha256
from test_phase8_rex_writer import plan_scenario


def offline_period(directory):
    """Reuse real runtime/REX submit/fill helpers, with both symbols in scope.

    Only XAUUSD runs here: this demonstrates offline computation, without the
    two-symbol exposure/session thresholds or external certification evidence.
    The snapshot helper supplies sparse bars, so its GAPs leave G8 unverified.
    """
    from dataclasses import replace
    from unittest.mock import patch
    from runtime.service import OperationalRuntime
    original = OperationalRuntime.__init__

    def init(runtime, config, *args, **kwargs):
        config = replace(config, v2_position_catch_up=True,
                         v2_position_catch_up_symbols=('XAUUSD', 'EURUSD'),
                         market_evidence_path=directory / 'evidence.db')
        return original(runtime, config, *args, **kwargs)

    db, ev = directory / 'copy.db', directory / 'evidence.db'
    with patch.object(OperationalRuntime, '__init__', init):
        plan_scenario(db, steps=((0, 100.), (15, 100.)))
    return db, ev


class OfflineG8IntTests(unittest.TestCase):
    def test_deterministic_period(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, ev = offline_period(Path(tmp))
            before = [sha256(path) for path in (db, ev)]
            out = Path(tmp) / 'g8int.json'
            self.assertEqual(g8int_main(['--trading-db', str(db), '--evidence-db', str(ev),
                                       '--out', str(out)]), 0)
            report = json.loads(out.read_text(encoding='utf-8'))
            statuses = {key.split('_')[0]: value['status'] for key, value in report['criteria'].items()}
            self.assertEqual(statuses, {
                'G1': 'PASS', 'G2': 'BLOCKED', 'G3': 'BLOCKED', 'G4': 'BLOCKED',
                'G5': 'BLOCKED', 'G6': 'PASS', 'G7': 'PASS', 'G8': 'NOT VERIFIED',
                'G9': 'PASS', 'G10': 'PASS', 'G11': 'BLOCKED', 'G12': 'PASS',
                'G13': 'BLOCKED', 'G14': 'PASS', 'G15': 'NOT VERIFIED'})
            self.assertNotIn('FAIL', [v['status'] for v in {**report['criteria'], **report['integration']}.values()])
            self.assertEqual(report['phase8_verdict'], 'BLOCKED')
            self.assertEqual(before, [sha256(path) for path in (db, ev)])


if __name__ == '__main__':
    unittest.main()
