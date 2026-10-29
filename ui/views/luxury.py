"""Main observation screens (dark + gold Floor theme, plain Spanish). OANDA demo mode: OANDA numbers and only
cycles / events at/after the demo epoch (``dashboard.since_epoch``); PAPER mode: the published PAPER evidence."""
import base64
from datetime import datetime, timezone
from html import escape
import json
import math
import os
from pathlib import Path
import sqlite3
from collections import deque
from functools import lru_cache
from zoneinfo import ZoneInfo

from runtime.scheduler import session_names
from storage.codec import parse_utc
from ui import i18n_es as es
from ui.components.glass import day_progress, sparkline_svg
from ui.adapters import from_persisted_snapshot
from ui.views.dashboard import (FAR_FUTURE, FIRST_CYCLE_WAIT, PAPER_SIZING_KEYS, ROW_LIMIT, epoch_or_far, is_current, payloads,
                                records, since_epoch)
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


def kpi(icon, phrase, value_html='', *, tone='', technical='', hint='', extra=''):
    """``extra``: trusted HTML under the value (e.g. ``sparkline_svg`` of a real series)."""
    value = f'<div class="value {tone}">{value_html}</div>' if value_html else ''
    return (f'<div class="kpi {tone}"><div class="icon" aria-hidden="true">{icon}</div>'
            f'<div class="phrase">{escape(phrase)}</div>{value}{extra}'
            f'<div class="foot2"><span class="tech">{escape(technical)}</span>{es.tip(hint) if hint else ""}</div></div>')


def pnl_sparkline(curve):
    """Sparkline of the REAL cumulative realized P&L series (``curve`` rows); '' without >= 2 finite points."""
    values = [row.get('Cumulative realized PnL') for row in curve or () if isinstance(row, dict)]
    last = next((v for v in reversed(values) if number(v)), None)
    color = '#4cf0a2' if last is None or last >= 0 else '#ff8099'
    return sparkline_svg(values, color, label='Evolución del P&L realizado acumulado')


def kpi_grid(st, tiles):
    """All KPI tiles in one CSS grid: 3 columns on desktop, 2 on tablets, 1 on phones (theme.css ``.kpis``)."""
    html(st, f'<div class="kpis">{"".join(tiles)}</div>')


def table(st, title, rows, columns=None, render=None, empty=None):
    """Spanish column headers; ``render`` maps a data key to an HTML cell renderer; ``empty``: text with no rows."""
    render = render or {}
    if not rows:
        card(st, title, summary=empty or MISSING)
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
            'position:absolute;top:-26px;left:10px;color:#a9b8b6;font-size:12px;font-weight:700;white-space:nowrap}'
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


def demo_unavailable_api():
    """Routing is ON but the demo module is unavailable: the demo screens with "sin datos", never PAPER numbers."""
    def epoch():
        from ui.views.dashboard import demo_since as dashboard_demo_since
        return epoch_or_far(dashboard_demo_since)

    def snapshot(_now):
        return {'epoch': epoch(), 'stats': None, 'curve': [], 'open_rows': [], 'closed_rows': [],
                'nav': None, 'balance': None, 'currency': None, 'capital': {'start': None, 'change': None},
                'stale': False, 'trades_error': 'unavailable'}
    return {'epoch': epoch, 'snapshot': snapshot,
            'progress': lambda _now: (epoch(), None, None)}


def demo_results_on():
    """Routing ON or ``AI_FLOOR_RESULTS_SOURCE=OANDA_DEMO`` (DEC-OANDA-ONLY-1). Read here, independently of the demo
    module; never raises (unreadable => routing flag only)."""
    try:
        from execution.results_source import demo_results
        return demo_results(os.environ)
    except Exception:  # noqa: BLE001
        return os.environ.get(DEMO_ROUTING_ENV) == '1'


def paper_hidden():
    """OANDA-demo-only mode: PAPER ledger / P&L sections are hidden (DEC-OANDA-ONLY-1)."""
    try:
        from execution.results_source import oanda_only
        return oanda_only(os.environ)
    except Exception:  # noqa: BLE001
        return False


