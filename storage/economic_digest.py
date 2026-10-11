"""V2 Phase 8 / P2a-lib (DEC-8.22-c, restricted scope): deterministic economic evidence digests. READ-ONLY.

A pure library: nothing here writes to any database, and no runtime module imports it yet (P3 REX, P2b genesis and
the chain-head anchors will reuse it later, under their own authorizations). It implements the V2 P8.6 design:

- ``cj(value)``: canonical JSON (design 3.3.2): ``json.dumps(sort_keys=True, separators=(",", ":"),
  ensure_ascii=False, allow_nan=False)`` over a mapped value: NULL -> null; INTEGER -> JSON integer; REAL -> the string
  ``float.hex(x)`` (exact, no decimal rounding); TEXT -> the string exactly as stored (never re-parsed or
  normalized); BLOB -> ``{"blob_hex": "<lowercase hex>"}``; bool -> true/false; tuples -> lists. NaN / infinity are
  rejected.
- ``H(prefix, text)``: SHA-256 (lowercase hex) of ``prefix + "\\n" + text`` in UTF-8 (domain-separated).
- ``psh(paper_state_tuple)`` (design 5.1.1): ``H("V2PAPER/STATE/1", cj(list(Store.paper_state(...))))``. It covers
  exactly what the whole-state CAS covers (account, OPEN positions, closed trades, orders, fills) and therefore NOT
  the CLOSED ``paper_positions`` rows: it is a concurrency identity, never a completeness proof.
- ``edg(conn)`` (design 5.1.3.1): the complete persisted economic state, version 1: EVERY row of the five PAPER
  tables in a fixed order (including CLOSED positions, terminal orders, fills and closed trades), rows ordered by
  primary key (SQLite BINARY collation = UTF-8 bytes), each row ``cj({column: value})`` over all columns. Per-table
  ``H("V2ECON/TABLE/1", name + "\\n" + count + "\\n" + "\\n".join(rows))`` and
  ``H("V2ECON/STATE/1", cj([[name, count, table_digest], ...]))``. Physical row order, page layout (VACUUM) and the
  computing process do not affect the result. A missing table or column fails closed (ValueError).
- ``expected_edg_genesis(account_id, starting_equity)`` (design 5.1.3.2 G2): the EDG of a genesis database holding
  exactly one account row as the runtime bootstrap writes it (``PaperAccount("1.0", id, e, e, e)`` through the same
  ``paper_encode``) and no other economic row.

Limits (stated, not hidden): a digest of a snapshot detects only PERSISTENT divergence; it cannot show an alteration
made and reverted between two observations. In the five tables every column is TEXT, so the REAL-as-hex-string
mapping can never collide with a stored TEXT inside EDG.
"""
import hashlib
import json
import math

EDG_VERSION = 1
# (table, primary key) in the fixed EDG order (design 5.1.3.1 item 2). Changing it needs a new EDG_VERSION.
EDG_TABLES = (("paper_accounts", "account_id"), ("paper_orders", "order_id"), ("paper_fills", "fill_id"),
              ("paper_positions", "position_id"), ("closed_trades", "trade_id"))
PSH_PREFIX = "V2PAPER/STATE/1"
EDG_TABLE_PREFIX = "V2ECON/TABLE/1"
EDG_STATE_PREFIX = "V2ECON/STATE/1"


def _map(value):
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite REAL is not canonical")
        return value.hex()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"blob_hex": bytes(value).hex()}
    if isinstance(value, (list, tuple)):
        return [_map(item) for item in value]
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("canonical objects need string keys")
        return {key: _map(item) for key, item in value.items()}
    raise TypeError(f"unsupported canonical value: {type(value).__name__}")


