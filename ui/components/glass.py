"""Glass UI building blocks: pure HTML builders (no Streamlit, no I/O). Every value is html-escaped; nothing here
invents data — a missing agent / series / day renders "Sin datos aún" or nothing at all.

Agent names and roles come from ``ui.agent_names`` (single source); the agent colours repeat the ``COL`` table of
``ui/static/floor/floor.html`` (that asset is never edited, so the values are mirrored here).
"""
from html import escape
import math

from ui.agent_names import display_name, long_role, short_role

# Mirror of ``const COL={...}`` in ui/static/floor/floor.html (the Floor's agent colours; keep both in sync).
AGENT_COLORS = {
    "Structure": "#a77bff", "Liquidity": "#4a7dff", "Macro": "#ffb020", "Risk": "#ff3d9a",
    "Reviewer": "#2ee59d", "Conflict": "#ff7a1a", "RiskEng": "#ff2e4d", "Exec": "#39ff88",
}
LEFT_AGENTS = ("Structure", "Liquidity", "Macro", "Risk")
RIGHT_AGENTS = ("Reviewer", "Conflict", "RiskEng", "Exec")
NO_DATA_TITLE = "Sin datos aún"
WAITING_FALLBACK = "Esperando el primer ciclo"
ANALYSIS_LIMIT = 220
DAYS_TOTAL = 30


def esc(value):
    return escape(str(value), quote=True)