def demo_api():
    """The OANDA demo view (lazy, guarded) while broker-demo routing is ON or the results source is OANDA_DEMO; None
    => PAPER screens unchanged. The flags are read here, independently of the demo module: ON + module unavailable =>
    demo screens with "sin datos"."""
    if not demo_results_on():
        return None
    try:
        from execution.demo_dashboard import demo_epoch, demo_progress, demo_snapshot
    except Exception:  # noqa: BLE001 - routing ON: never fall back to PAPER numbers
        return demo_unavailable_api()
    return {'epoch': demo_epoch, 'snapshot': demo_snapshot, 'progress': demo_progress}


BANNER = Path(__file__).resolve().parents[2] / 'runtime' / 'assets' / 'floor_banner.png'
VERSION = 'V2.1'
FLOOR_BUTTON = 'Abrir La mesa animada (Floor)'


@lru_cache(maxsize=1)
def banner_uri():
    """The repo's static Floor banner as a data URI (read-only asset; no request); None if it cannot be read."""
    try:
        return 'data:image/png;base64,' + base64.b64encode(BANNER.read_bytes()).decode('ascii')
    except OSError:
        return None


def hero_subline(demo, day):
    """"V2.1 · OANDA Demo · Día N de 30" (no counted day yet => "Esperando el primer ciclo")."""
    if not demo:
        return f'{VERSION} · práctica interna'
    known = isinstance(day, int) and not isinstance(day, bool) and day >= 1
    return f'{VERSION} · OANDA Demo · {es.demo_day_text(day) if known else FIRST_CYCLE_WAIT}'


def start_hero(st, now, *, demo, day=None):
    """Start page header with the Floor look: banner (or the serif gold wordmark), sub line, safety line."""
    uri = banner_uri()
    art = (f'<img src="{uri}" alt="AI Trading Floor">' if uri
           else f'<p class="wordmark">{escape(tr("AI TRADING FLOOR"))}</p>')
    badge = f'<span class="ex">{escape(tr("SAMPLE / DEMO"))}</span>' if st.session_state.get('sample_mode') else ''
    html(st, f'<div class="hero">{art}<div class="body"><div class="eyebrow">{escape(es.long_date_et(now))}</div>'
             f'<h1>{escape(es.PAGE_TITLES["Overview"])}</h1><div class="sub">{escape(hero_subline(demo, day))}</div>'
             f'<span class="banner">🧪 {escape(tr("PAPER · REAL EXECUTION DISABLED · NO REAL MONEY"))}</span>'
             f'{badge}</div></div>')
    open_floor_button(st)


def open_floor_button(st):
    """Same navigation as the ``?page=floor`` deep link: select the Floor page, then rerun the whole app."""
    def select():
        from ui.views.floor_view import PAGE
        st.session_state['luxury_navigation'] = PAGE
    if st.button(FLOOR_BUTTON, key='open-floor', type='primary', on_click=select):
        st.rerun(scope='app')


