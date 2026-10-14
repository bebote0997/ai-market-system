"""Luxury observation screens over published PAPER evidence only."""
from datetime import datetime, timezone
from html import escape
import json
import math
import os
from pathlib import Path
import sqlite3
from collections import deque
from zoneinfo import ZoneInfo

from runtime.scheduler import session_names
from storage.codec import parse_utc
from ui.adapters import from_persisted_snapshot
from ui.views.dashboard import ROW_LIMIT, payloads, records
from ui.state import dashboard_database
from ui.json_views import details, structured

PAGES = ("Overview", "The Floor", "Process", "Setups", "Trades", "Decisions", "Analysts · soon")
MISSING = "not available yet"


def display(value):
    if value is None or value == "":
        return MISSING
    if isinstance(value, float):
        return f"{value:,.5f}".rstrip("0").rstrip(".") if abs(value) < 10 else f"{value:,.2f}"
    if isinstance(value, str) and len(value) >= 19 and value[4:5] == "-" and value[10:11] == "T":
        try:
            return parse_utc(value).astimezone(ZoneInfo("America/New_York")).strftime("%d %b %Y %H:%M ET")
        except (ValueError, TypeError):
            return value
    if isinstance(value, (tuple, list, dict)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def e(value):
    return escape(display(value), quote=True)


def html(st, content):
    st.markdown(f'<div class="lux">{content}</div>', unsafe_allow_html=True)


def content(value):
    return details(value) if isinstance(structured(value), (list, tuple, dict)) else e(value)


def card(st, title, fields=(), *, value=None, summary=None):
    body = f'<div class="big num">{e(value)}</div>' if value is not None else ''
    if summary is not None:
        body += f'<div class="bubble">{content(summary)}</div>'
    body += ''.join(f'<div class="row"><span class="mute">{e(key)}</span><span>{content(val)}</span></div>'
                    for key, val in fields)
    html(st, f'<div class="card"><h3>{e(title)}</h3>{body}</div>')


def table(st, title, rows, columns=None):
    if not rows:
        card(st, title, summary=MISSING)
        return
    columns = columns or list(dict.fromkeys(key for row in rows for key in row if key != 'payload'))
    head = ''.join(f'<th>{e(key.replace("_", " "))}</th>' for key in columns)
    body = ''.join('<tr>'+''.join(f'<td>{content(row.get(key))}</td>' for key in columns)+'</tr>'
                   for row in rows[:ROW_LIMIT])
    html(st, f'<div class="card"><h3>{e(title)}</h3><div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div></div>')


def apply(st):
    # One stylesheet block per Streamlit render, after authentication.
    css = (Path(__file__).parents[1] / 'static' / 'luxury.css').read_text()
    st.markdown(f'<style>{css}</style>', unsafe_allow_html=True)


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def realized_r(trade, position):
    """Net PnL / original cash risk, using exact persisted position identity only."""
    if number(trade.get('realized_r')):
        return trade['realized_r']
    if not position or trade.get('position_id') != position.get('position_id'):
        return None
    stop = position.get('initial_stop')
    if stop is None:
        stop = position.get('stop')
    entry, quantity, multiplier, pnl = (position.get('entry_price'), position.get('quantity'),
                                      position.get('contract_multiplier'), trade.get('net_pnl'))
    if not all(number(v) for v in (entry, stop, quantity, multiplier, pnl)):
        return None
    risk = abs(entry-stop) * quantity * multiplier
    return pnl/risk if risk > 0 and quantity > 0 and multiplier > 0 else None


def open_position_rows(db):
    rows = payloads(db, 'paper_positions', statuses=('OPEN',))
    for row in rows:
        values = [row.get(k) for k in ('last_price', 'entry_price', 'quantity', 'contract_multiplier')]
        if row.get('unrealized_pnl') is None and all(number(v) for v in values) and row.get('side') in ('LONG', 'SHORT'):
            last, entry, quantity, multiplier = values
            if quantity > 0 and multiplier > 0:
                row['unrealized_pnl'] = (last-entry) * quantity * multiplier * (1 if row['side'] == 'LONG' else -1)
    return rows


def closed_trade_rows(db):
    rows = payloads(db, 'closed_trades')
    ids = [r['position_id'] for r in rows if r.get('position_id')]
    positions = {}
    if ids and db.execute("SELECT 1 FROM sqlite_master WHERE name='paper_positions'").fetchone():
        # At most 200 indexed identity lookups; do not load the position ledger.
        for row in db.execute('SELECT position_id,payload FROM paper_positions WHERE position_id IN (' +
                              ','.join('?' for _ in ids) + ')', ids):
            positions[row[0]] = json.loads(row[1])
    return [{**r, 'realized_r': realized_r(r, positions.get(r.get('position_id')))} for r in rows]


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


def ledger_statistics(db, now):
    """Stream scalar ledger fields; exact metrics, only the latest 200 curve points."""
    missing = dict.fromkeys(('Today PnL · ET', 'Win rate · closed trades', 'Avg R realized',
                             'Max drawdown · realized PnL'), MISSING)
    if db is None or not db.execute("SELECT 1 FROM sqlite_master WHERE name='closed_trades'").fetchone():
        return missing, []
    has_positions = db.execute("SELECT 1 FROM sqlite_master WHERE name='paper_positions'").fetchone()
    fields = ('position_id', 'entry_price', 'stop', 'initial_stop', 'quantity', 'contract_multiplier')
    position = ','.join(f"json_extract(p.payload,'$.{key}')" for key in fields) if has_positions else ','.join('NULL' for _ in fields)
    join = " LEFT JOIN paper_positions p ON p.position_id=json_extract(t.payload,'$.position_id')" if has_positions else ""
    cursor = db.execute("SELECT json_extract(t.payload,'$.net_pnl'),json_extract(t.payload,'$.exited_at'),"
                        "json_extract(t.payload,'$.realized_r')," + position + " FROM closed_trades t" + join +
                        " ORDER BY julianday(json_extract(t.payload,'$.exited_at')),t.rowid")
    count = wins = 0
    total = peak = drawdown = today_pnl = sum_r = 0.0
    known_r = valid = True
    today = now.astimezone(ZoneInfo('America/New_York')).date()
    curve = deque(maxlen=ROW_LIMIT)
    for pnl, stamp, stored_r, *position_values in cursor:
        position = dict(zip(fields, position_values))
        r_value = realized_r({'net_pnl': pnl, 'realized_r': stored_r,
                              'position_id': position.get('position_id')}, position)
        count += 1
        if not number(pnl) or not stamp:
            valid = False
            continue
        at = parse_utc(stamp)
        total += pnl
        peak = max(peak, total)
        drawdown = max(drawdown, peak-total)
        wins += pnl > 0
        if at.astimezone(ZoneInfo('America/New_York')).date() == today:
            today_pnl += pnl
        if number(r_value):
            sum_r += r_value
        else:
            known_r = False
        curve.append({'Closed at': at, 'Cumulative realized PnL': total})
    if not count or not valid:
        return missing, []
    return {'Today PnL · ET': f'{today_pnl:,.2f}',
            'Win rate · closed trades': f'{100*wins/count:.1f}%',
            'Avg R realized': f'{sum_r/count:.2f}R' if known_r else MISSING,
            'Max drawdown · realized PnL': f'{drawdown:,.2f} (cash)'}, list(curve)


def policy_value(states, key):
    for persisted_key in (key, key.lower()):
        if persisted_key in states:
            return states[persisted_key], 'system_state'
    # Only these two explicitly authorized nonsecret environment names are read.
    return os.environ.get(key, MISSING), 'env'


def overview(st, db, now):
    states = {r['key']: r['value'] for r in records(db, 'system_state')}
    accounts = payloads(db, 'paper_accounts')
    stats, curve = ledger_statistics(db, now)
    cols = st.columns([1.6, 1, 1])
    with cols[0]:
        if not accounts:
            card(st, 'Equity · PAPER', summary=MISSING)
        for account in accounts:
            card(st, 'Equity · PAPER', [('Account', account.get('account_id')),
                 ('Realized PnL', account.get('realized_pnl')), ('Unrealized PnL', account.get('unrealized_pnl'))],
                 value=account.get('equity', MISSING))
        if curve:
            st.caption('Closed-trade curve · latest 200 points · cumulative realized PnL over full ledger')
            import plotly.graph_objects as go
            fig = go.Figure(go.Scatter(x=[r['Closed at'] for r in curve], y=[r['Cumulative realized PnL'] for r in curve],
                                       mode='lines+markers', line=dict(color='#c8a96a', width=1.5)))
            fig.update_layout(height=220, margin=dict(l=65, r=15, t=10, b=35), paper_bgcolor='rgba(0,0,0,0)',
                              plot_bgcolor='rgba(0,0,0,0)', font=dict(family='IBM Plex Sans', color='#8a877f'),
                              xaxis=dict(gridcolor='#1c1c1f'), yaxis=dict(gridcolor='#1c1c1f', automargin=True))
            st.plotly_chart(fig, width='stretch', theme=None, key='equity-curve')
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
        opened = open_position_rows(db)
        table(st, 'Open positions', opened,
              ['symbol', 'side', 'quantity', 'entry_price', 'stop', 'target', 'unrealized_pnl'])
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
    closed = [{**r, 'record': 'closed', 'close_reason': r.get('reason')} for r in closed_trade_rows(db)]
    opened = [{**r, 'record': 'open'} for r in open_position_rows(db)]
    rows = closed + opened
    table(st, 'Closed trades · latest published', closed, ['trade_id', 'position_id', 'symbol', 'side',
          'entry_price', 'exit_price', 'net_pnl', 'realized_r', 'close_reason', 'exited_at'])
    table(st, 'Open positions', opened, ['position_id', 'symbol', 'side', 'quantity', 'entry_price', 'stop', 'target', 'unrealized_pnl'])
    if not rows:
        return
    selected = st.selectbox('Trade detail', range(len(rows)),
                            format_func=lambda i: display(rows[i].get('trade_id', rows[i].get('position_id'))))
    trade = rows[selected]
    process_link(st, trade.get('run_id'), 'trade-process')
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


def process_link(st, run_id, key):
    def select():
        st.session_state['process_run_id'] = run_id
        st.session_state['luxury_navigation'] = 'Process'
    if run_id:
        if st.button('Open Process · ' + run_id, key=key, on_click=select):
            st.rerun(scope='app')


def decision_rows(db):
    rows = []
    for source in ('runs', 'agent_decisions', 'risk_decisions'):
        page = records(db, source)
        slots = [r['slot_key'] for r in page]
        parents = {r['slot_key']: dict(r) for r in db.execute(
            'SELECT slot_key,run_id,symbol,as_of FROM runs WHERE slot_key IN (' +
            ','.join('?' for _ in slots) + ')', slots)} if slots else {}
        for row in page:
            rows.append({**parents.get(row['slot_key'], {}), **row, 'source': source})
    known = {(r.get('run_id'), r.get('agent_name')): r for r in rows if r['source'] == 'agent_decisions'}
    for snapshot in payloads(db, 'ui_snapshots'):
        for agent in snapshot.get('agents') or ():
            name = agent.get('name') or agent.get('agent_name')
            if not snapshot.get('run_id') or not name:
                continue
            existing = known.get((snapshot['run_id'], name))
            if existing is not None:
                # Missing confidence on an unavailable/failed agent is intentional, not a data gap.
                if existing.get('status') not in ('OK', 'PARTIAL') or existing.get('status') != agent.get('status') or existing.get('symbol') != snapshot.get('symbol'):
                    continue
                for key in ('bias', 'confidence', 'reasoning_summary'):
                    if existing.get(key) is None and agent.get(key) is not None:
                        existing[key] = agent[key]
                continue
            rows.append({**agent, 'agent_name': name, 'source': 'ui_snapshots',
                         'run_id': snapshot.get('run_id'), 'symbol': snapshot.get('symbol'),
                         'as_of': agent.get('timestamp') or snapshot.get('as_of')})
    rows.sort(key=lambda r: r.get('as_of') or '', reverse=True)
    return rows[:ROW_LIMIT]


def decisions(st, db):
    rows = decision_rows(db)
    if rows:
        selected = st.selectbox('Decision process', list(dict.fromkeys(r['run_id'] for r in rows if r.get('run_id'))))
        process_link(st, selected, 'decision-process')
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
            elif page == 'Setups':
                from ui.views.setups import render as render_setups
                render_setups(st, db)
            elif page == 'Process':
                from ui.views.process import render as render_process
                render_process(st, db)
            elif page == 'Trades':
                trades(st, db)
            elif page == 'Decisions':
                decisions(st, db)
    except (sqlite3.DatabaseError, ValueError, TypeError, KeyError) as exc:
        st.error(f'DASHBOARD_ERROR — {type(exc).__name__}: persisted evidence could not be read')
    html(st, '<div class="foot">Paper trading only · observation only</div>')
