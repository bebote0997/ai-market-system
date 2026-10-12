"""AI Trading Floor entry point: streamlit run ui/app.py."""
import sys
from pathlib import Path

# Streamlit executes this file directly, so expose the repository root for imports.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st
from ui.auth import remote_dashboard_enabled, require_dashboard_access

from ui.adapters import MARKETS, symbol_enablement
from ui.components import panels
from ui.fixtures.demo_floor import demo_market
from ui.pages import dashboard, experiment, floor, journal, markets, system, luxury
from ui.state import current_market, enabled_market_symbols, operational_store

st.set_page_config(page_title="AI Trading Floor · PAPER", layout="wide", page_icon="📊")
require_dashboard_access(st)
luxury.apply(st)

with st.sidebar:
    luxury.html(st, '<div class="brand">Trading Floor<small>PAPER · V2</small></div>')
    page = st.radio("Navigation", luxury.PAGES + dashboard.PAGES + ("FLOOR", "MARKETS", "JOURNAL", "EXPERIMENT", "SYSTEM"), key="luxury_navigation",
                    format_func=lambda name: name if name in luxury.PAGES else f"Legacy · {name.title()}")
    enabled_symbols = enabled_market_symbols()
    symbol = st.selectbox("Market", MARKETS,
                          format_func=lambda market: f"{market} · {symbol_enablement(market, enabled_symbols)}")
    st.caption("Enabled for experiment: " + ", ".join(enabled_symbols))
    st.toggle("SAMPLE / DEMO · FLOOR, MARKETS, JOURNAL, SYSTEM", value=not remote_dashboard_enabled(), key="sample_mode")
    refresh = st.toggle("Auto-refresh · every 60s", value=True, key="auto_refresh")
    st.caption("PAPER · REAL EXECUTION DISABLED")
    st.caption("Observation only · no live orders")

if page in luxury.PAGES:
    @st.fragment(run_every="60s" if refresh else None)
    def render_luxury():
        luxury.render(st, page, enabled_symbols)
    render_luxury()
    st.stop()

if page in dashboard.PAGES:
    st.markdown('<div class="safety">PAPER MODE · REAL EXECUTION DISABLED · NO REAL MONEY</div>', unsafe_allow_html=True)
    dashboard.render(st, page)
    st.stop()

vm = current_market(st, symbol, demo_market)
panels.header(st, vm)

if page == "FLOOR":
    floor.render(st, vm)
elif page == "MARKETS":
    markets.render(st, vm, lambda market: current_market(st, market, demo_market), enabled_symbols)
elif page == "JOURNAL":
    store = operational_store() if not vm.sample else None
    try:
        journal.render(st, vm, store.journal(limit=500) if store else ())
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
            st.warning("Review export temporarily unavailable; PAPER execution is unaffected.")
        system.render(st, vm, health(store) if store else None,
                      store.latest_review(symbol) if store else None,
                      store.notification_events() if store else (),
                      evidence_json, store=store)
    finally:
        if store:
            store.close()

if vm.run_id:
    with st.expander(f"AI MEETING · {vm.run_id}"):
        panels.meeting(st, vm)
