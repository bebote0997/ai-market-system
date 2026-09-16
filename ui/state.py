"""In-memory UI snapshot registry. Assignment is explicit; reruns only read."""
import os
from pathlib import Path
from dataclasses import replace

from ui.adapters import MARKETS, empty_market, from_persisted_snapshot


def operational_store():
    path = Path(os.environ.get("AI_FLOOR_DB_PATH", "data/runtime/trading_floor.db"))
    if not path.exists():
        return None
    from storage.database import Store
    return Store(path, readonly=True)


def current_market(st, symbol, sample_factory):
    if symbol not in MARKETS:
        raise ValueError("unsupported floor market")
    if st.session_state.get("sample_mode", True):
        return sample_factory(symbol)
    in_memory = st.session_state.get("floor_snapshots", {}).get(symbol)
    if in_memory is not None:
        return in_memory
    store = operational_store()
    if store is None:
        return empty_market(symbol)
    try:
        payload = store.load_snapshot(symbol)
        vm = from_persisted_snapshot(payload) if payload else empty_market(symbol)
        account, orders, _ = store.load_paper(store.get_state("account_id") or "paper-main")
        return replace(vm, account=account,
            positions=tuple(p for p in account.open_positions.values() if p.symbol == symbol) if account else (),
            orders=tuple(o for o in orders.values() if o.symbol == symbol))
    finally:
        store.close()


def set_snapshot(st, view_model):
    if view_model.symbol not in MARKETS:
        raise ValueError("unsupported floor market")
    snapshots = dict(st.session_state.get("floor_snapshots", {}))
    snapshots[view_model.symbol] = view_model
    st.session_state["floor_snapshots"] = snapshots
