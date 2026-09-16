"""AI Trading Floor entry point: streamlit run ui/app.py."""
import sys
from pathlib import Path

# Streamlit executes this file directly, so expose the repository root for imports.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st

from ui.adapters import MARKETS
from ui.components import panels
from ui.fixtures.demo_floor import demo_market
from ui.pages import experiment, floor, journal, markets, positions, system
from ui.state import current_market, operational_store
from ui.theme import apply

st.set_page_config(page_title="AI Trading Floor · PAPER", layout="wide", page_icon="📊")
apply(st)

with st.sidebar:
    st.caption("AI TRADING FLOOR · PAPER")
    page = st.radio("Navigation", ("FLOOR", "MARKETS", "POSITIONS", "JOURNAL", "EXPERIMENT", "SYSTEM"))
    symbol = st.selectbox("Market", MARKETS)
    st.toggle("SAMPLE / DEMO", value=True, key="sample_mode")
    st.caption("Observation only · no live orders")

vm = current_market(st, symbol, demo_market)
panels.header(st, vm)

if page == "FLOOR":
    floor.render(st, vm)
elif page == "MARKETS":
    markets.render(st, vm, lambda market: current_market(st, market, demo_market))
elif page == "POSITIONS":
    positions.render(st, vm)
elif page == "JOURNAL":
    store = operational_store() if not vm.sample else None
    try:
        journal.render(st, vm, store.journal(limit=500) if store else ())
    finally:
        if store:
            store.close()
elif page == "EXPERIMENT":
    experiment.render(st, vm)
else:
    store = operational_store() if not vm.sample else None
    try:
        from runtime.health import health
        system.render(st, vm, health(store) if store else None)
    finally:
        if store:
            store.close()

if vm.run_id:
    with st.expander(f"AI MEETING · {vm.run_id}"):
        panels.meeting(st, vm)
