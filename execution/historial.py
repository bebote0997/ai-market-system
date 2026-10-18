"""Read-only EURUSD reference statistics ("Historial / baseline") from EARLIER systems. Stdlib only; never raises.

These numbers are NOT OANDA demo trades and are never mixed with them: they come from optional, aggregated, local JSON
files (``data/historial/``, or ``AI_FLOOR_HISTORIAL_DIR``) built offline with ``scripts/historial_build.py``. Two known
sources, always returned in this order:

- ``v1_replay`` -> ``v1_replay_eurusd.json``: replay of the V1 strategy over Twelve Data candles.
- ``paper`` -> ``paper_closed_trades_eurusd.json``: PAPER closed trades export (aggregated, no rows).

Schema version 1 (exact key set, money-free: counts and R-multiples only)::

    {"version": 1, "source": "v1_replay"|"paper", "symbol": "EURUSD", "generated_at": ISO datetime,
     "period": {"from": ISO date, "to": ISO date}, "trades": int>=0, "wins": int>=0, "losses": int>=0,
     "win_rate": 0..1, "expectancy_r": number, "profit_factor": number|null, "max_drawdown_r": number>=0,
     "avg_r_win": number|null, "avg_r_loss": number|null, "notes": str<=200}

Unknown/missing keys, duplicate keys, NaN/Infinity, bool-as-int, another symbol/source, oversized files (> 64 KB),
symlinks or non-regular files => that source is reported as ``NO_DATA`` ("sin datos"). Missing data is never invented.
"""
from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
import math
import os
from pathlib import Path
import stat

SCHEMA_VERSION = 1
SYMBOL = "EURUSD"
MAX_BYTES = 64 * 1024
MAX_NOTES = 200
ENV_DIR = "AI_FLOOR_HISTORIAL_DIR"
DEFAULT_DIR = Path(__file__).resolve().parents[1] / "data" / "historial"
SOURCES = (("v1_replay", "v1_replay_eurusd.json", "Replay estrategia V1 (velas Twelve Data)"),
           ("paper", "paper_closed_trades_eurusd.json", "Trades cerrados PAPER (agregado)"))
FILENAMES = {source: filename for source, filename, _ in SOURCES}
KEYS = frozenset({"version", "source", "symbol", "generated_at", "period", "trades", "wins", "losses", "win_rate",
                  "expectancy_r", "profit_factor", "max_drawdown_r", "avg_r_win", "avg_r_loss", "notes"})
PERIOD_KEYS = frozenset({"from", "to"})
OK, NO_DATA = "OK", "NO_DATA"
DIGITS = 4


@dataclass(frozen=True)
class Baseline:
    source: str
    filename: str
    label: str
    status: str  # OK | NO_DATA
    stats: dict | None = None
    reason: str | None = None  # short, content-free reason for NO_DATA (never file content)


class SchemaError(ValueError):
    pass


def _is_int(value):
    return type(value) is int


def _is_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _require(condition, reason):
    if not condition:
        raise SchemaError(reason)


def _iso_datetime(value):
    _require(isinstance(value, str) and 0 < len(value) <= 40, "generated_at")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise SchemaError("generated_at") from None
    return parsed


def _iso_date(value, name):
    _require(isinstance(value, str) and len(value) == 10, name)
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise SchemaError(name) from None


def validate_baseline(data, source):
    """Strict schema v1 check. Returns a clean copy of ``data``; raises SchemaError(reason) on anything else."""
    _require(source in FILENAMES, "source")
    _require(isinstance(data, dict) and set(data) == KEYS, "keys")
    _require(_is_int(data["version"]) and data["version"] == SCHEMA_VERSION, "version")
    _require(data["source"] == source, "source")
    _require(data["symbol"] == SYMBOL, "symbol")
    _iso_datetime(data["generated_at"])
    period = data["period"]
    _require(isinstance(period, dict) and set(period) == PERIOD_KEYS, "period")
    _require(_iso_date(period["from"], "period") <= _iso_date(period["to"], "period"), "period")
    trades, wins, losses = data["trades"], data["wins"], data["losses"]
    _require(all(_is_int(v) and v >= 0 for v in (trades, wins, losses)) and wins + losses <= trades, "counts")
    _require(_is_number(data["win_rate"]) and 0 <= data["win_rate"] <= 1, "win_rate")
    expected_rate = wins / trades if trades else 0
    _require(abs(data["win_rate"] - expected_rate) <= 1e-3, "win_rate")
    _require(_is_number(data["expectancy_r"]), "expectancy_r")
    _require(_is_number(data["max_drawdown_r"]) and data["max_drawdown_r"] >= 0, "max_drawdown_r")
    for name in ("profit_factor", "avg_r_win", "avg_r_loss"):
        _require(data[name] is None or _is_number(data[name]), name)
    _require(data["profit_factor"] is None or data["profit_factor"] >= 0, "profit_factor")
    notes = data["notes"]
    _require(isinstance(notes, str) and len(notes) <= MAX_NOTES and notes.isprintable(), "notes")
    return {**data, "period": dict(period)}


def _reject_constant(name):
    raise SchemaError("non_finite")


def _no_duplicates(pairs):
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise SchemaError("duplicate_key")
    return dict(pairs)


def parse_json(text):
    """json.loads that refuses NaN/Infinity and duplicate keys."""
    return json.loads(text, parse_constant=_reject_constant, object_pairs_hook=_no_duplicates)


