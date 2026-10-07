"""V2 Phase 7 (P7.1 Batch C): per-cycle provider resilience (DEC-7.4 short-circuit, DEC-7.5 time budget).

``GuardedProvider`` wraps the cycle's provider for ONE AI floor run (create a new ``AICycleGuard`` per cycle):
- after a NON-TRANSIENT failure (QUOTA_EXHAUSTED, AUTH_ERROR, MODEL_UNAVAILABLE, NOT_CONFIGURED) every later agent of
  the same cycle is not called and fails with SHORT_CIRCUITED (the cause is recorded). The next cycle uses a new guard,
  so recovery is detected on the next call; nothing is disabled across cycles and no restart is needed;
- once the cycle's AI time budget is spent, later agents are not called and fail with AI_TIME_BUDGET_EXHAUSTED; a call
  in flight gets the remaining budget as its deadline (the adapter shortens its per-attempt timeout and stops retrying
  when a retry cannot start before the deadline).
Every guard outcome is an agent ERROR, i.e. fail closed exactly like any provider failure: it can only remove
execution eligibility, never add it. No deterministic calculation reads the guard.
"""
import time

from ai.outcomes import AI_TIME_BUDGET_EXHAUSTED, NON_TRANSIENT, OK, SHORT_CIRCUITED, classify_exception


class AIGuardError(RuntimeError):
    """A call the guard did not make. Sanitized: kind and (for a short-circuit) the non-transient cause only."""

    def __init__(self, kind, cause=None):
        self.kind = kind
        self.cause = cause
        self.http_status = None
        super().__init__(kind)


class AICycleGuard:
    def __init__(self, budget_seconds=120.0, monotonic=time.monotonic):
        if not isinstance(budget_seconds, (int, float)) or isinstance(budget_seconds, bool) or budget_seconds <= 0:
            raise ValueError("budget_seconds must be a positive number")
        self.budget_seconds = float(budget_seconds)
        self.monotonic = monotonic
        self.deadline = monotonic() + self.budget_seconds
        self.tripped = None  # the non-transient outcome that short-circuited this cycle

    def remaining(self):
        return self.deadline - self.monotonic()


class GuardedProvider:
    def __init__(self, provider, guard, health=None, clock=None):
        self.provider = provider
        self.guard = guard
        self.health = health
        self.clock = clock
        self.last_call = None

    @property
    def metadata(self):
        return dict(getattr(self.provider, "metadata", {}) or {})

    @property
    def name(self):
        return getattr(self.provider, "name", "unknown")

    def _observe(self, outcome, request):
        if self.health is None:
            return None, None
        at = self.clock() if self.clock is not None else request.as_of
        return self.health.observe(outcome, at=at, run_id=request.run_id)

    def generate(self, request):
        before = None if self.health is None else self.health.state
        if self.guard.tripped is not None:
            self.last_call = {"attempts": [], "short_circuit_cause": self.guard.tripped, "health_before": before,
                              "health_after": before}
            raise AIGuardError(SHORT_CIRCUITED, cause=self.guard.tripped)
        if self.guard.remaining() <= 0:
            self.last_call = {"attempts": [], "health_before": before, "health_after": before}
            raise AIGuardError(AI_TIME_BUDGET_EXHAUSTED)
        supports_deadline = hasattr(self.provider, "deadline")
        if supports_deadline:
            self.provider.deadline = self.guard.deadline
        try:
            response = self.provider.generate(request)
        except Exception as error:
            outcome = classify_exception(error)
            if outcome in NON_TRANSIENT:
                self.guard.tripped = outcome
            _, after = self._observe(outcome, request)
            self._capture(before, after)
            raise
        finally:
            if supports_deadline:
                self.provider.deadline = None
        _, after = self._observe(OK, request)  # the provider answered; content validity is judged by the validator
        self._capture(before, after)
        return response

    def _capture(self, before, after):
        inner = getattr(self.provider, "last_call", None)
        self.last_call = {**(inner if isinstance(inner, dict) else {}), "health_before": before, "health_after": after}


__all__ = ["AICycleGuard", "AIGuardError", "GuardedProvider"]
