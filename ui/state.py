"""In-memory UI snapshot registry. Assignment is explicit; reruns only read."""
from ui.adapters import MARKETS, empty_market


def current_market(st, symbol, sample_factory):
    if symbol not in MARKETS:
        raise ValueError("unsupported floor market")
    if st.session_state.get("sample_mode", True):
        return sample_factory(symbol)
    return st.session_state.get("floor_snapshots", {}).get(symbol, empty_market(symbol))


def set_snapshot(st, view_model):
    if view_model.symbol not in MARKETS:
        raise ValueError("unsupported floor market")
    snapshots = dict(st.session_state.get("floor_snapshots", {}))
    snapshots[view_model.symbol] = view_model
    st.session_state["floor_snapshots"] = snapshots