def _read_regular_file(path):
    """Bytes of a regular, non-symlink file <= MAX_BYTES (checked on the descriptor). Raises SchemaError/OSError."""
    if path.is_symlink():
        raise SchemaError("symlink")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        info = os.fstat(fd)
        _require(stat.S_ISREG(info.st_mode), "not_regular")
        _require(info.st_size <= MAX_BYTES, "oversize")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            raw = handle.read(MAX_BYTES + 1)
    finally:
        os.close(fd)
    _require(len(raw) <= MAX_BYTES, "oversize")
    return raw


def historial_dir(directory=None):
    if directory is not None:
        return Path(directory)
    override = os.environ.get(ENV_DIR, "").strip()
    return Path(override) if override else DEFAULT_DIR


def load_one(directory, source, filename, label):
    path = Path(directory) / filename
    try:
        if not path.exists() and not path.is_symlink():
            return Baseline(source, filename, label, NO_DATA, reason="missing")
        stats = validate_baseline(parse_json(_read_regular_file(path).decode("utf-8")), source)
    except SchemaError as exc:
        return Baseline(source, filename, label, NO_DATA, reason=str(exc) or "invalid")
    except Exception:  # unreadable / not UTF-8 / not JSON: the baseline is optional
        return Baseline(source, filename, label, NO_DATA, reason="invalid")
    return Baseline(source, filename, label, OK, stats=stats)


def load_baselines(directory=None):
    """One Baseline per known source, fixed order [v1_replay, paper]. Never raises."""
    try:
        base = historial_dir(directory)
    except Exception:
        base = DEFAULT_DIR
    results = []
    for source, filename, label in SOURCES:
        try:
            results.append(load_one(base, source, filename, label))
        except Exception:
            results.append(Baseline(source, filename, label, NO_DATA, reason="invalid"))
    return results


# --- offline aggregation (used by scripts/historial_build.py) -------------------------------------------------------

def normalize_symbol(value):
    return str(value or "").upper().replace("/", "").replace("_", "").replace("-", "").strip()


def _number(row, name):
    value = row.get(name)
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise ValueError(name)
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(name)
    return number


def _timestamp(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(name)
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def row_r(row):
    """R-multiple of one closed trade: ``r_multiple`` when given, else ``pnl / initial_risk`` (money) when given, else
    from prices: (exit - entry) / (entry - stop) for BUY, mirrored for SELL. Raises ValueError when not computable."""
    r = _number(row, "r_multiple")
    if r is not None:
        return r
    pnl, risk = _number(row, "pnl"), _number(row, "initial_risk")
    if pnl is not None and risk is not None:
        if risk <= 0:
            raise ValueError("initial_risk")
        return pnl / risk
    side = str(row.get("side") or "").strip().upper()
    entry, stop, exit_ = _number(row, "entry"), _number(row, "stop"), _number(row, "exit")
    if side not in ("BUY", "LONG", "SELL", "SHORT") or None in (entry, stop, exit_):
        raise ValueError("r")
    sign = 1 if side in ("BUY", "LONG") else -1
    risk = sign * (entry - stop)
    if risk <= 0:
        raise ValueError("stop")
    return sign * (exit_ - entry) / risk


def _round(value):
    return None if value is None else round(value, DIGITS)


def build_summary(rows, source, *, now=None, notes=""):
    """Aggregate closed trades (EURUSD rows only) into a schema-v1 dict. Pure; raises ValueError("row N: field")."""
    if source not in FILENAMES:
        raise ValueError("source")
    trades = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"row {index}: not an object")
        if normalize_symbol(row.get("symbol")) != SYMBOL:
            continue
        try:
            closed = _timestamp(row.get("closed_at"), "closed_at")
            opened = _timestamp(row.get("opened_at"), "opened_at") if row.get("opened_at") else closed
            if opened > closed:
                raise ValueError("opened_at")
            trades.append((closed, opened, row_r(row)))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"row {index}: {exc.args[0] if exc.args else 'invalid'}") from None
    trades.sort(key=lambda t: t[0])
    rs = [r for _, _, r in trades]
    winners, losers = [r for r in rs if r > 0], [r for r in rs if r < 0]
    cumulative = peak = drawdown = 0.0
    for r in rs:
        cumulative += r
        peak = max(peak, cumulative)
        drawdown = max(drawdown, peak - cumulative)
    gross_loss = -sum(losers)
    now = now or datetime.now(timezone.utc)
    if trades:
        period = {"from": min(o for _, o, _ in trades).date().isoformat(), "to": trades[-1][0].date().isoformat()}
    else:
        period = {"from": now.date().isoformat(), "to": now.date().isoformat()}
    return {"version": SCHEMA_VERSION, "source": source, "symbol": SYMBOL,
            "generated_at": now.astimezone(timezone.utc).isoformat(timespec="seconds"), "period": period,
            "trades": len(rs), "wins": len(winners), "losses": len(losers),
            "win_rate": _round(len(winners) / len(rs)) if rs else 0.0,
            "expectancy_r": _round(sum(rs) / len(rs)) if rs else 0.0,
            "profit_factor": _round(sum(winners) / gross_loss) if gross_loss > 0 else None,
            "max_drawdown_r": _round(drawdown),
            "avg_r_win": _round(sum(winners) / len(winners)) if winners else None,
            "avg_r_loss": _round(sum(losers) / len(losers)) if losers else None,
            "notes": notes}
