"""Browser snippets for non-secret cookies (theme, remember-me token). Failures never block the page.

The token itself is built in ``ui.auth`` and is an HMAC, not the password. ``Secure`` is added only when the
request arrived over HTTPS (Render's ``X-Forwarded-Proto``); a local HTTP dashboard would drop a Secure cookie.
"""
import json


def client_script(st, script):
    """Run ``script`` in the parent page. Streamlit's ``st.html`` needs ``unsafe_allow_javascript``."""
    html = getattr(st, "html", None)
    if not callable(html):
        return False
    try:
        html("<script>" + script + "</script>", unsafe_allow_javascript=True)
        return True
    except Exception:  # noqa: BLE001 - a test double or an older Streamlit must not break the page
        return False


def _https(st):
    context = getattr(st, "context", None)
    headers = getattr(context, "headers", None) if context is not None else None
    if not headers:
        return False
    try:
        proto = headers.get("x-forwarded-proto") or headers.get("X-Forwarded-Proto") or ""
    except Exception:  # noqa: BLE001
        return False
    return str(proto).split(",")[0].strip().lower() == "https"


def set_cookie(st, name, value, max_age):
    """Set (or clear, ``max_age`` 0) one cookie. Name is a short token; value cannot contain cookie delimiters."""
    if not isinstance(name, str) or not name.replace("_", "").isalnum():
        return False
    if not isinstance(value, str) or any(char in value for char in "\r\n;"):
        return False
    try:
        max_age = int(max_age)
    except (TypeError, ValueError):
        return False
    secure = "; Secure" if _https(st) else ""
    payload = f"{name}={value}; Max-Age={max_age}; Path=/; SameSite=Lax{secure}"
    return client_script(st, "document.cookie=" + json.dumps(payload) + ";")
