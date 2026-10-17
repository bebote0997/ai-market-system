"""Pure-ASGI security-headers middleware for the Streamlit dashboard (runs inside ``st.App``; stdlib only).

Streamlit itself sets no HSTS/CSP/X-Frame-Options/Referrer-Policy and Render does not let web services add custom
headers, so the app adds them to every HTTP response. WebSocket traffic is untouched. Headers are set (replacing any
existing value of the same name), never removed from the response body path, and a failure to add them never breaks
the request.

CSP notes (Streamlit needs inline styles/scripts and ``unsafe-eval`` for its bundled front end, same-origin
WebSocket, data/blob images): the policy still forbids framing (``frame-ancestors 'none'``), plugins, foreign form
targets and a foreign ``<base>``; everything is restricted to the same origin plus ``https:`` images and ``wss:``/
``https:`` connections.
"""
from collections.abc import Callable

HSTS = "max-age=31536000; includeSubDomains"
CSP = ("default-src 'self'; "
       "script-src 'self' 'unsafe-inline' 'unsafe-eval' blob:; "
       "style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data: blob: https:; "
       "font-src 'self' data:; "
       "connect-src 'self' wss: https:; "
       "media-src 'self' data: blob:; "
       "worker-src 'self' blob:; "
       "frame-src 'none'; "
       "object-src 'none'; "
       "base-uri 'self'; "
       "form-action 'self'; "
       "frame-ancestors 'none'")
SECURITY_HEADERS = {
    "strict-transport-security": HSTS,
    "content-security-policy": CSP,
    "x-frame-options": "DENY",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "permissions-policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    "cross-origin-opener-policy": "same-origin",
}


class SecurityHeadersMiddleware:
    def __init__(self, app: Callable, headers: dict | None = None):
        self.app = app
        self._headers = [(name.encode("latin-1"), value.encode("latin-1"))
                         for name, value in (SECURITY_HEADERS if headers is None else headers).items()]
        self._names = {name for name, _ in self._headers}

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message.get("type") == "http.response.start":
                try:
                    kept = [(k, v) for k, v in message.get("headers", []) if k.lower() not in self._names]
                    message = {**message, "headers": [*kept, *self._headers]}
                except Exception:  # noqa: BLE001 - never break the response because of header decoration
                    pass
            await send(message)

        await self.app(scope, receive, send_with_headers)
