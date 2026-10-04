"""SYSTEM HEALTH V2 core: passive, persisted per-component health (F01-T15..T19).

Observational only. It records facts that callers report (success, typed error,
heartbeat, progress) and projects a status from them. It never reads the clock,
calls providers, or touches setups, AI, risk, orders, positions or execution.

LIVENESS != PROGRESS (H04): ``heartbeat_at`` only proves the process is alive;
``progress_at``/``progress_stage``/``progress_ref`` record the latest completed
pipeline step. A fresh heartbeat with stale progress projects STALE, never HEALTHY.

Persistence is the isolated SYSTEM HEALTH sidecar (storage/health_store.py), never
trading_floor.db. Callers must treat any health failure as non-fatal to trading.
"""
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from enum import Enum
import hashlib
import json
import math
import re

from storage.codec import parse_utc, utc
from storage.health_store import HealthStore, HealthStoreError


class HealthStatus(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


class ProviderErrorType(str, Enum):
    """V2 provider failure taxonomy: V2_HANDOFF_REQUIREMENTS.md P2, plus MODEL_UNAVAILABLE and
    CONNECTION_ERROR, which the Phase 1 Master requirements define as explicit canonical classes."""
    RATE_LIMITED = "RATE_LIMITED"
    BILLING_OR_QUOTA = "BILLING_OR_QUOTA"
    TIMEOUT = "TIMEOUT"
    AUTH_FAILURE = "AUTH_FAILURE"
    PROVIDER_5XX = "PROVIDER_5XX"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    CONNECTION_ERROR = "CONNECTION_ERROR"
    UNKNOWN_PROVIDER_FAILURE = "UNKNOWN_PROVIDER_FAILURE"


# Names emitted by existing V1 providers/notifications, normalized here at the observability
# boundary only. Provider behavior is unchanged; the original name is preserved on the record.
LEGACY_ERROR_NAMES = {
    "RATE_LIMITED": ProviderErrorType.RATE_LIMITED,
    "QUOTA_EXHAUSTED": ProviderErrorType.BILLING_OR_QUOTA,
    "AUTH_ERROR": ProviderErrorType.AUTH_FAILURE,
    "ACCESS_DENIED": ProviderErrorType.AUTH_FAILURE,
    "INVALID_RESPONSE": ProviderErrorType.INVALID_RESPONSE,
    "TIMEOUT": ProviderErrorType.TIMEOUT,
    "MODEL_UNAVAILABLE": ProviderErrorType.MODEL_UNAVAILABLE,
    "CONNECTION_ERROR": ProviderErrorType.CONNECTION_ERROR,
    # Ambiguous legacy names: no evidence of a narrower class.
    "NOT_ENTITLED": ProviderErrorType.UNKNOWN_PROVIDER_FAILURE,
    "PROVIDER_ERROR": ProviderErrorType.UNKNOWN_PROVIDER_FAILURE,
    "PROVIDER_FAILURE": ProviderErrorType.UNKNOWN_PROVIDER_FAILURE,
}
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:\-]{0,63}")
_LEGACY = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=\-]+"),
    re.compile(r"(?i)\b(api[_-]?key|apikey|token|secret|password|passwd|authorization|access[_-]?key|webhook)"
               r"\s*[:=]\s*\S+"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(-----END [A-Z ]*PRIVATE KEY-----|$)", re.S),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"https?://\S+"),  # URLs may carry keys in paths or queries.
    re.compile(r"\b[A-Za-z0-9+/_\-]{32,}={0,2}"),  # Long opaque tokens.
)
MAX_REASON_LENGTH = 160


def classify_provider_error(*, kind=None, http_status=None, exception=None):
    """Return (ProviderErrorType, preserved legacy name or None). Deterministic; never raises.

    Precedence: explicit V2 type > known legacy name > HTTP status > exception type.
    Generic legacy names (PROVIDER_FAILURE/PROVIDER_ERROR) are refined by an HTTP status.
    """
    legacy = kind if isinstance(kind, str) and _LEGACY.fullmatch(kind) else None
    if legacy in ProviderErrorType.__members__:
        return ProviderErrorType(legacy), None  # Canonical name: error_type itself is the original name.
    mapped = LEGACY_ERROR_NAMES.get(legacy)
    if mapped is not None and legacy not in {"PROVIDER_FAILURE", "PROVIDER_ERROR"}:
        return mapped, legacy
    by_status = _from_status(http_status)
    if by_status is not None:
        return by_status, legacy
    if exception is not None and isinstance(exception, TimeoutError):
        return ProviderErrorType.TIMEOUT, legacy
    return ProviderErrorType.UNKNOWN_PROVIDER_FAILURE, legacy


