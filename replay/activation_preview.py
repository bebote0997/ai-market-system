"""V2 Phase 8 / P8.4 R3: READ-ONLY preview of the first chronological catch-up activation (DEC-8.6, DEC-8.7).

It never opens a source database for writing. Each source (trading DB, optional Market Evidence DB) is copied into a
private temporary directory through SQLite's online backup API from a ``mode=ro`` connection, and only the copies are
read. Source files are hashed before and after; ``source_unchanged`` must be true.

For every OPEN position (from the copy) it reports:
- the durable watermark (``last_processed_at``, else ``opened_at``);
- ``historic_missed``: what an independent bar-by-bar oracle finds over (opened_at, watermark], i.e. SL/TP touches the
  newest-bar path did not apply. REPORT ONLY (DEC-8.7): activation never revisits these bars and nothing is closed;
- ``catch_up_bars``: the closed 5m bars after the watermark that the first activation cycle would process;
- ``expected_close``: the oracle result over those bars (bar, price, reason, net PnL), and the expected realized PnL.
It also reports PENDING orders (the current gate bar and the bars that would be journaled as not evaluated), REVISION
anomalies of the evidence copy, data coverage gaps, and a list of pre-activation risks.

The oracle is independent of the audited component: Decimal arithmetic; per bar, in time order: open beyond SL -> exit
at open; open beyond TP -> exit at open; SL touched -> SL; TP touched -> TP; SL before TP within one bar.
Usage: python -m replay.activation_preview --trading-db PATH --as-of ISO [--evidence-db PATH] [--bars-json PATH]
       [--account paper-main] [--first-scope XAUUSD] --out report.json
"""
from datetime import timedelta, timezone
from decimal import Decimal
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile

from storage.codec import parse_utc, utc

FIVE = timedelta(minutes=5)
REPORT_VERSION = "V2_P8_ACTIVATION_PREVIEW_1"


def sha256(path):
    path = Path(path)
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fingerprint(path):
    """Hashes of the database file and its WAL/SHM companions (all must be unchanged by the preview)."""
    return {suffix or "db": sha256(str(path) + suffix) for suffix in ("", "-wal", "-shm")}


