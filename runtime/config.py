from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import os
import re

from ui.adapters import MARKETS

SUPPORTED_SYMBOLS = MARKETS
DEFAULT_ENABLED_SYMBOLS = ("XAUUSD", "EURUSD")
# V2 Phase 8 / P1-B (DEC-8.13, DEC-8.16): per-symbol catch-up scope.
CATCH_UP_SYMBOLS_ENV = "AI_FLOOR_V2_POSITION_CATCH_UP_SYMBOLS"
REX_ENV = "AI_FLOOR_V2_REX"  # V2 Phase 8 / P3: REX evidence flag (OFF by default)
RHALT_ENV = "AI_FLOOR_V2_RHALT"  # V2 Phase 8 / P4a: R-HALT admission gate (OFF by default; ON requires REX ON)
CATCH_UP_EXCLUDED_SYMBOLS = ("NAS100",)  # never in the catch-up scope, even if a future config enables it
_SCOPE_TOKEN = re.compile(r"[A-Z0-9]+")


def parse_catch_up_symbols(raw, enabled_symbols):
    """Strict grammar (P8.6 design 1.1): comma-separated tokens matching ``^[A-Z0-9]+$`` exactly (no whitespace, no
    lower case, no empty token or trailing comma), no duplicates, never NAS100, each in SUPPORTED_SYMBOLS and in
    ``enabled_symbols``. Returns the canonical tuple in ``enabled_symbols`` order; anything else raises ValueError."""
    if not isinstance(raw, str) or not raw:
        raise ValueError(f"{CATCH_UP_SYMBOLS_ENV} must be a non-empty list")
    tokens = raw.split(",")
    if any(not _SCOPE_TOKEN.fullmatch(token) for token in tokens):
        raise ValueError(f"{CATCH_UP_SYMBOLS_ENV}: invalid token (exact upper-case symbols, no spaces)")
    if len(set(tokens)) != len(tokens):
        raise ValueError(f"{CATCH_UP_SYMBOLS_ENV}: duplicate symbol")
    for token in tokens:
        if token in CATCH_UP_EXCLUDED_SYMBOLS:
            raise ValueError(f"{CATCH_UP_SYMBOLS_ENV}: {token} is never in the catch-up scope")
        if token not in SUPPORTED_SYMBOLS or token not in enabled_symbols:
            raise ValueError(f"{CATCH_UP_SYMBOLS_ENV}: {token} is not an enabled symbol")
    return tuple(symbol for symbol in enabled_symbols if symbol in tokens)


