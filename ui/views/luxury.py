"""Main observation screens over published PAPER evidence only (light theme, plain Spanish)."""
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
from ui import i18n_es as es
from ui.adapters import from_persisted_snapshot
from ui.views.dashboard import ROW_LIMIT, payloads, records
from ui.state import dashboard_database
from ui.json_views import details, structured

PAGES = ("Overview", "The Floor", "Process", "Setups", "Trades", "Decisions", "Analysts · soon", "Control")
MISSING = es.MISSING
tr = es.tr


def display(value):
    if value is None or value == "":
        return MISSING
    if isinstance(value, float):
        return f"{value:,.5f}".rstrip("0").rstrip(".") if abs(value) < 10 else f"{value:,.2f}"
    if isinstance(value, str) and len(value) >= 19 and value[4:5] == "-" and value[10:11] == "T":
        try:
            return es.datetime_et(parse_utc(value))
        except (ValueError, TypeError):
            return value
    if isinstance(value, datetime):
        return es.datetime_et(value)
    if isinstance(value, (tuple, list, dict)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def e(value):
    return escape(display(value), quote=True)


class Trusted(str):
    """HTML built here from escaped parts; only these cell values bypass escaping in ``card``."""


def html(st, content):
    st.markdown(f'<div class="tf">{content}</div>', unsafe_allow_html=True)


def content(value):
    return details(value) if isinstance(structured(value), (list, tuple, dict)) else e(value)


def state_chip(value):
    """Translated persisted state; the stored value stays available as a tooltip."""
    if value is None or value == "":
        return Trusted(f'<span class="chip">{escape(MISSING)}</span>')
    raw = str(value)
    kind = ('pos' if raw in ('OK', 'APPROVED', 'HEALTHY', 'CURRENT', 'COMPLETED', 'VALID_SETUP', 'PLAN_READY',
                             'ENABLED', 'RUNNING', 'DELIVERED')
            else 'neg' if raw in ('ERROR', 'FAILED', 'REJECTED', 'RISK_REJECTED', 'STALE_DATA', 'UNHEALTHY',
                                  'PROVIDER_FAILURE', 'CRITICAL')
            else 'warn' if raw in ('DEGRADED', 'WATCH', 'PARTIAL', 'AI_CAUTION', 'PENDING', 'WARNING', 'STOPPED',
                                   'DISABLED', 'PARTIAL_AI_FAILURE')
            else 'info' if raw in ('LONG', 'SHORT', 'OPEN') else '')
    return Trusted(f'<span class="chip {kind}" title="{escape(raw, quote=True)}">{escape(es.status_text(raw))}</span>')


def card(st, title, fields=(), *, value=None, summary=None):
    """Card with optional big value, summary and label/value rows. Only ``Trusted`` values skip escaping."""
    body = f'<div class="big num">{e(value)}</div>' if value is not None else ''
    if summary is not None:
        body += f'<div class="bubble">{content(summary)}</div>'
    body += ''.join(f'<div class="row"><span class="mute">{e(key)}</span>'
                    f'<span>{val if isinstance(val, Trusted) else content(val)}</span></div>' for key, val in fields)
    html(st, f'<div class="card"><h3>{e(title)}</h3>{body}</div>')


def kpi(icon, phrase, value_html='', *, tone='', technical='', hint=''):
    value = f'<div class="value {tone}">{value_html}</div>' if value_html else ''
    return (f'<div class="kpi {tone}"><div class="icon" aria-hidden="true">{icon}</div>'
            f'<div class="phrase">{escape(phrase)}</div>{value}'
            f'<div class="foot2"><span class="tech">{escape(technical)}</span>{es.tip(hint) if hint else ""}</div></div>')


def table(st, title, rows, columns=None, render=None):
    """Spanish column headers; ``render`` maps a data key to an HTML cell renderer."""
    render = render or {}
    if not rows:
        card(st, title, summary=MISSING)
        return
    columns = columns or list(dict.fromkeys(key for row in rows for key in row if key != 'payload'))
    head = ''.join(f'<th title="{escape(key, quote=True)}">{escape(es.column(key))}</th>' for key in columns)
    def cell(row, key):
        if key in render:
            return render[key](row)
        if row.get(key) is None or row.get(key) == '':
            return f'<span class="mute" title="{escape(MISSING)}">—</span>'
        return content(row.get(key))
    body = ''.join('<tr>' + ''.join(f'<td>{cell(row, key)}</td>'
                                    for key in columns) + '</tr>' for row in rows[:ROW_LIMIT])
    html(st, f'<div class="card"><h3>{e(title)}</h3><div class="scroll"><table><thead><tr>{head}</tr></thead>'
             f'<tbody>{body}</tbody></table></div></div>')


def apply(st):
    # One stylesheet block per Streamlit render, after authentication.
    css = (Path(__file__).parents[1] / 'static' / 'theme.css').read_text()
    # Separator before the legacy/detail pages (position depends on the number of main pages).
    child = len(PAGES) + 1
    css += ('\n[data-testid="stSidebar"] [role="radiogroup"] > div:nth-child(%d){margin-top:40px;position:relative;'
            'overflow:visible}\n[data-testid="stSidebar"] [role="radiogroup"] > div:nth-child(%d)::before{content:"%s";'
            'position:absolute;top:-26px;left:10px;color:#556070;font-size:12px;font-weight:700;white-space:nowrap}'
            % (child, child, tr('Legacy / Detail')))
    st.markdown(f'<style>{css}</style>', unsafe_allow_html=True)


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def realized_r_with_reason(trade, position):
    """(R, None) or (None, Spanish reason). R = net PnL / original cash risk, exact position identity only.

    The original stop is ``initial_stop`` once the stop was moved to entry (Day-2); while ``duration_stage`` is unset
    the stop was never moved, so the stored ``stop`` IS the original stop (Day-1 closes such as
    TIME_EXIT_DAY1_BELOW_0_5R), mirroring execution.trade_duration.
    """
    if number(trade.get('realized_r')):
        return trade['realized_r'], None
    if not position or trade.get('position_id') != position.get('position_id'):
        return None, 'sin datos de la posición original'
    stop = position.get('initial_stop')
    if stop is None and position.get('duration_stage') is None:
        stop = position.get('stop')
    if not number(stop):
        return None, es.STOP_UNKNOWN
    entry, quantity, multiplier, pnl = (position.get('entry_price'), trade.get('quantity', position.get('quantity')),
                                        position.get('contract_multiplier'), trade.get('net_pnl'))
    if not all(number(v) for v in (entry, quantity, multiplier, pnl)) or quantity <= 0 or multiplier <= 0:
        return None, 'faltan datos de la operación (precio, tamaño o multiplicador)'
    risk = abs(entry-stop) * quantity * multiplier
    if risk <= 0:
        return None, es.STOP_UNKNOWN
    return pnl/risk, None


def realized_r(trade, position):
    return realized_r_with_reason(trade, position)[0]


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
    result = []
    for r in rows:
        position = positions.get(r.get('position_id'))
        value, reason = realized_r_with_reason(r, position)
        extra = {'realized_r': value, 'realized_r_reason': reason}
        for key in ('planned_entry', 'stop', 'target', 'initial_stop'):
            if position and position.get(key) is not None and r.get(key) is None:
                extra['position_' + key] = position[key]
        result.append({**r, **extra})
    return result


EMPTY_STATS = {'trades': 0, 'wins': 0, 'total_pnl': None, 'today_pnl': None, 'max_drawdown': None,
               'avg_r': None, 'avg_r_reason': None}


def trade_statistics(trades, now):
    """Closed-trade metrics use the complete ledger, never a bounded page."""
    if not trades or not all(number(t.get('net_pnl')) and t.get('exited_at') for t in trades):
        return dict(EMPTY_STATS), []
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
    known = all(number(t.get('realized_r')) for t in trades)
    return {'trades': len(trades), 'wins': sum(t['net_pnl'] > 0 for t in trades), 'total_pnl': total,
            'today_pnl': today_pnl, 'max_drawdown': drawdown,
            'avg_r': sum(t['realized_r'] for t in trades)/len(trades) if known else None,
            'avg_r_reason': None if known else MISSING}, curve


def ledger_statistics(db, now):
    """Stream scalar ledger fields; exact metrics, only the latest 200 curve points."""
    if db is None or not db.execute("SELECT 1 FROM sqlite_master WHERE name='closed_trades'").fetchone():
        return dict(EMPTY_STATS), []
    has_positions = db.execute("SELECT 1 FROM sqlite_master WHERE name='paper_positions'").fetchone()
    fields = ('position_id', 'entry_price', 'stop', 'initial_stop', 'duration_stage', 'quantity', 'contract_multiplier')
    position = ','.join(f"json_extract(p.payload,'$.{key}')" for key in fields) if has_positions else ','.join('NULL' for _ in fields)
    join = " LEFT JOIN paper_positions p ON p.position_id=json_extract(t.payload,'$.position_id')" if has_positions else ""
    cursor = db.execute("SELECT json_extract(t.payload,'$.net_pnl'),json_extract(t.payload,'$.exited_at'),"
                        "json_extract(t.payload,'$.realized_r'),json_extract(t.payload,'$.quantity'),"
                        "json_type(t.payload,'$.quantity')," + position + " FROM closed_trades t" + join +
                        " ORDER BY julianday(json_extract(t.payload,'$.exited_at')),t.rowid")
    count = wins = unknown = 0
    total = peak = drawdown = today_pnl = sum_r = 0.0
    valid = True
    reason = None
    today = now.astimezone(ZoneInfo('America/New_York')).date()
    curve = deque(maxlen=ROW_LIMIT)
    for pnl, stamp, stored_r, trade_quantity, quantity_type, *position_values in cursor:
        position = dict(zip(fields, position_values))
        trade = {'net_pnl': pnl, 'realized_r': stored_r, 'position_id': position.get('position_id')}
        if quantity_type is not None:
            trade['quantity'] = trade_quantity
        r_value, r_reason = realized_r_with_reason(trade, position if has_positions else None)
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
            unknown += 1
            reason = reason or r_reason
        curve.append({'Closed at': at, 'Cumulative realized PnL': total})
    if not count or not valid:
        return dict(EMPTY_STATS), []
    if unknown:
        reason = f"{reason or MISSING} en {unknown} de {count} operaciones"
    return {'trades': count, 'wins': wins, 'total_pnl': total, 'today_pnl': today_pnl, 'max_drawdown': drawdown,
            'avg_r': None if unknown else sum_r/count, 'avg_r_reason': reason}, list(curve)


def policy_value(states, key):
    for persisted_key in (key, key.lower()):
        if persisted_key in states:
            return states[persisted_key], 'system_state'
    # Only these two explicitly authorized nonsecret environment names are read.
    return os.environ.get(key, MISSING), 'env'


def health_dot(status):
    raw = str(status or '')
    kind = '' if raw in ('HEALTHY', 'OK', 'CURRENT', 'RUNNING') else 'warn' if raw in ('DEGRADED', 'STOPPED', 'DISABLED') \
        else 'neg' if raw in ('FAILED', 'UNHEALTHY', 'ERROR', 'STALE_DATA') else 'off'
    return Trusted(f'<span class="dot {kind}"></span><span title="{escape(raw, quote=True)}">'
                   f'{escape(es.status_text(status))}</span>')


DEMO_ROUTING_ENV = 'AI_FLOOR_BROKER_DEMO_ROUTING'
DEMO_DEFAULT_EPOCH = datetime(2026, 10, 18, 22, 25, tzinfo=timezone.utc)  # == demo_epoch.DEFAULT_DEMO_EPOCH_UTC


def demo_unavailable_api():
    """Routing is ON but the demo module is unavailable: the demo screens with "sin datos", never PAPER numbers."""
    def snapshot(_now):
        return {'epoch': DEMO_DEFAULT_EPOCH, 'stats': None, 'curve': [], 'open_rows': [], 'closed_rows': [],
                'nav': None, 'balance': None, 'currency': None, 'capital': {'start': None, 'change': None},
                'stale': False, 'trades_error': 'unavailable'}
    return {'epoch': lambda: DEMO_DEFAULT_EPOCH, 'snapshot': snapshot,
            'progress': lambda _now: (DEMO_DEFAULT_EPOCH, None, None)}


def demo_api():
    """The OANDA demo view (lazy, guarded) while broker-demo routing is ON; None => PAPER screens unchanged. The flag
    is read here, independently of the demo module: ON + module unavailable => demo screens with "sin datos"."""
    if os.environ.get(DEMO_ROUTING_ENV) != '1':
        return None
    try:
        from execution.demo_dashboard import demo_epoch, demo_progress, demo_snapshot
    except Exception:  # noqa: BLE001 - routing ON: never fall back to PAPER numbers
        return demo_unavailable_api()
    return {'epoch': demo_epoch, 'snapshot': demo_snapshot, 'progress': demo_progress}


def overview(st, db, now):
    demo = demo_api()
    if demo is not None:
        demo_overview(st, db, now, demo['snapshot'](now))
        return
    states = {r['key']: r['value'] for r in records(db, 'system_state')}
    accounts = payloads(db, 'paper_accounts')
    stats, curve = ledger_statistics(db, now)
    account = accounts[0] if accounts else {}
    equity = account.get('equity')
    total = stats['total_pnl'] if stats['trades'] else account.get('realized_pnl')
    rate = f"{100*stats['wins']/stats['trades']:.0f}% ganadoras" if stats['trades'] else ''
    tiles = [
        kpi('💰', 'Capital de tu cuenta de práctica', escape(es.money(equity)), technical='Capital (equity)',
            hint=es.TOOLTIPS['equity']),
        kpi('📈' if not number(total) or total >= 0 else '📉', es.pnl_since_start(total),
            escape(es.money(total, signed=True)), tone=es.tone(total), technical='Ganancia realizada (PnL)',
            hint=es.TOOLTIPS['pnl']),
        kpi('🎯', es.wins_phrase(stats['wins'], stats['trades']), escape(rate), tone='teal',
            technical='Tasa de acierto (win rate)', hint=es.TOOLTIPS['win_rate']),
        kpi('🛡️', es.drawdown_phrase(stats['max_drawdown']), tone='orange', technical='Máxima caída (max drawdown)',
            hint=es.TOOLTIPS['drawdown']),
        kpi('📅', es.today_phrase(stats['today_pnl']), escape(es.money(stats['today_pnl'], signed=True)),
            tone=es.tone(stats['today_pnl']), technical='Resultado de hoy (ET)', hint=es.TOOLTIPS['today']),
        kpi('⚖️', es.avg_r_phrase(stats['avg_r'], stats['avg_r_reason']),
            escape(f"{stats['avg_r']:+.2f} R") if number(stats['avg_r']) else '', tone=es.tone(stats['avg_r']),
            technical='Promedio R realizado', hint=es.TOOLTIPS['avg_r']),
    ]
    for start in (0, 3):
        for col, tile in zip(st.columns(3), tiles[start:start+3]):
            with col:
                html(st, tile)
    left, right = st.columns([1.7, 1])
    with left:
        if curve:
            curve_chart(st, curve, tr('Closed-trade curve'),
                        tr('Closed-trade curve · latest 200 points · cumulative realized PnL over full ledger'))
        else:
            card(st, tr('Closed-trade curve'), summary='Aún no hay operaciones cerradas para dibujar la curva.')
        opened = open_position_rows(db)
        table(st, tr('Open positions'), opened, ['symbol', 'side', 'quantity', 'entry_price', 'stop', 'target',
                                                 'unrealized_pnl'], render=POSITION_CELLS)
    with right:
        side_panel(st, now, states)


def curve_chart(st, curve, title, caption):
    import plotly.graph_objects as go
    fig = go.Figure(go.Scatter(x=[r['Closed at'].astimezone(es.ET) for r in curve],
                               y=[r['Cumulative realized PnL'] for r in curve], mode='lines+markers',
                               line=dict(color='#2459d6', width=2.5, shape='hv'),
                               marker=dict(size=7, color='#2459d6'), fill='tozeroy',
                               fillcolor='rgba(36,89,214,0.08)',
                               hovertemplate='%{x|%d %b %H:%M} ET<br>$%{y:,.2f}<extra></extra>'))
    fig.add_hline(y=0, line_color='#98a2b3', line_width=1)
    fig.update_layout(height=260, margin=dict(l=65, r=15, t=10, b=40), paper_bgcolor='#ffffff',
                      plot_bgcolor='#ffffff', font=dict(family='Inter, Segoe UI, system-ui, sans-serif',
                                                        color='#556070', size=12),
                      xaxis=dict(gridcolor='#eef2f6', tickformat='%d/%m %H:%M'),
                      yaxis=dict(gridcolor='#eef2f6', automargin=True, tickprefix='$', title='Ganancia acumulada'))
    with st.container(key='curve-card'):
        html(st, f'<h3 style="margin:0">{escape(title)}</h3><div class="small">{escape(caption)} · hora ET'
                 '</div>')
        st.plotly_chart(fig, width='stretch', theme=None, key='equity-curve')


def side_panel(st, now, states):
    sessions = ', '.join(es.SESSIONS.get(name, name) for name in session_names(now)) or tr('Outside sessions')
    card(st, tr('Session'), [('Ahora mismo', sessions), (es.column('scheduler'), health_dot(states.get('scheduler'))),
         (es.column('last_success'), states.get('last_success')), (es.column('heartbeat'), states.get('heartbeat'))])
    path = Path(os.environ.get('AI_FLOOR_DB_PATH', 'data/runtime/trading_floor.db'))
    with dashboard_database(os.environ.get('AI_FLOOR_HEALTH_DB_PATH', str(path.with_name('system_health.db')))) as health_db:
        health = payloads(health_db, 'component_health')
    rows = [(es.COMPONENTS.get(name, name), health_dot(r.get('status'))) for r in health
            for name in [r.get('component', r.get('component_id', MISSING))]]
    rows += [(es.COMPONENTS[key], es.status_text(states.get(key)) if states.get(key) else MISSING)
             for key in ('market_data_provider', 'ai_provider')]
    rows += [(es.COMPONENTS['last_run'], display(states.get('last_run')))]
    card(st, tr('System'), rows)
    card(st, tr('Active policies'), [(es.POLICIES[key], f'{display(value)} · origen: {source}')
         for key in ('V2_PLANNER_POLICY', 'V2_OPPOSITE_SETUP_POLICY')
         for value, source in [policy_value(states, key)]])


DEMO_NO_DATA = 'sin datos'
DEMO_ARCHIVE_NOTE = ('Los datos PAPER anteriores al demo están archivados y no cuentan aquí '
                     '(ver Historial en Control).')


DEMO_TRADES_ERRORS = {'incomplete': 'totales incompletos: OANDA tiene más operaciones que el límite de páginas',
                      'deadline': 'OANDA tardó demasiado en responder',
                      'unavailable': 'la vista del demo no está disponible'}


def demo_no_data(demo):
    """"sin datos (<reason>)" for a demo snapshot without stats (failed, slow or incomplete OANDA read)."""
    return f"{DEMO_NO_DATA} ({DEMO_TRADES_ERRORS.get(demo.get('trades_error'), 'no se pudo leer OANDA')})"


def demo_title(epoch):
    return f'Demo OANDA (práctica) · desde {es.datetime_et(epoch)}'


def demo_money(value, signed=False):
    return es.money(value, signed=signed) if number(value) else DEMO_NO_DATA


def demo_pnl_phrase(total):
    if not number(total):
        return f'Resultado desde el inicio del demo: {DEMO_NO_DATA}'
    if total == 0:
        return 'Desde el inicio del demo: sin ganancias ni pérdidas'
    if total < 0:
        return f'Perdiste {es.money(-total)} desde el inicio del demo'
    return f'Ganaste {es.money(total)} desde el inicio del demo'


def demo_overview(st, db, now, demo):
    """Overview while broker-demo routing is ON: every number comes from the OANDA practice account since the demo
    epoch; a failed read is "sin datos", never PAPER numbers."""
    states = {r['key']: r['value'] for r in records(db, 'system_state')}
    stats = demo['stats']
    known = stats is not None
    total = stats['total_pnl'] if known else None
    trades_count = stats['trades'] if known else 0
    rate = f"{100*stats['wins']/trades_count:.0f}% ganadoras" if known and trades_count else ''
    start = demo['capital']['start']
    stale = ' · datos anteriores' if demo.get('stale') else ''
    html(st, f'<div class="card"><h3>{escape(demo_title(demo["epoch"]))}</h3>'
             f'<div class="small">{escape(DEMO_ARCHIVE_NOTE)}</div></div>')
    tiles = [
        kpi('💰', 'Capital de la cuenta demo OANDA', escape(demo_money(demo['nav'])),
            technical=f'NAV · inicio del demo {demo_money(start)}{stale}', hint=es.TOOLTIPS['equity']),
        kpi('📈' if not number(total) or total >= 0 else '📉', demo_pnl_phrase(total),
            escape(demo_money(total, signed=True)), tone=es.tone(total), technical='Ganancia realizada (PnL)',
            hint=es.TOOLTIPS['pnl']),
        kpi('🎯', es.wins_phrase(stats['wins'], trades_count) if known else f'Operaciones del demo: {DEMO_NO_DATA}',
            escape(rate), tone='teal', technical='Tasa de acierto (win rate)', hint=es.TOOLTIPS['win_rate']),
        kpi('🛡️', f'La mayor caída del resultado de operaciones cerradas: {demo_money(stats and stats["max_drawdown"])}',
            tone='orange', technical='Máxima caída (max drawdown)', hint=es.TOOLTIPS['drawdown']),
        kpi('📅', es.today_phrase(stats['today_pnl']) if known else f'Hoy: {DEMO_NO_DATA}',
            escape(demo_money(stats and stats['today_pnl'], signed=True)), tone=es.tone(stats and stats['today_pnl']),
            technical='Resultado de hoy (ET)', hint=es.TOOLTIPS['today']),
        kpi('⚖️', es.avg_r_phrase(stats['avg_r'], stats['avg_r_reason']) if known
            else f'Promedio vs riesgo: {DEMO_NO_DATA}',
            escape(f"{stats['avg_r']:+.2f} R") if known and number(stats['avg_r']) else '',
            tone=es.tone(stats and stats['avg_r']), technical='Promedio R realizado', hint=es.TOOLTIPS['avg_r']),
    ]
    for begin in (0, 3):
        for col, tile in zip(st.columns(3), tiles[begin:begin+3]):
            with col:
                html(st, tile)
    left, right = st.columns([1.7, 1])
    with left:
        title = 'Curva del demo OANDA (operaciones cerradas)'
        if demo['curve']:
            curve_chart(st, demo['curve'], title, 'Resultado realizado acumulado desde el inicio del demo')
        else:
            card(st, title, summary='Aún no hay operaciones cerradas del demo.' if known else demo_no_data(demo))
        if known:
            table(st, tr('Open positions'), demo['open_rows'], ['symbol', 'side', 'quantity', 'entry_price', 'stop',
                                                                'target', 'unrealized_pnl'], render=POSITION_CELLS)
        else:
            card(st, tr('Open positions'), summary=DEMO_NO_DATA)
    with right:
        side_panel(st, now, states)


def money_cell(key):
    def cell(row):
        value = row.get(key)
        if not number(value):
            return f'<span class="mute">{escape(es.NO_PRICE if key == "unrealized_pnl" else MISSING)}</span>'
        return f'<b class="{es.tone(value)}">{escape(es.money(value, signed=True))}</b>'
    return cell


def r_cell(row):
    value, reason = row.get('realized_r'), row.get('realized_r_reason')
    if not number(value):
        return f'<span class="mute">{escape(reason or MISSING)}</span>'
    return (f'<span class="{es.tone(value)}">{escape(es.r_phrase(value))}</span>'
            f'<div class="small">{value:+.2f} R</div>')


def reason_cell(row):
    raw = row.get('close_reason') or row.get('reason')
    return f'<span title="{escape(str(raw or ""), quote=True)}">{escape(es.close_reason_text(raw))}</span>'


POSITION_CELLS = {'side': lambda row: state_chip(row.get('side')), 'unrealized_pnl': money_cell('unrealized_pnl')}
TRADE_CELLS = {'side': lambda row: state_chip(row.get('side')), 'net_pnl': money_cell('net_pnl'),
               'realized_r': r_cell, 'close_reason': reason_cell}


def floor(st, db, symbols):
    snapshots = {r['symbol']: r for r in payloads(db, 'ui_snapshots')}
    positions = payloads(db, 'paper_positions', statuses=('OPEN',))
    cols = st.columns(max(1, len(symbols)))
    for col, symbol in zip(cols, symbols):
        with col:
            snapshot = snapshots.get(symbol)
            vm = from_persisted_snapshot(snapshot) if snapshot is not None else None
            opened = [p for p in positions if p.get('symbol') == symbol]
            card(st, f'Mesa · {symbol}', [('Estado', state_chip(vm.state if vm else None)),
                 ('Oportunidad', state_chip(vm.setup_status if vm else None)),
                 ('Dirección', state_chip(vm.setup_side) if vm and vm.setup_side else MISSING),
                 (es.column('freshness'), state_chip(vm.freshness if vm else None)),
                 ('Última actualización', vm.as_of if vm else None),
                 ('Operación abierta', ', '.join(es.status_text(p.get('side')) + ' · abierta' for p in opened)
                  or tr('No open position'))])
    for symbol in symbols:
        snapshot = snapshots.get(symbol)
        if snapshot is None:
            card(st, f'{symbol} · {tr("Agent rooms")} / {tr("Risk Engine")}', summary=MISSING)
            continue
        vm = from_persisted_snapshot(snapshot)
        html(st, f'<h3 style="margin:8px 0 0">{escape(symbol)} · {escape(tr("Agent rooms"))}</h3>')
        cols = st.columns(3)
        for index, agent in enumerate(vm.agents):
            with cols[index % 3]:
                ok = agent.status != 'MISSING'
                card(st, es.AGENTS.get(agent.name, agent.name), [('Estado', state_chip(agent.status)),
                     ('Tendencia', state_chip(agent.bias) if ok else MISSING),
                     ('Confianza', agent.confidence), ('Hora', agent.timestamp)],
                     summary=agent.reasoning_summary or MISSING)
        decision = [(k, v) for k, v in vars(vm.risk_decision).items() if k != 'status'] if vm.risk_decision else []
        card(st, f'{symbol} · {tr("Risk Engine")}', [('Estado', state_chip(vm.risk_status))]
             + [(es.column(key), value) for key, value in decision])


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


def slippage(trade, plan):
    """Planned entry vs fill. Pips only for EURUSD (1 pip = 0.0001); other symbols show the price difference."""
    planned = next((v for v in (trade.get('planned_entry'), trade.get('position_planned_entry'),
                                plan.get('planned_entry'), plan.get('entry')) if number(v)), None)
    fill = trade.get('entry_price')
    if not number(planned) or not number(fill):
        return [('Entrada planeada', MISSING), ('Entrada real (fill)', fill), ('Diferencia', 'sin entrada planeada guardada')]
    diff = fill - planned
    worse = diff > 0 if trade.get('side') == 'LONG' else diff < 0 if trade.get('side') == 'SHORT' else None
    text = f'{diff:+.5f}'.rstrip('0').rstrip('.') if abs(diff) < 10 else f'{diff:+,.2f}'
    if trade.get('symbol') == 'EURUSD':
        text += f' ({diff/0.0001:+.1f} pips)'
    verdict = 'sin diferencia' if diff == 0 else 'en contra' if worse else 'a favor' if worse is False else ''
    return [('Entrada planeada', planned), ('Entrada real (fill)', fill),
            ('Diferencia', f'{text} · {verdict}' if verdict else text)]


def trade_label(row):
    side = es.status_text(row.get('side'))
    if row['record'] == 'closed':
        return (f"{row.get('symbol', '')} · {side} · {es.money(row.get('net_pnl'), signed=True)} · "
                f"{display(row.get('exited_at'))}")
    return f"{row.get('symbol', '')} · {side} · abierta desde {display(row.get('opened_at'))}"


def demo_trades(st, demo):
    """Trades while broker-demo routing is ON: OANDA demo trades (closed + open) since the epoch only."""
    html(st, f'<div class="card"><h3>{escape(demo_title(demo["epoch"]))}</h3>'
             f'<div class="small">{escape(DEMO_ARCHIVE_NOTE)}</div></div>')
    if demo['stats'] is None:
        card(st, 'Operaciones del demo OANDA', summary=demo_no_data(demo))
        return
    closed = list(reversed(demo['closed_rows']))
    table(st, 'Operaciones cerradas del demo OANDA', closed, ['exited_at', 'symbol', 'side', 'quantity',
          'entry_price', 'exit_price', 'net_pnl', 'realized_r'], render=TRADE_CELLS)
    table(st, tr('Open positions'), demo['open_rows'], ['opened_at', 'symbol', 'side', 'quantity', 'entry_price',
          'stop', 'target', 'unrealized_pnl'], render=POSITION_CELLS)


def trades(st, db):
    demo = demo_api()
    if demo is not None:
        demo_trades(st, demo['snapshot'](datetime.now(timezone.utc)))
        return
    closed = [{**r, 'record': 'closed', 'close_reason': r.get('reason')} for r in closed_trade_rows(db)]
    opened = [{**r, 'record': 'open'} for r in open_position_rows(db)]
    rows = closed + opened
    table(st, tr('Closed trades · latest published'), closed, ['exited_at', 'symbol', 'side', 'entry_price',
          'exit_price', 'net_pnl', 'realized_r', 'close_reason'], render=TRADE_CELLS)
    table(st, tr('Open positions'), opened, ['opened_at', 'symbol', 'side', 'quantity', 'entry_price', 'stop', 'target',
          'unrealized_pnl'], render=POSITION_CELLS)
    if not rows:
        return
    selected = st.selectbox(tr('Trade detail'), range(len(rows)), format_func=lambda i: trade_label(rows[i]))
    trade = rows[selected]
    process_link(st, trade.get('run_id'), 'trade-process')
    plan = linked_plan(db, trade)
    left, right = st.columns(2)
    with left:
        stop = plan.get('stop', trade.get('position_initial_stop', trade.get('position_stop', trade.get('stop'))))
        target = plan.get('target', trade.get('position_target', trade.get('target')))
        entry = next((v for v in (plan.get('entry'), plan.get('planned_entry'), trade.get('planned_entry'),
                                  trade.get('position_planned_entry')) if number(v)), None)
        card(st, tr('Plan'), [('Entrada planeada', entry),
             ('Stop loss (límite de pérdida)', stop), ('Meta de ganancia (TP)', target),
             ('Ganancia posible vs riesgo (R:R)', f"1 : {display(plan['risk_reward'])}" if number(plan.get('risk_reward')) else None)])
        rows_html = ''.join(f'<div class="row"><span class="mute">{e(k)}</span><span>{e(v)}</span></div>'
                            for k, v in slippage(trade, plan))
        html(st, f'<div class="card"><h3>{escape(tr("Slippage · plan vs fill"))}</h3>{rows_html}'
                 f'<div class="meta">{es.tip(es.TOOLTIPS["slippage"])}</div></div>')
    with right:
        pnl = trade.get('net_pnl') if trade['record'] == 'closed' else trade.get('unrealized_pnl')
        fields = [('Símbolo', escape(str(trade.get('symbol') or MISSING))), ('Dirección', state_chip(trade.get('side'))),
                  ('Entrada real (fill)', e(trade.get('entry_price')))]
        if trade['record'] == 'closed':
            fields += [('Salida', e(trade.get('exit_price'))), ('Resultado', money_cell('net_pnl')(trade)),
                       ('Resultado vs riesgo', r_cell(trade)), ('Por qué se cerró', reason_cell(trade)),
                       ('Cerrada', e(trade.get('exited_at')))]
        else:
            fields += [('Va ganando / perdiendo', money_cell('unrealized_pnl')(trade)),
                       ('Abierta', e(trade.get('opened_at')))]
        big = (f'<div class="big num {es.tone(pnl)}">{escape(es.money(pnl, signed=True))}</div>' if number(pnl)
               else f'<div class="big mute" style="font-size:22px">{escape(es.NO_PRICE if trade["record"] == "open" else MISSING)}</div>')
        rows_html = ''.join(f'<div class="row"><span class="mute">{escape(k)}</span><span>{v}</span></div>' for k, v in fields)
        html(st, f'<div class="card"><h3>{escape(tr("Execution"))}</h3>{big}{rows_html}</div>')


def process_link(st, run_id, key):
    def select():
        st.session_state['process_run_id'] = run_id
        st.session_state['luxury_navigation'] = 'Process'
    if run_id:
        if st.button(tr('Open Process · ') + run_id, key=key, on_click=select):
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


SOURCES = {'runs': 'Ciclo de análisis', 'agent_decisions': 'Agente de IA', 'risk_decisions': 'Motor de riesgo',
           'ui_snapshots': 'Foto del ciclo'}
DECISION_CELLS = {
    'status': lambda row: state_chip(row.get('status')), 'bias': lambda row: state_chip(row.get('bias')) if row.get('bias') else f'<span class="mute" title="{escape(MISSING)}">—</span>',
    'source': lambda row: f'<span title="{escape(row["source"], quote=True)}">{escape(SOURCES.get(row["source"], row["source"]))}</span>',
    'agent_name': lambda row: escape(es.AGENTS.get(row.get('agent_name'), row.get('agent_name') or '—')),
    'run_id': lambda row: (f'<span class="small" title="{escape(row["run_id"], quote=True)}">{escape(row["run_id"][:8])}…</span>'
                           if row.get('run_id') else '—'),
}


def since_epoch(rows, epoch):
    """Rows whose ``as_of`` is at/after ``epoch`` (missing or unparsable timestamps are left out)."""
    kept = []
    for row in rows:
        try:
            if parse_utc(row.get('as_of')) >= epoch:
                kept.append(row)
        except (ValueError, TypeError):
            continue
    return kept


def decisions(st, db):
    rows = decision_rows(db)
    demo = demo_api()
    if demo is not None:
        rows = since_epoch(rows, demo['epoch']())
    if rows:
        selected = st.selectbox(tr('Decision process'), list(dict.fromkeys(r['run_id'] for r in rows if r.get('run_id'))))
        process_link(st, selected, 'decision-process')
    rows.sort(key=lambda r: r.get('as_of') or '', reverse=True)
    table(st, tr('Decisions · newest first'), rows[:ROW_LIMIT], ['as_of', 'symbol', 'source', 'agent_name', 'status',
          'bias', 'confidence', 'reasoning_summary', 'reason', 'run_id'], render=DECISION_CELLS)


def render(st, page, symbols):
    now = datetime.now(timezone.utc)
    badge = f'<span class="ex">{escape(tr("SAMPLE / DEMO"))}</span>' if st.session_state.get('sample_mode') else ''
    html(st, f'<div class="top"><div><div class="eyebrow">{escape(es.long_date_et(now))}</div>'
             f'<h1>{escape(es.PAGE_TITLES.get(page, page))}</h1></div>{badge}</div>'
             f'<span class="banner">🧪 {escape(tr("PAPER · REAL EXECUTION DISABLED · NO REAL MONEY"))}</span>')
    try:
        if page == 'Analysts · soon':
            card(st, tr('Post-trade analysis department · soon'), summary=tr(
                'Future analysts will review closed trades and propose improvements. Proposals require owner approval; nothing is automatic.'))
            return
        if page == 'Control':
            from ui.views.control import render as render_control
            render_control(st)
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
        st.error(f'{tr("DASHBOARD_ERROR")} ({type(exc).__name__}).')
    finally:
        html(st, f'<div class="foot">{escape(tr("Paper trading only · observation only"))}</div>')
