"""V2 Phase 9 (F09-T12): small static chart for trade alerts from caller-supplied bar closes. No new deps.

``sparkline_svg`` draws an inline SVG polyline from the closes exactly as given (scaled to the box only; no
smoothing, resampling or invented points). ``chart_png`` renders a PNG with plotly + kaleido ONLY when both are
already installed; otherwise it returns None and the alert carries the SVG alone. No closes -> None, rendered as
the explicit ``NO_DATA`` state.
"""
import importlib.util
import logging

LOG = logging.getLogger(__name__)
WIDTH, HEIGHT, PAD = 240, 48, 3
STROKE = "#c9a227"


def sparkline_svg(closes, *, width=WIDTH, height=HEIGHT):
    """Inline SVG sparkline of ``closes`` or None when there are no bars."""
    values = tuple(closes or ())
    if not values:
        return None
    if len(values) == 1:
        values = values * 2  # one bar is a flat line, not a fabricated trend
    low, high = min(values), max(values)
    span = high - low
    step = (width - 2 * PAD) / (len(values) - 1)
    points = " ".join(
        f"{PAD + i * step:.1f},{(height / 2 if not span else PAD + (high - v) / span * (height - 2 * PAD)):.1f}"
        for i, v in enumerate(values))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" aria-label="Close price sparkline">'
            f'<polyline fill="none" stroke="{STROKE}" stroke-width="1.5" points="{points}"/></svg>')


def kaleido_available():
    return all(importlib.util.find_spec(name) is not None for name in ("plotly", "kaleido"))


def chart_png(closes):
    """PNG bytes via plotly+kaleido when both are installed and closes exist; otherwise None. Never raises."""
    values = tuple(closes or ())
    if not values or not kaleido_available():
        return None
    try:
        import plotly.graph_objects as go
        figure = go.Figure(go.Scatter(y=values, mode="lines", line={"color": STROKE, "width": 2}))
        figure.update_layout(width=480, height=160, margin={"l": 8, "r": 8, "t": 8, "b": 8},
                             paper_bgcolor="#121821", plot_bgcolor="#121821",
                             xaxis={"visible": False}, yaxis={"visible": False})
        return figure.to_image(format="png")
    except Exception as exc:  # noqa: BLE001 - a chart never blocks an alert
        LOG.warning("component=alerts event=CHART_RENDER_FAILED error_type=%s", type(exc).__name__)
        return None


__all__ = ["chart_png", "kaleido_available", "sparkline_svg"]