@dataclass(frozen=True)
class RuntimeConfig:
    db_path: Path = Path("data/runtime/trading_floor.db")
    cadence_minutes: int = 15
    enabled_symbols: tuple = DEFAULT_ENABLED_SYMBOLS
    sessions: tuple = ("LONDON", "NEW_YORK")
    scheduler_enabled: bool = False
    starting_equity: float = 10000.0
    account_id: str = "paper-main"
    max_age_seconds: tuple = (("1h", 7200), ("15m", 1800), ("5m", 600))
    market_provider_mode: str = "twelve_data"
    ai_provider_mode: str = "deterministic"
    macro_provider_mode: str = "none"
    # V2 Phase 2 / B2.3B: Market Evidence ingestion + chronological position catch-up. OFF by
    # default. V2 Phase 8 / P8.4 (DEC-8.6, R1): from_env reads it ONLY from the exact value
    # AI_FLOOR_V2_POSITION_CATCH_UP="1" together with AI_FLOOR_MARKET_EVIDENCE_PATH; any other non-empty
    # value fails closed. Activation itself still needs the P8 activation gate and Owner approval.
    v2_position_catch_up: bool = False
    market_evidence_path: Path | None = None
    # V2 Phase 8 / P1-B (DEC-8.13 / 8.14 / 8.16): the symbols managed by catch-up when the flag is ON (canonical,
    # non-empty); () when OFF. Enabled symbols outside it keep the legacy newest-bar path (observe-only evidence).
    v2_position_catch_up_symbols: tuple = ()
    # V2 Phase 7 / P7.1 Batch B: durable AI_CALL rows (observability only; never a decision input). OFF by default;
    # from_env never sets it. Activation needs P7.2 certification and separate owner approval.
    v2_ai_call_audit: bool = False
    # V2 Phase 7 / P7.1 Batch C: per-cycle AI short-circuit + time budget + provider health events. Fail-closed only
    # (can only remove execution eligibility). OFF by default; from_env never sets it.
    v2_ai_resilience: bool = False
    # V2 Phase 8 / P3 (DEC-8.22-d; DEC-8.21b PROVISIONAL, implementation only): REX run evidence in the journal
    # (observability only, alternative B). OFF by default; from_env reads only the exact value AI_FLOOR_V2_REX="1".
    # Implementation is not activation: turning it on operationally needs a separate Owner authorization.
    v2_rex: bool = False
    # V2 Phase 8 / P4a (DEC-8.17b Alternative 1): R-HALT admission gate and post-halt persistence policy. OFF by
    # default; ON requires v2_rex (the admission evidence lives in REX). Implementation is not activation.
    v2_rhalt: bool = False

    def __post_init__(self):
        if (self.cadence_minutes <= 0 or 60 % self.cadence_minutes or
                not self.enabled_symbols or len(set(self.enabled_symbols)) != len(self.enabled_symbols) or
                not set(self.enabled_symbols) <= set(SUPPORTED_SYMBOLS)):
            raise ValueError("invalid cadence or enabled_symbols")
        if not set(self.sessions) <= {"LONDON", "NEW_YORK"} or self.starting_equity <= 0:
            raise ValueError("invalid sessions or equity")
        if self.market_provider_mode not in {"none", "massive", "twelve_data"} or self.ai_provider_mode not in {"deterministic", "openai"}:
            raise ValueError("invalid provider mode")
        if self.macro_provider_mode not in {"none", "finnhub", "official_hybrid", "fxmacrodata"}:
            raise ValueError("invalid macro provider mode")
        if not isinstance(self.v2_position_catch_up, bool):
            raise ValueError("v2_position_catch_up must be a bool")
        if not isinstance(self.v2_ai_call_audit, bool):
            raise ValueError("v2_ai_call_audit must be a bool")
        if not isinstance(self.v2_ai_resilience, bool):
            raise ValueError("v2_ai_resilience must be a bool")
        if not isinstance(self.v2_rex, bool):
            raise ValueError("v2_rex must be a bool")
        if not isinstance(self.v2_rhalt, bool):
            raise ValueError("v2_rhalt must be a bool")
        if self.v2_rhalt and not self.v2_rex:
            raise ValueError("v2_rhalt requires v2_rex (the admission evidence is recorded in REX)")
        if self.v2_position_catch_up and (
                self.market_evidence_path is None
                or Path(self.market_evidence_path).resolve() == Path(self.db_path).resolve()):
            raise ValueError("v2_position_catch_up requires a separate market_evidence_path")
        scope = self.v2_position_catch_up_symbols
        if not isinstance(scope, tuple) or any(not isinstance(symbol, str) for symbol in scope):
            raise ValueError("v2_position_catch_up_symbols must be a tuple of symbols")
        if self.v2_position_catch_up != bool(scope):
            raise ValueError("v2_position_catch_up ON requires a non-empty v2_position_catch_up_symbols; OFF requires ()")
        if scope and parse_catch_up_symbols(",".join(scope), tuple(self.enabled_symbols)) != scope:
            raise ValueError("v2_position_catch_up_symbols must be canonical (enabled_symbols order)")

    def fingerprint(self):
        content = {"cadence_minutes": self.cadence_minutes, "enabled_symbols": self.enabled_symbols,
                   "supported_symbols": SUPPORTED_SYMBOLS,
                   "sessions": self.sessions, "scheduler_enabled": self.scheduler_enabled,
                   "max_age_seconds": self.max_age_seconds, "paper_mode": True,
                   "market_provider_mode": self.market_provider_mode,
                   "ai_provider_mode": self.ai_provider_mode,
                   "macro_provider_mode": self.macro_provider_mode}
        if self.v2_position_catch_up:  # OFF keeps the V1 fingerprint byte-identical.
            content["v2_position_catch_up"] = True
            content["v2_position_catch_up_symbols"] = list(self.v2_position_catch_up_symbols)  # P1-B: exact scope
        if self.v2_ai_call_audit:  # OFF keeps the fingerprint byte-identical.
            content["v2_ai_call_audit"] = True
        if self.v2_ai_resilience:  # OFF keeps the fingerprint byte-identical.
            content["v2_ai_resilience"] = True
        if self.v2_rex:  # P3: OFF keeps the fingerprint byte-identical.
            content["v2_rex"] = True
        if self.v2_rhalt:  # P4a: OFF keeps the fingerprint byte-identical.
            content["v2_rhalt"] = True
        return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()

    def catch_up_applies(self, symbol):
        """P1-B: True only for a symbol inside the ON catch-up scope; every other symbol keeps the legacy path."""
        return self.v2_position_catch_up and symbol in self.v2_position_catch_up_symbols

    @property
    def certification_eligibility(self):
        """P1-B (DEC-8.16, design 1.9.3): mixed-path scopes are TECHNICAL_ONLY; the full scope still needs G-8.INT;
        OFF is the legacy (HIGH-8.1) path. A label only: it certifies nothing."""
        if not self.v2_position_catch_up:
            return "LEGACY_NOT_CERTIFIABLE"
        if set(self.v2_position_catch_up_symbols) != set(self.enabled_symbols):
            return "TECHNICAL_ONLY"
        return "REQUIRES_G8_INT"

    @classmethod
    def from_env(cls):
        from runtime.env import load_local_env
        load_local_env()
        enabled = tuple(s.strip().upper() for s in
                        os.environ.get("AI_FLOOR_ENABLED_SYMBOLS", ",".join(DEFAULT_ENABLED_SYMBOLS)).split(","))
        catch_up = os.environ.get("AI_FLOOR_V2_POSITION_CATCH_UP", "")
        if catch_up not in ("", "0", "1"):
            raise ValueError("AI_FLOOR_V2_POSITION_CATCH_UP must be unset, '0' or '1'")  # never a truthy guess
        evidence_path = os.environ.get("AI_FLOOR_MARKET_EVIDENCE_PATH") or None
        raw_scope = os.environ.get(CATCH_UP_SYMBOLS_ENV)  # None = absent; "" = present and empty
        if catch_up != "1" and raw_scope is not None:
            raise ValueError(f"{CATCH_UP_SYMBOLS_ENV} is set while the catch-up flag is OFF")  # DEC-8.13
        scope = parse_catch_up_symbols(raw_scope, enabled) if catch_up == "1" else ()
        rex = os.environ.get(REX_ENV, "")
        if rex not in ("", "0", "1"):
            raise ValueError(f"{REX_ENV} must be unset, '0' or '1'")  # never a truthy guess
        rhalt = os.environ.get(RHALT_ENV, "")
        if rhalt not in ("", "0", "1"):
            raise ValueError(f"{RHALT_ENV} must be unset, '0' or '1'")
        return cls(db_path=Path(os.environ.get("AI_FLOOR_DB_PATH", "data/runtime/trading_floor.db")),
                   v2_position_catch_up=catch_up == "1",  # __post_init__ rejects ON without a separate evidence path
                   v2_position_catch_up_symbols=scope,
                   v2_rex=rex == "1",
                   v2_rhalt=rhalt == "1",
                   market_evidence_path=None if evidence_path is None else Path(evidence_path),
                   enabled_symbols=enabled,
                   scheduler_enabled=os.environ.get("AI_FLOOR_SCHEDULER", "0") == "1",
                   market_provider_mode=os.environ.get("AI_FLOOR_MARKET_PROVIDER", "twelve_data"),
                   ai_provider_mode=os.environ.get("AI_FLOOR_AI_PROVIDER", "deterministic"),
                   macro_provider_mode=os.environ.get("AI_FLOOR_MACRO_PROVIDER", "none"))


