"""In-memory SYSTEM HEALTH observers for facts the trading DB does not persist (pattern B).

Called AFTER a business operation has already produced its result, with that result (or the
exception it raised). They only read it and record to the SYSTEM HEALTH sidecar. Every public
observer is exception-isolated: it returns True/False and NEVER raises, so a health failure
cannot change, block or trigger any trading, risk, broker or execution behavior.
None of these is wired into the runtime cycle in Phase 1 / Batch 2.
"""
import logging

import hashlib

from runtime.system_health import NOT_CONFIGURED, ComponentErrorType, HealthStatus, SystemHealth

LOG = logging.getLogger(__name__)
TOKEN_KEYS = ("input_tokens", "output_tokens", "total_tokens")


def safe_observe(action):
    """Run one observation; swallow and log (type only) any failure. Never raises."""
    try:
        action()
        return True
    except Exception as exc:  # noqa: BLE001 - observational boundary
        LOG.warning("component=system_health event=OBSERVATION_FAILED error_type=%s", type(exc).__name__)
        return False


def observe_ai_provider(health, provider, *, at, observation_id, latency_ms=None):
    """F01-T04: the provider object's own last outcome (typed kind, model, usage). No call is made."""
    def action():
        metadata = dict(getattr(provider, "metadata", {}) or {})
        name = str(metadata.get("provider") or getattr(provider, "name", "") or "unknown")
        usage = getattr(provider, "last_usage", None) or {}
        details = {"model": metadata.get("model"),
                   **{key: usage[key] for key in TOKEN_KEYS if isinstance(usage.get(key), int)}}
        failure = getattr(provider, "last_failure", None)
        if not failure and getattr(provider, "health", None) == NOT_CONFIGURED:
            failure = NOT_CONFIGURED  # No call can have succeeded; recorded as a state, never HEALTHY.
        if failure:
            health.record_error("ai_provider", provider=name, at=at, observation_id=observation_id, kind=failure,
                                latency_ms=latency_ms, reason=f"provider failure {failure}", details=details)
        else:
            health.record_success("ai_provider", provider=name, at=at, observation_id=observation_id,
                                  latency_ms=latency_ms, details=details)
    return safe_observe(action)


def observe_agent_response(health, response, *, at, observation_id, provider_kind=None, latency_ms=None):
    """F01-T05..T08: one agent's already-validated response. ``provider_kind`` is the typed provider
    failure when the agent errored because of its provider (agent health != provider health)."""
    def action():
        metadata = dict(getattr(response, "model_metadata", {}) or {})
        details = {"model": metadata.get("model"), "result_class": response.status}
        provider = str(metadata.get("provider") or "")
        warnings = tuple(getattr(response, "warnings", ()) or ())
        if response.status in {"OK", "PARTIAL", "NO_DATA"}:
            health.record_success(response.agent_name, provider=provider, at=at, observation_id=observation_id,
                                  latency_ms=latency_ms, details=details)
        else:
            reason = warnings[0] if warnings else "agent error"
            kind = provider_kind or (None if str(reason).startswith("provider_exception") else "INVALID_RESPONSE")
            health.record_error(response.agent_name, provider=provider, at=at, observation_id=observation_id,
                                kind=kind, latency_ms=latency_ms, reason=reason, details=details)
    return safe_observe(action)


def observe_risk_evaluation(health, *, at, observation_id, decision=None, exception=None, latency_ms=None):
    """F01-T09: APPROVED and REJECTED are both successful evaluations; only an exception or a
    contract-invalid decision is a health failure."""
    def action():
        status = getattr(decision, "status", None)
        if exception is None and status in {"APPROVED", "REJECTED"}:
            health.record_success("risk_engine", at=at, observation_id=observation_id, latency_ms=latency_ms,
                                  details={"decision": status})
        else:
            reason = (f"risk exception {type(exception).__name__}" if exception is not None
                      else f"invalid risk decision status {status!r}")
            health.record_error("risk_engine", at=at, observation_id=observation_id, latency_ms=latency_ms,
                                component_error=ComponentErrorType.RISK_ENGINE_ERROR, reason=reason)
    return safe_observe(action)


