"""OANDA practice demo clock for the screens: "Día N de 30" counted from the demo EPOCH (pure; never raises).

Day 1 = the New York calendar date of the epoch; every NY date after it adds one. Uncapped (> 30 = the demo is
complete; display with ``ui.i18n_es.demo_day_text``). Unknown / naive timestamps or ``now`` before the epoch => None.
Single source of truth: the rule lives in ``execution.demo_epoch`` (the Slack / email alerts and the daily summary
use the same function).
"""
from execution.demo_epoch import demo_day_from_epoch

__all__ = ["demo_day_from_epoch"]