def persistent_write_probe(db, path):
    """V2 Phase 8 / P8.4F-G (DEC-8.11): True only if the SQLite database at ``path`` (open on connection ``db`` in
    autocommit mode) accepts a PERSISTENT write. A TEMP table lives in SQLite's temp database and proves nothing about
    the file, so the probe takes the write lock (BEGIN IMMEDIATE), creates and fills a table on ``main``, and ALWAYS
    rolls back: no data, schema or schema-version change. It also requires OS write access to the file and to its
    directory (rollback journal / WAL / SHM). SQLite errors propagate; callers treat them as not writable."""
    path = Path(path)
    if not (os.access(path, os.W_OK) and os.access(path.parent, os.W_OK)):
        return False

    def schema():
        return (db.execute("SELECT type, name, sql FROM main.sqlite_master ORDER BY type, name").fetchall(),
                db.execute("PRAGMA main.schema_version").fetchone()[0], db.execute("PRAGMA main.user_version").fetchone()[0])

    before = schema()
    db.execute("BEGIN IMMEDIATE")
    try:
        db.execute("CREATE TABLE main.v2_preflight_write_probe(value INTEGER)")
        db.execute("INSERT INTO main.v2_preflight_write_probe VALUES(1)")
    finally:
        if db.in_transaction:
            db.execute("ROLLBACK")
    return schema() == before


