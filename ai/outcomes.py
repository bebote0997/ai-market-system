"""V2 Phase 7 (P7.1 Batch A): typed AI call outcomes and the non-authoritative AI availability summary.

Observability only. Nothing here is read by any deterministic gate: final_status, paper_policy, Risk, the Conflict
Engine and execution keep their certified semantics. Outcomes are derived only from evidence the provider adapter or
the validator actually produced; nothing is guessed.
"""

AVAILABILITY_VERSION = "V2_P7_AI_AVAILABILITY_1"

# Typed outcomes of one agent call.
OK = "OK"
NO_DATA = "NO_DATA"
AUTH_ERROR = "AUTH_ERROR"
RATE_LIMITED = "RATE_LIMITED"
QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"  # HTTP 404 model not found / not available to the key
TIMEOUT = "TIMEOUT"
CONNECTION_ERROR = "CONNECTION_ERROR"
INVALID_RESPONSE = "INVALID_RESPONSE"  # incomplete or structurally invalid provider output
INVALID_SCHEMA = "INVALID_SCHEMA"  # parsed, but identity/enum/confidence contract violated
UNGROUNDED_RESPONSE = "UNGROUNDED_RESPONSE"  # cites evidence ids that were not supplied
PROVIDER_FAILURE = "PROVIDER_FAILURE"  # provider-side error (5xx, other 4xx)
UNKNOWN_PROVIDER_ERROR = "UNKNOWN_PROVIDER_ERROR"  # any non-provider exception
MODEL_REPORTED_ERROR = "MODEL_REPORTED_ERROR"  # schema-valid response whose own status is ERROR
NOT_CONFIGURED = "NOT_CONFIGURED"
AI_TIME_BUDGET_EXHAUSTED = "AI_TIME_BUDGET_EXHAUSTED"  # Batch C: per-cycle budget (no call or no more retries)
SHORT_CIRCUITED = "SHORT_CIRCUITED"  # Batch C: skipped after a non-transient failure in the same cycle

FAILURE_OUTCOMES = frozenset({AUTH_ERROR, RATE_LIMITED, QUOTA_EXHAUSTED, MODEL_UNAVAILABLE, TIMEOUT, CONNECTION_ERROR,
                              INVALID_RESPONSE, INVALID_SCHEMA, UNGROUNDED_RESPONSE, PROVIDER_FAILURE,
                              UNKNOWN_PROVIDER_ERROR, NOT_CONFIGURED, AI_TIME_BUDGET_EXHAUSTED, SHORT_CIRCUITED,
                              MODEL_REPORTED_ERROR})
# Non-transient within a cycle: retrying the same provider in the same cycle cannot succeed.
NON_TRANSIENT = frozenset({AUTH_ERROR, QUOTA_EXHAUSTED, MODEL_UNAVAILABLE, NOT_CONFIGURED})
TRANSIENT = frozenset({RATE_LIMITED, TIMEOUT, CONNECTION_ERROR, PROVIDER_FAILURE})

_PROVIDER_KINDS = {"AUTH_ERROR": AUTH_ERROR, "RATE_LIMITED": RATE_LIMITED, "QUOTA_EXHAUSTED": QUOTA_EXHAUSTED,
                   "MODEL_UNAVAILABLE": MODEL_UNAVAILABLE, "INCOMPLETE_RESPONSE": INVALID_RESPONSE,
                   "INVALID_STRUCTURED_RESPONSE": INVALID_RESPONSE, "PROVIDER_ERROR": PROVIDER_FAILURE,
                   "NOT_CONFIGURED": NOT_CONFIGURED, "AI_TIME_BUDGET_EXHAUSTED": AI_TIME_BUDGET_EXHAUSTED,
                   "SHORT_CIRCUITED": SHORT_CIRCUITED}
OUTCOME_WARNING_PREFIX = "ai_outcome:"


def classify_exception(error):
    """Typed outcome of a provider exception. Uses only the sanitized ``kind``/``timed_out`` the adapter set."""
    kind = getattr(error, "kind", None)
    if kind == "CONNECTION_ERROR":
        return TIMEOUT if getattr(error, "timed_out", False) is True else CONNECTION_ERROR
    if isinstance(kind, str) and kind in _PROVIDER_KINDS:
        return _PROVIDER_KINDS[kind]
    return UNKNOWN_PROVIDER_ERROR


def classify_validation(reason):
    """Typed outcome of a ``validate_ai_response`` rejection reason."""
    return UNGROUNDED_RESPONSE if str(reason).startswith("ungrounded_") else INVALID_SCHEMA


def outcome_of(response):
    """Typed outcome of an already-final agent response (as returned by ``ai.runtime.call_agent``)."""
    if response is None:
        return None
    if response.status in {"OK", "PARTIAL"}:
        return OK
    if response.status == "NO_DATA":
        return NO_DATA
    warnings = tuple(response.warnings or ())
    typed = next((w[len(OUTCOME_WARNING_PREFIX):] for w in warnings if str(w).startswith(OUTCOME_WARNING_PREFIX)),
                 None)
    if typed in FAILURE_OUTCOMES:
        return typed
    if warnings and not str(warnings[0]).startswith("provider_exception"):
        return classify_validation(warnings[0])
    return UNKNOWN_PROVIDER_ERROR


def ai_availability(responses):
    """Non-authoritative availability summary of the agents that were invoked (``{agent_name: response}``).

    ALL_AVAILABLE (no agent ERROR) · PARTIAL_FAILURE · TOTAL_FAILURE (every invoked agent ERROR) · NO_AGENTS.
    NO_DATA is reported separately and is not a failure. This summary never feeds any decision.
    """
    agents = {name: {"status": r.status, "outcome": outcome_of(r)} for name, r in responses.items() if r is not None}
    failed = sorted(name for name, item in agents.items() if item["status"] == "ERROR")
    state = ("NO_AGENTS" if not agents else "TOTAL_FAILURE" if len(failed) == len(agents)
             else "PARTIAL_FAILURE" if failed else "ALL_AVAILABLE")
    return {"version": AVAILABILITY_VERSION, "authoritative": False, "state": state, "agents": agents,
            "failed_agents": failed, "failure_outcomes": sorted({agents[name]["outcome"] for name in failed}),
            "no_data_agents": sorted(name for name, item in agents.items() if item["status"] == "NO_DATA")}


__all__ = ["AI_TIME_BUDGET_EXHAUSTED", "AUTH_ERROR", "AVAILABILITY_VERSION", "CONNECTION_ERROR", "FAILURE_OUTCOMES",
           "INVALID_RESPONSE", "INVALID_SCHEMA", "MODEL_REPORTED_ERROR", "MODEL_UNAVAILABLE", "NON_TRANSIENT", "NOT_CONFIGURED", "NO_DATA",
           "OK", "OUTCOME_WARNING_PREFIX", "PROVIDER_FAILURE", "QUOTA_EXHAUSTED", "RATE_LIMITED", "SHORT_CIRCUITED",
           "TIMEOUT", "TRANSIENT", "UNGROUNDED_RESPONSE", "UNKNOWN_PROVIDER_ERROR", "ai_availability",
           "classify_exception", "classify_validation", "outcome_of"]
