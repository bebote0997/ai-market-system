"""Code-level pause of PAPER symbols (DEC-XAU-OFF-1, owner 2026-10-19 11:35 ET).

A paused symbol stays in ``RuntimeConfig.enabled_symbols`` (the config fingerprint, the cloud preflight and the
catch-up scope are unchanged, so the experiment resumes), but the PAPER engine never runs a cycle for it: no slot
claim, no AI call, no market data for new setups, no cycle alerts. Its open PAPER position and pending orders are
closed / cancelled once, at market (simulated), with ``SYMBOL_PAUSED`` (``OperationalRuntime.close_paused_symbols``).
Not an environment variable on purpose: changing the enabled list would stop the runner.
"""
import logging

PAUSED_SYMBOLS = frozenset({"XAUUSD"})  # DEC-XAU-OFF-1
EXIT_REASON = "SYMBOL_PAUSED"

LOG = logging.getLogger("ai_floor.runtime")
_logged = set()


def is_paused(symbol):
    return symbol in PAUSED_SYMBOLS


def log_paused_once(symbol):
    """One INFO line per paused symbol per process."""
    if symbol not in _logged:
        _logged.add(symbol)
        LOG.info("component=scheduler event=SYMBOL_PAUSED symbol=%s", symbol)
