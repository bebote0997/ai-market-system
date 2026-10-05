"""Passive SYSTEM HEALTH hook points for existing runtime/provider code (V2 Phase 1).

Standard library only. With no sink installed (the default) every hook is a no-op.
``emit`` never raises an Exception: a failing sink is logged by type only and ignored, so
the calling business code continues exactly as if health did not exist. Hooks only pass
along values the caller has already computed; they never return anything to the caller.
"""
import logging

LOG = logging.getLogger(__name__)
_sink = None


def install_health_sink(sink):
    """Install the object whose methods receive hook events (e.g. SystemHealthSink)."""
    global _sink
    _sink = sink


def uninstall_health_sink():
    global _sink
    _sink = None


def emit(event, **facts):
    """Deliver one observation to the installed sink. Returns None; never raises an Exception."""
    sink = _sink
    if sink is None:
        return
    try:
        getattr(sink, event)(**facts)
    except Exception as exc:  # noqa: BLE001 - observational boundary
        LOG.warning("component=system_health event=HOOK_FAILED hook=%s error_type=%s", event, type(exc).__name__)
