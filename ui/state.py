"""In-memory UI snapshot registry. Assignment is explicit; reruns only read."""
import os
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from dataclasses import replace

from ui.adapters import MARKETS, empty_market, from_persisted_snapshot
from runtime.config import DEFAULT_ENABLED_SYMBOLS


@contextmanager
def dashboard_database(path=None):
    """Read published tables without requiring a runtime schema migration."""
    path = Path(path or os.environ.get("AI_FLOOR_DB_PATH", "data/runtime/trading_floor.db"))
    if not path.exists():
        yield None
        return
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        yield db
    finally:
        db.close()


def operational_store():
    path = Path(os.environ.get("AI_FLOOR_DB_PATH", "data/runtime/trading_floor.db"))
    if not path.exists():
        return None
    from storage.database import Store
    return Store(path, readonly=True)


def enabled_market_symbols():
    """Read the runtime's published selection; never infer it from UI fixtures."""
    with dashboard_database() as db:
        if db is None or not db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='system_state'").fetchone():
            return DEFAULT_ENABLED_SYMBOLS
        row = db.execute("SELECT value FROM system_state WHERE key='enabled_symbols'").fetchone()
        raw = row[0] if row else None
        enabled = tuple(json.loads(raw)) if raw else DEFAULT_ENABLED_SYMBOLS
        return enabled if enabled and set(enabled) <= set(MARKETS) else DEFAULT_ENABLED_SYMBOLS


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
        # Runtime load_paper also reconstructs every historical fill/trade. Display
        # only needs account totals, open positions and a bounded order history.
        from storage.codec import paper_decode
        account_row = store.db.execute('SELECT payload FROM paper_accounts WHERE account_id=?',
                                       (store.get_state('account_id') or 'paper-main',)).fetchone()
        account = paper_decode('PaperAccount', account_row[0]) if account_row else None
        positions = tuple(paper_decode('PaperPosition', row[0]) for row in store.db.execute(
            "SELECT payload FROM paper_positions WHERE json_extract(payload,'$.status')='OPEN' "
            "AND json_extract(payload,'$.symbol')=? LIMIT 200", (symbol,)))
        orders = tuple(paper_decode('PaperOrder', row[0]) for row in store.db.execute(
            "SELECT payload FROM paper_orders WHERE json_extract(payload,'$.symbol')=? "
            "ORDER BY (json_extract(payload,'$.status')='PENDING') DESC,rowid DESC LIMIT 200", (symbol,)))
        return replace(vm, account=account,
                       positions=positions, orders=orders)
    finally:
        store.close()


def set_snapshot(st, view_model):
    if view_model.symbol not in MARKETS:
        raise ValueError("unsupported floor market")
    snapshots = dict(st.session_state.get("floor_snapshots", {}))
    snapshots[view_model.symbol] = view_model
    st.session_state["floor_snapshots"] = snapshots
