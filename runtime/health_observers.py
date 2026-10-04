"""In-memory SYSTEM HEALTH observers for facts the trading DB does not persist (pattern B).

Called AFTER a business operation has already produced its result, with that result (or the
exception it raised). They only read it and record to the SYSTEM HEALTH sidecar. Every public
observer is exception-isolated: it returns True/False and NEVER raises, so a health failure
cannot change, block or trigger any trading, risk, broker or execution behavior.
None of these is wired into the runtime cycle in Phase 1 / Batch 2.
"""
import logging

from runtime.system_health import HealthStatus

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
                                exception=exception, reason=reason)
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
                                exception=exception, latency_ms=latency_ms, details=details,
                                reason=f"paper operation {operation} raised {type(exception).__name__}")
    return safe_observe(action)


def observe_scheduler_condition(health, *, at, observation_id, status, reason):
    """F01-T01: an explicit non-error scheduler condition (e.g. STALE progress) seen by a supervisor."""
    return safe_observe(lambda: health.record_state("scheduler", at=at, observation_id=observation_id,
                                                    status=HealthStatus(status), reason=reason))
