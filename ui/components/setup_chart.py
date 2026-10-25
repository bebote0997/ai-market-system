"""Persisted 15m OHLC or pre-demo 5m close prices; never synthesize candles."""
from datetime import datetime
from html import escape
import math
from zoneinfo import ZoneInfo

import plotly.graph_objects as go

BG, GRID, GOLD, UP, DOWN = '#0b0b0c', '#1c1c1f', '#c8a96a', '#9fb89a', '#c48a7f'
ET = ZoneInfo('America/New_York')


def build_setup_figure(bars, plan, symbol):
    if not bars:
        raise ValueError('chart unavailable: bars not persisted')
    close_only = all(bar.get('source') == 'candle_cache' and bar.get('timeframe') == '5m' for bar in bars)
    times, candles = [], []
    for bar in bars:
        if not close_only and str(bar.get('timeframe', '15m')).lower() not in ('15m', '15min'):
            raise ValueError('15m bars required')
        raw = bar.get('timestamp') or bar.get('start') or bar.get('bar_start')
        at = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        if at.tzinfo is None:
            raise ValueError('bar timezone required')
        keys = ('close',) if close_only else ('open', 'high', 'low', 'close')
        if any(isinstance(bar[key], bool) for key in keys):
            raise ValueError('numeric prices required')
        values = [float(bar[key]) for key in keys]
        if not all(math.isfinite(v) and v > 0 for v in values):
            raise ValueError('finite OHLC required')
        if not close_only and (values[1] < max(values[0], values[3]) or values[2] > min(values[0], values[3]) or values[1] < values[2]):
            raise ValueError('invalid OHLC range')
        times.append(at.astimezone(ET))
        candles.append(values)
    ordered = sorted(zip(times, candles), key=lambda item: item[0])
    times, candles = zip(*ordered)
    frame_label = '5m closes · no OHLC' if close_only else '15m'
    fig = go.Figure(go.Scatter(x=times, y=[v[0] for v in candles], mode='lines',
        line=dict(color=GOLD), name='5m close') if close_only else go.Candlestick(x=times,
        open=[v[0] for v in candles], high=[v[1] for v in candles],
        low=[v[2] for v in candles], close=[v[3] for v in candles],
        increasing_line_color=UP, increasing_fillcolor=UP,
        decreasing_line_color=DOWN, decreasing_fillcolor=DOWN, name='15m'))
    entry, stop, target = (plan.get(key) for key in ('entry', 'stop', 'target'))
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (entry, stop, target)):
        quantity = plan.get('quantity')
        multiplier = plan.get('contract_multiplier')
        # Money labels need persisted quantity AND contract multiplier; never assume 1.
        known = all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0
                    for v in (quantity, multiplier))
        risk, reward = abs(entry-stop), abs(target-entry)
        scale = quantity * multiplier if known else None
        risk_label = f'${risk*scale:,.2f} risk' if known else f'{risk:,.2f} price distance'
        reward_label = f'${reward*scale:,.2f} reward' if known else f'{reward:,.2f} price distance'
        rr = reward/risk if risk else None
        ratio = '1:3' if rr is not None and math.isclose(rr, 3) else (f'1:{rr:.2f}' if rr is not None else 'R:R unavailable')
        for level, label, color in ((entry, 'ENTRY', GOLD),
                (stop, f'STOP LOSS · {risk_label}', DOWN),
                (target, f'TAKE PROFIT {ratio} · {reward_label}', UP)):
            fig.add_hline(y=level, line_dash='dash', line_color=color,
                          annotation_text=f'{label} · {level:,.2f}', annotation_font_color=color)
        for a, b, color in ((entry, stop, DOWN), (entry, target, UP)):
            fig.add_hrect(y0=min(a,b), y1=max(a,b), fillcolor=color, opacity=0.12,
                          line_width=0, layer='below')
    else:
        fig.add_annotation(text='Plan levels not persisted', xref='paper', yref='paper', x=0, y=1, showarrow=False)
    fig.update_layout(title=dict(text=f'{escape(symbol)} — {frame_label}<br>'
        f'<span style="font-size:12px">Last: {candles[-1][-1]:,.2f}<br>'
        f'{times[0]:%d %b %H:%M} → {times[-1]:%d %b %H:%M} ET</span>',
        x=0.02, xanchor='left', font=dict(family='Cormorant Garamond', size=18)),
        paper_bgcolor=BG, plot_bgcolor=BG, font=dict(family='IBM Plex Sans', color='#d8d4cc'),
        xaxis=dict(gridcolor=GRID, rangeslider=dict(visible=False), title='America/New_York'),
        yaxis=dict(gridcolor=GRID, side='right'), height=600,
        margin=dict(l=30, r=40, t=110, b=45), showlegend=False)
    return fig


# Dashboard-only restyle in the app's dark + gold Floor palette (the name is historical). build_setup_figure (shared
# with the setup e-mail PNG) is left unchanged.
LIGHT = {'bg': '#0d1416', 'grid': '#1f2c2e', 'text': '#c9d4d2', 'entry': '#d9b45a', 'up': '#3ddc84', 'down': '#ff4d6d'}
LABELS_ES = (('TAKE PROFIT', 'TAKE PROFIT (TP)'), ('STOP LOSS', 'STOP LOSS'), ('ENTRY', 'ENTRADA'),
             ('price distance', 'de distancia de precio'), (' risk', ' de riesgo'), (' reward', ' de beneficio'),
             ('R:R unavailable', 'R:R no disponible'), ('Plan levels not persisted', 'Niveles del plan no guardados'),
             ('5m closes · no OHLC', 'cierres de 5 min · sin velas completas'), ('Last:', 'Último:'),
             ('5m close', 'cierre 5 min'))


MONTHS_ES = dict(zip(('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'),
                     ('ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic')))


def _es(text):
    import re
    for old, new in LABELS_ES:
        text = text.replace(old, new)
    return re.sub(r'\b(' + '|'.join(MONTHS_ES) + r')\b', lambda m: MONTHS_ES[m.group(1)], text)


def light_setup_figure(fig):
    colors = {GOLD: LIGHT['entry'], UP: LIGHT['up'], DOWN: LIGHT['down']}
    trace = fig.data[0]
    if trace.type == 'scatter':
        trace.line.color = LIGHT['entry']
        trace.name = _es(trace.name)
    else:
        for side, color in ((trace.increasing, LIGHT['up']), (trace.decreasing, LIGHT['down'])):
            side.line.color = color
            side.fillcolor = color
    for shape in fig.layout.shapes:
        if shape.type == 'line':
            shape.line.color = colors.get(shape.line.color, shape.line.color)
        elif shape.type == 'rect':
            shape.fillcolor = colors.get(shape.fillcolor, shape.fillcolor)
            shape.opacity = 0.08
    for annotation in fig.layout.annotations:
        annotation.text = _es(annotation.text)
        if annotation.font.color in colors:
            annotation.font.color = colors[annotation.font.color]
    fig.update_layout(title=dict(text=_es(fig.layout.title.text),
                                 font=dict(family='Inter, Segoe UI, system-ui, sans-serif', size=17, color='#d9b45a')),
                      paper_bgcolor=LIGHT['bg'], plot_bgcolor=LIGHT['bg'],
                      font=dict(family='Inter, Segoe UI, system-ui, sans-serif', color=LIGHT['text']),
                      xaxis=dict(gridcolor=LIGHT['grid'], title='Hora de Nueva York (ET)', tickformat='%d/%m %H:%M'),
                      yaxis=dict(gridcolor=LIGHT['grid']))
    return fig
