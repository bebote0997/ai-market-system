"""Read-only "Floor" page: the animated isometric trading floor (ui/static/floor/floor.html, approved mockup v4).

The HTML is a static asset; Python injects the JSON payload of ``ui.floor_payload.build_floor_payload`` (escaped so
it cannot close the ``<script>`` tag). The browser fetches nothing. Rendered as a sandboxed ``srcdoc`` iframe with
``st.iframe`` (``streamlit.components.v1.html`` on Streamlit versions without it; that API is deprecated).
"""
from datetime import datetime, timezone
from pathlib import Path

from ui.floor_payload import build_floor_payload, guarded, payload_json

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


def render(st, symbols):
    try:
        payload = current_payload(st, symbols)
    except Exception:  # noqa: BLE001 - DB unopenable etc.: empty floor ("—"), never a broken page
        payload = build_floor_payload(sample_mode=bool(st.session_state.get("sample_mode")))
    embed(st, render_html(payload))
    st.caption("Floor · solo lectura · PAPER / demo · ninguna orden se envía desde esta vista. "
               "«—» = sin datos aún" + (" · «EJEMPLO» = datos de ejemplo (modo muestra)" if payload["example_fields"]
                                        else ""))
