"""Best-effort Slack alerts for broker-demo router events (``BrokerDemoRouter.on_alert``).

``broker_alert_sink_from_env`` returns None unless ``AI_FLOOR_ALERTS_ENABLED == "1"`` (exact, same rule as
``runtime.alert_dispatch.alerts_enabled``) and ``SLACK_WEBHOOK_URL`` is a Slack HTTPS webhook. The sink turns a
whitelisted event kind into one Spanish line built only from a safe subset of the detail (symbol, side, units, reason,
trade id masked to its last 4 characters, P&L / floor amounts); tokens, account ids, client ids and URLs never reach
the message. At most one identical (kind, reason) message per minute and 20 messages per hour per process; extras are
dropped silently and counted. Delivery uses ``runtime.notifications.send_slack`` with a 5 s timeout from a daemon worker thread fed by a bounded
queue (``QUEUE_MAX``; overflow is dropped and counted), so a slow Slack never delays the router (emergency / loss
flattening); the sink never raises and failures are logged by type only.
"""
from collections import deque
from decimal import Decimal
import logging
import os
import queue
import re
import threading
import time

from runtime.alert_dispatch import alerts_enabled
from runtime.notifications import _slack_post, send_slack, valid_slack_webhook

LOG = logging.getLogger("ai_floor.broker.alerts")
TIMEOUT_SECONDS = 5
DEDUP_SECONDS = 60
HOURLY_CAP = 20
QUEUE_MAX = 20
PREFIX = "[OANDA demo]"
MESSAGES = {
    "ACCEPTED": "Orden enviada",
    "REJECTED": "Orden rechazada",
    "UNKNOWN": "Orden en estado desconocido (bloquea entradas hasta reconciliar)",
    "CLOSED": "Operación cerrada",
    "CLOSE_UNKNOWN": "Cierre en estado desconocido",
    "CLOSE_FAILED": "Fallo al cerrar operación",
    "CLOSED_AT_BROKER": "Operación cerrada en el broker (SL/TP)",
    "BREAKEVEN": "Stop movido a breakeven",
    "DRIFT": "Desajuste broker/estado local: entradas bloqueadas",
    "UNRESOLVED_ACTION": "Acción sin resolver: entradas bloqueadas",
    "DAILY_LOSS_HALT": "Límite de pérdida diaria alcanzado: entradas detenidas",
    "DAILY_LOSS_RESUMED": "Nuevo día: límite de pérdida diaria reanudado",
    "DRAWDOWN_HALT": "Drawdown máximo alcanzado: trading detenido",
    "FLATTEN_DAILY_LOSS": "Cierre de posiciones por pérdida diaria",
    "FLATTEN_DRAWDOWN": "Cierre de posiciones por drawdown",
    "EMERGENCY_BUTTON": "Botón de emergencia: cerrando operaciones",
    "FLATTEN_REQUEST_DONE": "Emergencia: todas las operaciones cerradas",
    "DRIFT_CLEARED": "Desajuste resuelto: entradas desbloqueadas",
    "DRIFT_CLEAR_REFUSED": "Desbloqueo de desajuste rechazado",
    "FRIDAY_FLATTEN_FAILED": "Cierre previo al viernes incompleto",
    "MANUAL_CLOSE": "Cierre manual del owner (botón «Cerrar operación»)",
    "MANUAL_CLOSE_FAILED": "Cierre manual rechazado por el broker (la operación sigue abierta)",
}
_SAFE = re.compile(r"[^A-Za-z0-9_:.\-]")
_LONG_DIGITS = re.compile(r"\d{5,}")
_URL = re.compile(r"\w+://\S*")
_CENT = Decimal("0.01")


def _word(value, limit=48):
    """Short code-like token (reasons, sides, symbols); anything URL-like or carrying long digit runs is masked."""
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return None
    text = _SAFE.sub("", str(value))
    if "http" in text.lower() or "hooks" in text.lower():
        return "redacted"
    return _LONG_DIGITS.sub("#", text)[:limit] or None


def _amount(value):
    try:
        out = Decimal(str(value))
    except (ArithmeticError, ValueError, TypeError):
        return None
    return str(out.quantize(_CENT)) if out.is_finite() else None


def mask_id(value):
    text = _SAFE.sub("", str(value or ""))
    return f"…{text[-4:]}" if text else None


def _reason(detail):
    reasons = detail.get("reasons")
    if isinstance(reasons, (list, tuple)):
        return ",".join(filter(None, (_word(r, 32) for r in reasons[:4]))) or None
    return _word(detail.get("reason"))


def _count(value):
    return len(value) if isinstance(value, (list, tuple, dict, set)) else None