def observe_paper_operation(health, *, at, observation_id, operation, exception=None, latency_ms=None):
    """F01-T10: a completed PAPER operation (including a legitimate rejection) is success;
    only an operational exception is a failure. REAL execution is never involved."""
    def action():
        details = {"operation": operation, "real_execution": "DISABLED"}
        if exception is None:
            health.record_success("paper_broker", provider="paper", at=at, observation_id=observation_id,
                                  latency_ms=latency_ms, details=details)
        else:
            health.record_error("paper_broker", provider="paper", at=at, observation_id=observation_id,
                                component_error=ComponentErrorType.PAPER_BROKER_ERROR, latency_ms=latency_ms,
                                details=details,
                                reason=f"paper operation {operation} raised {type(exception).__name__}")
    return safe_observe(action)


def observe_scheduler_condition(health, *, at, observation_id, status, reason):
    """F01-T01: an explicit non-error scheduler condition (e.g. STALE progress) seen by a supervisor."""
    return safe_observe(lambda: health.record_state("scheduler", at=at, observation_id=observation_id,
                                                    status=HealthStatus(status), reason=reason))


def _oid(*parts):
    return "hook:" + hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:40]


def _compact(moment):
    return moment.strftime("%Y%m%dT%H%M%SZ")


class SystemHealthSink:
    """Receives runtime.health_hooks events and records them in the SYSTEM HEALTH sidecar.

    Install with runtime.health_hooks.install_health_sink(SystemHealthSink(health)). Any failure
    here is contained by health_hooks.emit; nothing is returned to the business code.
    """

    def __init__(self, health):
        if not isinstance(health, SystemHealth):
            raise ValueError("SystemHealth required")
        self.health = health

    def market_data_observed(self, *, symbol, slot, snapshot, data_state, provider_mode):
        """F01-T02: per-timeframe latest bar of the snapshot the runtime ALREADY loaded and judged."""
        provider = str(provider_mode or "unknown")
        frames = snapshot if isinstance(snapshot, dict) else {}
        for timeframe in sorted(frames):
            index = getattr(frames[timeframe], "index", None)
            latest = None if index is None or len(index) == 0 else index.max().to_pydatetime()
            component = f"market_data:{symbol}:{timeframe}"
            details = {"symbol": symbol, "timeframe": timeframe, "data_state": data_state, "observed_at": slot.isoformat(),
                       "latest_bar_timestamp": latest.isoformat() if latest is not None else None}
            oid = _oid("market", symbol, timeframe, slot.isoformat(), data_state, details["latest_bar_timestamp"])
            if data_state == "CURRENT":
                self.health.record_success(component, provider=provider, at=slot, observation_id=oid, details=details)
            else:
                self.health.record_state(component, provider=provider, at=slot, observation_id=oid, details=details,
                                         status=HealthStatus.STALE if data_state == "STALE_DATA" else HealthStatus.UNKNOWN,
                                         reason=data_state)
            if latest is not None:
                self.health.record_progress(component, provider=provider, at=latest, observation_id=_oid("bar", oid),
                                            stage="bar_received", reference=_compact(latest))

    def provider_call_observed(self, *, provider, request, latency_ms, response=None, error=None):
        """F01-T04: one real provider call's outcome; no call is made and nothing is returned."""
        metadata = dict(getattr(provider, "metadata", {}) or {})
        name = str(metadata.get("provider") or "unknown")
        usage = (getattr(provider, "last_usage", None) or {}) if error is None else {}  # Never stale usage on error.
        details = {"model": metadata.get("model"), "agent": getattr(request, "agent_name", None),
                   **{key: usage[key] for key in TOKEN_KEYS if isinstance(usage.get(key), int)}}
        at = request.as_of
        outcome = "OK" if error is None else f"ERROR:{getattr(error, 'kind', None) or type(error).__name__}"
        oid = _oid("provider", name, getattr(request, "run_id", None), details["agent"], outcome, latency_ms)
        if error is None:
            self.health.record_success("ai_provider", provider=name, at=at, observation_id=oid, latency_ms=latency_ms,
                                       details=details)
        else:
            kind = getattr(error, "kind", None)
            self.health.record_error("ai_provider", provider=name, at=at, observation_id=oid, latency_ms=latency_ms,
                                     kind=kind, http_status=getattr(error, "http_status", None), exception=error,
                                     reason=f"{details['agent']}: {kind or type(error).__name__}", details=details)
