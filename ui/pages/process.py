"""Per-cycle evidence, joined only by persisted identities."""
import json

from ui.pages.dashboard import history, payloads, records

STEPS = ('Data intake', 'Agent outputs', 'Setup reviewer + deterministic setup',
         'Conflict engine / opposite-setup policy', 'Planner R:R and plan',
         'Risk Engine', 'Paper Broker entry', 'Paper Broker exit', 'Final outcome')


def decoded(row):
    result = dict(row)
    for key in ('payload', 'plan', 'evidence', 'provider_metadata', 'warnings'):
        if isinstance(result.get(key), str):
            result[key] = json.loads(result[key])
    return result


def cycle_evidence(db, run):
    run_id = run['run_id']
    linked = history(db, run_id=run_id)
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
    entry = [r for r in linked if r['record'] in ('paper_orders', 'paper_positions')]
    exits = [r for r in linked if r['record'] == 'closed_trades']
    result = dict(zip(STEPS, (intake, agents or snapshot.get('agents', []),
        setups + ([{'review': review['setup']}] if review.get('setup') else []), conflict,
        plans, [decoded(r) for r in records(db, 'risk_decisions', run_id=run_id)],
        entry, exits, [decoded(run)] + [{'net_pnl': r.get('payload', {}).get('net_pnl')} for r in exits])))
    gaps = [step for step, rows in result.items() if not rows]
    if not snapshot.get('bars'):
        gaps.append('Cycle OHLC bars')
    if not review.get('macro_evidence'):
        gaps.append('Macro provider/news evidence')
    if not review.get('planner_policy') and not any(r.get('plan', {}).get('planner_policy') for r in setups):
        gaps.append('Per-cycle planner policy')
    return result, gaps


def render(st, db):
    from ui.pages.luxury import card, MISSING
    runs, offset = [], 0
    while True:
        page = records(db, 'runs', offset=offset)
        runs.extend(page)
        if len(page) < 200:
            break
        offset += 200
    symbol = st.selectbox('Process symbol', ['All'] + sorted({r['symbol'] for r in runs}))
    runs = [r for r in runs if symbol == 'All' or r['symbol'] == symbol]
    runs.sort(key=lambda r: (r['as_of'], r['started_at']), reverse=True)
    if not runs:
        card(st, 'Process', summary=f'{MISSING} · not persisted yet')
        for step in STEPS:
            card(st, step, summary='not persisted yet')
        card(st, 'Gaps', summary=', '.join(STEPS))
        return
    ids = [r['run_id'] for r in runs]
    requested = st.session_state.get('process_run_id')
    selected = st.selectbox('Cycle · newest first', ids,
                            index=ids.index(requested) if requested in ids else 0)
    st.session_state['process_run_id'] = selected
    evidence, gaps = cycle_evidence(db, runs[ids.index(selected)])
    st.caption('Timestamps below are persisted record timestamps; absent step timestamps are not inferred.')
    for index, (step, rows) in enumerate(evidence.items(), 1):
        card(st, f'{index}. {step}', summary=rows or 'not persisted yet')
    card(st, 'Gaps · not persisted yet', summary=', '.join(gaps) or 'None')
