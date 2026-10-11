"""Read-only closed-bar evidence and static alert charts; no provider calls."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
import json
import logging
import math
import sqlite3
import threading


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


def evidence_closes(path, symbol, occurred_at, *, limit=48):
    """Read the last N closed 5m bars as of the event; never create or modify a DB."""
    if not path or not symbol or not occurred_at:
        return ()
    if not isinstance(limit, int) or limit <= 0:
        raise ValueError("chart bar limit must be positive")
    try:
        at = datetime.fromisoformat(occurred_at)
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("chart event timestamp must be timezone-aware")
        at = at.astimezone(timezone.utc)
        cutoff = (at - timedelta(minutes=5)).isoformat()
        with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro",
                                     uri=True, timeout=1)) as db:
            rows = db.execute(
                "SELECT payload FROM market_evidence WHERE symbol=? AND timeframe='5m' "
                "AND bar_start<=? AND json_extract(payload, '$.is_closed')=1 "
                "ORDER BY bar_start DESC LIMIT ?", (symbol, cutoff, limit)).fetchall()
        closes = []
        for row in reversed(rows):
            bar = json.loads(row[0])
            start = datetime.fromisoformat(bar["bar_start"])
            close = bar["close"]
            if (bar["symbol"] != symbol or bar["timeframe"] != "5m" or bar["is_closed"] is not True
                    or start.tzinfo is None or start.utcoffset() is None
                    or start + timedelta(minutes=5) > at):
                raise ValueError("chart evidence contract mismatch")
            if isinstance(close, bool) or not isinstance(close, (int, float)) or not math.isfinite(close) or close <= 0:
                raise ValueError("chart close must be a positive finite price")
            closes.append(float(close))
        return tuple(closes)
    except Exception as exc:  # noqa: BLE001 - missing/corrupt evidence cannot block alerts
        LOG.warning("component=alerts event=CHART_READ_FAILED error_type=%s", type(exc).__name__)
        return ()


def chart_png(closes):
    """Render via matplotlib Agg only in the alert worker; failure preserves SVG fallback."""
    values = tuple(closes or ())
    if not values or threading.current_thread().name != "alert-delivery":
        return None
    try:
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure
        figure = Figure(figsize=(4.8, 1.6), dpi=100, facecolor="#121821")
        canvas = FigureCanvasAgg(figure)
        axes = figure.add_axes((0.02, 0.05, 0.96, 0.9), facecolor="#121821")
        axes.plot(values, color=STROKE, linewidth=2)
        axes.set_axis_off()
        output = BytesIO()
        canvas.print_png(output)
        return output.getvalue()
    except Exception as exc:  # noqa: BLE001 - a chart never blocks an alert
        LOG.warning("component=alerts event=CHART_RENDER_FAILED error_type=%s", type(exc).__name__)
        return None


__all__ = ["chart_png", "evidence_closes", "sparkline_svg"]