def catch_up_scope_checks(config, *, as_of=None):
    """V2 Phase 8 / P1-B (DEC-8.14 / 8.16, P8.6 design 1.4 and 1.7): preflight checks of the per-symbol catch-up
    scope, ONLY when the flag is ON ({} otherwise, so the OFF report is unchanged). READ-ONLY.

    - ``catch_up_scope_valid``: the scope is canonical, non-empty, inside enabled_symbols, never NAS100.
    - ``catch_up_evidence_contiguous`` (STRICT, around the clock): for every in-scope symbol, the newest closed 5m bar
      at ``as_of`` is committed in the Evidence Store, and for every open position of that symbol every 5m bar after
      its watermark up to that bar is committed, with no GAP anomaly and no uncommitted LATE anomaly in the window.
      No calendar, backfill or synthetic bar. An absent or empty Evidence Store fails (Owner decision: the first
      activation needs a prior evidence-preparation stage; no exception)."""
    if not config.v2_position_catch_up:
        return {}
    import sqlite3
    from datetime import datetime, timedelta, timezone
    from storage.codec import parse_utc
    scope, enabled = tuple(config.v2_position_catch_up_symbols), tuple(config.enabled_symbols)
    checks = {"catch_up_scope_valid": False, "catch_up_evidence_contiguous": False}
    try:
        checks["catch_up_scope_valid"] = bool(scope) and parse_catch_up_symbols(",".join(scope), enabled) == scope
    except ValueError:
        pass
    five = timedelta(minutes=5)
    now = datetime.now(timezone.utc)
    as_of = (as_of or now).astimezone(timezone.utc)
    if as_of > now:
        return checks  # NE6: a future as-of is never accepted
    newest = as_of.replace(minute=as_of.minute - as_of.minute % 5, second=0, microsecond=0) - five
    evidence_path = config.market_evidence_path
    if evidence_path is None or not Path(evidence_path).is_file():
        return checks
    positions = {}
    trading = Path(config.db_path)
    try:
        if trading.is_file():
            db = sqlite3.connect(trading.resolve().as_uri() + "?mode=ro", uri=True)
            try:
                for (payload,) in db.execute("SELECT payload FROM paper_positions "
                                             "WHERE json_extract(payload,'$.status')='OPEN'"):
                    data = json.loads(payload)
                    watermark = data.get("last_processed_at") or data.get("opened_at")
                    positions.setdefault(data.get("symbol"), []).append(parse_utc(watermark))
            finally:
                db.close()
        ev = sqlite3.connect(Path(evidence_path).resolve().as_uri() + "?mode=ro", uri=True)
        try:
            for symbol in scope:
                committed = {parse_utc(b) for (b,) in ev.execute(
                    "SELECT bar_start FROM market_evidence WHERE symbol=? AND timeframe='5m'", (symbol,))}
                if newest not in committed:
                    return checks  # trailing gap / stale / empty evidence
                anomalies = [(kind, parse_utc(b)) for kind, b in ev.execute(
                    "SELECT kind, bar_start FROM evidence_anomalies WHERE symbol=? AND timeframe='5m' "
                    "AND kind IN ('GAP','LATE')", (symbol,))]
                for watermark in positions.get(symbol, []):
                    start = watermark.replace(second=0, microsecond=0)
                    start = start.replace(minute=start.minute - start.minute % 5)
                    stamp = start + five if start <= watermark else start
                    while stamp <= newest:
                        if stamp not in committed:
                            return checks
                        stamp += five
                    for kind, bar_start in anomalies:
                        if watermark < bar_start <= newest and (kind == "GAP" or bar_start not in committed):
                            return checks
        finally:
            ev.close()
    except (OSError, ValueError, TypeError, sqlite3.Error):
        return checks
    checks["catch_up_evidence_contiguous"] = True
    return checks


