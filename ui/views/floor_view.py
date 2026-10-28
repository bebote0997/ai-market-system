"""Read-only "Floor" page: the animated isometric trading floor (ui/static/floor/floor.html, approved mockup v4).

The HTML is a static asset; Python injects the JSON payload of ``ui.floor_payload.build_floor_payload`` (escaped so
it cannot close the ``<script>`` tag). The browser fetches nothing. Rendered as a sandboxed ``srcdoc`` iframe with
``st.iframe`` (``streamlit.components.v1.html`` on Streamlit versions without it; that API is deprecated).
``render`` rebuilds the payload on every run of the page's 60 s auto-refresh fragment (``ui/app.py``), so a cycle that
starts RUNNING (``payload["running"]``) shows up at the next refresh: the new ``srcdoc`` reloads the iframe. Idle, the
asset draws one static frame; it animates only while that payload says a cycle is running.

Glass layout (desktop): ``st.columns([1, 2.6, 1])`` = left agent cards (Mateo, Sofía, Andrés, Valentina), the
unchanged iframe, right agent cards (Lucía, Diego, Camila, Carlos), all from the same payload (``ui.components.glass``).
The cards sit in ``<details class="gl-agents">`` blocks: always open on desktop, a tap-to-open row on phones (CSS only).

Below the floor, the trade manager / risk desk offers ONE owner action, «Cerrar operación» (``ui.views.floor_actions``;
this module itself never imports any execution / broker code).
"""
from datetime import datetime, timezone
from pathlib import Path

from ui.components.glass import LEFT_AGENTS, RIGHT_AGENTS, agents_panel
from ui.floor_payload import build_floor_payload, guarded, payload_json
from ui.views.floor_actions import render_manual_close

PAGE = "Floor"
LABEL = "Floor"  # == PAGE: ui.i18n_es.page_key(label) maps unknown labels back to themselves
ASSET = Path(__file__).resolve().parents[1] / "static" / "floor" / "floor.html"
PLACEHOLDER = "__FLOOR_PAYLOAD__"
HEIGHT = 860  # desktop; the asset's own @media (max-width:820px) layout handles phone widths inside the iframe


def render_html(payload):
    """The asset with the payload injected (exactly once)."""
    template = ASSET.read_text(encoding="utf-8")
    if template.count(PLACEHOLDER) != 1:
        raise ValueError("floor asset must contain exactly one payload placeholder")
    return template.replace(PLACEHOLDER, payload_json(payload))


def current_payload(st, symbols, now=None):
    from ui.state import dashboard_database
    from ui.views.luxury import demo_api
    now = now or datetime.now(timezone.utc)
    demo = guarded(demo_api)
    with dashboard_database() as db:
        return build_floor_payload(sample_mode=bool(st.session_state.get("sample_mode")), now=now, db=db,
                                   demo=demo, enabled_symbols=symbols)


def embed(st, html):
    if callable(getattr(st, "iframe", None)):
        st.iframe(html, height=HEIGHT)
    else:  # older Streamlit
        import streamlit.components.v1 as components
        components.html(html, height=HEIGHT, scrolling=False)


def agents_html(st, agent_ids, payload, title):
    st.markdown('<div class="tf">' + agents_panel(agent_ids, payload.get("agents"), title=title,
                                                  waiting_text=payload.get("waiting_text"),
                                                  example_fields=payload.get("example_fields") or ()) + '</div>',
                unsafe_allow_html=True)


def render(st, symbols):
    try:
        payload = current_payload(st, symbols)
    except Exception:  # noqa: BLE001 - DB unopenable etc.: empty floor ("—"), never a broken page
        payload = build_floor_payload(sample_mode=bool(st.session_state.get("sample_mode")))
    with st.container(key="floor-stage"):
        left, centre, right = st.columns([1, 2.6, 1])
        with left:
            agents_html(st, LEFT_AGENTS, payload, "Equipo de análisis")
        with centre:
            embed(st, render_html(payload))
        with right:
            agents_html(st, RIGHT_AGENTS, payload, "Revisión y ejecución")
    render_manual_close(st)
    st.caption("Floor · solo lectura salvo «Cerrar operación» (owner, demo OANDA de práctica) · ninguna orden nueva "
               "se envía desde esta vista. "
               "«—» = sin datos aún" + (" · «EJEMPLO» = datos de ejemplo (modo muestra)" if payload["example_fields"]
                                        else ""))
