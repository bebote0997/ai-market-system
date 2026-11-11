"""Live signer and HTTP caller. Imported only when a real transport is built.

``cryptography`` is what the official Python example uses for Ed25519. It is not pinned in this repository.
Without it, building the live transport fails closed. TODO: owner authorizes adding the pin.
"""
import json
import urllib.error
import urllib.request

from execution.broker_port import BrokerGuardError


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def urllib_caller(method, url, headers, body, timeout=10):
    request = urllib.request.Request(url, data=body or None, method=method, headers=headers)
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read()
            payload = json.loads(raw.decode("utf-8")) if raw else {}
            return response.status, payload if isinstance(payload, dict) else {}, dict(response.headers)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeError, json.JSONDecodeError):
            payload = {}
        return exc.code, payload if isinstance(payload, dict) else {}, dict(exc.headers or {})


def signer_from_env(env):
    secret = env.get("EXN_PRIVATE_KEY")
    if not isinstance(secret, str) or len(secret.strip()) < 32:
        raise BrokerGuardError("EXN_PRIVATE_KEY missing")
    text = secret.strip()

    def sign(payload):
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
            from cryptography.hazmat.primitives.serialization import load_pem_private_key
        except ImportError as exc:
            raise BrokerGuardError("cryptography_not_installed") from exc
        if "BEGIN" in text:
            key = load_pem_private_key(text.encode("utf-8"), password=None)
        else:
            raw = bytes.fromhex(text)
            seed = raw[:32]
            key = Ed25519PrivateKey.from_private_bytes(seed)
        return key.sign(payload)

    return sign
