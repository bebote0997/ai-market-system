"""OANDA practice demo clock for the screens: "Día N de 30" counted from the demo EPOCH (pure; never raises).

Day 1 = the New York calendar date of the epoch; every NY date after it adds one. Uncapped (> 30 = the demo is
complete; display with ``ui.i18n_es.demo_day_text``). Unknown / naive timestamps or ``now`` before the epoch => None.
"""
from datetime import datetime

from ui.i18n_es import ET


def demo_day_from_epoch(epoch, now):
    """Uncapped demo day (int >= 1) of ``now`` for a demo that started at ``epoch``; None when unknown."""
    if not isinstance(epoch, datetime) or not isinstance(now, datetime):
        return None
    if epoch.tzinfo is None or now.tzinfo is None or now < epoch:
        return None
    return (now.astimezone(ET).date() - epoch.astimezone(ET).date()).days + 1
