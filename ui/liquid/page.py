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


def load(env=None, now=None, book=None, account_reader=None, curve=None):
    """Live view. No network client and no sample book. ``book`` and ``curve`` stay empty unless a caller has them."""
    import os
    from ui.exness_present import _flags
    from ui.liquid.model import build_dashboard
    env = os.environ if env is None else env
    now = now or datetime.now(timezone.utc)
    snapshot, errors = _system(now)
    return build_dashboard(env=env, now=now, flags=_flags(env), account_reader=account_reader, book=book,
                           health=snapshot, errors=errors, curve=curve)


def render(st, section):
    from ui.liquid.render import fragment
    body = fragment(load(), section=section if section in PAGES else "Inicio")
    html = getattr(st, "html", None)
    if callable(html):
        try:
            html(body, unsafe_allow_javascript=True)
        except TypeError:
            html(body)
    else:
        st.markdown(body, unsafe_allow_html=True)
    st.caption("Solo lectura. Este panel no cierra operaciones ni cambia frenos.")
