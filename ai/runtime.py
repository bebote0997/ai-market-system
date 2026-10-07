"""AI call runtime: grounding enforcement, failure isolation, audit log and
call-count control. No component outside this module invokes a provider
directly, so every AI call is validated the same way.
"""
import copy
import dataclasses
from datetime import datetime, timezone
import time

from ai.contracts import AI_SCHEMA_VERSION, AIResponse, validate_ai_response
from ai.outcomes import (MODEL_REPORTED_ERROR, NO_DATA, OK, OUTCOME_WARNING_PREFIX, classify_exception,
                         classify_validation)


def _error_response(request, *warnings):
    return AIResponse(
        AI_SCHEMA_VERSION, request.run_id, request.as_of, request.symbol,
        request.agent_name, "ERROR", warnings=tuple(warnings),
    )


def _no_data_response(request):
    return AIResponse(
        AI_SCHEMA_VERSION, request.run_id, request.as_of, request.symbol,
        request.agent_name, "NO_DATA",
    )


class AuditLog:
    """In-memory audit trail. Durable persistence is deferred to Phase 6C."""

    def __init__(self):
        self.entries = []

    def record(self, run_id, agent_name, prompt_version, evidence_id_list, response, validation_reason, provider_metadata,
               **typed):
        entry = {
            "run_id": run_id,
            "agent": agent_name,
            "prompt_version": prompt_version,
            "evidence_ids": tuple(evidence_id_list),
            "response": response,
            "validation": validation_reason,
            "provider_metadata": dict(provider_metadata or {}),
            # V2 P7.1 (DEC-7.1): typed outcome, sanitized provider error kind and HTTP status (never a body or key).
            "outcome": typed.get("outcome"),
            "error_kind": typed.get("error_kind"),
            "http_status": typed.get("http_status"),
            # V2 P7.1 Batch B (DEC-7.2/7.10): call timing/attempts/response id and the evidence fingerprint.
            "call": typed.get("call"),
            "evidence_fingerprint": typed.get("evidence_fingerprint"),
        }
        self.entries.append(entry)
        return entry

    def calls_for_run(self, run_id):
        return [entry for entry in self.entries if entry["run_id"] == run_id]

    @property
    def call_count(self):
        return len(self.entries)


def has_usable_evidence(request):
    return any(isinstance(item, dict) for item in request.deterministic_evidence)


def call_agent(provider, request, audit_log=None):
    """Invoke `provider.generate(request)`, validate the response against the
    request it was given, and isolate any provider failure so it never takes
    down the rest of the floor run. Skips the call entirely (cost control)
    when there is no deterministic evidence to interpret.
    """
    from ai.call_audit import evidence_fingerprint
    evidence_id_list = tuple(
        item.get("evidence_id") for item in request.deterministic_evidence if isinstance(item, dict)
    )
    fingerprint = evidence_fingerprint(request)
    if not has_usable_evidence(request):
        response = _no_data_response(request)
        if audit_log is not None:
            audit_log.record(request.run_id, request.agent_name, request.prompt_version, evidence_id_list, response,
                             "skipped_no_data", {}, outcome=NO_DATA, call={"called": False},
                             evidence_fingerprint=fingerprint)
        return response

    error_kind = http_status = None
    call = {"called": True, "requested_at": datetime.now(timezone.utc).isoformat()}
    started = time.perf_counter()
    try:
        response = provider.generate(request)
    except Exception as error:  # noqa: BLE001 - any provider failure is isolated
        outcome = classify_exception(error)
        kind = getattr(error, "kind", None)
        error_kind = kind if isinstance(kind, str) else None
        status = getattr(error, "http_status", None)
        http_status = status if isinstance(status, int) and not isinstance(status, bool) else None
        # DEC-7.1: the typed outcome is ADDED after the unchanged legacy warning (System Health reads warnings[0]).
        response = _error_response(request, f"provider_exception:{type(error).__name__}",
                                   OUTCOME_WARNING_PREFIX + outcome)
        reason = "provider_exception"
    else:
        valid, reason = validate_ai_response(response, request)
        if not valid:
            outcome = classify_validation(reason)
            response = _error_response(request, reason, OUTCOME_WARNING_PREFIX + outcome)
        elif response.status == "ERROR":  # schema-valid response in which the model itself reports an error
            outcome = MODEL_REPORTED_ERROR
            response = dataclasses.replace(response, warnings=tuple(response.warnings) + (OUTCOME_WARNING_PREFIX + outcome,))
        else:
            outcome = NO_DATA if response.status == "NO_DATA" else OK
    if audit_log is not None:
        call.update(responded_at=datetime.now(timezone.utc).isoformat(),
                    latency_ms=round((time.perf_counter() - started) * 1000, 3))
        detail = getattr(provider, "last_call", None)
        if isinstance(detail, dict):  # provider-level attempts / response id / health (sanitized by the provider)
            call.update({k: detail[k] for k in ("attempts", "response_id", "health_before", "health_after",
                                                 "short_circuit_cause", "late_response_rejected",
                                                 "late_response_status", "late_response_usage") if k in detail})
        audit_log.record(
            request.run_id, request.agent_name, request.prompt_version, evidence_id_list,
            response, reason, getattr(provider, "metadata", {}),
            outcome=outcome, error_kind=error_kind, http_status=http_status, call=call,
            evidence_fingerprint=fingerprint,
        )
    return response


def snapshot_account(account):
    """Read-only view of a PaperAccount for AI consumption. AI never receives
    the live mutable object, only a deep-copied plain snapshot.
    """
    return copy.deepcopy(vars(account))
