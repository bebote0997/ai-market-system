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
import hashlib
import hmac
import logging
import math
import os
import secrets
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
# Remember-me: a signed token in a cookie. The password never leaves the server and is not stored in the browser.
REMEMBER_COOKIE = "aif_remember"
REMEMBER_ISSUE = "dashboard_remember_issue"
REMEMBER_CLEAR = "dashboard_remember_clear"
REMEMBER_TTL_SECONDS = 30 * 24 * 3600
REMEMBER_VERSION = "v1"


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


def _remember_key(password):
    return hmac.new(b"aifloor-remember-v1", password.encode("utf-8"), hashlib.sha256).digest()


def issue_remember_token(password, now, ttl=REMEMBER_TTL_SECONDS):
    """``v1.<exp>.<nonce>.<hmac>``. Stateless: changing the password invalidates every outstanding token."""
    exp = int(now) + int(ttl)
    nonce = secrets.token_hex(16)
    message = f"{exp}.{nonce}".encode("ascii")
    signature = hmac.new(_remember_key(password), message, hashlib.sha256).hexdigest()
    return f"{REMEMBER_VERSION}.{exp}.{nonce}.{signature}"


def remember_token_valid(token, password, now):
    """True only for a well-formed, unexpired token signed with the current password. Never raises."""
    try:
        if not isinstance(token, str) or not password_strong_enough(password):
            return False
        version, exp, nonce, signature = token.split(".")
        if version != REMEMBER_VERSION or not nonce or len(nonce) > 64:
            return False
        expires = int(exp)
        if expires < now or expires > now + REMEMBER_TTL_SECONDS + 120:
            return False
        message = f"{expires}.{nonce}".encode("ascii")
        expected = hmac.new(_remember_key(password), message, hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected)
    except (TypeError, ValueError):
        return False


def request_cookie(st, name):
    context = getattr(st, "context", None)
    cookies = getattr(context, "cookies", None) if context is not None else None
    if not cookies:
        return ""
    try:
        value = cookies.get(name, "")
    except Exception:  # noqa: BLE001 - a strange cookie jar is the same as no cookie
        return ""
    return value if isinstance(value, str) else ""


def _flush_remember_cookie(st):
    """Write or clear the remember cookie once, after a login that asked for it. No-op without the flag."""
    from ui.client_script import set_cookie
    state = st.session_state
    token = state.pop(REMEMBER_ISSUE, None)
    if isinstance(token, str) and token:
        set_cookie(st, REMEMBER_COOKIE, token, REMEMBER_TTL_SECONDS)
        return
    if state.pop(REMEMBER_CLEAR, None):
        set_cookie(st, REMEMBER_COOKIE, "0", 0)


_LOGIN_CSS = """<style>
.stApp:has(.aif-login){min-height:100vh;background:
 radial-gradient(42vmax 34vmax at 0% 0%,rgba(47,123,255,.28),transparent 70%),
 radial-gradient(38vmax 32vmax at 100% 8%,rgba(168,85,247,.22),transparent 68%),#05071a;color:#f5f7ff}
.stApp:has(.aif-login) .block-container{max-width:460px;margin:8vh auto 0;padding:28px 26px 22px;border-radius:22px;
 background:rgba(10,16,40,.62);border:1px solid rgba(34,211,238,.45);
 box-shadow:inset 0 1px 0 rgba(255,255,255,.12),0 18px 50px rgba(0,0,0,.35);
 -webkit-backdrop-filter:blur(18px) saturate(140%);backdrop-filter:blur(18px) saturate(140%)}
.aif-login h1{font-family:Didot,Georgia,serif;font-weight:400;font-size:26px;letter-spacing:.04em;
 color:#e8eefc;margin:0 0 6px;line-height:1.25}
.aif-login p{color:#c5d0e6;font-size:14px;margin:0 0 14px}
.stApp:has(#tf-theme-light):has(.aif-login){background:#f4f7fc;color:#1a1a1a}
.stApp:has(#tf-theme-light) .block-container{background:rgba(255,255,255,.75);border-color:rgba(29,78,216,.35)}
.stApp:has(#tf-theme-light) .aif-login h1{color:#1e3a8a}
.stApp:has(#tf-theme-light) .aif-login p{color:#555555}
@media(max-width:640px){
 .stApp:has(.aif-login) .block-container{margin:12px 12px 0;padding:20px 16px;max-width:none}
}
</style>"""


