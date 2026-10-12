"""Validated setups and exact-cycle persisted chart data, shared with email."""
import json

from ui.pages.dashboard import ROW_LIMIT, payloads, records


def validated_setups(db, *, run_id=''):
    rows, offset = [], 0
    while True:
        page = records(db, 'setups', run_id=run_id, offset=offset)
        for row in page:
            run = dict(db.execute('SELECT * FROM runs WHERE slot_key=?', (row['slot_key'],)).fetchone() or {})
            if not run:
                continue
            snapshots = [s for s in payloads(db, 'ui_snapshots', run_id=run['run_id'])
                         if s.get('run_id') == run['run_id'] and s.get('symbol') == run['symbol']]
            snapshot = snapshots[0] if snapshots else {}
            if row['status'] not in ('VALID_SETUP', 'PLAN_READY') and snapshot.get('setup_status', snapshot.get('state')) not in ('VALID_SETUP', 'PLAN_READY'):
                continue
            plan = json.loads(row['plan']) if row.get('plan') else snapshot.get('plan')
            plan = dict(plan or {})
            risks = records(db, 'risk_decisions', run_id=run['run_id'])
            if risks:
                for key in ('quantity', 'contract_multiplier'):
                    if risks[0].get(key) is not None:
                        plan[key] = risks[0][key]
            reviews = payloads(db, 'review_reports', run_id=run['run_id'])
            setup = reviews[0].get('setup') or {} if reviews else {}
            execution = reviews[0].get('execution') or {} if reviews else {}
            setup_id = plan.get('setup_id') or setup.get('setup_id') or execution.get('setup_id')
            if not setup_id:
                decisions = [j for j in payloads(db, 'journal', run_id=run['run_id'])
                             if j.get('event_type') == 'EXECUTION_DECISION']
                setup_id = decisions[0].get('setup_id') if decisions else None
            bars = snapshot.get('bars') or []
            if isinstance(bars, dict):
                bars = bars.get('15m', [])
            else:
                # An unlabelled list is not proof that candles are 15m.
                bars = [b for b in bars if str(b.get('timeframe', '')).lower() in ('15m', '15min')]
            status = row['status'] if row['status'] in ('VALID_SETUP', 'PLAN_READY') else snapshot.get('setup_status', snapshot.get('state'))
            rows.append({**run, 'setup_id': setup_id, 'setup_status': status,
                         'plan': plan, 'bars': bars, 'setup': setup})
        if len(page) < ROW_LIMIT:
            break
        offset += ROW_LIMIT
    # Exact-run snapshots can carry validated setups even without a legacy setups row.
    existing = {r['run_id'] for r in rows}
    for snapshot in payloads(db, 'ui_snapshots', run_id=run_id):
        if snapshot.get('setup_status', snapshot.get('state')) not in ('VALID_SETUP', 'PLAN_READY') or snapshot.get('run_id') in existing:
            continue
        parents = records(db, 'runs', run_id=snapshot.get('run_id')) if snapshot.get('run_id') else []
        if len(parents) != 1 or parents[0]['symbol'] != snapshot.get('symbol'):
            continue
        bars = snapshot.get('bars') or []
        bars = bars.get('15m', []) if isinstance(bars, dict) else [b for b in bars if b.get('timeframe') in ('15m', '15min')]
        plan = snapshot.get('plan') or {}
        rows.append({**parents[0], 'setup_status': snapshot.get('setup_status', snapshot.get('state')), 'setup_id': plan.get('setup_id'),
                     'plan': plan, 'bars': bars, 'setup': {}})
    return sorted(rows, key=lambda r: r['as_of'], reverse=True)


def render(st, db):
    from ui.components.setup_chart import build_setup_figure
    from ui.pages.luxury import card, process_link, MISSING
    rows = validated_setups(db)
    if not rows:
        card(st, 'Validated setups', summary=f'{MISSING} · not persisted yet')
        return
    symbol = st.selectbox('Setups symbol', ['All'] + sorted({r['symbol'] for r in rows}))
    for index, row in enumerate(rows):
        if symbol != 'All' and row['symbol'] != symbol:
            continue
        card(st, row['symbol'] + ' · ' + row['setup_status'],
             [('Run ID', row['run_id']), ('Setup ID', row['setup_id']), ('As of', row['as_of'])], summary=row['setup'])
        process_link(st, row['run_id'], f'setup-process-{index}')
        if not row['setup_id']:
            card(st, 'Gaps', summary='Setup ID not persisted; setup email dedup unavailable')
        if not row['bars']:
            card(st, 'Chart', summary='chart unavailable: bars not persisted')
            card(st, 'Gaps', summary='Exact-cycle 15m OHLC bars not persisted')
        else:
            try:
                st.plotly_chart(build_setup_figure(row['bars'], row['plan'], row['symbol']), width='stretch', key=f'setup-chart-{index}')
            except (ValueError, KeyError, TypeError):
                card(st, 'Chart', summary='chart unavailable: persisted bars invalid')
