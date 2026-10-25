"""Validated setups and exact-cycle persisted chart data, shared with email."""
import json

from ui.views.dashboard import ROW_LIMIT, payloads, records


def validated_setups(db, *, run_id='', offset=0):
    rows = []
    page = records(db, 'setups', run_id=run_id, offset=offset, statuses=('VALID_SETUP', 'PLAN_READY'))
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
    # Exact-run snapshots can carry validated setups even without a legacy setups row.
    existing = {r['run_id'] for r in rows}
    for snapshot in (payloads(db, 'ui_snapshots', run_id=run_id) if offset == 0 else []):
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


def iter_validated_setups(db, *, run_id=''):
    """Reconcile history one bounded page at a time; do not retain the ledger."""
    offset = 0
    while True:
        yield from validated_setups(db, run_id=run_id, offset=offset)
        if len(records(db, 'setups', run_id=run_id, offset=offset, statuses=('VALID_SETUP', 'PLAN_READY'))) < ROW_LIMIT:
            return
        offset += ROW_LIMIT


SETUPS_EMPTY_DEMO = 'Ningún setup válido desde el inicio del demo'
TRADED_UNKNOWN = 'sin datos'


def traded_ids(db, demo_snapshot=None):
    """Run / setup ids that became a trade: OANDA demo trades (``aif-<run_id>``) in demo mode, PAPER positions / closed
    trades otherwise. None when unknown (failed OANDA read)."""
    if demo_snapshot is not None:
        if demo_snapshot.get('stats') is None:
            return None
        rows = list(demo_snapshot.get('open_rows') or ()) + list(demo_snapshot.get('closed_rows') or ())
        return {row['client_id'][4:] for row in rows
                if isinstance(row.get('client_id'), str) and row['client_id'].startswith('aif-')}
    ids = set()
    for table in ('paper_positions', 'closed_trades'):
        for row in payloads(db, table):
            ids.update(str(row[key]) for key in ('run_id', 'setup_id') if row.get(key))
    return ids


def setup_side(row):
    return (row.get('plan') or {}).get('side') or (row.get('setup') or {}).get('side')


def setup_table_rows(rows, traded):
    """One compact row per setup for the list (Hora ET, Símbolo, Lado, Estado, Entrada / SL / TP, R:R, ¿Operado?)."""
    from ui.views.luxury import display, number
    result = []
    for row in rows:
        plan = row.get('plan') or {}
        levels = ' / '.join(display(plan.get(key)) if number(plan.get(key)) else '—' for key in ('entry', 'stop', 'target'))
        rr = plan.get('risk_reward')
        if traded is None:
            done = TRADED_UNKNOWN
        else:
            done = 'Sí' if {row.get('run_id'), row.get('setup_id')} & traded else 'No'
        result.append({'as_of': row.get('as_of'), 'symbol': row.get('symbol'), 'side': setup_side(row),
                       'setup_status': row.get('setup_status'), 'levels': levels,
                       'risk_reward': f'1 : {display(rr)}' if number(rr) else None, 'traded': done})
    return result


def render(st, db):
    from datetime import datetime, timezone
    from ui.components.setup_chart import build_setup_figure, light_setup_figure
    from ui.views.dashboard import FIRST_CYCLE_WAIT, since_epoch
    from ui.views.luxury import MISSING, card, demo_api, demo_since, display, process_link, state_chip, table, tr
    from ui.i18n_es import cycle_label
    demo = demo_api()
    since = demo_since(demo)
    rows = since_epoch(validated_setups(db), since, ('as_of',))[:ROW_LIMIT]
    from runtime.setup_candle_reader import cache_path
    from ui.candle_cache import cached_candles
    if not rows:
        if since is None:
            card(st, tr('Validated setups'), summary=f'{MISSING} · {tr("not persisted yet")}')
        else:
            cycles = since_epoch(records(db, 'runs'), since, ('as_of', 'started_at'))
            card(st, tr('Validated setups'), summary=SETUPS_EMPTY_DEMO if cycles else FIRST_CYCLE_WAIT)
        return
    st.caption(tr('Latest 200 setups · cache: closed 5m prices as of setup; OHLC only when persisted.'))
    symbol = st.selectbox(tr('Setups symbol'), ['All'] + sorted({r['symbol'] for r in rows}),
                          format_func=lambda value: 'Todos' if value == 'All' else value)
    visible = [row for row in rows if symbol == 'All' or row['symbol'] == symbol]
    snapshot = demo['snapshot'](datetime.now(timezone.utc)) if demo is not None else None
    traded = traded_ids(db, snapshot)
    table(st, tr('Validated setups'), setup_table_rows(visible, traded),
          ['as_of', 'symbol', 'side', 'setup_status', 'levels', 'risk_reward', 'traded'],
          render={'side': lambda r: state_chip(r.get('side')) if r.get('side') else '—',
                  'setup_status': lambda r: state_chip(r.get('setup_status'))})
    selected = st.selectbox(tr('Setup · newest first'), range(len(visible)),
                            format_func=lambda i: cycle_label(visible[i]['as_of'], visible[i]['symbol']))
    for index, row in enumerate([visible[selected]]):
        row['bars'] = cached_candles(str(cache_path()), row['symbol'], row['as_of']) or row['bars'][-48:]
        card(st, f"{row['symbol']} · {cycle_label(row['as_of'])}", [('Estado', state_chip(row['setup_status'])),
             ('Hora', display(row['as_of'])), ('Lado', state_chip(setup_side(row)) if setup_side(row) else MISSING)],
             summary=row['setup'] or None)
        with st.expander('ID técnico'):
            st.code(f"{tr('Setup ID')}: {row['setup_id'] or MISSING}\n{tr('Run ID')}: {row['run_id']}", language=None)
        process_link(st, row['run_id'], f'setup-process-{index}', cycle_label(row['as_of'], row['symbol']))
        if not row['setup_id']:
            card(st, tr('Gaps'), summary=tr('Setup ID not persisted; setup email dedup unavailable'))
        if not row['bars']:
            card(st, tr('Chart'), summary=tr('chart unavailable: bars not persisted'))
            card(st, tr('Gaps'), summary=tr('Exact-cycle 15m OHLC bars not persisted'))
        else:
            try:
                st.caption(tr('5m close prices · candle_cache · no OHLC available') if row['bars'][0].get('source') == 'candle_cache' else tr('Persisted 15m OHLC'))
                st.plotly_chart(light_setup_figure(build_setup_figure(row['bars'], row['plan'], row['symbol'])),
                                width='stretch', theme=None, key=f'setup-chart-{index}')
            except (ValueError, KeyError, TypeError):
                card(st, tr('Chart'), summary=tr('chart unavailable: persisted bars invalid'))
