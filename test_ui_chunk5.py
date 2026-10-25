"""Presentation regressions for the five chunk-5 review findings."""
from datetime import datetime, timezone
import json
import shutil
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from ui.views import control, floor_view, system

EPOCH = datetime(2026, 10, 25, tzinfo=timezone.utc)
OLD = '2026-10-24T23:59:59Z'
NEW = '2026-10-25T00:00:00Z'


class ControlStatusTests(unittest.TestCase):
    def test_status_priority_matrix(self):
        for emergency in (False, True):
            for enabled in (False, True):
                for mode in ('OFF', 'DEMO'):
                    for routing in ('off', 'dry_run', 'on'):
                        for read_ok in (False, True):
                            with self.subTest(emergency=emergency, enabled=enabled, mode=mode,
                                              routing=routing, read_ok=read_ok):
                                text, _ = control.account_status(
                                    {'key': 'broker_demo', 'enabled': enabled, 'mode': mode},
                                    {'emergency_stop': emergency, 'connected': False}, routing, read_ok)
                                self.assertEqual('Conectado · activo' in text,
                                                 not emergency and enabled and mode != 'OFF'
                                                 and routing == 'on' and read_ok)
                                if emergency:
                                    self.assertIn('Parada de emergencia', text)
                                elif not enabled or mode == 'OFF':
                                    self.assertIn('Cuenta apagada', text)
                                elif read_ok:
                                    self.assertIn('Conectado', text)
                                    if routing == 'dry_run':
                                        self.assertIn('dry-run', text)
                                    elif routing == 'off':
                                        self.assertIn('enrutado apagado', text)

    def test_history_reader_labels_are_normalized_and_fallback_is_clean(self):
        record = SimpleNamespace(source='paper', filename='x.json', label='Trades cerrados PAPER (agregado)',
                                 status='OK', stats={'trades': 2})
        with patch('execution.historial.load_baselines', return_value=[record]):
            rows = control.history_baselines()
            self.assertEqual(rows[1][2], 'Operaciones cerradas de práctica interna (agregado)')
            self.assertEqual(rows[1][3], {'trades': 2})
        with patch('execution.historial.load_baselines', side_effect=OSError):
            self.assertEqual(control.history_baselines()[1][2],
                             'Operaciones cerradas de práctica interna (agregado)')


class SystemEpochTests(unittest.TestCase):
    def test_aggregate_export_filters_reviews_and_events(self):
        raw = json.dumps({'reviews': [{'as_of': OLD}, {'as_of': NEW}, {}],
                          'notification_events': [{'timestamp_utc': OLD}, {'timestamp_utc': NEW}, {}]})
        result = json.loads(system.filter_evidence_json(raw, EPOCH))
        self.assertEqual(result['reviews'], [{'as_of': NEW}])
        self.assertEqual(result['notification_events'], [{'timestamp_utc': NEW}])
        self.assertEqual(system.filter_evidence_json(raw, None), raw)

    def test_run_export_blocks_old_or_missing_time_and_filters_journal(self):
        for stamp in (OLD, None):
            self.assertIsNone(system.filter_evidence_json(json.dumps({'review': {'as_of': stamp}}), EPOCH))
        raw = json.dumps({'review': {'as_of': NEW}, 'journal': [{'timestamp': OLD}, {'timestamp': NEW}]})
        self.assertEqual(json.loads(system.filter_evidence_json(raw, EPOCH))['journal'], [{'timestamp': NEW}])
        self.assertEqual(system.filter_evidence_json(raw, None), raw)

    def test_pre_epoch_audit_shows_no_record_journal_or_download(self):
        st, store = Mock(), Mock()
        st.text_input.return_value = 'old-run'
        store.review_report.return_value = {'run_id': 'old-run', 'as_of': OLD}
        vm = SimpleNamespace(sample=False, freshness='CURRENT', warnings=(), prompt_versions={}, schema_version='1')
        with patch('ui.views.system.dataframe'), patch('ui.views.dashboard.demo_since', return_value=EPOCH):
            system.render(st, vm, operational_health={'status': 'OK'}, store=store)
        st.info.assert_called_once_with('ese ciclo es anterior al inicio del demo y no se muestra')
        st.json.assert_not_called()
        st.download_button.assert_not_called()
        store.journal.assert_not_called()

    def test_current_audit_keeps_record_and_download_with_filtered_journal(self):
        st, store = Mock(), Mock()
        st.text_input.return_value = 'new-run'
        store.review_report.return_value = {'run_id': 'new-run', 'as_of': NEW}
        store.journal.return_value = [{'timestamp': OLD}, {'timestamp': NEW}]
        vm = SimpleNamespace(sample=False, freshness='CURRENT', warnings=(), prompt_versions={}, schema_version='1')
        with patch('ui.views.system.dataframe') as table, patch('runtime.review_export.run_evidence_json',
                return_value=json.dumps({'review': {'as_of': NEW}, 'journal': store.journal.return_value})):
            system.render(st, vm, operational_health={'status': 'OK'}, store=store, since=EPOCH)
        st.json.assert_called_once()
        self.assertEqual(table.call_args_list[1].args[1], [{'timestamp': NEW}])
        exported = json.loads(st.download_button.call_args.args[1])
        self.assertEqual(exported['journal'], [{'timestamp': NEW}])


class FloorExpiryTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node unavailable')
    def test_animation_stops_at_deadline_and_draws_idle_once(self):
        asset = floor_view.ASSET.read_text()
        loop = asset[asset.index('function expireRunning()'):asset.index('\nfit();', asset.index('function expireRunning()'))]
        script = '''const assert=require('node:assert/strict');
let ACTIVE=true,ANIM=true,STEP=2;const FIX=null,DEADLINE=1000,t0=0;
let now=999,pending=[],draws=[],idle=false,removed=false;Date.now=()=>now;
const document={body:{classList:{add:c=>{idle=c==='idle'}}},getElementById:()=>({remove:()=>removed=true})};
const requestAnimationFrame=fn=>pending.push(fn),draw=t=>draws.push([t,STEP,ANIM]);
''' + loop + '''
assert.equal(pending.length,1);pending.shift()(10);
assert.equal(pending.length,1);now=1000;pending.shift()(20);
assert.equal(pending.length,0);assert.equal(ACTIVE,false);
assert.deepEqual(draws.at(-1),[0,null,false]);assert.equal(idle,true);assert.equal(removed,true);
'''
        subprocess.run(['node', '-e', script], check=True, capture_output=True, text=True)
