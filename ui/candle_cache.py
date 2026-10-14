"""Small, expiring UI cache; email reads directly and snapshots its own evidence."""
import streamlit as st
from runtime.setup_candle_reader import read_candles


@st.cache_data(ttl=30, max_entries=32, show_spinner=False)
def cached_candles(path, symbol, as_of):
    return read_candles(path, symbol, as_of, limit=48)