def overview(st, db, now):
    demo = demo_api()
    if demo is not None:
        day = demo_day(demo, now)
        start_hero(st, now, demo=True, day=day)
        demo_overview(st, db, now, demo['snapshot'](now), day=day)
        return
    start_hero(st, now, demo=False)
    states = {r['key']: r['value'] for r in records(db, 'system_state')}
    accounts = payloads(db, 'paper_accounts')
    stats, curve = ledger_statistics(db, now)
    account = accounts[0] if accounts else {}
    equity = account.get('equity')
    total = stats['total_pnl'] if stats['trades'] else account.get('realized_pnl')
    rate = f"{100*stats['wins']/stats['trades']:.0f}%" if stats['trades'] else ''
    tiles = [
        kpi('💰', 'Equity · práctica interna', escape(es.money(equity)), technical='Balance + P&L de posiciones abiertas',
            hint=es.TOOLTIPS['equity']),
        kpi('📈' if not number(total) or total >= 0 else '📉', es.pnl_since_start(total),
            escape(es.money(total, signed=True)), tone=es.tone(total), technical='P&L neto · operaciones cerradas',
            hint=es.TOOLTIPS['pnl'], extra=pnl_sparkline(curve)),
        kpi('🎯', es.wins_phrase(stats['wins'], stats['trades']), escape(rate), tone='teal',
            technical='Ganadoras / operaciones cerradas', hint=es.TOOLTIPS['win_rate']),
        kpi('🛡️', es.drawdown_phrase(stats['max_drawdown']), tone='orange', technical='Mayor caída desde un máximo',
            hint=es.TOOLTIPS['drawdown']),
        kpi('📅', es.today_phrase(stats['today_pnl']), escape(es.money(stats['today_pnl'], signed=True)),
            tone=es.tone(stats['today_pnl']), technical='P&L realizado · día de Nueva York', hint=es.TOOLTIPS['today']),
        kpi('⚖️', es.avg_r_phrase(stats['avg_r'], stats['avg_r_reason']),
            escape(f"{stats['avg_r']:+.2f} R") if number(stats['avg_r']) else '', tone=es.tone(stats['avg_r']),
            technical='Media del R realizado por operación', hint=es.TOOLTIPS['avg_r']),
    ]
    kpi_grid(st, tiles)
    left, right = st.columns([1.7, 1])
    with left:
        if curve:
            curve_chart(st, curve, tr('Closed-trade curve'),
                        tr('Closed-trade curve · latest 200 points · cumulative realized PnL over full ledger'))
        else:
            card(st, tr('Closed-trade curve'), summary='Aún no hay operaciones cerradas.')
        opened = open_position_rows(db)
        table(st, tr('Open positions'), opened, ['symbol', 'side', 'quantity', 'entry_price', 'stop', 'target',
                                                 'unrealized_pnl'], render=POSITION_CELLS, empty='Sin posiciones abiertas.')
    with right:
        side_panel(st, now, states)


def curve_chart(st, curve, title, caption):
    import plotly.graph_objects as go
    fig = go.Figure(go.Scatter(x=[r['Closed at'].astimezone(es.ET) for r in curve],
                               y=[r['Cumulative realized PnL'] for r in curve], mode='lines+markers',
                               line=dict(color='#d9b45a', width=2.5, shape='hv'),
                               marker=dict(size=7, color='#19e3d3'), fill='tozeroy',
                               fillcolor='rgba(217,180,90,0.10)',
                               hovertemplate='%{x|%d %b %H:%M} ET<br>$%{y:,.2f}<extra></extra>'))
    fig.add_hline(y=0, line_color='#7f9593', line_width=1)
    fig.update_layout(height=260, margin=dict(l=65, r=15, t=10, b=40), paper_bgcolor='rgba(0,0,0,0)',
                      plot_bgcolor='rgba(8,10,16,0.35)', font=dict(family='Inter, Segoe UI, system-ui, sans-serif',
                                                        color='#c9d4d2', size=13),
                      hoverlabel=dict(bgcolor='#0d0d0e', bordercolor='#d9b45a', font=dict(color='#e8eceb')),
                      xaxis=dict(gridcolor='rgba(255,255,255,0.08)', linecolor='rgba(255,255,255,0.16)',
                                 tickformat='%d/%m %H:%M'),
                      yaxis=dict(gridcolor='rgba(255,255,255,0.08)', linecolor='rgba(255,255,255,0.16)',
                                 zerolinecolor='rgba(255,255,255,0.16)', automargin=True,
                                 tickprefix='$', title='P&L realizado acumulado'))
    with st.container(key='curve-card'):
        html(st, f'<h3 style="margin:0">{escape(title)}</h3><div class="small">{escape(caption)} · hora ET'
                 '</div>')
        st.plotly_chart(fig, width='stretch', theme=None, key='equity-curve')


def cycle_time(states, key, since=None):
    """``last_success`` / ``last_run`` with ONE rule: a value before the demo epoch (or unparsable in demo mode) is
    not the current cycle => "Esperando el primer ciclo"."""
    value = states.get(key)
    if since is not None and not is_current(value, since):
        return FIRST_CYCLE_WAIT
    return display(value)