def truncate(text, limit=ANALYSIS_LIMIT):
    """``text`` cut to at most ``limit`` characters (word boundary when possible) with «…»; None/empty => ''."""
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit - 1]
    space = cut.rfind(" ")
    return (cut[:space] if space > limit // 2 else cut).rstrip(" ,.;:·") + "…"


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def agent_card(agent_id, view, *, waiting_text=None, example=False):
    """One agent card from the REAL ``payload["agents"][agent_id]`` (``None`` => "Sin datos aún")."""
    color = AGENT_COLORS.get(agent_id, "#d9b45a")
    name = display_name(agent_id)
    initial = name[:1].upper() or "?"
    head = (f'<div class="gl-hd"><span class="gl-avatar" aria-hidden="true">{esc(initial)}</span>'
            f'<div class="gl-who"><div class="gl-name">{esc(name)}</div>'
            f'<div class="gl-role" title="{esc(long_role(agent_id))}">{esc(short_role(agent_id))}</div></div>')
    badge = '<span class="gl-ex">EJEMPLO</span>' if example else ''
    if not isinstance(view, dict):
        return (f'<article class="gl-agent gl-empty" style="--c:{color}">{head}'
                f'<span class="gl-status">{esc(NO_DATA_TITLE)}</span></div>'
                f'<div class="gl-bubble gl-muted">{esc(waiting_text or WAITING_FALLBACK)}</div></article>')
    status = view.get("status")
    status_html = f'<span class="gl-status">{esc(status)}</span>' if status else ''
    analysis = truncate(view.get("analysis"))
    bubble = (f'<div class="gl-bubble">{esc(analysis)}</div>' if analysis
              else f'<div class="gl-bubble gl-muted">{esc(NO_DATA_TITLE)}</div>')
    facts = []
    bias = view.get("bias")
    if bias in ("LONG", "SHORT"):
        facts.append(f'<span class="gl-bias {"pos" if bias == "LONG" else "neg"}">{esc(bias)}</span>')
    confidence = view.get("confidence")
    if finite(confidence):
        pct = max(0.0, min(100.0, 100.0 * confidence))
        facts.append(f'<span class="gl-conf" title="Confianza"><span class="gl-meter" aria-hidden="true">'
                     f'<i style="width:{pct:.0f}%"></i></span>{pct:.0f}%</span>')
    if view.get("as_of"):
        facts.append(f'<span class="gl-time">{esc(view["as_of"])}</span>')
    foot = f'<div class="gl-facts">{"".join(facts)}</div>' if facts else ''
    return (f'<article class="gl-agent" style="--c:{color}">{head}{status_html}</div>{badge}{bubble}{foot}'
            '</article>')


def agents_panel(agent_ids, agents, *, title, waiting_text=None, example_fields=()):
    """``<details class="gl-agents">``: closed by default (phones show only the summary row until tapped); on
    desktop theme.css shows the content and hides the summary. No JavaScript."""
    agents = agents or {}
    example = set(example_fields or ())
    cards = "".join(agent_card(agent_id, agents.get(agent_id), waiting_text=waiting_text,
                               example=f"agents.{agent_id}" in example) for agent_id in agent_ids)
    names = ", ".join(display_name(agent_id) for agent_id in agent_ids)
    return (f'<details class="gl-agents"><summary><span class="gl-sum-title">{esc(title)} · {len(agent_ids)} agentes'
            f'</span><span class="gl-sum-names">{esc(names)}</span><span class="gl-sum-hint">toca para ver</span>'
            f'</summary><div class="gl-agents-body">{cards}</div></details>')


def sparkline_svg(values, color, *, label="Evolución", width=120, height=36):
    """Inline SVG sparkline of a REAL series (non-finite points dropped); '' with fewer than 2 points.

    Deterministic, no external references; a flat series is drawn as a centred line."""
    points = [float(v) for v in values or () if finite(v)]
    if len(points) < 2:
        return ''
    low, high = min(points), max(points)
    pad = 3.0
    span = high - low
    step = (width - 2 * pad) / (len(points) - 1)

    def y(value):
        if span == 0:
            return height / 2
        return pad + (high - value) * (height - 2 * pad) / span
    coords = [(pad + i * step, y(v)) for i, v in enumerate(points)]
    line = " ".join(f"{x:.1f},{yy:.1f}" for x, yy in coords)
    area = f"{coords[0][0]:.1f},{height} {line} {coords[-1][0]:.1f},{height}"
    color = esc(color)
    return (f'<svg class="gl-spark" role="img" aria-label="{esc(label)}" viewBox="0 0 {width} {height}" '
            f'preserveAspectRatio="none">'
            f'<polygon points="{area}" fill="{color}" fill-opacity="0.14"/>'
            f'<polyline points="{line}" fill="none" stroke="{color}" stroke-width="2" stroke-linejoin="round" '
            f'stroke-linecap="round" vector-effect="non-scaling-stroke"/></svg>')


def known_day(day):
    return isinstance(day, int) and not isinstance(day, bool) and day >= 1


def day_progress(day, total=DAYS_TOTAL):
    """"Día N de 30" progress bar (clamped 0-100 %); '' when the day is unknown."""
    if not known_day(day):
        return ''
    pct = max(0.0, min(100.0, 100.0 * day / total))
    return (f'<div class="gl-progress" role="progressbar" aria-valuemin="0" aria-valuemax="{total}" '
            f'aria-valuenow="{min(day, total)}" aria-label="Día {day} de {total}">'
            f'<i style="width:{pct:.0f}%"></i></div>')


def step_strip(steps, *, title="Pasos del ciclo seleccionado"):
    """Process step strip. ``steps``: ``[(label, done)]`` in order; done = persisted evidence exists."""
    items = "".join(
        f'<li class="gl-step{" done" if done else ""}"><span class="gl-step-dot" aria-hidden="true"></span>'
        f'<span class="gl-step-label">{esc(label)}</span>'
        f'<span class="gl-step-state">{"hecho" if done else "pendiente"}</span></li>' for label, done in steps)
    done = sum(1 for _, ok in steps if ok)
    return (f'<div class="gl-steps-card"><div class="gl-steps-head"><h3>{esc(title)}</h3>'
            f'<span class="gl-steps-count">{done} de {len(steps)} con evidencia</span></div>'
            f'<ol class="gl-steps">{items}</ol></div>')
