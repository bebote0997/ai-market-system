"""V2 Phase 7 (P7.1 Batch C): AI provider health state machine (READY / DEGRADED / FAILED / UNKNOWN).

Observability only: no gate reads this state. It lives in the process, so recovery needs no restart. Transitions:
  UNKNOWN (no provider evidence yet)
  any        --success (OK, NO_DATA from the model, MODEL_REPORTED_ERROR)--> READY   (PROVIDER_RECOVERED if it was
                                                                                       DEGRADED/FAILED)
  any        --non-transient (QUOTA_EXHAUSTED, AUTH_ERROR, MODEL_UNAVAILABLE, NOT_CONFIGURED)--> FAILED  (immediately)
  READY/...  --transient or invalid output--> DEGRADED, FAILED after ``failed_after`` consecutive failures
  outcomes with no provider evidence (call skipped: AI_TIME_BUDGET_EXHAUSTED, SHORT_CIRCUITED) -> no transition
Each state change produces one event (PROVIDER_DEGRADED / PROVIDER_FAILED / PROVIDER_RECOVERED) with the incident
start, the triggering outcome and, on recovery, the first-known-good time and incident duration.
"""
from ai.outcomes import (AI_TIME_BUDGET_EXHAUSTED, MODEL_REPORTED_ERROR, NO_DATA, NON_TRANSIENT, OK,
                         SHORT_CIRCUITED)

READY, DEGRADED, FAILED, UNKNOWN = "READY", "DEGRADED", "FAILED", "UNKNOWN"
SUCCESS = frozenset({OK, NO_DATA, MODEL_REPORTED_ERROR})
NOT_OBSERVED = frozenset({AI_TIME_BUDGET_EXHAUSTED, SHORT_CIRCUITED, None})


class ProviderHealthTracker:
    def __init__(self, failed_after=3):
        if type(failed_after) is not int or failed_after < 1:
            raise ValueError("failed_after must be an integer >= 1")
        self.failed_after = failed_after
        self.state = UNKNOWN
        self.consecutive_failures = 0
        self.incident_started_at = None
        self.last_outcome = None
        self.events = []

    def observe(self, outcome, *, at, run_id=None):
        """Apply one provider outcome; returns (state_before, state_after)."""
        before = self.state
        if outcome in NOT_OBSERVED:
            return before, before
        self.last_outcome = outcome
        if outcome in SUCCESS:
            self.consecutive_failures = 0
            self.state = READY
            if before in (DEGRADED, FAILED):
                started = self.incident_started_at
                self.incident_started_at = None
                self._event("PROVIDER_RECOVERED", before, outcome, at, run_id, incident_started_at=started,
                            first_known_good_at=at,
                            duration_seconds=None if started is None else (at - started).total_seconds())
            return before, self.state
        self.consecutive_failures += 1
        if self.incident_started_at is None:
            self.incident_started_at = at
        failed = outcome in NON_TRANSIENT or self.consecutive_failures >= self.failed_after
        self.state = FAILED if failed else DEGRADED
        if self.state != before:
            self._event("PROVIDER_FAILED" if failed else "PROVIDER_DEGRADED", before, outcome, at, run_id,
                        incident_started_at=self.incident_started_at)
        return before, self.state

    def _event(self, name, before, outcome, at, run_id, **extra):
        self.events.append({"event": name, "from": before, "to": self.state, "outcome": outcome,
                            "at": at.isoformat(), "run_id": run_id, "consecutive_failures": self.consecutive_failures,
                            **{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in extra.items()}})

    def drain(self):
        events, self.events = self.events, []
        return events


__all__ = ["DEGRADED", "FAILED", "ProviderHealthTracker", "READY", "UNKNOWN"]
