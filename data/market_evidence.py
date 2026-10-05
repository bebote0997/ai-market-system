"""V2 Market Evidence Engine foundation (Phase 2 / Batch 1): H02 evidence processing.

Isolated and observational. It turns already-loaded closed bars into committed, durable market
evidence: every new bar of a (symbol, timeframe) stream, in chronological order, exactly once,
across overlapping provider windows, retries, duplicate slots and restarts. It never calls a
provider, reads a clock, or touches setups, AI, risk, orders, fills, positions, PnL or execution,
and it is not wired into the V1 runtime.

Contract (inherited from V1, not redefined):
- A bar is identified by its window START timestamp (the V1 provider index), normalized to UTC.
- Only closed bars are evidence: ``is_closed`` is True and ``start + duration <= as_of``. This is
  exactly the rule both V1 providers apply before a bar reaches the runtime.
- Timeframes and durations are the V1 ones: 1h, 15m, 5m.

Model:
- Identity: (symbol, timeframe, bar_start). Provider is evidence, not identity.
- Ordering: per stream only, by bar_start. No cross-symbol or cross-timeframe priority exists.
- Watermark: the committed bar with the highest stream_seq; commits are strictly chronological,
  so stream_seq order == chronological order.
- Commit unit: one bar per transaction (bar + any gap record), so a crash keeps every bar
  committed before it and loses none after it; a restart re-presents and resumes.
- Anomalies are recorded, never repaired: GAP (missing intervals; no candle is fabricated),
  LATE (an uncommitted bar older than the watermark), REVISION (a committed bar re-presented with
  different content; the committed evidence is never overwritten).
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math

import pandas as pd

from runtime.config import DEFAULT_ENABLED_SYMBOLS
from storage.codec import parse_utc, utc
from storage.evidence_store import EvidenceStore

# Same timeframes and durations as data/twelve_data_provider.py and data/massive_provider.py.
TIMEFRAMES = {"1h": timedelta(hours=1), "15m": timedelta(minutes=15), "5m": timedelta(minutes=5)}
TIMEFRAME_ORDER = ("1h", "15m", "5m")  # Fixed iteration order for snapshots; implies no priority.
ANOMALY_KINDS = ("GAP", "LATE", "REVISION")


class EvidenceError(ValueError):
    """A presentation violates the market-evidence contract. Nothing from it is committed."""


def _price(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise EvidenceError(f"{name}: positive finite price required")
    return float(value)


@dataclass(frozen=True)
class MarketBar:
    symbol: str
    timeframe: str
    start: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None
    provider: str | None = None
    is_closed: bool = field(default=False)

    def __post_init__(self):
        if not isinstance(self.symbol, str) or not self.symbol:
            raise EvidenceError("symbol required")
        if self.timeframe not in TIMEFRAMES:
            raise EvidenceError("unsupported timeframe")
        if not isinstance(self.start, datetime) or self.start.tzinfo is None or self.start.utcoffset() is None:
            raise EvidenceError("bar start: timezone-aware datetime required")
        object.__setattr__(self, "start", self.start.astimezone(timezone.utc))
        o, h, l, c = (_price(getattr(self, k), k) for k in ("open", "high", "low", "close"))
        if h < max(o, c) or l > min(o, c) or h < l:
            raise EvidenceError("inconsistent OHLC")
        for name, value in (("open", o), ("high", h), ("low", l), ("close", c)):
            object.__setattr__(self, name, value)
        if self.volume is not None:
            if isinstance(self.volume, bool) or not isinstance(self.volume, (int, float)) or \
                    not math.isfinite(self.volume) or self.volume < 0:
                raise EvidenceError("volume: non-negative finite number or None required")
            object.__setattr__(self, "volume", float(self.volume))
        if self.provider is not None and not isinstance(self.provider, str):
            raise EvidenceError("provider: string or None required")
        if type(self.is_closed) is not bool:
            raise EvidenceError("is_closed: bool required")

    @property
    def end(self):
        return self.start + TIMEFRAMES[self.timeframe]

    @property
    def bar_start(self):
        return utc(self.start)

    @property
    def key(self):
        return self.symbol, self.timeframe, self.bar_start

    @property
    def digest(self):
        """Market content only (OHLCV); the provider label is evidence, not content."""
        content = {"open": self.open, "high": self.high, "low": self.low, "close": self.close,
                   "volume": self.volume}
        return hashlib.sha256(_canonical(content).encode("utf-8")).hexdigest()

    def closed_at(self, as_of):
        return self.is_closed and self.end <= as_of

    def payload(self):
        return _canonical({"symbol": self.symbol, "timeframe": self.timeframe, "bar_start": self.bar_start,
                           "open": self.open, "high": self.high, "low": self.low, "close": self.close,
                           "volume": self.volume, "provider": self.provider, "is_closed": self.is_closed})

    @classmethod
    def from_payload(cls, text):
        data = json.loads(text)
        data["start"] = parse_utc(data.pop("bar_start"))
        return cls(**data)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _aware(value, name):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise EvidenceError(f"{name}: timezone-aware datetime required")
    return value.astimezone(timezone.utc)


def _scalar(value):
    return value.item() if hasattr(value, "item") else value


def bars_from_frame(frame, *, symbol, timeframe):
    """Adapter for the existing V1 provider frame shape (UTC DatetimeIndex of bar starts;
    Open/High/Low/Close, optional volume/provider, symbol, timeframe, is_closed). Read-only."""
    if timeframe not in TIMEFRAMES:
        raise EvidenceError("unsupported timeframe")
    if not isinstance(frame, pd.DataFrame) or not isinstance(frame.index, pd.DatetimeIndex):
        raise EvidenceError("frame: DataFrame with DatetimeIndex required")
    if frame.empty:
        return ()
    if frame.index.tz is None:
        raise EvidenceError("frame: timezone-aware index required")
    if frame.index.has_duplicates:
        raise EvidenceError("frame: duplicate bar timestamps")
    missing = {"Open", "High", "Low", "Close", "is_closed"} - set(frame.columns)
    if missing:
        raise EvidenceError(f"frame: missing columns {sorted(missing)}")
    bars = []
    for stamp, row in frame.iterrows():
        for column, expected in (("symbol", symbol), ("timeframe", timeframe)):
            if column in frame.columns and row[column] != expected:
                raise EvidenceError(f"frame: {column} mismatch")
        volume = _scalar(row["volume"]) if "volume" in frame.columns else None
        if isinstance(volume, float) and math.isnan(volume):
            volume = None  # V1 represents an absent volume as None/NaN.
        provider = row["provider"] if "provider" in frame.columns else None
        bars.append(MarketBar(symbol, timeframe, stamp.to_pydatetime(),
                              *(_scalar(row[k]) for k in ("Open", "High", "Low", "Close")),
                              volume=volume, provider=provider if isinstance(provider, str) else None,
                              is_closed=_scalar(row["is_closed"])))  # Non-bool is rejected by MarketBar.
    return tuple(bars)


@dataclass(frozen=True)
class Anomaly:
    kind: str
    symbol: str
    timeframe: str
    bar_start: str
    details: dict


@dataclass(frozen=True)
class IngestResult:
    symbol: str
    timeframe: str
    as_of: str
    presented: int
    committed: tuple  # MarketBar, chronological
    duplicates: tuple  # bar_start of already-committed identical bars
    held: tuple  # bar_start of bars not yet closed at as_of (and every bar after the first one)
    late: tuple  # bar_start of uncommitted bars older than the watermark
    revisions: tuple  # bar_start of committed bars re-presented with different content
    gaps: tuple  # Anomaly(kind="GAP") recorded by this ingestion
    watermark: str | None

    @property
    def contiguous(self):
        """True only when this ingestion recorded no gap; a gap is never silently 'complete'."""
        return not self.gaps


class MarketEvidenceEngine:
    def __init__(self, store, *, enabled_symbols=DEFAULT_ENABLED_SYMBOLS):
        if not isinstance(store, EvidenceStore):
            raise ValueError("MARKET EVIDENCE sidecar (EvidenceStore) required; the trading DB is never used")
        self.store = store
        self.enabled_symbols = tuple(enabled_symbols)

    # --- writes -------------------------------------------------------------------------------
    def ingest(self, symbol, timeframe, bars, *, as_of):
        """Commit every new closed bar of one stream, chronologically, each exactly once."""
        as_of = _aware(as_of, "as_of")
        self._stream(symbol, timeframe)
        bars = tuple(bars)
        unique = {}
        for bar in bars:
            if not isinstance(bar, MarketBar) or (bar.symbol, bar.timeframe) != (symbol, timeframe):
                raise EvidenceError("bar does not belong to this stream")
            if bar.key in unique and unique[bar.key] != bar:
                raise EvidenceError(f"conflicting duplicate bar {bar.bar_start} in one presentation")
            unique[bar.key] = bar
        ordered = sorted(unique.values(), key=lambda b: b.start)
        eligible, held = self._split_closed(ordered, as_of)
        outcome = {"COMMITTED": [], "DUPLICATE": [], "LATE": [], "REVISION": []}
        gaps = []
        for bar in eligible:
            kind, gap = self._commit(bar)
            outcome[kind].append(bar if kind == "COMMITTED" else bar.bar_start)
            if gap is not None:
                gaps.append(gap)
        last = self.watermark(symbol, timeframe)
        return IngestResult(symbol, timeframe, utc(as_of), len(bars), tuple(outcome["COMMITTED"]),
                            tuple(outcome["DUPLICATE"]), tuple(b.bar_start for b in held),
                            tuple(outcome["LATE"]), tuple(outcome["REVISION"]), tuple(gaps),
                            None if last is None else last.bar_start)

    def ingest_snapshot(self, symbol, snapshot, *, as_of):
        """Ingest an already-loaded V1 snapshot {timeframe: frame}. Every frame is validated
        before anything is committed; a missing timeframe is presented as no bars (NO_DATA)."""
        if not isinstance(snapshot, dict):
            raise EvidenceError("snapshot: mapping required")
        presented = {tf: bars_from_frame(snapshot[tf], symbol=symbol, timeframe=tf) if tf in snapshot else ()
                     for tf in TIMEFRAME_ORDER}
        return {tf: self.ingest(symbol, tf, presented[tf], as_of=as_of) for tf in TIMEFRAME_ORDER}

    @staticmethod
    def _split_closed(ordered, as_of):
        """Closed prefix only: a bar after a not-yet-closed one is held too, so chronology holds."""
        for index, bar in enumerate(ordered):
            if not bar.closed_at(as_of):
                return ordered[:index], ordered[index:]
        return ordered, []

    def _commit(self, bar):
        """One transaction per bar: identity check, watermark check, gap record, insert."""
        symbol, timeframe, bar_start = bar.key
        with self.store.transaction():
            db = self.store.db
            existing = db.execute("SELECT digest FROM market_evidence WHERE symbol=? AND timeframe=? AND bar_start=?",
                                  bar.key).fetchone()
            if existing is not None:
                if existing[0] == bar.digest:
                    return "DUPLICATE", None
                self._anomaly("REVISION", bar, {"committed_digest": existing[0], "presented_digest": bar.digest,
                                                "presented_provider": bar.provider})
                return "REVISION", None
            top = db.execute("SELECT bar_start, stream_seq FROM market_evidence WHERE symbol=? AND timeframe=? "
                             "ORDER BY stream_seq DESC LIMIT 1", (symbol, timeframe)).fetchone()
            gap = None
            if top is not None:
                watermark = parse_utc(top[0])
                if bar.start <= watermark:
                    self._anomaly("LATE", bar, {"watermark": top[0], "presented_digest": bar.digest})
                    return "LATE", None
                expected = watermark + TIMEFRAMES[timeframe]
                if bar.start > expected:
                    missing = (bar.start - watermark) / TIMEFRAMES[timeframe] - 1
                    gap = self._anomaly("GAP", bar, {"after": top[0], "before": bar_start,
                                                     "missing_seconds": (bar.start - expected).total_seconds(),
                                                     "missing_intervals": missing})
            db.execute("INSERT INTO market_evidence(symbol,timeframe,bar_start,stream_seq,payload,digest) "
                       "VALUES(?,?,?,?,?,?)", (*bar.key, 1 if top is None else top[1] + 1, bar.payload(), bar.digest))
            return "COMMITTED", gap

    def _anomaly(self, kind, bar, details):
        anomaly = Anomaly(kind, bar.symbol, bar.timeframe, bar.bar_start, details)
        payload = _canonical(details)
        anomaly_id = hashlib.sha256("|".join((kind, *bar.key, bar.digest)).encode("utf-8")).hexdigest()
        self.store.db.execute("INSERT OR IGNORE INTO evidence_anomalies(anomaly_id,symbol,timeframe,kind,bar_start,payload) "
                              "VALUES(?,?,?,?,?,?)", (anomaly_id, *bar.key[:2], kind, bar.bar_start, payload))
        return anomaly

    # --- reads --------------------------------------------------------------------------------
    def _stream(self, symbol, timeframe):
        if symbol not in self.enabled_symbols:
            raise EvidenceError(f"symbol {symbol!r} is not enabled")
        if timeframe not in TIMEFRAMES:
            raise EvidenceError("unsupported timeframe")

    def committed(self, symbol, timeframe, *, after=None):
        """Committed evidence of one stream in chronological (stream_seq) order, optionally after a bar start."""
        if timeframe not in TIMEFRAMES:
            raise EvidenceError("unsupported timeframe")
        rows = self.store.db.execute("SELECT payload FROM market_evidence WHERE symbol=? AND timeframe=? "
                                     "ORDER BY stream_seq", (symbol, timeframe)).fetchall()
        bars = tuple(MarketBar.from_payload(row[0]) for row in rows)
        if after is not None:
            after = _aware(after, "after")
            bars = tuple(bar for bar in bars if bar.start > after)
        return bars

    def watermark(self, symbol, timeframe):
        row = self.store.db.execute("SELECT payload FROM market_evidence WHERE symbol=? AND timeframe=? "
                                    "ORDER BY stream_seq DESC LIMIT 1", (symbol, timeframe)).fetchone()
        return None if row is None else MarketBar.from_payload(row[0])

    def anomalies(self, symbol=None, timeframe=None):
        rows = self.store.db.execute("SELECT kind, symbol, timeframe, bar_start, payload, anomaly_id "
                                     "FROM evidence_anomalies").fetchall()
        found = [(Anomaly(r[0], r[1], r[2], r[3], json.loads(r[4])), r[5]) for r in rows
                 if (symbol is None or r[1] == symbol) and (timeframe is None or r[2] == timeframe)]
        found.sort(key=lambda item: (item[0].symbol, item[0].timeframe, parse_utc(item[0].bar_start),
                                     item[0].kind, item[1]))
        return tuple(anomaly for anomaly, _ in found)


__all__ = ["Anomaly", "EvidenceError", "IngestResult", "MarketBar", "MarketEvidenceEngine", "TIMEFRAMES",
           "bars_from_frame"]
