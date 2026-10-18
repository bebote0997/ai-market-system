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
     "avg_r_win": number>0|null, "avg_r_loss": number<0|null, "notes": str<=200 (may be empty)}

Consistency: ``trades == 0`` => zero counts/rates/drawdown and null profit_factor/averages; ``wins > 0`` <=> avg_r_win
present (same for losses/avg_r_loss); with no break-even trades, ``expectancy_r`` must match
``win_rate * avg_r_win + (1 - win_rate) * avg_r_loss`` within 0.05 R.

Unknown/missing keys, duplicate keys, NaN/Infinity, bool-as-int, another symbol/source, oversized files (> 64 KB),
symlinks (the file or ANY component of the data directory path) or non-regular files => that source is reported as
``NO_DATA`` ("sin datos"). Missing data is never invented.
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
EXPECTANCY_TOLERANCE = 0.05
SYMLINK_PATH = "ruta con enlace simbólico"


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


class RowError(ValueError):
    """Content-free conversion error: the message names only the row number and the field, never the value."""


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
    profit_factor, avg_win, avg_loss = data["profit_factor"], data["avg_r_win"], data["avg_r_loss"]
    _require(profit_factor is None or profit_factor >= 0, "profit_factor")
    _require(avg_win is None or avg_win > 0, "avg_r_win")
    _require(avg_loss is None or avg_loss < 0, "avg_r_loss")
    _require((wins > 0) == (avg_win is not None), "avg_r_win")
    _require((losses > 0) == (avg_loss is not None), "avg_r_loss")
    if trades == 0:
        _require(data["win_rate"] == 0 and data["expectancy_r"] == 0 and data["max_drawdown_r"] == 0, "empty")
        _require(profit_factor is None, "profit_factor")
    if avg_win is not None and avg_loss is not None and wins + losses == trades:  # break-even trades: skip
        rate = data["win_rate"]
        expected = rate * avg_win + (1 - rate) * avg_loss
        _require(abs(data["expectancy_r"] - expected) <= EXPECTANCY_TOLERANCE, "expectancy_r")
    notes = data["notes"]
    _require(isinstance(notes, str) and len(notes) <= MAX_NOTES and (notes == "" or notes.isprintable()), "notes")
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


def has_symlink_component(path, base=None):
    """True when any component of ``path`` (made absolute and normalized, NOT resolved) below ``base`` -- or below the
    filesystem root when ``path`` is not under ``base`` -- is a symlink (checked with lstat). Components that do not
    exist yet are not symlinks. Other OSErrors propagate."""
    path = Path(os.path.abspath(path))
    anchor = Path(path.anchor)
    if base is not None and path.is_relative_to(os.path.abspath(base)):
        anchor = Path(os.path.abspath(base))
    current = anchor
    for part in path.relative_to(anchor).parts:
        current = current / part
        try:
            if stat.S_ISLNK(os.lstat(current).st_mode):
                return True
        except FileNotFoundError:
            return False
    return False


def historial_dir(directory=None):
    """Data directory as an absolute, normalized (not resolved) path: ``directory``, else ``AI_FLOOR_HISTORIAL_DIR``,
    else ``data/historial`` in the repository."""
    if directory is None:
        override = os.environ.get(ENV_DIR, "").strip()
        directory = override or DEFAULT_DIR
    return Path(os.path.abspath(directory))


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
        refused = has_symlink_component(base, DEFAULT_DIR.parents[1])
    except Exception:
        base, refused = DEFAULT_DIR, True
    if refused:  # ruta con enlace simbólico (or unreadable path): refuse every source
        return [Baseline(source, filename, label, NO_DATA, reason="symlink_path") for source, filename, label in SOURCES]
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
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise RowError(name)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise RowError(name) from None  # never chain: float()'s message quotes the value
    if not math.isfinite(number):
        raise RowError(name)
    return number


def _timestamp(value, name):
    if not isinstance(value, str) or not value.strip():
        raise RowError(name)
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        raise RowError(name) from None  # fromisoformat's message quotes the value
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
            raise RowError("initial_risk")
        return pnl / risk
    side = str(row.get("side") or "").strip().upper()
    entry, stop, exit_ = _number(row, "entry"), _number(row, "stop"), _number(row, "exit")
    if side not in ("BUY", "LONG", "SELL", "SHORT") or None in (entry, stop, exit_):
        raise RowError("r")
    sign = 1 if side in ("BUY", "LONG") else -1
    risk = sign * (entry - stop)
    if risk <= 0:
        raise RowError("stop")
    return sign * (exit_ - entry) / risk


def _round(value):
    return None if value is None else round(value, DIGITS)


def build_summary(rows, source, *, now=None, notes=""):
    """Aggregate closed trades (EURUSD rows only) into a schema-v1 dict that ``validate_baseline`` accepts. Pure; raises
    RowError("row N: field") whose message never contains row content. Each R is rounded to DIGITS before classifying
    it as win (> 0), loss (< 0) or break-even, so the averages keep their sign after rounding."""
    if source not in FILENAMES:
        raise RowError("source")
    trades = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise RowError(f"row {index}: not an object")
        if normalize_symbol(row.get("symbol")) != SYMBOL:
            continue
        try:
            closed = _timestamp(row.get("closed_at"), "closed_at")
            opened = _timestamp(row.get("opened_at"), "opened_at") if row.get("opened_at") else closed
            if opened > closed:
                raise RowError("opened_at")
            trades.append((closed, opened, _round(row_r(row))))
        except RowError as exc:
            raise RowError(f"row {index}: {exc.args[0]}") from None
        except Exception:  # anything unexpected: still content-free
            raise RowError(f"row {index}: invalid") from None
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