def catch_up_storage_checks(config, *, mount=None, disk_mounted=True):
    """V2 Phase 8 / P8.4 (DEC-8.6, R2): fail-closed checks of the Market Evidence Store, ONLY when
    ``v2_position_catch_up`` is ON ({} otherwise). Never creates the store: an absent store needs a writable parent
    on the durable mount; an existing one must open with the evidence schema and accept a write probe. There is no
    fallback: with any check False the preflight is NOT_READY (no silent degradation to the newest-bar path)."""
    if not config.v2_position_catch_up:
        return {}
    import sqlite3
    from storage.evidence_store import EvidenceStore
    raw = config.market_evidence_path
    path = None if raw is None else Path(raw)
    checks = {"catch_up_evidence_path_set": path is not None,
              "catch_up_evidence_separate": False, "catch_up_evidence_durable": False,
              "catch_up_evidence_writable_schema": False}
    if path is None:
        return checks
    checks["catch_up_evidence_separate"] = (path.resolve() != Path(config.db_path).resolve()
                                            and path.name != Path(config.db_path).name)
    if mount is not None:
        checks["catch_up_evidence_durable"] = bool(disk_mounted) and path.is_absolute() and (
            path == mount or Path(mount) in path.parents)
    else:
        checks["catch_up_evidence_durable"] = bool(disk_mounted)
    if not checks["catch_up_evidence_separate"]:
        return checks
    try:
        if path.exists():
            # P8.4F (P8.5 HIGH): a PERSISTENT write probe on ``main``, always rolled back (no residue).
            store = EvidenceStore(path)
            try:
                checks["catch_up_evidence_writable_schema"] = persistent_write_probe(store.db, path)
            finally:
                store.close()
        else:
            parent = path.parent
            checks["catch_up_evidence_writable_schema"] = parent.is_dir() and os.access(parent, os.W_OK)
    except (OSError, RuntimeError, ValueError, sqlite3.Error):
        checks["catch_up_evidence_writable_schema"] = False
    return checks
