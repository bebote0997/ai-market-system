"""Per-cycle evidence, joined only by persisted identities."""
import json

from ui.views.dashboard import (FIRST_CYCLE_WAIT, PAPER_SIZING_KEYS, hide_paper_results, history, paper_hidden,
                                payloads, records, since_epoch)

STEPS = ('Data intake', 'Agent outputs', 'Setup reviewer + deterministic setup',
         'Conflict engine / opposite-setup policy', 'Planner R:R and plan',
         'Risk Engine', 'Paper Broker entry', 'Paper Broker exit', 'Final outcome')

OANDA_LABELS = {'Paper Broker entry': 'Entrada en la demo OANDA', 'Paper Broker exit': 'Salida en la demo OANDA'}


def decoded(row):
    result = dict(row)
    for key in ('payload', 'plan', 'evidence', 'provider_metadata', 'warnings'):
        if isinstance(result.get(key), str):
            result[key] = json.loads(result[key])
    return result


def oanda_steps(run_id):
    """OANDA-demo entry / exit / outcome rows routed from this cycle (DEC-OANDA-ONLY-1); unreadable => NO_DATA."""
    from execution.broker_demo_state import CLOSED, CLOSED_AT_BROKER, read_run_trades
    from ui.i18n_es import NO_DATA
    trades = read_run_trades(run_id)
    if trades is None:
        return [], [], [{'source': 'OANDA_DEMO', 'outcome': NO_DATA}]
    entry = [{'source': 'OANDA_DEMO', **t} for t in trades if t.get('trade_id') or t.get('fill_price')]
    exits = [{'source': 'OANDA_DEMO', **t} for t in trades if t.get('status') in (CLOSED, CLOSED_AT_BROKER)]
    outcome = [{'source': 'OANDA_DEMO', 'client_id': t['client_id'], 'status': t['status'],
                'closed_reason': t.get('closed_reason')} for t in trades]
    return entry, exits, outcome or [{'source': 'OANDA_DEMO', 'outcome': NO_DATA}]


def cycle_evidence(db, run):
    run_id = run['run_id']
    hidden = paper_hidden()
    linked = history(db, run_id=run_id)
    if hidden:  # OANDA-demo-only: PAPER fills / exits / P&L never reach the owner; persisted evidence untouched
        linked = hide_paper_results(linked)
    reviews = payloads(db, 'review_reports', run_id=run_id)
    snapshots = [s for s in payloads(db, 'ui_snapshots', run_id=run_id)
                 if s.get('run_id') == run_id]
    review = reviews[0] if reviews else {}
    snapshot = snapshots[0] if snapshots else {}
    journal = [r for r in linked if r['record'] == 'journal']
    setups = [r for r in linked if r['record'] == 'setups']
    agents = [decoded(r) for r in records(db, 'agent_decisions', run_id=run_id)]
    intake = []
    metadata = decoded(run).get('provider_metadata')
    if metadata:
        intake.append({'provider_metadata': metadata, 'as_of': run.get('as_of')})
    if review.get('macro_evidence'):
        intake.append({'macro_evidence': review['macro_evidence'], 'as_of': review.get('as_of')})
    if snapshot.get('bars'):
        intake.append({'bars': snapshot['bars'], 'as_of': snapshot.get('as_of')})
    conflict = [r for r in journal if r.get('source') in ('opposite_policy', 'conflict_engine')
                or any(word in r.get('event_type', '') for word in ('CONFLICT', 'OPPOSITE', 'STRUCTURE_REVERSE'))
                or (r.get('event_type') == 'EXECUTION_DECISION' and
                    any(word in str(r.get('payload', {})) for word in ('OPPOSITE', 'CONFLICT', 'STRUCTURE')))]
    plans = [dict(plan=r['plan']) for r in setups if r.get('plan')]
    if review.get('target_decision'):
        plans.append({'target_decision': review['target_decision']})
    plans.extend(r for r in journal if r.get('event_type') == 'PLAN_CREATED')
    setups.extend(r for r in journal if r.get('source') == 'setup')
    entry = [r for r in linked if r['record'] in ('paper_orders', 'paper_fills', 'paper_positions')]
    exits = [r for r in linked if r['record'] == 'closed_trades']
    outcome = [{'net_pnl': (r.get('payload') or {}).get('net_pnl')} for r in exits]
    result = dict(zip(STEPS, (intake, agents or snapshot.get('agents') or review.get('agents', []),
        setups + ([{'review': review['setup']}] if review.get('setup') else []), conflict,
        plans, [decoded(r) for r in records(db, 'risk_decisions', run_id=run_id)]
        + [r for r in journal if r.get('event_type') == 'RISK_V2_DECISION' or r.get('source') == 'risk'],
        entry, exits, [decoded(run)] + outcome)))
    if hidden:
        result = {step: hide_paper_results(rows) for step, rows in result.items()}
        entry, exits, outcome = oanda_steps(run_id)  # after the scrub: OANDA fill prices are owner-facing results
        result.update(zip(STEPS[-3:], (entry, exits, result[STEPS[-1]] + outcome)))
    gaps = [step for step, rows in result.items() if not rows]
    if not snapshot.get('bars'):
        gaps.append('Cycle OHLC bars')
    if not review.get('macro_evidence'):
        gaps.append('Macro provider/news evidence')
    if not review.get('planner_policy') and not any((r.get('plan') if isinstance(r.get('plan'), dict) else {}).get('planner_policy') for r in setups):
        gaps.append('Per-cycle planner policy')
    return result, gaps