def _from_status(status):
    if type(status) is not int:
        return None
    if status == 429:
        return ProviderErrorType.RATE_LIMITED
    if status == 402:
        return ProviderErrorType.BILLING_OR_QUOTA
    if status in (401, 403):
        return ProviderErrorType.AUTH_FAILURE
    if status in (408, 504):
        return ProviderErrorType.TIMEOUT
    if 500 <= status <= 599:
        return ProviderErrorType.PROVIDER_5XX
    return None


def sanitize_reason(text):
    """Short, single-line, credential-free reason. Anything secret-like or URL-like is redacted."""
    if text is None:
        return None
    text = str(text)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", text).strip()
    return text[:MAX_REASON_LENGTH] or None


def _name(value, field, *, optional=False):
    if value is None and optional:
        return ""
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise ValueError(f"{field}: 1-64 chars [A-Za-z0-9_.:-] required")
    return value


def _at(value):
    return utc(value)  # Rejects missing or naive datetimes; no clock is ever read.


@dataclass(frozen=True)
class HealthPolicy:
    """Explicit projection thresholds; no defaults are assumed by this module."""
    failed_after_consecutive_errors: int
    heartbeat_stale_after_seconds: float
    progress_stale_after_seconds: float

    def __post_init__(self):
        if type(self.failed_after_consecutive_errors) is not int or self.failed_after_consecutive_errors < 1:
            raise ValueError("failed_after_consecutive_errors must be an integer >= 1")
        for name in ("heartbeat_stale_after_seconds", "progress_stale_after_seconds"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a positive finite number")


@dataclass(frozen=True)
class ComponentHealth:
    component: str
    provider: str  # "" for component-level health.
    status: str
    last_success_at: str | None
    last_error_at: str | None
    last_checked_at: str | None
    heartbeat_at: str | None
    progress_at: str | None
    progress_stage: str | None
    progress_ref: str | None
    consecutive_errors: int
    latency_ms: float | None
    error_type: str | None
    legacy_error_name: str | None
    sanitized_error_reason: str | None

    def to_json(self):
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, text):
        try:
            record = cls(**json.loads(text))
            HealthStatus(record.status)
            if record.error_type is not None:
                ProviderErrorType(record.error_type)
        except (TypeError, ValueError) as exc:
            raise HealthStoreError("corrupt SYSTEM HEALTH record") from exc
        return record


def empty_health(component, provider=""):
    return ComponentHealth(component, provider, HealthStatus.UNKNOWN.value, None, None, None, None, None, None, None,
                           0, None, None, None, None)