def cj(value):
    """Canonical JSON text of ``value`` (design 3.3.2)."""
    return json.dumps(_map(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def H(prefix, text):  # noqa: N802 - the design's name
    """Domain-separated SHA-256: ``sha256(prefix + "\\n" + text)`` in UTF-8, lowercase hex."""
    if not isinstance(prefix, str) or not isinstance(text, str):
        raise TypeError("H takes two strings")
    return hashlib.sha256((prefix + "\n" + text).encode("utf-8")).hexdigest()


def psh(paper_state_tuple):
    """Digest of ``Store.paper_state(account, orders, fills)`` exactly as returned (design 5.1.1)."""
    state = paper_state_tuple
    if not isinstance(state, tuple) or len(state) != 5:
        raise ValueError("psh takes the 5-element tuple returned by Store.paper_state")
    account, *groups = state
    if account is not None and not isinstance(account, str):
        raise ValueError("paper_state account element must be an encoded string or None")
    if any(not isinstance(group, tuple) or any(not isinstance(item, str) for item in group) for group in groups):
        raise ValueError("paper_state groups must be tuples of encoded strings")
    return H(PSH_PREFIX, cj(list(state)))


def _digest(rows_by_table):
    tables, summary = {}, []
    for name, _ in EDG_TABLES:
        rows = rows_by_table[name]
        digest = H(EDG_TABLE_PREFIX, name + "\n" + str(len(rows)) + "\n" + "\n".join(cj(row) for row in rows))
        tables[name] = {"count": len(rows), "digest": digest}
        summary.append([name, len(rows), digest])
    return {"edg_version": EDG_VERSION, "edg": H(EDG_STATE_PREFIX, cj(summary)), "tables": tables}


def _read_tables(conn):
    rows_by_table = {}
    for name, key in EDG_TABLES:
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
        if exists is None:
            raise ValueError(f"economic table missing: {name}")
        cursor = conn.execute(f'SELECT * FROM "{name}" ORDER BY "{key}" COLLATE BINARY')
        columns = [column[0] for column in cursor.description]
        if key not in columns:
            raise ValueError(f"economic table {name} lacks its primary key {key}")
        rows_by_table[name] = [dict(zip(columns, tuple(row))) for row in cursor.fetchall()]
    return rows_by_table


def edg(conn):
    """Complete economic digest of the database open on ``conn`` (read-only use). Fails closed on schema drift.

    All five tables are read inside ONE SQLite read transaction, so the digest is a single consistent snapshot, never
    a mix of states committed by a concurrent writer between two SELECTs (WAL: one snapshot; rollback journal: the
    SHARED lock is held until the end). If the caller already holds a transaction it is reused and never committed or
    rolled back here (the digest then reflects the caller's own view). Otherwise a deferred, read-only transaction is
    opened and ended here; it writes nothing. If a transaction cannot be confirmed, the call fails closed.
    """
    owned = not conn.in_transaction
    if owned:
        conn.execute("BEGIN")
    try:
        if not conn.in_transaction:
            raise RuntimeError("EDG requires a single read transaction; consistency cannot be guaranteed")
        rows_by_table = _read_tables(conn)
        if not conn.in_transaction:
            raise RuntimeError("EDG read transaction ended during the calculation; snapshot not consistent")
    finally:
        if owned and conn.in_transaction:
            conn.execute("ROLLBACK")  # our own read-only transaction: nothing to undo, releases the snapshot
    return _digest(rows_by_table)


def expected_edg_genesis(account_id, starting_equity):
    """EDG of a genesis DB: one account row as the runtime bootstrap writes it, every other economic table empty."""
    from execution.contracts import PaperAccount
    from storage.codec import paper_encode
    account = PaperAccount("1.0", account_id, starting_equity, starting_equity, starting_equity)
    rows = {name: [] for name, _ in EDG_TABLES}
    rows["paper_accounts"] = [{"account_id": account_id, "payload": paper_encode(account)}]
    return _digest(rows)


__all__ = ["EDG_TABLES", "EDG_VERSION", "H", "cj", "edg", "expected_edg_genesis", "psh"]
