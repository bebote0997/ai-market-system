"""Market Evidence readiness for turning AI_FLOOR_V2_POSITION_CATCH_UP ON (Phase 13 evidence preparation). READ-ONLY.

    python -m replay.evidence_readiness --trading-db PATH --evidence-db PATH [--symbols XAUUSD,EURUSD]
                                        [--as-of ISO8601] [--json]

Both databases are opened ``mode=ro`` (nothing is created or written). Per symbol it reports the newest committed 5m
bar, the 5m bars missing since each OPEN position's watermark, the GAP / uncommitted LATE anomalies in that window and
a READY / NOT_READY verdict computed by ``runtime.config.evidence_contiguity_report``, the same function behind the
preflight check ``catch_up_evidence_contiguous`` (strict, around the clock: a market-closed weekend inside a
position's window is NOT_READY by design). The newest closed bar is the one closed at ``--as-of`` (default: now), so a
verdict is only meaningful within the 5 minutes after an in-session cycle committed it.
Exit code: 0 all READY, 1 any NOT_READY, 2 usage error.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys

from runtime.config import DEFAULT_ENABLED_SYMBOLS, evidence_contiguity_report, newest_closed_5m

MAX_LISTED = 12  # missing bars printed per position (the count is always exact)


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
            missing = position["missing"]
            lines.append(f"  position {position['position_id']} watermark={position['watermark']} "
                         f"missing_bars={len(missing)}")
            if missing:
                more = "" if len(missing) <= MAX_LISTED else f" ... (+{len(missing) - MAX_LISTED})"
                lines.append("    missing: " + ", ".join(missing[:MAX_LISTED]) + more)
            for kind, bar_start in position["anomalies"]:
                lines.append(f"    anomaly {kind} at {bar_start}")
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