def build_message(kind, detail):
    """One Spanish line for a whitelisted kind (None otherwise). ``detail`` may be a dict or a free-text string."""
    label = MESSAGES.get(kind)
    if label is None:
        return None
    parts = [f"{PREFIX} {label}"]
    if isinstance(detail, str):
        text = _LONG_DIGITS.sub("#", _URL.sub("", detail)).strip()[:160]
        return " · ".join(parts + ([text] if text else []))
    detail = detail if isinstance(detail, dict) else {}
    order = " ".join(filter(None, (_word(detail.get("symbol"), 12), _word(detail.get("side"), 8))))
    units = _word(detail.get("units"), 12)
    if units:
        order = f"{order} {units} u".strip()
    if order:
        parts.append(order)
    if detail.get("trade_id"):
        parts.append(f"trade {mask_id(detail['trade_id'])}")
    for key, name in (("pnl", "P&L"), ("floor", "suelo")):
        amount = None if detail.get(key) is None else _amount(detail[key])
        if amount is not None:
            parts.append(f"{name} {amount}")
    for key, name in (("trades", "operaciones"), ("client_ids", "operaciones"), ("results", "operaciones")):
        count = _count(detail.get(key))
        if count:
            parts.append(f"{name}: {count}")
            break
    if isinstance(detail.get("open_trades"), int):
        parts.append(f"abiertas: {detail['open_trades']}")
    window = _word(detail.get("window"), 16)
    if window:
        parts.append(f"ventana: {window}")
    reason = _reason(detail)
    if reason:
        parts.append(f"motivo: {reason}")
    return " · ".join(parts)


class BrokerAlertSink:
    """Callable ``(kind, detail)``; rate limited, never raises."""

    def __init__(self, webhook_url, *, transport=None, clock=None, timeout=TIMEOUT_SECONDS, background=False):
        self._webhook_url = webhook_url
        self._queue = queue.Queue(maxsize=QUEUE_MAX) if background else None
        self._worker = None
        self._worker_lock = threading.Lock()
        self._transport = transport or _slack_post
        self._clock = clock or time.monotonic
        self._timeout = min(float(timeout), TIMEOUT_SECONDS)
        self._last = {}
        self._sent = deque()
        self.sent = 0
        self.dropped = 0
        self.failed = 0

    def __repr__(self):
        return f"BrokerAlertSink(sent={self.sent}, dropped={self.dropped}, failed={self.failed})"

    __str__ = __repr__

    def _allowed(self, key, now):
        last = self._last.get(key)
        if last is not None and now - last < DEDUP_SECONDS:
            return False
        while self._sent and now - self._sent[0] >= 3600:
            self._sent.popleft()
        if len(self._sent) >= HOURLY_CAP:
            return False
        self._last[key] = now
        self._sent.append(now)
        return True

    def __call__(self, kind, detail=None):
        try:
            text = build_message(kind, detail)
            if text is None:
                return
            key = (kind, _reason(detail) if isinstance(detail, dict) else None)
            if not self._allowed(key, self._clock()):
                self.dropped += 1
                return
            if self._queue is None:
                self._deliver(text)
                return
            self._ensure_worker()
            try:
                self._queue.put_nowait(text)
            except queue.Full:
                self.dropped += 1
        except Exception as exc:  # noqa: BLE001 - alerting never breaks routing
            self.failed += 1
            LOG.warning("component=broker_alerts event=SEND_FAILED error_type=%s", type(exc).__name__)

    def _deliver(self, text):
        try:
            send_slack(self._transport, self._webhook_url, {"text": text}, self._timeout)
            self.sent += 1
        except Exception as exc:  # noqa: BLE001 - alerting never breaks routing
            self.failed += 1
            LOG.warning("component=broker_alerts event=SEND_FAILED error_type=%s", type(exc).__name__)

    def _ensure_worker(self):
        with self._worker_lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._worker = threading.Thread(target=self._run, name="broker-alerts", daemon=True)
            self._worker.start()

    def _run(self):
        while True:
            try:
                text = self._queue.get()
                try:
                    self._deliver(text)
                finally:
                    self._queue.task_done()
            except Exception:  # noqa: BLE001 - the worker never dies noisily
                continue

    def flush(self, timeout=10.0):
        """Wait until queued messages were delivered (tests / shutdown); True when drained."""
        if self._queue is None:
            return True
        deadline = time.monotonic() + timeout
        while self._queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.01)
        return not self._queue.unfinished_tasks


def broker_alert_sink_from_env(env=None, transport=None, clock=None):
    """BrokerAlertSink when alerts are ON and a Slack webhook is configured; else None (nothing sent)."""
    env = os.environ if env is None else env
    if not alerts_enabled(env):
        return None
    url = env.get("SLACK_WEBHOOK_URL")
    if not url:
        return None
    if not valid_slack_webhook(url):
        LOG.warning("component=broker_alerts event=DISABLED reason=invalid_webhook_url")
        return None
    return BrokerAlertSink(url, transport=transport, clock=clock, background=transport is None)


__all__ = ["MESSAGES", "BrokerAlertSink", "broker_alert_sink_from_env", "build_message", "mask_id"]
