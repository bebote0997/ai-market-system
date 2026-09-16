"""AI call runtime: grounding enforcement, failure isolation, audit log and
call-count control. No component outside this module invokes a provider
directly, so every AI call is validated the same way.
"""
import copy

from ai.contracts import AI_SCHEMA_VERSION, AIResponse, validate_ai_response


def _error_response(request, reason):
    return AIResponse(
        AI_SCHEMA_VERSION, request.run_id, request.as_of, request.symbol,
        request.agent_name, "ERROR", warnings=(reason,),
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

    def record(self, run_id, agent_name, prompt_version, evidence_id_list, response, validation_reason, provider_metadata):
        entry = {
            "run_id": run_id,
            "agent": agent_name,
            "prompt_version": prompt_version,
            "evidence_ids": tuple(evidence_id_list),
            "response": response,
            "validation": validation_reason,
            "provider_metadata": dict(provider_metadata or {}),
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
    evidence_id_list = tuple(
        item.get("evidence_id") for item in request.deterministic_evidence if isinstance(item, dict)
    )
    if not has_usable_evidence(request):
        response = _no_data_response(request)
        if audit_log is not None:
            audit_log.record(request.run_id, request.agent_name, request.prompt_version, evidence_id_list, response, "skipped_no_data", {})
        return response

    try:
        response = provider.generate(request)
    except Exception as error:  # noqa: BLE001 - any provider failure is isolated
        response = _error_response(request, f"provider_exception:{type(error).__name__}")
        reason = "provider_exception"
    else:
        valid, reason = validate_ai_response(response, request)
        if not valid:
            response = _error_response(request, reason)
    if audit_log is not None:
        audit_log.record(
            request.run_id, request.agent_name, request.prompt_version, evidence_id_list,
            response, reason, getattr(provider, "metadata", {}),
        )
    return response


def snapshot_account(account):
    """Read-only view of a PaperAccount for AI consumption. AI never receives
    the live mutable object, only a deep-copied plain snapshot.
    """
    return copy.deepcopy(vars(account))