def side_panel(st, now, states, since=None):
    """``since``: demo epoch (None in PAPER mode). The heartbeat is live and always shown as stored."""
    sessions = ', '.join(es.SESSIONS.get(name, name) for name in session_names(now)) or tr('Outside sessions')
    card(st, tr('Session'), [('Ahora mismo', sessions), (es.column('scheduler'), health_dot(states.get('scheduler'))),
         (es.column('last_run'), cycle_time(states, 'last_run', since)),
         (es.column('last_success'), cycle_time(states, 'last_success', since)),
         (es.column('heartbeat'), display(states.get('heartbeat')))])
    path = Path(os.environ.get('AI_FLOOR_DB_PATH', 'data/runtime/trading_floor.db'))
    with dashboard_database(os.environ.get('AI_FLOOR_HEALTH_DB_PATH', str(path.with_name('system_health.db')))) as health_db:
        health = payloads(health_db, 'component_health')
    rows = [(es.COMPONENTS.get(name, name), health_dot(r.get('status'))) for r in health
            for name in [r.get('component', r.get('component_id', MISSING))]]
    rows += [(es.COMPONENTS[key], es.status_text(states.get(key)) if states.get(key) else MISSING)
             for key in ('market_data_provider', 'ai_provider')]
    card(st, tr('System'), rows)
    card(st, tr('Active policies'), [(es.POLICIES[key], f'{display(value)} · origen: {"configuración del servidor" if source == "env" else es.readable(source)}')
         for key in ('V2_PLANNER_POLICY', 'V2_OPPOSITE_SETUP_POLICY')
         for value, source in [policy_value(states, key)]])


DEMO_NO_DATA = 'sin datos'
DEMO_ARCHIVE_NOTE = 'Los datos de la práctica anterior están archivados y no cuentan aquí.'


DEMO_TRADES_ERRORS = {'incomplete': 'totales incompletos: OANDA tiene más operaciones que el límite de páginas',
                      'deadline': 'OANDA tardó demasiado en responder',
                      'unavailable': 'la vista del demo no está disponible'}


def demo_no_data(demo):
    """"sin datos (<reason>)" for a demo snapshot without stats (failed, slow or incomplete OANDA read)."""
    return f"{DEMO_NO_DATA} ({DEMO_TRADES_ERRORS.get(demo.get('trades_error'), 'no se pudo leer OANDA')})"


def demo_since(demo):
    """Epoch filter of the demo screens: None in PAPER mode (``demo`` None); an unreadable epoch => nothing passes."""
    from ui.views.dashboard import demo_since as dashboard_demo_since
    return None if demo is None else dashboard_demo_since()


def demo_day(demo, now):
    """Uncapped demo day counted from the EPOCH's New York date (``ui.demo_clock``) or None; never raises."""
    from ui.demo_clock import demo_day_from_epoch
    try:
        return demo_day_from_epoch(demo_since(demo), now)
    except Exception:  # noqa: BLE001 - no day counter rather than a broken screen
        return None


def demo_title(epoch):
    epoch = epoch_or_far(lambda: epoch)
    if epoch == FAR_FUTURE:
        return 'Demo OANDA (práctica) · inicio desconocido'
    return f'Demo OANDA (práctica) · desde {es.datetime_et(epoch)}'


def demo_money(value, signed=False):
    return es.money(value, signed=signed) if number(value) else DEMO_NO_DATA


NO_TRADES_YET = 'sin operaciones aún'


def demo_pnl_phrase(total):
    if not number(total):
        return f'P&L realizado: {DEMO_NO_DATA}'
    return 'P&L realizado · desde el inicio del demo'


def unrealized_total(open_rows):
    """Sum of the open positions' unrealized P&L; 0.0 with no open position; None when any value is unknown."""
    values = [row.get('unrealized_pnl') for row in open_rows or ()]
    return float(sum(values)) if all(number(v) for v in values) else None


def mute(text):
    return f'<span class="mute">{escape(text)}</span>'