def _login_form(st, *, expired, password, now, state, limiter, ttl, sleep):
    markdown = getattr(st, "markdown", None)
    if callable(markdown):
        light = bool(state.get("ui_theme"))
        markdown(_LOGIN_CSS + ('<div id="tf-theme-light" hidden></div>' if light else '')
                 + '<div class="aif-login"><h1>AI Trading Floor</h1>'
                 '<p>Panel privado · OANDA Demo (práctica). La contraseña no se guarda en el navegador.</p></div>',
                 unsafe_allow_html=True)
    else:
        st.title("AI Trading Floor · panel privado · OANDA Demo (práctica)")
    if expired:
        st.info("La sesión expiró; vuelve a entrar.")
    toggle = getattr(st, "toggle", None)
    if callable(toggle):
        if "ui_theme" not in state:
            state["ui_theme"] = request_cookie(st, "aif_theme") == "light"
        light = bool(toggle("Modo claro", key="ui_theme"))
        from ui.client_script import set_cookie
        set_cookie(st, "aif_theme", "light" if light else "dark", 365 * 24 * 3600)
    locked_until = max(limiter.locked_until(now), state.get(LOCKED_UNTIL) or 0.0)
    if now < locked_until:
        return _deny(st, "Demasiados intentos fallidos. Intenta de nuevo en "
                         f"{math.ceil(locked_until - now)} s.")
    supplied = st.text_input("Contraseña", type="password") or ""
    remember = False
    checkbox = getattr(st, "checkbox", None)
    if callable(checkbox):
        remember = bool(checkbox("Recordar sesión en este navegador (30 días)"))
        caption = getattr(st, "caption", None)
        if callable(caption):
            caption("La sesión queda en una cookie firmada que caduca a los 30 días. La contraseña no se guarda.")
    if st.button("Entrar"):
        if hmac.compare_digest(supplied.encode("utf-8"), password.encode("utf-8")):
            state[FAILURES] = 0
            state.pop(LOCKED_UNTIL, None)
            state[AUTHENTICATED] = True
            state[EXPIRES_AT] = now + ttl
            if remember:
                state[REMEMBER_ISSUE] = issue_remember_token(password, time.time())
                state.pop(REMEMBER_CLEAR, None)
            else:
                state[REMEMBER_CLEAR] = True
                state.pop(REMEMBER_ISSUE, None)
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
            st.error("Contraseña incorrecta")
    st.stop()
    return False


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
                         "(or run `streamlit run ui/app.py --server.address=127.0.0.1` for local use)")
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
        _flush_remember_cookie(st)
        return True
    expired = state.get(AUTHENTICATED) is True
    state[AUTHENTICATED] = False
    state.pop(EXPIRES_AT, None)
    if expired:
        LOG.info("component=dashboard event=SESSION_EXPIRED")
    remembered = request_cookie(st, REMEMBER_COOKIE)
    if remembered and remember_token_valid(remembered, password, time.time()):
        state[FAILURES] = 0
        state[AUTHENTICATED] = True
        state[EXPIRES_AT] = now + ttl
        LOG.info("component=dashboard event=LOGIN_REMEMBER")
        return True
    return _login_form(st, expired=expired, password=password, now=now, state=state, limiter=limiter,
                       ttl=ttl, sleep=sleep)


def owner_authenticated(st, env=None, *, clock=time.monotonic):
    """True only for a session that logged in with the owner password (``AI_FLOOR_DASHBOARD_PASSWORD``, strong
    enough) and has not expired. The password-less loopback mode is NOT owner-authenticated: owner-only actions stay
    disabled there. Never raises."""
    env = os.environ if env is None else env
    try:
        if not password_strong_enough(env.get("AI_FLOOR_DASHBOARD_PASSWORD")):
            return False
        state = st.session_state
        expires_at = state.get(EXPIRES_AT)
        return (state.get(AUTHENTICATED) is True and isinstance(expires_at, (int, float))
                and not isinstance(expires_at, bool) and clock() < expires_at)
    except Exception:  # noqa: BLE001 - unknown session: not the owner
        return False