def _latency(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError("latency_ms must be a non-negative finite number")
    return float(value)


def _later(new, old):
    return old is None or parse_utc(new) >= parse_utc(old)


class SystemHealth:
    """Persisted health core. Every update is idempotent by ``observation_id``."""

    def __init__(self, store, policy):
        if not isinstance(store, HealthStore):
            raise ValueError("SYSTEM HEALTH sidecar (HealthStore) required; the trading DB is never used")
        if not isinstance(policy, HealthPolicy):
            raise ValueError("HealthPolicy required")
        self.store, self.policy = store, policy

    # --- writes -------------------------------------------------------------------------------------
    def record_success(self, component, *, at, observation_id, provider=None, latency_ms=None):
        return self._observe("SUCCESS", component, provider, at, observation_id, latency_ms=_latency(latency_ms))

    def record_error(self, component, *, at, observation_id, provider=None, kind=None, http_status=None,
                     exception=None, reason=None, latency_ms=None):
        error_type, legacy = classify_provider_error(kind=kind, http_status=http_status, exception=exception)
        return self._observe("ERROR", component, provider, at, observation_id, latency_ms=_latency(latency_ms),
                             error_type=error_type.value, legacy_error_name=legacy, reason=sanitize_reason(reason))

    def record_heartbeat(self, component, *, at, observation_id, provider=None):
        """Liveness only; never changes success, error or progress facts."""
        return self._observe("HEARTBEAT", component, provider, at, observation_id)

    def record_progress(self, component, *, at, observation_id, stage, reference=None, provider=None):
        """Latest completed pipeline step (stage/run/bar)."""
        return self._observe("PROGRESS", component, provider, at, observation_id, stage=_name(stage, "stage"),
                             reference=_name(reference, "reference", optional=True) or None)

    def _observe(self, kind, component, provider, at, observation_id, **facts):
        component, provider = _name(component, "component"), _name(provider, "provider", optional=True)
        observation_id = _name(observation_id, "observation_id")
        observation = {"kind": kind, "component": component, "provider": provider, "at": _at(at), **facts}
        payload = json.dumps(observation, sort_keys=True, separators=(",", ":"))
        with self.store.transaction():
            previous = self.store.db.execute("SELECT payload FROM health_observations WHERE observation_id=?",
                                             (observation_id,)).fetchone()
            if previous is not None:
                if previous[0] != payload:
                    raise ValueError(f"observation_id {observation_id} reused with different content")
                return self._read(component, provider)  # Idempotent retry: nothing is counted twice.
            self.store.db.execute(
                "INSERT INTO health_observations(observation_id,component,provider,observed_at,payload,digest) "
                "VALUES(?,?,?,?,?,?)", (observation_id, component, provider, observation["at"], payload,
                                        hashlib.sha256(payload.encode("utf-8")).hexdigest()))
            record = self._apply(self._read(component, provider), observation)
            self.store.db.execute(
                "INSERT INTO component_health(component,provider,payload) VALUES(?,?,?) "
                "ON CONFLICT(component,provider) DO UPDATE SET payload=excluded.payload",
                (component, provider, record.to_json()))
            return record

    def _apply(self, record, observation):
        at, kind = observation["at"], observation["kind"]
        if kind == "HEARTBEAT":
            return replace(record, heartbeat_at=at) if _later(at, record.heartbeat_at) else record
        if kind == "PROGRESS":
            if not _later(at, record.progress_at):
                return record
            return replace(record, progress_at=at, progress_stage=observation["stage"],
                           progress_ref=observation["reference"])
        if not _later(at, record.last_checked_at):
            return record  # Out-of-order result: logged, never regresses newer facts.
        if kind == "SUCCESS":
            return replace(record, status=HealthStatus.HEALTHY.value, last_success_at=at, last_checked_at=at,
                           consecutive_errors=0, latency_ms=observation["latency_ms"], error_type=None,
                           legacy_error_name=None, sanitized_error_reason=None)
        errors = record.consecutive_errors + 1
        status = (HealthStatus.FAILED if errors >= self.policy.failed_after_consecutive_errors
                  else HealthStatus.DEGRADED)
        return replace(record, status=status.value, last_error_at=at, last_checked_at=at, consecutive_errors=errors,
                       latency_ms=observation["latency_ms"], error_type=observation["error_type"],
                       legacy_error_name=observation["legacy_error_name"],
                       sanitized_error_reason=observation["reason"])

    # --- reads (read-only projection for Batch 2/3) ---------------------------------------------------
    def _read(self, component, provider):
        row = self.store.db.execute("SELECT payload FROM component_health WHERE component=? AND provider=?",
                                    (component, provider)).fetchone()
        return ComponentHealth.from_json(row[0]) if row else empty_health(component, provider)

    def get(self, component, provider=None):
        return self._read(_name(component, "component"), _name(provider, "provider", optional=True))

    def records(self):
        rows = self.store.db.execute("SELECT payload FROM component_health ORDER BY component, provider")
        return tuple(ComponentHealth.from_json(row[0]) for row in rows)

    def project(self, record, *, now):
        """Status at ``now`` plus separate liveness and progress views. Pure; writes nothing."""
        if not isinstance(now, datetime):
            raise ValueError("now: timezone-aware datetime required")
        now = parse_utc(utc(now))
        age = lambda value: None if value is None else (now - parse_utc(value)).total_seconds()
        heartbeat_age, progress_age, checked_age = age(record.heartbeat_at), age(record.progress_at), age(
            record.last_checked_at)
        liveness = ("UNKNOWN" if heartbeat_age is None else
                    "ALIVE" if 0 <= heartbeat_age <= self.policy.heartbeat_stale_after_seconds else "NOT_ALIVE")
        progress = ("UNKNOWN" if progress_age is None else
                    "ADVANCING" if 0 <= progress_age <= self.policy.progress_stale_after_seconds else "STALLED")
        status = HealthStatus(record.status)
        if status is HealthStatus.HEALTHY and checked_age is not None and checked_age > \
                self.policy.progress_stale_after_seconds:
            status = HealthStatus.STALE
        if progress == "STALLED" and status in (HealthStatus.HEALTHY, HealthStatus.UNKNOWN):
            status = HealthStatus.STALE  # Alive but not advancing is never HEALTHY.
        return {"component": record.component, "provider": record.provider, "status": status.value,
                "liveness": liveness, "progress": progress, "record": asdict(record)}