def demo_kpis(demo):
    """Start page tiles: real OANDA practice numbers only. A failed read => "sin datos (motivo)"; no closed trade yet
    => "sin operaciones aún" for win rate / R / drawdown (never a blank tile)."""
    stats = demo['stats']
    known = stats is not None
    reason = demo_no_data(demo)
    count = stats['trades'] if known else 0
    traded = known and count > 0
    stale = ' · datos anteriores' if demo.get('stale') else ''
    total = stats['total_pnl'] if known else None
    today = stats['today_pnl'] if known else None
    open_rows = demo['open_rows'] if known else []
    unrealized = unrealized_total(open_rows) if known else None
    avg_r = stats['avg_r'] if traded else None

    def money_value(value):
        return escape(demo_money(value, signed=True)) if number(value) else mute(reason if not known else DEMO_NO_DATA)

    def by_trades(value_html):
        return value_html if traded else mute(NO_TRADES_YET if known else reason)

    rate = escape(f"{100 * stats['wins'] / count:.0f}%") if traded else ''
    r_value = (escape(f'{avg_r:+.2f} R') if number(avg_r) else mute(stats['avg_r_reason'] or MISSING)) if traded else ''
    drawdown = escape(demo_money(stats['max_drawdown'])) if traded else ''
    return [
        kpi('💰', 'NAV (equity) · OANDA demo', escape(demo_money(demo['nav'])),
            technical=f"Capital inicial {demo_money(demo['capital']['start'])}{stale}", hint=es.TOOLTIPS['equity']),
        kpi('🏦', 'Balance · OANDA demo', escape(demo_money(demo['balance'])),
            technical=f'Saldo sin contar las posiciones abiertas{stale}'),
        kpi('📈' if not number(total) or total >= 0 else '📉', demo_pnl_phrase(total), money_value(total),
            tone=es.tone(total), technical='Suma del P&L neto de las operaciones cerradas', hint=es.TOOLTIPS['pnl'],
            extra=pnl_sparkline(demo.get('curve')) if number(total) else ''),
        kpi('📊', 'P&L no realizado', money_value(unrealized), tone=es.tone(unrealized),
            technical='Posiciones abiertas a precio de mercado', hint=es.TOOLTIPS['unrealized']),
        kpi('📅', 'P&L de hoy (ET)', money_value(today), tone=es.tone(today),
            technical='P&L realizado · día de Nueva York', hint=es.TOOLTIPS['today']),
        kpi('📂', 'Posiciones abiertas', escape(str(len(open_rows))) if known else mute(reason), tone='teal',
            technical='Abiertas ahora en la cuenta demo'),
        kpi('✅', 'Operaciones cerradas', escape(str(count)) if known else mute(reason), tone='teal',
            technical='Desde el inicio del demo'),
        kpi('🎯', es.wins_phrase(stats['wins'], count) if traded else 'Acierto', by_trades(rate), tone='teal',
            technical='Ganadoras / operaciones cerradas', hint=es.TOOLTIPS['win_rate']),
        kpi('⚖️', 'R medio (expectativa)', by_trades(r_value), tone=es.tone(avg_r),
            technical='Media del R realizado por operación', hint=es.TOOLTIPS['avg_r']),
        kpi('🛡️', 'Caída máxima (P&L realizado)', by_trades(drawdown), tone='orange',
            technical='Mayor caída desde un máximo', hint=es.TOOLTIPS['drawdown']),
    ]


