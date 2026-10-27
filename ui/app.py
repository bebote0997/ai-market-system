"""AI Trading Floor entry point: `streamlit run ui/app.py --server.address=127.0.0.1`."""
import sys
from pathlib import Path

# Streamlit executes this file directly, so expose the repository root for imports.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:  # Redact secrets from every log record of this process (idempotent across Streamlit reruns).
    from execution.log_redaction import install_log_redaction
    install_log_redaction()
except Exception:  # noqa: BLE001 - log hardening never blocks the dashboard
    pass

import streamlit as st
from ui.auth import remote_dashboard_enabled, require_dashboard_access

from ui.adapters import MARKETS, empty_market
from ui.components import panels
from ui.fixtures.demo_floor import demo_market
from ui.views import dashboard, experiment, floor, journal, markets, system, luxury
from ui.views import floor_view
from ui import demo_refresher
from ui import i18n_es as es
from ui.i18n_es import tr
from ui.state import current_market, default_market_index, enabled_market_symbols, operational_store

st.set_page_config(page_title="AI Trading Floor · OANDA Demo", layout="wide", page_icon="📊")
require_dashboard_access(st)
luxury.apply(st)
demo_refresher.ensure_started()  # idempotent: the Floor demo cache producer (also started by ui/asgi_app.py)

if st.query_params.get("page") == "floor" and "luxury_navigation" not in st.session_state:
    st.session_state["luxury_navigation"] = floor_view.PAGE  # alert deep link (runtime.trade_alerts.floor_url)

with st.sidebar:
    luxury.html(st, f'<div class="brand">{tr("Trading Floor")}<small>{tr("PAPER · V2")}</small></div>')
    page = st.radio(tr("Navigation"), luxury.PAGES + (floor_view.PAGE,) + dashboard.PAGES
                    + ("FLOOR", "MARKETS", "JOURNAL", "EXPERIMENT", "SYSTEM"), key="luxury_navigation",
                    format_func=lambda key: floor_view.LABEL if key == floor_view.PAGE else es.page_label(key))
    enabled_symbols = enabled_market_symbols()
    symbol = st.selectbox(tr("Market"), MARKETS, index=default_market_index(enabled_symbols),
                          format_func=lambda market: f"{market} · {es.symbol_status(market, enabled_symbols)}")
    st.caption(tr("Enabled for experiment: ") + es.enabled_caption(enabled_symbols))
    st.toggle(tr("SAMPLE / DEMO · FLOOR, MARKETS, JOURNAL, SYSTEM"), value=not remote_dashboard_enabled(), key="sample_mode")
    refresh = st.toggle(tr("Auto-refresh · every 60s"), value=True, key="auto_refresh")
    st.caption("🧪 " + tr("PAPER · REAL EXECUTION DISABLED"))
    st.caption(tr("Observation only · no live orders"))

if page in luxury.PAGES:
    @st.fragment(run_every="60s" if refresh else None)
    def render_luxury():
        luxury.render(st, page, enabled_symbols)
    render_luxury()
    st.stop()

if page == floor_view.PAGE:
    warming = refresh and demo_refresher.warming_up()  # cold demo caches: first retry after 8 s, then every 60 s

    @st.fragment(run_every=(demo_refresher.FIRST_RETRY if warming else "60s") if refresh else None)
    def render_floor_view():
        floor_view.render(st, enabled_symbols)
        if warming and not demo_refresher.warming_up():
            st.rerun()  # data arrived: a full run registers the fragment with the 60 s refresh again
    render_floor_view()
    st.stop()

if page in dashboard.PAGES:
    st.markdown(f'<div class="safety">{tr("PAPER MODE · REAL EXECUTION DISABLED · NO REAL MONEY")}</div>', unsafe_allow_html=True)
    dashboard.render(st, page)
    st.stop()

since = dashboard.demo_since()  # OANDA demo: a snapshot from before the epoch is not the current market


def market_view(market):
    view = current_market(st, market, demo_market)
    if not view.sample and since is not None and not dashboard.is_current(view.as_of, since):
        return empty_market(market)
    return view


vm = market_view(symbol)
panels.header(st, vm)

if page == "FLOOR":
    floor.render(st, vm)
elif page == "MARKETS":
    markets.render(st, vm, market_view, enabled_symbols)
elif page == "JOURNAL":
    store = operational_store() if not vm.sample else None
    try:
        journal.render(st, vm, dashboard.since_epoch(store.journal(limit=500), since, ("timestamp",)) if store else ())
    finally:
        if store:
            store.close()
elif page == "EXPERIMENT":
    experiment.render(st, vm, enabled_symbols)
else:
    store = operational_store() if not vm.sample else None
    try:
        from runtime.health import health
        from runtime.review_export import review_bundle_json
        try:
            evidence_json = review_bundle_json(store) if store else None
        except (OSError, RuntimeError, ValueError, TypeError):
            evidence_json = None
            st.warning(tr("Review export temporarily unavailable; PAPER execution is unaffected."))
        review = store.latest_review(symbol) if store else None
        if since is not None and review and not dashboard.is_current(review.get("as_of"), since):
            review = None
        system.render(st, vm, health(store) if store else None, review,
                      dashboard.since_epoch(dashboard.payloads(store.db, 'notification_events'), since,
                                            ("timestamp_utc",)) if store else (),
                      evidence_json, store=store, since=since)
    finally:
        if store:
            store.close()

if vm.run_id:
    with st.expander(f"{tr('AI MEETING')} · {es.cycle_label(vm.as_of, vm.symbol)}"):
        panels.meeting(st, vm)
