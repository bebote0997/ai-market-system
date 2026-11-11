"""EXN-* request signing for the Exness Trader API.

The signed payload field order below matches the authentication example. The docs do not say whether the server
requires that order or compact JSON. TODO: confirm canonical JSON. The empty-body SHA-256 is the documented constant.
"""
import base64
from hashlib import sha256
import json


SIGN_VERSION = "1"
EMPTY_BODY_HASH = "47DEQpj8HBSa-_TImW-5JCeuQeRkm5NMpJWZG3hSuFU"
_FIELDS = ("api_key", "idempotency_key", "timestamp", "sign_version", "method", "path", "body_hash")


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def body_hash(body):
    raw = b"" if body is None else body if isinstance(body, (bytes, bytearray)) else str(body).encode("utf-8")
    return b64url(sha256(raw).digest())


def payload_bytes(api_key, idempotency_key, timestamp, method, path, hashed):
    document = {"api_key": api_key, "idempotency_key": idempotency_key, "timestamp": int(timestamp),
                "sign_version": 1, "method": method.upper(), "path": path, "body_hash": hashed}
    encoded = json.dumps(document, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    # Keep the documented field order. json.dumps sorts only if asked; insertion order is preserved.
    if tuple(json.loads(encoded)) != _FIELDS:
        raise ValueError("signed payload field order drifted")
    return encoded


def headers(api_key, idempotency_key, timestamp, method, path, body, sign):
    """Headers for one request. ``sign`` takes the decoded EXN-DATA bytes and returns the signature bytes."""
    hashed = body_hash(body)
    data = payload_bytes(api_key, idempotency_key, timestamp, method, path, hashed)
    signature = sign(data)
    if not isinstance(signature, (bytes, bytearray)) or not signature:
        raise ValueError("signer returned no signature")
    return {
        "EXN-API-KEY": api_key,
        "EXN-IDEMPOTENCY-KEY": idempotency_key,
        "EXN-TIMESTAMP": str(int(timestamp)),
        "EXN-SIGN-VERSION": SIGN_VERSION,
        "EXN-DATA": b64url(data),
        "EXN-SIGN": b64url(signature),
    }