def demo_overview(st, db, now, demo, day=None):
    """Overview while broker-demo routing is ON: every number comes from the OANDA practice account since the demo
    epoch; a failed read is "sin datos", never PAPER numbers. Shows "Día N de 30" and the daily-loss status."""
    states = {r['key']: r['value'] for r in records(db, 'system_state')}
    known = demo['stats'] is not None
    html(st, f'<div class="card"><h3>{escape(demo_title(demo["epoch"]))}</h3>'
             f'<div class="small"><b>{escape(es.demo_day_text(day) if day else FIRST_CYCLE_WAIT)}</b> · '
             f'{escape(DEMO_ARCHIVE_NOTE)}</div>{day_progress(day, es.DEMO_LENGTH_DAYS)}</div>')
    kpi_grid(st, demo_kpis(demo))
    left, right = st.columns([1.7, 1])
    with left:
        title = 'Curva de operaciones cerradas · OANDA demo'
        if demo['curve']:
            curve_chart(st, demo['curve'], title, 'P&L realizado acumulado desde el inicio del demo')
        else:
            card(st, title, summary='Aún no hay operaciones cerradas en el demo.' if known else demo_no_data(demo))
        if known:
            table(st, tr('Open positions'), demo['open_rows'], ['symbol', 'side', 'quantity', 'entry_price', 'stop',
                  'target', 'unrealized_pnl'], render=POSITION_CELLS, empty='Sin posiciones abiertas.')
        else:
            card(st, tr('Open positions'), summary=demo_no_data(demo))
    with right:
        card(st, es.DAILY_LOSS_TITLE, es.daily_loss_rows(demo.get('daily_loss')))
        side_panel(st, now, states, epoch_or_far(lambda: demo['epoch']))


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
    return f'<span class="{es.tone(value)}">{escape(es.r_phrase(value))}</span>'


def reason_cell(row):
    raw = row.get('close_reason') or row.get('reason')
    return f'<span title="{escape(str(raw or ""), quote=True)}">{escape(es.close_reason_text(raw))}</span>'


def demo_r_cell(row):
    """R of a routed demo trade (P/L / (|entry - initial stop| * units)); "—" when the initial stop is unknown."""
    value = row.get('realized_r')
    if not number(value):
        return f'<span class="mute" title="{escape(row.get("realized_r_reason") or "", quote=True)}">{es.R_UNKNOWN}</span>'
    return r_cell(row)


POSITION_CELLS = {'side': lambda row: state_chip(row.get('side')), 'unrealized_pnl': money_cell('unrealized_pnl')}
TRADE_CELLS = {'side': lambda row: state_chip(row.get('side')), 'net_pnl': money_cell('net_pnl'),
               'realized_r': r_cell, 'close_reason': reason_cell}
DEMO_TRADE_CELLS = {**TRADE_CELLS, 'realized_r': demo_r_cell}


def paused(symbol):
    """``runtime.symbol_pause.is_paused`` (read only, same source as floor_payload.symbol_states); never raises."""
    return es.symbol_status(symbol, ()) == es.PAUSED_TEXT


def current_snapshots(db, since):
    """Latest ``ui_snapshots`` per symbol; in demo mode only those whose ``as_of`` is at/after the epoch."""
    return {r['symbol']: r for r in since_epoch(payloads(db, 'ui_snapshots'), since, ('as_of',))}