def _snapshot(source, target):
    """Consistent copy through the backup API from a read-only connection (includes committed WAL content)."""
    src = sqlite3.connect(Path(source).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def oracle(side, entry, stop, target, quantity, multiplier, cost_rate, bars):
    entry, stop, target = (Decimal(str(v)) for v in (entry, stop, target))
    quantity, multiplier, cost_rate = Decimal(str(quantity)), Decimal(str(multiplier)), Decimal(str(cost_rate))
    for bar in bars:
        o, h, l = Decimal(str(bar["open"])), Decimal(str(bar["high"])), Decimal(str(bar["low"]))
        long = side == "LONG"
        cases = ((o <= stop if long else o >= stop, o, "stop"), (o >= target if long else o <= target, o, "target"),
                 (l <= stop if long else h >= stop, stop, "stop"), (h >= target if long else l <= target, target, "target"))
        hit = next(((price, reason) for touched, price, reason in cases if touched), None)
        if hit is None:
            continue
        price, reason = hit
        gross = ((price - entry) if long else (entry - price)) * quantity * multiplier
        cost = cost_rate * quantity * entry / 100
        return {"bar_start": bar["bar_start"], "exit_price": str(price), "reason": reason,
                "net_pnl": str(gross - cost)}
    return None


def _bars_from_evidence(path):
    db = sqlite3.connect(path)
    try:
        rows = [json.loads(r[0]) for r in db.execute(
            "SELECT payload FROM market_evidence WHERE timeframe='5m' ORDER BY symbol, stream_seq")]
        revisions = [{"symbol": r[0], "timeframe": r[1], "bar_start": r[2], "anomaly_id": r[3]} for r in db.execute(
            "SELECT symbol, timeframe, bar_start, anomaly_id FROM evidence_anomalies WHERE kind='REVISION' "
            "ORDER BY symbol, bar_start, anomaly_id")]
    finally:
        db.close()
    return rows, revisions


def _by_symbol(rows):
    bars = {}
    for row in rows:
        bars.setdefault(row["symbol"], []).append({k: row[k] for k in ("bar_start", "open", "high", "low", "close")})
    for symbol in bars:
        bars[symbol].sort(key=lambda b: parse_utc(b["bar_start"]))
    return bars


def _closed_between(bars, after, as_of):
    return [b for b in bars if after < parse_utc(b["bar_start"]) and parse_utc(b["bar_start"]) + FIVE <= as_of]


def _gaps(bars):
    starts = [parse_utc(b["bar_start"]) for b in bars]
    return [utc(a) for a, b in zip(starts, starts[1:]) if b - a > FIVE]


def preview(trading_db, *, as_of, evidence_db=None, bars_json=None, account_id="paper-main", first_scope=("XAUUSD",)):
    from storage.database import Store  # read-only use of the copy
    as_of = as_of.astimezone(timezone.utc)
    sources = [Path(trading_db)] + ([Path(evidence_db)] if evidence_db else [])
    before = {str(p): _fingerprint(p) for p in sources}
    with tempfile.TemporaryDirectory(prefix="activation-preview-") as tmp:
        trading_copy = Path(tmp) / "trading_copy.db"
        _snapshot(trading_db, trading_copy)
        rows, revisions = [], []
        if evidence_db:
            evidence_copy = Path(tmp) / "evidence_copy.db"
            _snapshot(evidence_db, evidence_copy)
            rows, revisions = _bars_from_evidence(evidence_copy)
        if bars_json:
            rows += json.loads(Path(bars_json).read_text(encoding="utf-8"))
        bars = _by_symbol(rows)
        store = Store(trading_copy, readonly=True)
        try:
            account, orders, _ = store.load_paper(account_id)
        finally:
            store.close()
    after = {str(p): _fingerprint(p) for p in sources}
    if account is None:
        raise ValueError("account not found in the trading DB copy")
    positions, risks, expected_realized = [], [], Decimal(str(account.realized_pnl))
    for symbol, position in sorted(account.open_positions.items()):
        series = bars.get(symbol, [])
        opened = position.opened_at
        watermark = position.last_processed_at or opened
        historic = _closed_between(series, opened, watermark)
        forward = _closed_between(series, watermark, as_of)
        args = (position.side, position.entry_price, position.stop, position.target, position.quantity,
                position.contract_multiplier or 1.0, position.cost_rate)
        missed = oracle(*args, historic)
        expected = oracle(*args, forward)
        if expected is not None:
            expected_realized += Decimal(expected["net_pnl"])
        coverage_gaps = _gaps([b for b in series if parse_utc(b["bar_start"]) > opened
                               and parse_utc(b["bar_start"]) + FIVE <= as_of])
        item = {"symbol": symbol, "position_id": position.position_id, "side": position.side,
                "entry": position.entry_price, "stop": position.stop, "target": position.target,
                "quantity": position.quantity, "opened_at": utc(opened), "watermark": utc(watermark),
                "historic_bars_checked": len(historic), "historic_missed": missed,
                "catch_up_bars": [b["bar_start"] for b in forward], "expected_close": expected,
                "coverage_gaps": coverage_gaps, "in_first_activation_scope": symbol in first_scope}
        positions.append(item)
        if missed is not None:
            risks.append({"risk": "HISTORIC_DIVERGENCE", "symbol": symbol, "detail": missed,
                          "treatment": "REPORT_ONLY (DEC-8.7): never revisited or closed by activation"})
        if expected is not None:
            risks.append({"risk": "IMMEDIATE_CLOSE_EXPECTED", "symbol": symbol, "detail": expected})
        if not series:
            risks.append({"risk": "NO_BAR_DATA", "symbol": symbol})
        if coverage_gaps:
            risks.append({"risk": "MISSING_BARS", "symbol": symbol, "detail": coverage_gaps})
        if symbol not in first_scope:
            risks.append({"risk": "OUTSIDE_FIRST_ACTIVATION_SCOPE", "symbol": symbol})
    pending = []
    for order in sorted((o for o in orders.values() if o.status == "PENDING"), key=lambda o: o.order_id):
        series = [b for b in bars.get(order.symbol, []) if parse_utc(b["bar_start"]) + FIVE <= as_of]
        gate = series[-1]["bar_start"] if series else None
        skipped = [b["bar_start"] for b in series
                   if parse_utc(b["bar_start"]) > order.as_of and b["bar_start"] != gate]
        pending.append({"order_id": order.order_id, "symbol": order.symbol, "side": order.side,
                        "as_of": utc(order.as_of), "gate_bar": gate, "not_evaluated_bars": skipped,
                        "risk_policy_version": order.risk_policy_version, "setup_id": order.setup_id})
    if pending:
        risks.append({"risk": "PENDING_ORDERS_PRESENT", "count": len(pending)})
    if revisions:
        risks.append({"risk": "REVISION_PRESENT", "count": len(revisions)})
    return {"report_version": REPORT_VERSION, "as_of": utc(as_of), "account_id": account_id,
            "read_only": True, "source_unchanged": before == after, "source_hashes": before,
            "realized_pnl": account.realized_pnl, "expected_realized_after_first_cycle": str(expected_realized),
            "positions": positions, "pending_orders": pending, "revisions": revisions, "risks": risks}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--trading-db", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--evidence-db")
    parser.add_argument("--bars-json")
    parser.add_argument("--account", default="paper-main")
    parser.add_argument("--first-scope", default="XAUUSD")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    sources = {Path(p).resolve() for p in (args.trading_db, args.evidence_db, args.bars_json) if p}
    if Path(args.out).resolve() in sources:
        raise SystemExit("refusing to write the report over a source file")
    report = preview(args.trading_db, as_of=parse_utc(args.as_of), evidence_db=args.evidence_db,
                     bars_json=args.bars_json, account_id=args.account,
                     first_scope=tuple(s.strip() for s in args.first_scope.split(",") if s.strip()))
    Path(args.out).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return 0 if report["source_unchanged"] else 2


if __name__ == "__main__":
    sys.exit(main())
