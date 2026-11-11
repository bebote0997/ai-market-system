"""Optional xAI HTTP 429 policy. Off unless ``XAI_RATE_LIMIT_V2`` is exactly ``1``.

Off (unset or any other value) is not used by the adapter: the legacy retry count,
the 15 second Retry-After stop, and unrestricted concurrency stay as they are.

On, one process-wide lock wraps each xAI HTTP call, including its 429 waits, so
two agents cannot be in flight together. A 429 that is not a quota error waits
with exponential backoff plus jitter, or at least the Retry-After value when
that header is present. The shared wait budget then fails the call closed.
This module never selects another provider.
"""
import math
import os
import threading


FLAG = "XAI_RATE_LIMIT_V2"

# One symbol cycle posts the specialists back to back, and a macro retry can
# run that floor again. 90s of 429 sleeps in the process stays inside the
# 300s watchdog and well inside the 15-minute slot. A single Retry-After above
# 60s is not waited out: sleeping it would spend the budget on one header.
TOTAL_WAIT_CAP_SECONDS = 90.0
MAX_SINGLE_WAIT_SECONDS = 60.0
QUIET_RESET_SECONDS = 120.0
MAX_ATTEMPTS = 6
BASE_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 30.0

CALL_LOCK = threading.Lock()


class _Budget:
    def __init__(self):
        self.spent = 0.0
        self.last_finished = None


_BUDGET = _Budget()


def enabled(environ=None):
    """True only for the exact value ``1``. ``0``, empty, ``true``, and `` 1`` are off."""
    source = os.environ if environ is None else environ
    return source.get(FLAG) == "1"


def reset_state():
    """Test hook. Production never clears the budget except after a quiet gap."""
    with CALL_LOCK:
        _BUDGET.spent = 0.0
        _BUDGET.last_finished = None


def _roll(now):
    finished = _BUDGET.last_finished
    if finished is not None and now - finished >= QUIET_RESET_SECONDS:
        _BUDGET.spent = 0.0
        _BUDGET.last_finished = None


def plan_wait(attempt, retry_after, *, now, uniform):
    """Seconds to sleep before another 429 try, or None to fail closed.

    ``attempt`` is the zero-based index of the attempt that just failed.
    ``uniform(low, high)`` supplies jitter and is called only when a wait fits.
    The caller holds ``CALL_LOCK``. A returned wait is at least the Retry-After
    value when the header is present, and never above the remaining budget.
    """
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 0:
        return None
    if attempt + 1 >= MAX_ATTEMPTS:
        return None
    _roll(now)
    if retry_after is None:
        base = min(MAX_BACKOFF_SECONDS, BASE_SECONDS * (2 ** attempt))
    elif isinstance(retry_after, bool) or not isinstance(retry_after, (int, float)):
        return None
    else:
        base = float(retry_after)
    if not math.isfinite(base) or base < 0 or base > MAX_SINGLE_WAIT_SECONDS:
        return None
    remaining = TOTAL_WAIT_CAP_SECONDS - _BUDGET.spent
    if base - remaining > 1e-9:
        return None
    if retry_after is None:
        jitter_hi = min(1.0, 0.25 * base)
    else:
        jitter_hi = min(1.0, 0.25 * max(base, 1.0))
    room = min(MAX_SINGLE_WAIT_SECONDS, remaining) - base
    if room < -1e-9:
        return None
    jitter_hi = min(jitter_hi, max(0.0, room))
    jitter = 0.0
    if jitter_hi > 0:
        drawn = uniform(0.0, jitter_hi)
        if isinstance(drawn, (int, float)) and not isinstance(drawn, bool) and math.isfinite(drawn):
            jitter = min(max(0.0, float(drawn)), jitter_hi)
    return base + jitter


def reserve_wait(seconds):
    """Count a wait that is about to start. The caller holds ``CALL_LOCK``."""
    _BUDGET.spent += seconds


def finish_wait(now):
    """Mark the quiet clock from the moment the wait actually ended."""
    _BUDGET.last_finished = now
