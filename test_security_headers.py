"""Security headers middleware (ui/security_headers.py) and its wiring in runtime.cloud / ui.asgi_app. Offline."""
import asyncio
import importlib.util
from pathlib import Path
import unittest

from ui.security_headers import CSP, HSTS, SECURITY_HEADERS, SecurityHeadersMiddleware

ROOT = Path(__file__).resolve().parent


def call(app, scope):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    return sent


async def downstream(scope, receive, send):
    await send({"type": "http.response.start", "status": 200,
                "headers": [(b"content-type", b"text/plain"), (b"X-Frame-Options", b"SAMEORIGIN"),
                            (b"set-cookie", b"a=b")]})
    await send({"type": "http.response.body", "body": b"ok"})


class MiddlewareTests(unittest.TestCase):
    def headers(self, scope=None):
        sent = call(SecurityHeadersMiddleware(downstream), scope or {"type": "http", "method": "GET", "path": "/"})
        start = sent[0]
        self.assertEqual((start["status"], sent[1]["body"]), (200, b"ok"))
        return start["headers"]

    def test_all_security_headers_present_with_expected_values(self):
        headers = {k.decode(): v.decode() for k, v in self.headers()}
        for name, value in SECURITY_HEADERS.items():
            self.assertEqual(headers[name], value)
        self.assertEqual(headers["strict-transport-security"], HSTS)
        self.assertEqual(headers["x-frame-options"], "DENY")
        self.assertEqual(headers["x-content-type-options"], "nosniff")
        self.assertEqual(headers["referrer-policy"], "no-referrer")

    def test_existing_headers_are_kept_and_conflicting_ones_replaced(self):
        raw = self.headers()
        names = [k.lower() for k, _ in raw]
        self.assertEqual(names.count(b"x-frame-options"), 1)  # downstream SAMEORIGIN replaced, not duplicated
        self.assertIn((b"content-type", b"text/plain"), raw)
        self.assertIn((b"set-cookie", b"a=b"), raw)

    def test_csp_blocks_framing_plugins_and_foreign_targets(self):
        for directive in ("frame-ancestors 'none'", "object-src 'none'", "base-uri 'self'", "form-action 'self'",
                          "default-src 'self'", "frame-src 'none'"):
            self.assertIn(directive, CSP)
        self.assertNotIn("script-src *", CSP)
        self.assertNotIn("http:", CSP)

    def test_websocket_and_lifespan_scopes_pass_through_untouched(self):
        seen = []

        async def app(scope, receive, send):
            seen.append(scope["type"])
            await send({"type": "websocket.accept"})

        for kind in ("websocket", "lifespan"):
            sent = call(SecurityHeadersMiddleware(app), {"type": kind})
            self.assertEqual(sent, [{"type": "websocket.accept"}])
        self.assertEqual(seen, ["websocket", "lifespan"])

    def test_header_decoration_failure_never_breaks_the_response(self):
        async def odd(scope, receive, send):
            await send({"type": "http.response.start", "status": 204, "headers": [("not-bytes", 1)]})
            await send({"type": "http.response.body", "body": b""})

        sent = call(SecurityHeadersMiddleware(odd), {"type": "http"})
        self.assertEqual(sent[0]["status"], 204)


@unittest.skipUnless(importlib.util.find_spec("streamlit") and importlib.util.find_spec("starlette"), "streamlit not installed")
class WiringTests(unittest.TestCase):
    def test_asgi_entry_wraps_the_dashboard_with_the_middleware(self):
        source = (ROOT / "ui" / "asgi_app.py").read_text(encoding="utf-8")
        self.assertIn('st.App("app.py", middleware=[Middleware(SecurityHeadersMiddleware)])', source)
        self.assertTrue((ROOT / "ui" / "app.py").exists())

    def test_cloud_serve_uses_the_headers_entry_by_default_and_has_a_rollback(self):
        from runtime import cloud
        source = (ROOT / "runtime" / "cloud.py").read_text(encoding="utf-8")
        self.assertIn('"ui/asgi_app.py"', source)
        self.assertIn('AI_FLOOR_SECURITY_HEADERS") == "0"', source)
        self.assertTrue(callable(cloud.serve))


if __name__ == "__main__":
    unittest.main()
