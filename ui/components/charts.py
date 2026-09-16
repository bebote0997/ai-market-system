"""Plotly price action, based solely on supplied closed bars and annotations."""
from ui.theme import TOKENS

ALLOWED_ANNOTATIONS = {"BOS", "SWING_HIGH", "SWING_LOW", "SUPPORT", "RESISTANCE", "LIQUIDITY", "SWEEP", "ORDER_BLOCK_POTENTIAL", "ENTRY", "STOP", "TARGET"}
ALLOWED_SOURCES = {"PYTHON", "AI", "PLAN", "USER_CONFIG"}


def price_action_figure(bars, annotations=()):
    if not bars:
        return None
    import plotly.graph_objects as go
    fig = go.Figure(go.Candlestick(x=[b.timestamp for b in bars], open=[b.open for b in bars],
        high=[b.high for b in bars], low=[b.low for b in bars], close=[b.close for b in bars],
        increasing_line_color=TOKENS["success"], decreasing_line_color=TOKENS["danger"], name="15M"))
    for item in annotations:
        kind, source, price = item.get("kind"), item.get("source"), item.get("price")
        if kind not in ALLOWED_ANNOTATIONS or source not in ALLOWED_SOURCES or price is None:
            continue
        prospective = item.get("prospective", False)
        label = f"{kind} · {source} · {'POTENTIAL' if prospective else 'PAPER PLAN' if source == 'PLAN' else 'OBSERVED'}"
        fig.add_hline(y=price, line_dash="dash" if prospective else "solid",
                      line_color=TOKENS["caution"] if prospective else TOKENS["info"],
                      annotation_text=label, annotation_font_color=TOKENS["text-secondary"])
    fig.update_layout(height=480, margin=dict(l=10, r=15, t=15, b=10),
        paper_bgcolor=TOKENS["surface-1"], plot_bgcolor=TOKENS["surface-1"],
        font_color=TOKENS["text-primary"], xaxis_rangeslider_visible=False,
        xaxis=dict(gridcolor=TOKENS["border"]), yaxis=dict(gridcolor=TOKENS["border"], side="right"),
        showlegend=False)
    return fig
