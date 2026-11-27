"""Streamlit entry for the Liquid Glass panel. Reads existing stores. Does not write them."""
from datetime import datetime, timezone

PAGES = ("Inicio", "Posiciones", "Historial", "Riesgo", "Sistema")


def label(key):
    return key if key in PAGES else None


def _system(now):
    try:
        from ui.state import operational_store
        store = operational_store()
    except Exception:  # noqa: BLE001 - a schema the dashboard cannot open is sin datos, not a crash
        return None, None
    if store is None:
        return None, None
    snapshot, errors = None, None
    try:
        try:
            from runtime.health import health
            snapshot = health(store, now=now)
        except Exception:  # noqa: BLE001 - the panel shows sin datos and still closes the store
            snapshot = None
        try:
            errors = []
            for row in store.journal(limit=200):
                if isinstance(row, dict) and row.get("severity") == "ERROR":
                    errors.append({"at": row.get("timestamp"), "event": row.get("event_type")})
                if len(errors) >= 8:
                    break
        except Exception:  # noqa: BLE001
            errors = None
    finally:
        store.close()
    return snapshot, errors


def load(env=None, now=None, book=None, account_reader=None, curve=None, live_reader=None):
    """Vista en vivo. La foto del ciclo y, si se pide, el precio cacheado. Sin libro de ejemplo."""
    import os
    from ui.exness_present import _flags
    from ui.liquid.model import build_dashboard
    from ui.liquid.snapshot import latest
    env = os.environ if env is None else env
    now = now or datetime.now(timezone.utc)
    health, errors = _system(now)
    marks = None
    if live_reader is not None:
        try:
            marks = live_reader(env, now)
        except Exception:  # noqa: BLE001 - el panel sigue con la foto si la lectura viva falla
            marks = None
    return build_dashboard(env=env, now=now, flags=_flags(env), account_reader=account_reader, book=book,
                           health=health, errors=errors, curve=curve, snapshot=latest(env), marks=marks)


_CHROME = """<style>
section[data-testid="stSidebar"],[data-testid="stSidebarCollapsedControl"],
[data-testid="stHeader"],[data-testid="stToolbar"]{display:none !important}
html .stApp.aif-nav-open .block-container,
html .stApp .block-container,
html [data-testid="stMainBlockContainer"]{
padding:0 !important;max-width:100% !important}
</style>"""


def _draw(st, body):
    html = getattr(st, "html", None)
    if callable(html):
        try:
            html(body, unsafe_allow_javascript=True)
        except TypeError:
            html(body)
    else:
        st.markdown(body, unsafe_allow_html=True)


def render(st, section):
    """Panel a sangre. Se refresca cada 20 s sin tocar la pestaña ni el scroll del navegador."""
    from ui.liquid.live import panel_marks
    from ui.liquid.render import fragment
    st.markdown(_CHROME, unsafe_allow_html=True)
    chosen = section if section in PAGES else "Inicio"

    def _paint():
        body = fragment(load(live_reader=panel_marks), section=chosen)
        _draw(st, body)
        st.caption("Solo lectura. Este panel no cierra operaciones ni cambia frenos.")

    # El fragmento vuelve a leer la foto. El script del panel restaura pestaña y scroll.
    paint = getattr(st, "fragment", None)
    if callable(paint):
        paint(run_every="20s")(_paint)()
    else:
        _paint()
