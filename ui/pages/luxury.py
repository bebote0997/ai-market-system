"""Luxury observation screens over published PAPER evidence only."""
from datetime import datetime, timezone
from html import escape
import json
import os
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from runtime.scheduler import session_names
from storage.codec import parse_utc
from ui.adapters import from_persisted_snapshot
from ui.pages.dashboard import ROW_LIMIT, payloads, records
from ui.state import dashboard_database

PAGES = ("Overview", "The Floor", "Trades", "Decisions", "Analysts · soon")
MISSING = "not available yet"


def display(value):
    if value is None or value == "":
        return MISSING
    if isinstance(value, (tuple, list, dict)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def e(value):
    return escape(display(value), quote=True)


def html(st, content):
    st.markdown(f'<div class="lux">{content}</div>', unsafe_allow_html=True)


def card(st, title, fields=(), *, value=None, summary=None):
    body = f'<div class="big num">{e(value)}</div>' if value is not None else ''
    if summary is not None:
        body += f'<div class="bubble">{e(summary)}</div>'
    body += ''.join(f'<div class="row"><span class="mute">{e(key)}</span><span>{e(val)}</span></div>'
                    for key, val in fields)
    html(st, f'<div class="card"><h3>{e(title)}</h3>{body}</div>')


def table(st, title, rows, columns=None):
    if not rows:
        card(st, title, summary=MISSING)
        return
    columns = columns or list(dict.fromkeys(key for row in rows for key in row if key != 'payload'))
    head = ''.join(f'<th>{e(key.replace("_", " "))}</th>' for key in columns)
    body = ''.join('<tr>'+''.join(f'<td>{e(row.get(key))}</td>' for key in columns)+'</tr>'
                   for row in rows[:ROW_LIMIT])
    html(st, f'<div class="card"><h3>{e(title)}</h3><div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div></div>')


def apply(st):
    # One stylesheet block per Streamlit render, after authentication.
    css = (Path(__file__).parents[1] / 'static' / 'luxury.css').read_text()
    st.markdown(f'<style>{css}</style>', unsafe_allow_html=True)


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def trade_statistics(trades, now):
    """Closed-trade metrics use the complete ledger, never a bounded page."""
    result = dict.fromkeys(('Today PnL · ET', 'Win rate · closed trades', 'Avg R realized',
                            'Max drawdown · realized PnL'), MISSING)
    if not trades or not all(number(t.get('net_pnl')) and t.get('exited_at') for t in trades):
        return result, []
    ordered = sorted(trades, key=lambda t: parse_utc(t['exited_at']))
    total, peak, drawdown, curve = 0.0, 0.0, 0.0, []
    today = now.astimezone(ZoneInfo('America/New_York')).date()
    today_pnl = 0.0
    for trade in ordered:
        at = parse_utc(trade['exited_at'])
        total += trade['net_pnl']
        peak = max(peak, total)
        drawdown = max(drawdown, peak-total)
        curve.append({'Closed at': at, 'Cumulative realized PnL': total})
        if at.astimezone(ZoneInfo('America/New_York')).date() == today:
            today_pnl += trade['net_pnl']
    result['Today PnL · ET'] = f'{today_pnl:,.2f}'
    result['Win rate · closed trades'] = f'{100*sum(t["net_pnl"] > 0 for t in trades)/len(trades):.1f}%'
    result['Max drawdown · realized PnL'] = f'{drawdown:,.2f} (cash)'
    if all(number(t.get('realized_r')) for t in trades):
        result['Avg R realized'] = f'{sum(t["realized_r"] for t in trades)/len(trades):.2f}R'
    return result, curve


def all_closed(db):
    rows, offset = [], 0
    while True:
        page = payloads(db, 'closed_trades', offset=offset)
        rows.extend(page)
        if len(page) < ROW_LIMIT:
            return rows
        offset += ROW_LIMIT


def policy_value(states, key):
    for persisted_key in (key, key.lower()):
        if persisted_key in states:
            return states[persisted_key], 'system_state'
    # Only these two explicitly authorized nonsecret environment names are read.
    return os.environ.get(key, MISSING), 'env'


def overview(st, db, now):
    states = {r['key']: r['value'] for r in records(db, 'system_state')}
    accounts = payloads(db, 'paper_accounts')
    trades = all_closed(db)
    stats, curve = trade_statistics(trades, now)
    cols = st.columns([1.6, 1, 1])
    with cols[0]:
        if not accounts:
            card(st, 'Equity · PAPER', summary=MISSING)
        for account in accounts:
            card(st, 'Equity · PAPER', [('Account', account.get('account_id')),
                 ('Realized PnL', account.get('realized_pnl')), ('Unrealized PnL', account.get('unrealized_pnl'))],
                 value=account.get('equity', MISSING))
        if curve:
            st.caption('Closed-trade curve · cumulative realized PnL only')
            st.line_chart(curve, x='Closed at', y='Cumulative realized PnL', color='#c8a96a', width='stretch')
        else:
            card(st, 'Closed-trade curve', summary=MISSING)
    with cols[1]:
        card(st, 'Session', [('Current · runtime session windows', ', '.join(session_names(now)) or 'Outside sessions'),
             ('Scheduler', states.get('scheduler')), ('Last success', states.get('last_success')),
             ('Heartbeat', states.get('heartbeat'))])
    with cols[2]:
        path = Path(os.environ.get('AI_FLOOR_DB_PATH', 'data/runtime/trading_floor.db'))
        with dashboard_database(os.environ.get('AI_FLOOR_HEALTH_DB_PATH', str(path.with_name('system_health.db')))) as health_db:
            health = payloads(health_db, 'component_health')
        card(st, 'System', [(r.get('component', r.get('component_id', MISSING)), r.get('status')) for r in health]
             + [(key, states.get(key)) for key in ('market_data_provider', 'ai_provider', 'last_run')])
    for col, (key, value) in zip(st.columns(4), stats.items()):
        with col:
            card(st, key, value=value)
    left, right = st.columns([2, 1])
    with left:
        table(st, 'Open positions', payloads(db, 'paper_positions', statuses=('OPEN',)),
              ['symbol', 'side', 'entry_price', 'stop', 'target', 'unrealized_pnl'])
    with right:
        card(st, 'Active policies', [(key, f'{display(value)} · source: {source}')
             for key in ('V2_PLANNER_POLICY', 'V2_OPPOSITE_SETUP_POLICY')
             for value, source in [policy_value(states, key)]])


def floor(st, db, symbols):
    snapshots = {r['symbol']: r for r in payloads(db, 'ui_snapshots')}
    positions = payloads(db, 'paper_positions', statuses=('OPEN',))
    cols = st.columns(max(1, len(symbols)))
    for col, symbol in zip(cols, symbols):
        with col:
            snapshot = snapshots.get(symbol)
            vm = from_persisted_snapshot(snapshot) if snapshot is not None else None
            opened = [p for p in positions if p.get('symbol') == symbol]
            card(st, f'Desk · {symbol}', [('State', vm.state if vm else None),
                 ('Setup', vm.setup_status if vm else None), ('Side', vm.setup_side if vm else None),
                 ('Freshness', vm.freshness if vm else None), ('As of', vm.as_of if vm else None),
                 ('Open position', ', '.join(display(p.get('side'))+' OPEN' for p in opened) or 'No open position')])
    for symbol in symbols:
        snapshot = snapshots.get(symbol)
        if snapshot is None:
            card(st, f'{symbol} · Agent rooms / Risk Engine', summary=MISSING)
            continue
        vm = from_persisted_snapshot(snapshot)
        st.caption(f'{symbol} · Agent rooms')
        cols = st.columns(3)
        for index, agent in enumerate(vm.agents):
            with cols[index % 3]:
                card(st, agent.name, [('Status', agent.status if agent.status != 'MISSING' else None),
                     ('Bias', agent.bias if agent.status != 'MISSING' else None), ('Confidence', agent.confidence),
                     ('Timestamp', agent.timestamp)], summary=agent.reasoning_summary or MISSING)
        card(st, f'{symbol} · Risk Engine', [('Status', vm.risk_status if vm.risk_status != 'NOT CALLED' else None)]
             + list(vars(vm.risk_decision).items() if vm.risk_decision else []))


def linked_plan(db, trade):
    """Exact setup/run or origin-order identities; never match by price geometry."""
    filters = {key: trade[key] for key in ('run_id', 'setup_id') if trade.get(key)}
    setups = records(db, 'setups', **filters) if filters else []
    plans = [json.loads(row['plan']) for row in setups if row.get('plan')]
    plans = [plan for plan in plans if plan is not None]
    if trade.get('setup_id'):
        plans = [plan for plan in plans if plan.get('setup_id') == trade['setup_id']]
    if len(plans) == 1:
        return plans[0]
    orders = payloads(db, 'paper_orders', **filters) if filters else []
    matched = [o for o in orders if trade.get('origin_order_id') and o.get('order_id') == trade['origin_order_id']]
    if len(matched) == 1:
        return matched[0]
    risks = records(db, 'risk_decisions', **filters) if filters else []
    return risks[0] if len(risks) == 1 else {}


def trades(st, db):
    closed = [{**r, 'record': 'closed'} for r in payloads(db, 'closed_trades')]
    opened = [{**r, 'record': 'open'} for r in payloads(db, 'paper_positions', statuses=('OPEN',))]
    rows = closed + opened
    table(st, 'Closed trades · latest published', closed, ['trade_id', 'position_id', 'symbol', 'side',
          'entry_price', 'exit_price', 'net_pnl', 'exited_at'])
    table(st, 'Open positions', opened, ['position_id', 'symbol', 'side', 'entry_price', 'stop', 'target'])
    if not rows:
        return
    selected = st.selectbox('Trade detail', range(len(rows)),
                            format_func=lambda i: display(rows[i].get('trade_id', rows[i].get('position_id'))))
    trade = rows[selected]
    plan = linked_plan(db, trade)
    left, right = st.columns(2)
    with left:
        card(st, 'Plan', [(label, plan.get(key)) for label, key in
             [('Entry', 'entry'), ('Stop', 'stop'), ('Target', 'target'), ('R:R', 'risk_reward')]])
        if plan.get('planned_entry') is not None:
            card(st, 'Order planned entry', [('Entry', plan['planned_entry'])])
    with right:
        card(st, 'Execution', [(label, trade.get(key)) for label, key in
             [('Fill', 'entry_price'), ('Exit', 'exit_price'), ('Net PnL', 'net_pnl'),
              ('R realized', 'realized_r'), ('Exit reason', 'reason')]])


def decisions(st, db):
    rows = []
    for source in ('runs', 'agent_decisions', 'risk_decisions'):
        for row in records(db, source):
            parent = dict(db.execute('SELECT run_id,symbol,as_of FROM runs WHERE slot_key=?',
                                    (row['slot_key'],)).fetchone() or {})
            rows.append({**parent, **row, 'source': source})
    rows.sort(key=lambda r: r.get('as_of') or '', reverse=True)
    table(st, 'Decisions · newest first', rows[:ROW_LIMIT], ['as_of', 'symbol', 'run_id', 'source',
          'agent_name', 'status', 'bias', 'confidence', 'reasoning_summary', 'reason', 'error'])


def render(st, page, symbols):
    now = datetime.now(timezone.utc)
    badge = '<span class="ex">SAMPLE / DEMO</span>' if st.session_state.get('sample_mode') else ''
    html(st, f'<div class="top"><div><div class="eyebrow">{e(now.strftime("%A · %d %B %Y · %H:%M UTC"))}</div><h1>{e(page)}</h1></div>{badge}</div>')
    html(st, '<div class="eyebrow gold">PAPER · REAL EXECUTION DISABLED · NO REAL MONEY</div>')
    try:
        if page == 'Analysts · soon':
            card(st, 'Post-trade analysis department · soon', summary='Future analysts will review closed trades and propose improvements. Proposals require owner approval; nothing is automatic.')
            return
        with dashboard_database() as db:
            if page == 'Overview':
                overview(st, db, now)
            elif page == 'The Floor':
                floor(st, db, symbols)
            elif page == 'Trades':
                trades(st, db)
            elif page == 'Decisions':
                decisions(st, db)
    except (sqlite3.DatabaseError, ValueError, TypeError, KeyError) as exc:
        st.error(f'DASHBOARD_ERROR — {type(exc).__name__}: persisted evidence could not be read')
    html(st, '<div class="foot">Paper trading only · observation only</div>')