def gap_label(gap):
    from ui.i18n_es import STEPS as LABELS
    if gap.endswith(' timestamp'):
        return LABELS.get(gap[:-len(' timestamp')], gap[:-len(' timestamp')]) + ' (hora)'
    return LABELS.get(gap, gap)


def scrub_paper_sizing(value):
    """Demo mode: drop the PAPER account's sizing fields (equity at decision ~10k), any depth."""
    if isinstance(value, dict):
        return {k: scrub_paper_sizing(v) for k, v in value.items() if k not in PAPER_SIZING_KEYS}
    if isinstance(value, list):
        return [scrub_paper_sizing(item) for item in value]
    return value


def symbol_option(symbol):
    from ui.i18n_es import PAUSED_TEXT, symbol_status
    if symbol == 'All':
        return 'Todos'
    return f'{symbol} · {PAUSED_TEXT}' if symbol_status(symbol, ()) == PAUSED_TEXT else symbol


def render(st, db):
    from ui.components.glass import step_strip
    from ui.views.luxury import card, MISSING, demo_api, demo_since, html, state_chip, tr
    from ui.i18n_es import STEPS as LABELS, AGENTS, cycle_label, status_text
    since = demo_since(demo_api())  # OANDA demo: only runs at/after the epoch (a pre-epoch deep link is ignored)
    runs = since_epoch(records(db, 'runs'), since, ('as_of', 'started_at'))
    requested = st.session_state.get('process_run_id')
    if requested and requested not in {r['run_id'] for r in runs}:
        runs = since_epoch(records(db, 'runs', run_id=requested), since, ('as_of', 'started_at')) + runs[:199]
    if since is not None and not runs:
        card(st, tr('Process'), summary=FIRST_CYCLE_WAIT)
        return
    st.caption(tr('Latest 200 cycles; exact run links can open older cycles.'))
    symbol = st.selectbox(tr('Process symbol'), ['All'] + sorted({r['symbol'] for r in runs}), format_func=symbol_option)
    runs = [r for r in runs if symbol == 'All' or r['symbol'] == symbol]
    runs.sort(key=lambda r: (r['as_of'], r['started_at']), reverse=True)
    pending = tr('not persisted yet')
    if not runs:
        card(st, tr('Process'), summary=f'{MISSING} · {pending}')
        for step in STEPS:
            card(st, LABELS[step], summary=pending)
        card(st, tr('Gaps'), summary=', '.join(LABELS[step] for step in STEPS))
        return
    ids = [r['run_id'] for r in runs]
    labels = {r['run_id']: cycle_label(r.get('as_of'), r.get('symbol')) for r in runs}
    requested = st.session_state.get('process_run_id')
    selected = st.selectbox(tr('Cycle · newest first'), ids, format_func=lambda run: labels.get(run, run),
                            index=ids.index(requested) if requested in ids else 0)
    st.session_state['process_run_id'] = selected
    with st.expander('ID técnico'):
        st.code(selected or MISSING, language=None)
    evidence, gaps = cycle_evidence(db, runs[ids.index(selected)])
    if since is not None:
        evidence = {step: scrub_paper_sizing(rows) for step, rows in evidence.items()}
    oanda = paper_hidden()
    step_labels = {step: OANDA_LABELS.get(step, LABELS[step]) if oanda else LABELS[step] for step in STEPS}
    html(st, step_strip([(step_labels.get(step, step), bool(rows)) for step, rows in evidence.items()]))
    st.caption(tr('Timestamps below are persisted record timestamps; absent step timestamps are not inferred.'))
    for index, (step, rows) in enumerate(evidence.items(), 1):
        timestamps = []
        for row in rows:
            value = {**row, **row['payload']} if isinstance(row.get('payload'), dict) else row
            at = next((value.get(k) for k in ('timestamp', 'timestamp_utc', 'as_of', 'opened_at', 'exited_at', 'completed_at', 'started_at', 'filled_at', 'fill_timestamp') if value.get(k)), None)
            if at:
                timestamps.append(at)
        if rows and not timestamps:
            gaps.append(step + ' timestamp')
        statuses = []
        for row in rows:
            value = {**row, **row['payload']} if isinstance(row.get('payload'), dict) else row
            statuses.extend(str(value[k]) for k in ('status', 'final_status', 'decision', 'execution_status') if value.get(k))
        status = ', '.join(status_text(s) for s in dict.fromkeys(statuses)) or (tr('Persisted evidence') if rows else pending)
        from ui.views.luxury import display
        label = OANDA_LABELS.get(step, LABELS[step]) if paper_hidden() else LABELS[step]
        card(st, f'{index}. {label}', [('Estado', status),
             ('Hora', ', '.join(dict.fromkeys(display(t) for t in timestamps)) or pending)],
             summary=rows or pending)
        if step == 'Agent outputs':
            for agent in rows:
                name = agent.get('agent_name') or agent.get('name') or agent.get('agent')
                card(st, AGENTS.get(name, name),
                     [('Estado', state_chip(agent.get('status'))), ('Tendencia', state_chip(agent.get('bias')))]
                     + [(label, agent.get(k)) for label, k in (('Confianza', 'confidence'), ('Hora', 'as_of'),
                        ('Hora del agente', 'timestamp'), ('Resumen', 'reasoning_summary'), ('Evidencia', 'evidence'),
                        ('Conflictos', 'conflicts'))])
    card(st, tr('Gaps · not persisted yet'), summary=', '.join(dict.fromkeys(gap_label(g) for g in gaps)) or tr('None'))