def floor(st, db, symbols):
    since = demo_since(demo_api())
    snapshots = current_snapshots(db, since)
    hidden = paper_hidden()  # DEC-OANDA-ONLY-1: no PAPER position on the desks
    positions = [] if hidden else payloads(db, 'paper_positions', statuses=('OPEN',))
    cols = st.columns(max(1, len(symbols)))
    for col, symbol in zip(cols, symbols):
        with col:
            if paused(symbol):
                card(st, f'Mesa · {symbol}', summary=f'{symbol} · {es.PAUSED_TEXT}')
                continue
            snapshot = snapshots.get(symbol)
            if snapshot is None and since is not None:
                card(st, f'Mesa · {symbol}', summary=FIRST_CYCLE_WAIT)
                continue
            vm = from_persisted_snapshot(snapshot) if snapshot is not None else None
            opened = [p for p in positions if p.get('symbol') == symbol]
            card(st, f'Mesa · {symbol}', [('Estado', state_chip(vm.state if vm else None)),
                 ('Setup', state_chip(vm.setup_status if vm else None)),
                 ('Lado', state_chip(vm.setup_side) if vm and vm.setup_side else MISSING),
                 (es.column('freshness'), state_chip(vm.freshness if vm else None)),
                 ('Última actualización', vm.as_of if vm else None),
                 ('Posición abierta', 'ver La mesa animada / Trades (demo OANDA)' if hidden else
                  ', '.join(es.status_text(p.get('side')) + ' · abierta' for p in opened) or tr('No open position'))])
    for symbol in symbols:
        snapshot = snapshots.get(symbol)
        if paused(symbol):
            continue
        if snapshot is None:
            card(st, f'{symbol} · {tr("Agent rooms")} / {tr("Risk Engine")}',
                 summary=FIRST_CYCLE_WAIT if since is not None else MISSING)
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
        decision = ([(k, v) for k, v in vars(vm.risk_decision).items() if k not in ('status', 'schema_version')]
                    if vm.risk_decision else [])
        if since is not None:  # the PAPER account's sizing (equity at decision ~10k) is not the OANDA demo's
            decision = [(k, v) for k, v in decision if k not in PAPER_SIZING_KEYS]
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
        return [('Entrada planificada', MISSING), ('Ejecución', fill), ('Deslizamiento', 'sin entrada planificada guardada')]
    diff = fill - planned
    worse = diff > 0 if trade.get('side') == 'LONG' else diff < 0 if trade.get('side') == 'SHORT' else None
    text = f'{diff:+.5f}'.rstrip('0').rstrip('.') if abs(diff) < 10 else f'{diff:+,.2f}'
    if trade.get('symbol') == 'EURUSD':
        text += f' ({diff/0.0001:+.1f} pips)'
    verdict = '0 · sin deslizamiento' if diff == 0 else 'en contra' if worse else 'a favor' if worse is False else ''
    return [('Entrada planificada', planned), ('Ejecución', fill),
            ('Deslizamiento', f'{text} · {verdict}' if verdict else text)]


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
        card(st, 'Operaciones · OANDA demo', summary=demo_no_data(demo))
        return
    closed = list(reversed(demo['closed_rows']))
    table(st, 'Operaciones cerradas · OANDA demo', closed, ['exited_at', 'symbol', 'side', 'quantity',
          'entry_price', 'exit_price', 'net_pnl', 'realized_r'], render=DEMO_TRADE_CELLS,
          empty='Aún no hay operaciones cerradas en el demo.')
    table(st, tr('Open positions'), demo['open_rows'], ['opened_at', 'symbol', 'side', 'quantity', 'entry_price',
          'stop', 'target', 'unrealized_pnl'], render=POSITION_CELLS, empty='Sin posiciones abiertas.')


