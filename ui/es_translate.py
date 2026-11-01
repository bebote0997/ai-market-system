"""Spanish translations of agent notes through the OpenAI model already configured for the app.

No new environment variables. A call happens only when ``AI_FLOOR_AI_PROVIDER`` is ``openai`` and
``OPENAI_API_KEY`` is set (the same names the runner uses). Each distinct text is translated once per process
and remembered, including a failure, so a refresh does not call again. One page render waits at most a single
short budget (the next note does not start another wait) and otherwise shows the original. Nothing here
raises to the caller, and the key is never logged.
"""
from contextlib import contextmanager
import json
import os
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_FAIL = object()
_MISS = object()
_CACHE = {}
_EVENTS = {}
_LOCK = threading.Lock()
_BUDGET_UNTIL = 0.0
_LAST_TRANSLATE = 0.0
_BUDGET_SECONDS = 1.2
_PAGE_GAP = 3.0  # quiet longer than this is a new page render (the Floor retries at 8 s)
_TIMEOUT = 2.5
_TRANSPORT = None
_ENV = None

_INSTRUCTIONS = (
    "Traduce al español el texto del usuario. Es una nota de un analista de trading. "
    "Conserva números, precios, símbolos (EURUSD, XAUUSD) y marcos (1H, 15M, 5M, BOS). "
    "No añadas datos que no estén en el texto. Devuelve solo el texto en español."
)


def clear_cache():
    global _BUDGET_UNTIL, _LAST_TRANSLATE
    with _LOCK:
        _CACHE.clear()
        _EVENTS.clear()
    _BUDGET_UNTIL = 0.0
    _LAST_TRANSLATE = 0.0


@contextmanager
def override(transport, env):
    """Tests: a synchronous translator and a stand-in environment. No network."""
    global _TRANSPORT, _ENV
    previous = _TRANSPORT, _ENV
    _TRANSPORT, _ENV = transport, env
    clear_cache()
    try:
        yield
    finally:
        _TRANSPORT, _ENV = previous
        clear_cache()


def _env():
    return os.environ if _ENV is None else _ENV


def configured():
    env = _env()
    if env.get("AI_FLOOR_AI_PROVIDER") != "openai":
        return None
    key = env.get("OPENAI_API_KEY")
    if not isinstance(key, str) or not key.strip():
        return None
    model = env.get("OPENAI_MODEL") or "gpt-5.6-terra"
    return key.strip(), str(model)


def _output_text(raw):
    if not isinstance(raw, dict) or raw.get("status") != "completed":
        return None
    parts = [part.get("text") for item in raw.get("output") or () if isinstance(item, dict) and item.get("type") == "message"
             for part in item.get("content") or () if isinstance(part, dict) and part.get("type") == "output_text"]
    if len(parts) != 1 or not isinstance(parts[0], str):
        return None
    text = " ".join(parts[0].strip().strip('"').split())
    if not text or len(text) > 8000 or any(ord(ch) < 32 and ch not in "\t" for ch in text):
        return None
    return text


def _http(text, key, model, timeout):
    payload = {"model": model, "store": False, "instructions": _INSTRUCTIONS, "input": text}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request("https://api.openai.com/v1/responses", data=body,
                      headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
                      method="POST")
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed host, no redirects followed by default for POST
        return json.load(response)


def _store(text, value):
    with _LOCK:
        _CACHE[text] = value
        event = _EVENTS.get(text)
    if event is not None:
        event.set()


def _work(text, key, model):
    transport = _TRANSPORT or _http
    try:
        spanish = _output_text(transport(text, key, model, _TIMEOUT)) if transport is _http else transport(text, key, model, _TIMEOUT)
        # A test transport may return the Spanish string directly.
        if transport is not _http and isinstance(spanish, str):
            spanish = " ".join(spanish.split()) or None
        elif transport is not _http and isinstance(spanish, dict):
            spanish = _output_text(spanish)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, TypeError):
        spanish = None
    _store(text, spanish if spanish else _FAIL)


def _ensure(text, key, model):
    with _LOCK:
        if text in _CACHE or text in _EVENTS:
            return _EVENTS.get(text)
        event = threading.Event()
        _EVENTS[text] = event
    if _TRANSPORT is not None:
        _work(text, key, model)
        return event
    threading.Thread(target=_work, args=(text, key, model), name="es-translate", daemon=True).start()
    return event


def _wait(event):
    """Wait inside one short window per page render. Do not open another window for the next text.

    A render that translates every stored note used to start a fresh 1.2 s wait after each window expired, so the
    Floor script never finished and the phone stayed on the loading spinner. A later render (a few seconds of quiet)
    may open one new window. Never raises.
    """
    global _BUDGET_UNTIL, _LAST_TRANSLATE
    now = time.monotonic()
    idle = now - _LAST_TRANSLATE
    _LAST_TRANSLATE = now
    if event is None or event.is_set():
        return
    if _BUDGET_UNTIL <= 0.0 or idle > _PAGE_GAP:
        _BUDGET_UNTIL = now + _BUDGET_SECONDS
    elif now >= _BUDGET_UNTIL:
        return
    remaining = _BUDGET_UNTIL - time.monotonic()
    if remaining > 0:
        event.wait(remaining)


def translate_es(text):
    """Spanish text, or None when it cannot be translated (the caller shows the original). Never raises."""
    text = " ".join(str(text or "").split())
    if not text:
        return None
    with _LOCK:
        if text in _CACHE:
            value = _CACHE[text]
            return None if value is _FAIL else value
    setup = configured()
    if setup is None:
        return None
    try:
        event = _ensure(text, *setup)
        _wait(event)
        with _LOCK:
            value = _CACHE.get(text, _MISS)
        if value is _MISS or value is _FAIL:
            return None
        return value
    except Exception:  # noqa: BLE001 - a translator failure is the original text, not a broken page
        return None
