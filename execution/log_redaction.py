"""Process-wide log redaction. Stdlib only; never raises.

``install_log_redaction()`` wraps ``logging``'s record factory so the final message of EVERY record (``msg % args``)
is redacted at creation time, before any handler/formatter sees it, and pre-renders a redacted ``exc_text`` /
``stack_info`` for records carrying a traceback. ``logging.Formatter.formatException`` / ``formatStack`` are also
wrapped, so a traceback formatted later (e.g. after a handler cleared ``exc_text``) is redacted too. Installing twice
only refreshes the secret snapshot (idempotent). If redaction of a record fails, its message is replaced by a generic
string: the raw text is never passed through.

Redacted: values (>= 8 chars) of env vars whose NAME contains TOKEN, SECRET, PASSWORD, API_KEY, WEBHOOK, APP_PASSWORD
or COOKIE; ``Authorization: ...`` headers; ``Bearer <x>``; OANDA tokens (``<32 hex>-<32 hex>``); ``sk-...`` keys;
Slack webhook URLs; URL userinfo (``scheme://user:pass@``); OANDA-style account ids (masked as ``101-…4567-001``).
"""
import logging
import os
import re
import threading

REDACTED = "[REDACTED]"
FAILED_MESSAGE = "[log message suppressed: redaction failed]"
MIN_SECRET_LENGTH = 8
SECRET_NAME_RE = re.compile(r"TOKEN|SECRET|PASSWORD|API_KEY|WEBHOOK|APP_PASSWORD|COOKIE", re.IGNORECASE)

_PATTERNS = (
    # ``Authorization: Bearer x`` / ``'Authorization': 'Basic x'`` / ``authorization=x`` -> scheme and value hidden.
    (re.compile(r"(?i)(\bauthorization['\"]?\s*[:=]\s*['\"]?)(?:(?:bearer|basic|token|digest)\s+)?[^\s'\",;}]+"),
     r"\1" + REDACTED),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=\-]+"), "Bearer " + REDACTED),
    (re.compile(r"(?i)https?://hooks\.slack(?:-gov)?\.com/[^\s'\"<>]*"), "https://hooks.slack.com/" + REDACTED),
    (re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)[^\s/@'\"<>]+@"), r"\1" + REDACTED + "@"),
    (re.compile(r"\b[0-9a-fA-F]{32}-[0-9a-fA-F]{32}\b"), REDACTED),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"), "sk-" + REDACTED),
    (re.compile(r"\b(\d{3})-\d{3}-(\d{5,})-(\d{3})\b"), lambda m: f"{m.group(1)}-…{m.group(2)[-4:]}-{m.group(3)}"),
)

_LOCK = threading.Lock()
_STATE = {"secrets": ()}


def env_secrets(environ=None):
    """Values (stripped and raw, >= 8 chars) of env vars with a secret-like name; longest first."""
    environ = os.environ if environ is None else environ
    values = set()
    for name, value in list(environ.items()):
        if not isinstance(name, str) or not isinstance(value, str) or not SECRET_NAME_RE.search(name):
            continue
        for candidate in (value, value.strip()):
            if len(candidate) >= MIN_SECRET_LENGTH:
                values.add(candidate)
    return tuple(sorted(values, key=len, reverse=True))


def redact(text, extra_secrets=()):
    """``text`` with every known secret shape and every ``extra_secrets`` value (>= 4 chars) replaced."""
    text = text if isinstance(text, str) else str(text)
    literals = [s for s in extra_secrets if isinstance(s, str) and len(s) >= 4]
    for secret in sorted(literals, key=len, reverse=True):
        text = text.replace(secret, REDACTED)
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _redact_known(text):
    return redact(text, _STATE["secrets"])


def _redact_record(record):
    try:
        message = record.getMessage()
        record.msg, record.args = _redact_known(message), ()
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)  # wrapped: already redacted
        elif record.exc_text:
            record.exc_text = _redact_known(record.exc_text)
        if record.exc_text:
            # The raw (unredacted) exception/traceback never leaves this function: handlers print ``exc_text``.
            record.exc_text = _redact_known(record.exc_text)
            record.exc_info = None
        if record.stack_info:
            record.stack_info = _redact_known(record.stack_info)
    except Exception:  # noqa: BLE001 - never let logging raise, never pass the raw text through
        record.msg, record.args = FAILED_MESSAGE, ()
        record.exc_info = record.exc_text = record.stack_info = None


def _make_factory(previous):
    def factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        _redact_record(record)
        return record

    factory._ai_floor_redaction = True
    factory.previous = previous
    return factory


def _wrap_formatter_method(name):
    original = getattr(logging.Formatter, name)
    if getattr(original, "_ai_floor_redaction", False):
        return

    def wrapper(self, value):
        try:
            return _redact_known(original(self, value))
        except Exception:  # noqa: BLE001
            return FAILED_MESSAGE

    wrapper._ai_floor_redaction = True
    wrapper.__wrapped__ = original
    setattr(logging.Formatter, name, wrapper)


def install_log_redaction(environ=None):
    """Install (or refresh the secret snapshot of) the process-wide redaction. True on success; never raises."""
    try:
        with _LOCK:
            _STATE["secrets"] = env_secrets(environ)
            current = logging.getLogRecordFactory()
            if not getattr(current, "_ai_floor_redaction", False):
                logging.setLogRecordFactory(_make_factory(current))
            _wrap_formatter_method("formatException")
            _wrap_formatter_method("formatStack")
        return True
    except Exception:  # noqa: BLE001 - logging hardening must never break startup
        return False


def installed():
    return getattr(logging.getLogRecordFactory(), "_ai_floor_redaction", False) is True


def uninstall_log_redaction():
    """Restore the previous factory and formatter methods (tests only)."""
    with _LOCK:
        current = logging.getLogRecordFactory()
        if getattr(current, "_ai_floor_redaction", False):
            logging.setLogRecordFactory(current.previous)
        for name in ("formatException", "formatStack"):
            method = getattr(logging.Formatter, name)
            if getattr(method, "_ai_floor_redaction", False):
                setattr(logging.Formatter, name, method.__wrapped__)
        _STATE["secrets"] = ()
