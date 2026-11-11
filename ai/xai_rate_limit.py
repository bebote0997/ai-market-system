"""Optional xAI HTTP 429 policy. Off unless ``XAI_RATE_LIMIT_V2`` is exactly ``1``.

Off (unset or any other value) is not used by the adapter: the legacy retry count,
the 15 second Retry-After stop, and unrestricted concurrency stay as they are.

On, one process-wide lock wraps each xAI call, including its waits. HTTP time and
sleeps share a per-call wall budget and a cycle wall budget. Production does not
set ``provider.deadline`` (``v2_ai_resilience`` defaults off), so these budgets
stand on their own. A 429 that is not a quota error waits with exponential backoff
plus jitter, or at least Retry-After when that header is present, and only when
the leftover budget can hold that wait plus another full timeout. Otherwise the
call fails closed. This module never selects another provider.
"""
import math
import os
import threading


FLAG = "XAI_RATE_LIMIT_V2"

# The heartbeat is written after the symbol cycle returns, and the watchdog
# restarts the service when that heartbeat is older than 300s
# (runtime.cloud.WATCHDOG_STALE_SECONDS). v2_ai_resilience is off, so nothing
# else stops a slow xAI call. One cycle is 5 specialists plus a macro retry of
# the whole floor (10 calls). Each attempt may sit in HTTP for the full timeout
# (default 30s) while holding the process lock.
#
# Uncapped: 10 calls x 6 attempts x 30s = 1800s, plus sleeps.
# Capped: an attempt starts only when both leftovers can hold one full timeout,
# and a retry only when they can also hold the sleep. xAI wall time therefore
# ends at or under CYCLE_WALL_SECONDS (180s), 120s under the watchdog.
# One agent stops after two full timeouts plus a short sleep, because a third
# timeout does not fit in CALL_WALL_SECONDS (90s).
CALL_WALL_SECONDS = 90.0
CYCLE_WALL_SECONDS = 180.0
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


def wall_spent():
    """HTTP time plus waits charged to the current cycle. The caller is outside the lock."""
    with CALL_LOCK:
        return _BUDGET.spent


def _roll(now):
    finished = _BUDGET.last_finished
    if finished is not None and now - finished >= QUIET_RESET_SECONDS:
        _BUDGET.spent = 0.0
        _BUDGET.last_finished = None


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def remaining_wall(now, call_started):
    """Seconds of wall clock left for this call and for the whole cycle. Caller holds the lock."""
    _roll(now)
    cycle_left = CYCLE_WALL_SECONDS - _BUDGET.spent
    call_left = CALL_WALL_SECONDS - (now - call_started)
    return min(cycle_left, call_left)


def attempt_fits(now, call_started, timeout):
    """True when a full HTTP timeout still fits in both wall budgets."""
    if not _finite(timeout) or timeout <= 0:
        return False
    return remaining_wall(now, call_started) + 1e-9 >= float(timeout)


def wait_fits(now, call_started, wait, timeout):
    """True when ``wait`` plus another full timeout fit in both wall budgets."""
    if not _finite(wait) or wait < 0:
        return False
    return remaining_wall(now, call_started) + 1e-9 >= float(wait) + float(timeout)


def plan_wait(attempt, retry_after, *, now, call_started, timeout, uniform):
    """Seconds to sleep before another 429 try, or None to fail closed.

    ``attempt`` is the zero-based index of the attempt that just failed.
    ``uniform(low, high)`` supplies jitter and is called only when a wait fits.
    The caller holds ``CALL_LOCK``. A returned wait is at least the Retry-After
    value when the header is present, and the leftover after that wait can still
    hold one full ``timeout``.
    """
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 0:
        return None
    if attempt + 1 >= MAX_ATTEMPTS:
        return None
    if not attempt_fits(now, call_started, timeout):
        return None
    room = remaining_wall(now, call_started) - float(timeout)
    if room < -1e-9:
        return None
    if retry_after is None:
        base = min(MAX_BACKOFF_SECONDS, BASE_SECONDS * (2 ** attempt))
    elif not _finite(retry_after):
        return None
    else:
        base = float(retry_after)
    if not math.isfinite(base) or base < 0 or base > MAX_SINGLE_WAIT_SECONDS or base - room > 1e-9:
        return None
    if retry_after is None:
        jitter_hi = min(1.0, 0.25 * base)
    else:
        jitter_hi = min(1.0, 0.25 * max(base, 1.0))
    jitter_room = min(MAX_SINGLE_WAIT_SECONDS, room) - base
    if jitter_room < -1e-9:
        return None
    jitter_hi = min(jitter_hi, max(0.0, jitter_room))
    jitter = 0.0
    if jitter_hi > 0:
        drawn = uniform(0.0, jitter_hi)
        if _finite(drawn):
            jitter = min(max(0.0, float(drawn)), jitter_hi)
    return base + jitter


def charge(seconds, now):
    """Count HTTP time that already elapsed. The caller holds ``CALL_LOCK``."""
    if not _finite(seconds) or seconds < 0:
        seconds = 0.0
    _BUDGET.spent += float(seconds)
    _BUDGET.last_finished = now


def reserve_wait(seconds):
    """Count a wait that is about to start. The caller holds ``CALL_LOCK``."""
    _BUDGET.spent += seconds


def release_wait(seconds):
    """Give back a reservation when the sleep did not finish. The caller holds the lock."""
    if not _finite(seconds) or seconds < 0:
        return
    _BUDGET.spent -= float(seconds)
    if not math.isfinite(_BUDGET.spent) or _BUDGET.spent < 0:
        _BUDGET.spent = 0.0


def finish_wait(now):
    """Mark the quiet clock from the moment the wait actually ended."""
    _BUDGET.last_finished = now
