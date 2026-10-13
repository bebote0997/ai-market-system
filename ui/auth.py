"""Remote Streamlit read gate; runtime state is never touched here.

Hardening (security audit M-1, L-1, L-3):
- bytes comparison with ``hmac.compare_digest`` (non-ASCII input/passwords never raise);
- minimum password length, enforced here and at cloud startup;
- per-session lockout plus a process-wide failure budget (a new browser session does not reset it);
- a fixed delay after each failure and a log line per failure that never contains the input;
- authenticated sessions expire after ``AI_FLOOR_DASHBOARD_SESSION_TTL_SECONDS`` (default 8 h);
- without a password the dashboard opens only when Streamlit is bound to a loopback address.
"""
from collections import deque
import hmac
import logging
import math
import os
import threading
import time

LOG = logging.getLogger(__name__)

MIN_PASSWORD_LENGTH = 12
SESSION_MAX_FAILURES = 5
SESSION_LOCKOUT_SECONDS = 300
GLOBAL_MAX_FAILURES = 20
GLOBAL_WINDOW_SECONDS = 900
GLOBAL_LOCKOUT_SECONDS = 900
FAILURE_DELAY_SECONDS = 1.0
DEFAULT_SESSION_TTL_SECONDS = 8 * 3600
SESSION_TTL_BOUNDS = (300, 7 * 24 * 3600)
LOOPBACK_ADDRESSES = frozenset({"localhost", "127.0.0.1", "::1"})

AUTHENTICATED = "dashboard_authenticated"
EXPIRES_AT = "dashboard_auth_expires_at"
FAILURES = "dashboard_login_failures"
LOCKED_UNTIL = "dashboard_locked_until"


class LoginLimiter:
    """Process-wide failed-login budget shared by every browser session (thread-safe)."""

    def __init__(self, *, max_failures=GLOBAL_MAX_FAILURES, window=GLOBAL_WINDOW_SECONDS,
                 lockout=GLOBAL_LOCKOUT_SECONDS):
        self.max_failures = max_failures
        self.window = window
        self.lockout = lockout
        self._failures = deque()
        self._locked_until = 0.0
        self._lock = threading.Lock()

    def locked_until(self, now):
        with self._lock:
            return self._locked_until if now < self._locked_until else 0.0

    def record_failure(self, now):
        with self._lock:
            while self._failures and now - self._failures[0] >= self.window:
                self._failures.popleft()
            self._failures.append(now)
            if len(self._failures) >= self.max_failures:
                self._failures.clear()
                self._locked_until = now + self.lockout
                LOG.warning("component=dashboard event=LOGIN_GLOBAL_LOCKOUT seconds=%d", self.lockout)


_LIMITER = LoginLimiter()


def remote_dashboard_enabled(env=None):
    env = os.environ if env is None else env
    return env.get("RENDER") == "true" or env.get("AI_FLOOR_REMOTE_DASHBOARD") == "1"


def password_strong_enough(password):
    return isinstance(password, str) and len(password) >= MIN_PASSWORD_LENGTH


def session_ttl_seconds(env=None):
    env = os.environ if env is None else env
    raw = env.get("AI_FLOOR_DASHBOARD_SESSION_TTL_SECONDS")
    if raw is None or raw == "":
        return DEFAULT_SESSION_TTL_SECONDS
    low, high = SESSION_TTL_BOUNDS
    if not raw.isdigit() or not low <= int(raw) <= high:
        raise ValueError("AI_FLOOR_DASHBOARD_SESSION_TTL_SECONDS must be an integer in "
                         f"[{low}, {high}]")
    return int(raw)


def _loopback_only(st):
    get_option = getattr(st, "get_option", None)
    address = get_option("server.address") if get_option else None
    return isinstance(address, str) and address.strip().strip("[]") in LOOPBACK_ADDRESSES


def _deny(st, message):
    st.error(message)
    st.stop()
    return False


def require_dashboard_access(st, env=None, *, clock=time.monotonic, sleep=time.sleep, limiter=None):
    env = os.environ if env is None else env
    limiter = _LIMITER if limiter is None else limiter
    password = env.get("AI_FLOOR_DASHBOARD_PASSWORD")
    if not password:
        if not remote_dashboard_enabled(env) and _loopback_only(st):
            return True
        return _deny(st, "Dashboard access is not configured: set AI_FLOOR_DASHBOARD_PASSWORD "
                         "(or bind Streamlit to 127.0.0.1 for local use)")
    if not password_strong_enough(password):
        return _deny(st, "Dashboard access is misconfigured: AI_FLOOR_DASHBOARD_PASSWORD must have at "
                         f"least {MIN_PASSWORD_LENGTH} characters")
    try:
        ttl = session_ttl_seconds(env)
    except ValueError as exc:
        return _deny(st, f"Dashboard access is misconfigured: {exc}")

    state = st.session_state
    now = clock()
    expires_at = state.get(EXPIRES_AT)
    if state.get(AUTHENTICATED) is True and isinstance(expires_at, (int, float)) and now < expires_at:
        return True
    expired = state.get(AUTHENTICATED) is True
    state[AUTHENTICATED] = False
    state.pop(EXPIRES_AT, None)
    if expired:
        LOG.info("component=dashboard event=SESSION_EXPIRED")

    st.title("AI Trading Floor · private PAPER dashboard")
    if expired:
        st.info("Session expired; sign in again.")
    locked_until = max(limiter.locked_until(now), state.get(LOCKED_UNTIL) or 0.0)
    if now < locked_until:
        return _deny(st, "Too many failed sign-in attempts. Try again in "
                         f"{math.ceil(locked_until - now)} s.")
    supplied = st.text_input("Password", type="password") or ""
    if st.button("Sign in"):
        if hmac.compare_digest(supplied.encode("utf-8"), password.encode("utf-8")):
            state[FAILURES] = 0
            state.pop(LOCKED_UNTIL, None)
            state[AUTHENTICATED] = True
            state[EXPIRES_AT] = now + ttl
            LOG.info("component=dashboard event=LOGIN_OK")
            st.rerun()
        else:
            failures = int(state.get(FAILURES) or 0) + 1
            # The supplied value is never logged or echoed.
            LOG.warning("component=dashboard event=LOGIN_FAILED session_failures=%d", failures)
            limiter.record_failure(now)
            if failures >= SESSION_MAX_FAILURES:
                failures = 0
                state[LOCKED_UNTIL] = now + SESSION_LOCKOUT_SECONDS
            state[FAILURES] = failures
            sleep(FAILURE_DELAY_SECONDS)
            st.error("Invalid password")
    st.stop()
    return False
