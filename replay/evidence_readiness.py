"""Market Evidence readiness for turning AI_FLOOR_V2_POSITION_CATCH_UP ON (Phase 13 evidence preparation). READ-ONLY.

    python -m replay.evidence_readiness --trading-db PATH --evidence-db PATH [--symbols XAUUSD,EURUSD]
                                        [--as-of ISO8601] [--json]

Both databases are opened ``mode=ro`` (nothing is created or written). Per symbol it reports the newest committed 5m
bar, the 5m bars missing since each OPEN position's watermark, the GAP / uncommitted LATE anomalies in that window and
a READY / NOT_READY verdict with the same rule as the preflight check ``catch_up_evidence_contiguous``
(``runtime.config.catch_up_scope_checks``; strict, around the clock: a market-closed weekend inside a position's window
is NOT_READY by design). The detail is bounded: missing bars are counted arithmetically and at most MAX_LISTED are
listed; a window longer than MAX_WINDOW is NOT_READY without enumeration. The newest closed bar is the one closed at
``--as-of`` (default: now), so a verdict is only meaningful within the 5 minutes after an in-session cycle committed it.
Exit code: 0 all READY, 1 any NOT_READY, 2 usage error.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import sys

from runtime.config import DEFAULT_ENABLED_SYMBOLS
from storage.codec import parse_utc, utc

FIVE = timedelta(minutes=5)
MAX_LISTED = 50  # missing bars / anomalies listed per position (the missing count is always exact)
MAX_WINDOW = timedelta(days=14)  # a longer position window is NOT_READY without enumerating it


def newest_closed_5m(as_of):
    """The start of the newest 5m bar closed at ``as_of`` (UTC)."""
    as_of = as_of.astimezone(timezone.utc)
    return as_of.replace(minute=as_of.minute - as_of.minute % 5, second=0, microsecond=0) - FIVE


def _first_after(watermark):
    """The first 5m bar start strictly after ``watermark`` (the preflight's rule)."""
    start = watermark.replace(second=0, microsecond=0)
    start = start.replace(minute=start.minute - start.minute % 5)
    return start + FIVE if start <= watermark else start


def _open_positions(trading_db):
    """{symbol: [(position_id, watermark)]}; an absent trading DB means no open positions."""
    positions = {}
    trading = Path(trading_db)
    if not trading.is_file():
        return positions
    db = sqlite3.connect(trading.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        for (payload,) in db.execute("SELECT payload FROM paper_positions WHERE json_extract(payload,'$.status')='OPEN'"):
            data = json.loads(payload)
            watermark = data.get("last_processed_at") or data.get("opened_at")
            positions.setdefault(data.get("symbol"), []).append((data.get("position_id"), parse_utc(watermark)))
    finally:
        db.close()
    return positions


def _position(ev, symbol, position_id, watermark, newest):
    entry = {"position_id": position_id, "watermark": utc(watermark), "missing_count": 0, "missing": [],
             "anomalies": [], "anomaly_count": 0}
    if newest - watermark > MAX_WINDOW:
        entry["error"] = f"window longer than {MAX_WINDOW.days} days (not enumerated)"
        return entry, False
    first = _first_after(watermark)
    expected = (newest - first) // FIVE + 1 if first <= newest else 0
    window = (symbol, utc(watermark), utc(newest))  # canonical utc() text sorts chronologically
    committed = {parse_utc(b) for (b,) in ev.execute(
        "SELECT bar_start FROM market_evidence WHERE symbol=? AND timeframe='5m' AND bar_start>? AND bar_start<=?",
        window)}
    on_grid = {b for b in committed if (b - first) % FIVE == timedelta(0)}
    entry["missing_count"] = expected - len(on_grid)
    stamp = first
    while stamp <= newest and len(entry["missing"]) < min(MAX_LISTED, entry["missing_count"]):
        if stamp not in on_grid:
            entry["missing"].append(utc(stamp))
        stamp += FIVE
    blocking = sorted((kind, bar_start) for kind, bar_start in (
        (kind, parse_utc(b)) for kind, b in ev.execute(
            "SELECT kind, bar_start FROM evidence_anomalies WHERE symbol=? AND timeframe='5m' AND kind IN ('GAP','LATE') "
            "AND bar_start>? AND bar_start<=?", window))
        if kind == "GAP" or bar_start not in committed)
    entry["anomaly_count"] = len(blocking)
    entry["anomalies"] = [(kind, utc(bar_start)) for kind, bar_start in blocking[:MAX_LISTED]]
    return entry, not entry["missing_count"] and not blocking


def evidence_contiguity_report(trading_db, evidence_db, symbols, *, newest):
    """Per symbol: the newest committed 5m bar, whether ``newest`` (the newest closed bar) is committed, every OPEN
    position's bounded detail and ``ready``. SQLite / parse errors propagate (the caller reports NOT_READY)."""
    positions = _open_positions(trading_db)
    report = {}
    ev = sqlite3.connect(Path(evidence_db).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        for symbol in symbols:
            (top,) = ev.execute("SELECT MAX(bar_start) FROM market_evidence WHERE symbol=? AND timeframe='5m'",
                                (symbol,)).fetchone()
            present = ev.execute("SELECT 1 FROM market_evidence WHERE symbol=? AND timeframe='5m' AND bar_start=?",
                                 (symbol, utc(newest))).fetchone() is not None
            entry = {"newest_closed": utc(newest), "newest_committed": None if top is None else utc(parse_utc(top)),
                     "newest_present": present, "positions": []}
            ready = entry["newest_present"]  # a trailing gap / stale / empty evidence is never ready
            for position_id, watermark in positions.get(symbol, []):
                detail, clean = _position(ev, symbol, position_id, watermark, newest)
                entry["positions"].append(detail)
                ready = ready and clean
            entry["ready"] = ready
            report[symbol] = entry
    finally:
        ev.close()
    return report


def readiness(trading_db, evidence_db, symbols, *, as_of=None):
    """{symbol: entry} with ``verdict`` READY / NOT_READY; an unreadable store is NOT_READY with an ``error``."""
    as_of = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    newest = newest_closed_5m(as_of)
    if not Path(evidence_db).is_file():
        return {s: {"verdict": "NOT_READY", "error": "evidence store absent", "newest_closed": None} for s in symbols}
    try:
        report = evidence_contiguity_report(trading_db, evidence_db, symbols, newest=newest)
    except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
        return {s: {"verdict": "NOT_READY", "error": f"{type(exc).__name__}: {exc}", "newest_closed": None}
                for s in symbols}
    for entry in report.values():
        entry["verdict"] = "READY" if entry.pop("ready") else "NOT_READY"
    return report


def _text(report, as_of):
    lines = [f"as_of={as_of.isoformat()}"]
    for symbol, entry in report.items():
        lines.append(f"{symbol}: {entry['verdict']}")
        if "error" in entry:
            lines.append(f"  error: {entry['error']}")
            continue
        lines.append(f"  newest_closed={entry['newest_closed']} committed={entry['newest_present']} "
                     f"newest_committed={entry['newest_committed']}")
        if not entry["positions"]:
            lines.append("  open_positions: none")
        for position in entry["positions"]:
            lines.append(f"  position {position['position_id']} watermark={position['watermark']} "
                         f"missing_bars={position['missing_count']}")
            if "error" in position:
                lines.append(f"    error: {position['error']}")
            missing = position["missing"]
            if missing:
                more = position["missing_count"] - len(missing)
                lines.append("    missing: " + ", ".join(missing) + (f" ... (+{more})" if more else ""))
            for kind, bar_start in position["anomalies"]:
                lines.append(f"    anomaly {kind} at {bar_start}")
            if position["anomaly_count"] > len(position["anomalies"]):
                lines.append(f"    ... (+{position['anomaly_count'] - len(position['anomalies'])} anomalies)")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m replay.evidence_readiness", description=__doc__.splitlines()[0])
    parser.add_argument("--trading-db", required=True)
    parser.add_argument("--evidence-db", required=True)
    parser.add_argument("--symbols", default=",".join(DEFAULT_ENABLED_SYMBOLS))
    parser.add_argument("--as-of", default=None, help="aware ISO-8601 timestamp (default: now)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    symbols = tuple(s for s in args.symbols.split(",") if s)
    if not symbols:
        parser.error("--symbols must name at least one symbol")
    as_of = datetime.now(timezone.utc)
    if args.as_of is not None:
        try:
            as_of = datetime.fromisoformat(args.as_of)
        except ValueError:
            parser.error("--as-of must be ISO-8601")
        if as_of.tzinfo is None:
            parser.error("--as-of must be timezone-aware")
    report = readiness(args.trading_db, args.evidence_db, symbols, as_of=as_of)
    print(json.dumps({"as_of": as_of.isoformat(), "symbols": report}, indent=2, sort_keys=True) if args.json
          else _text(report, as_of))
    return 0 if all(entry["verdict"] == "READY" for entry in report.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