def trades(st, db):
    demo = demo_api()
    if demo is not None:
        demo_trades(st, demo['snapshot'](datetime.now(timezone.utc)))
        return
    closed = [{**r, 'record': 'closed', 'close_reason': r.get('reason')} for r in closed_trade_rows(db)]
    opened = [{**r, 'record': 'open'} for r in open_position_rows(db)]
    rows = closed + opened
    table(st, tr('Closed trades · latest published'), closed, ['exited_at', 'symbol', 'side', 'entry_price',
          'exit_price', 'net_pnl', 'realized_r', 'close_reason'], render=TRADE_CELLS,
          empty='Aún no hay operaciones cerradas.')
    table(st, tr('Open positions'), opened, ['opened_at', 'symbol', 'side', 'quantity', 'entry_price', 'stop', 'target',
          'unrealized_pnl'], render=POSITION_CELLS, empty='Sin posiciones abiertas.')
    if not rows:
        return
    selected = st.selectbox(tr('Trade detail'), range(len(rows)), format_func=lambda i: trade_label(rows[i]))
    trade = rows[selected]
    process_link(st, trade.get('run_id'), 'trade-process', 'ciclo de esta operación')
    plan = linked_plan(db, trade)
    left, right = st.columns(2)
    with left:
        stop = plan.get('stop', trade.get('position_initial_stop', trade.get('position_stop', trade.get('stop'))))
        target = plan.get('target', trade.get('position_target', trade.get('target')))
        entry = next((v for v in (plan.get('entry'), plan.get('planned_entry'), trade.get('planned_entry'),
                                  trade.get('position_planned_entry')) if number(v)), None)
        card(st, tr('Plan'), [('Entrada planificada', entry),
             ('Stop loss (SL)', stop), ('Take profit (TP)', target),
             ('R:R', f"1 : {display(plan['risk_reward'])}" if number(plan.get('risk_reward')) else None)])
        rows_html = ''.join(f'<div class="row"><span class="mute">{e(k)}</span><span>{e(v)}</span></div>'
                            for k, v in slippage(trade, plan))
        html(st, f'<div class="card"><h3>{escape(tr("Slippage · plan vs fill"))}</h3>{rows_html}'
                 f'<div class="meta">{es.tip(es.TOOLTIPS["slippage"])}</div></div>')
    with right:
        pnl = trade.get('net_pnl') if trade['record'] == 'closed' else trade.get('unrealized_pnl')
        fields = [('Símbolo', escape(str(trade.get('symbol') or MISSING))), ('Lado', state_chip(trade.get('side'))),
                  ('Ejecución', e(trade.get('entry_price')))]
        if trade['record'] == 'closed':
            fields += [('Salida', e(trade.get('exit_price'))), ('P&L neto', money_cell('net_pnl')(trade)),
                       ('R realizado', r_cell(trade)), ('Motivo de salida', reason_cell(trade)),
                       ('Cerrada', e(trade.get('exited_at')))]
        else:
            fields += [('P&L no realizado', money_cell('unrealized_pnl')(trade)),
                       ('Abierta', e(trade.get('opened_at')))]
        big = (f'<div class="big num {es.tone(pnl)}">{escape(es.money(pnl, signed=True))}</div>' if number(pnl)
               else f'<div class="big mute" style="font-size:22px">{escape(es.NO_PRICE if trade["record"] == "open" else MISSING)}</div>')
        rows_html = ''.join(f'<div class="row"><span class="mute">{escape(k)}</span><span>{v}</span></div>' for k, v in fields)
        html(st, f'<div class="card"><h3>{escape(tr("Execution"))}</h3>{big}{rows_html}</div>')


def process_link(st, run_id, key, label=None):
    """Button to the run on Proceso; ``label`` (e.g. ``es.cycle_label``) instead of the raw run UUID."""
    def select():
        st.session_state['process_run_id'] = run_id
        st.session_state['luxury_navigation'] = 'Process'
    if run_id:
        if st.button(tr('Open Process · ') + (label or es.short_id(run_id)), key=key, on_click=select):
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


def run_labels(rows):
    """{run_id: "Ciclo del 26 oct 03:00 ET · EURUSD"} from the rows' own ``as_of`` / ``symbol`` (first row wins)."""
    labels = {}
    for row in rows:
        if row.get('run_id') and row['run_id'] not in labels:
            labels[row['run_id']] = es.cycle_label(row.get('as_of'), row.get('symbol'))
    return labels


def decisions(st, db):
    rows = decision_rows(db)
    since = demo_since(demo_api())
    rows = since_epoch(rows, since, ('as_of',))
    if not rows and since is not None:
        card(st, tr('Decisions · newest first'), summary=FIRST_CYCLE_WAIT)
        return
    if rows:
        labels = run_labels(rows)
        selected = st.selectbox(tr('Decision process'), list(labels), format_func=lambda run: labels[run])
        process_link(st, selected, 'decision-process', labels.get(selected))
    rows.sort(key=lambda r: r.get('as_of') or '', reverse=True)
    table(st, tr('Decisions · newest first'), rows[:ROW_LIMIT], ['as_of', 'symbol', 'source', 'agent_name', 'status',
          'bias', 'confidence', 'reasoning_summary', 'reason', 'run_id'], render=DECISION_CELLS)


def render(st, page, symbols):
    now = datetime.now(timezone.utc)
    badge = f'<span class="ex">{escape(tr("SAMPLE / DEMO"))}</span>' if st.session_state.get('sample_mode') else ''
    if page != 'Overview':  # the start page draws its own Floor-style hero (start_hero)
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
