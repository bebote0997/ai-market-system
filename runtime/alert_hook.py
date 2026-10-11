"""Post-cycle alerts: bounded, nonblocking admission; all I/O belongs to one daemon worker.

OFF unless AI_FLOOR_ALERTS_ENABLED is exactly 1. Durable snapshots are written by the worker before
sending, and pending snapshots are replayed at startup. Queue admission itself is not durable: a
process crash before the worker persists an event, or queue overflow, can lose that alert.
"""
import logging
import os
import queue
import threading
import time

from runtime.alert_catalog import from_notification
from runtime.alert_dispatch import alerts_enabled, dispatcher_from_env

LOG = logging.getLogger(__name__)
LEDGER_ENV = "AI_FLOOR_ALERTS_LEDGER_PATH"


class AlertHook:
    def __init__(self, *, environ=None, dispatcher_factory=None, maxsize=1000):
        if not isinstance(maxsize, int) or maxsize <= 0:
            raise ValueError("alert queue maxsize must be positive")
        self.environ = dict(os.environ if environ is None else environ)
        self.dispatcher = None
        self._queue = queue.Queue(maxsize=maxsize)
        self._closed = threading.Event()
        self._ready = threading.Event()
        self._admission = threading.Lock()
        self._worker = None
        if not alerts_enabled(self.environ):
            self._ready.set()
            return
        try:
            factory = dispatcher_factory or dispatcher_from_env
            self.dispatcher = factory(ledger_path=self.environ.get(LEDGER_ENV) or ":memory:",
                                      environ=self.environ)
            self._worker = threading.Thread(target=self._run, name="alert-delivery", daemon=True)
            self._worker.start()
        except Exception as exc:  # noqa: BLE001 - alerting never controls anything
            LOG.warning("component=alerts event=HOOK_FAILED error_type=%s", type(exc).__name__)
            self._ready.set()

    def __call__(self, events):
        """Only enqueue immutable notification events; return admission counts (not delivery status)."""
        if self._worker is None or self._closed.is_set():
            return None
        queued = dropped = 0
        try:
            for event in events:
                with self._admission:
                    if self._closed.is_set():
                        return None
                    try:
                        self._queue.put_nowait(event)
                        queued += 1
                    except queue.Full:
                        dropped += 1
            if dropped:
                LOG.warning("component=alerts event=QUEUE_FULL dropped=%d", dropped)
            return {"queued": queued, "dropped": dropped}
        except Exception as exc:  # noqa: BLE001
            LOG.warning("component=alerts event=HOOK_FAILED error_type=%s", type(exc).__name__)
            return None

    def _run(self):
        try:
            try:
                self.dispatcher.recover_pending()
            except Exception as exc:  # noqa: BLE001
                LOG.warning("component=alerts event=RECOVERY_FAILED error_type=%s", type(exc).__name__)
            finally:
                self._ready.set()
            recovery_at = time.monotonic() + 1.0
            while not self._closed.is_set() or not self._queue.empty():
                if not self._closed.is_set() and time.monotonic() >= recovery_at:
                    try:
                        self.dispatcher.recover_pending()
                    except Exception as exc:  # noqa: BLE001
                        LOG.warning("component=alerts event=RECOVERY_FAILED error_type=%s", type(exc).__name__)
                    recovery_at = time.monotonic() + 1.0
                try:
                    event = self._queue.get(timeout=0.05)
                except queue.Empty:
                    continue
                try:
                    alert = _to_alert(event)
                    if alert is not None:
                        self.dispatcher.dispatch(alert)
                except Exception as exc:  # noqa: BLE001
                    LOG.warning("component=alerts event=WORKER_FAILED error_type=%s", type(exc).__name__)
                finally:
                    self._queue.task_done()
        finally:
            self.dispatcher.ledger.close()

    def flush(self, timeout=10.0):
        """Wait at most timeout seconds for startup recovery and admitted work; True when drained."""
        deadline = time.monotonic() + max(0.0, timeout)
        if not self._ready.wait(max(0.0, deadline - time.monotonic())):
            return False
        with self._queue.all_tasks_done:
            while self._queue.unfinished_tasks:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._queue.all_tasks_done.wait(remaining)
        return True

    def close(self, timeout=10.0):
        """Stop admission and drain in the worker; bounded join, safe to call again after a timeout."""
        with self._admission:
            self._closed.set()
        if self._worker is None:
            return True
        self._worker.join(max(0.0, timeout))
        return not self._worker.is_alive()


def _to_alert(event):
    try:
        return from_notification(event)
    except Exception as exc:  # noqa: BLE001 - one malformed event never blocks the others
        LOG.warning("component=alerts event=MAPPING_FAILED error_type=%s", type(exc).__name__)
        return None


__all__ = ["AlertHook", "LEDGER_ENV"]
