"""FX stop-loss and fixed take-profit (DEC-FX-SL-3, supersedes DEC-FX-SL-2 / DEC-FX-SL-1). Pure values and
functions; no wall clock, no I/O.

For every FX symbol the engine trades (a pair of two fiat currencies, e.g. EURUSD; never XAUUSD or indices) the SL is
FIXED at exactly 25 pips from the entry (owner, 2026-11-03: "25 sl y 50 tp"): the defaults set FX_SL_MIN_PIPS =
FX_SL_MAX_PIPS = 25, so ``clamp_stop`` moves any structural invalidation onto entry -/+ 25 pips (WIDENED when it was
closer, NARROWED when it was farther, UNCHANGED when it already was 25). TP is FIXED at FX_TP_RR x that distance (2R,
i.e. 50 pips), replacing the fixed 3R target for FX symbols. Non-FX symbols are untouched. The EURUSD pullback limit
(execution/eurusd_pullback.py) applies the same 25 / 50 pips measured from the limit price.

Configuration: FIXED in code (DEC-FX-SL-3). The former per-key overrides ``AI_FLOOR_FX_SL_MIN_PIPS`` /
``AI_FLOOR_FX_SL_MAX_PIPS`` / ``AI_FLOOR_FX_TP_RR`` are no longer read: if any of them is present in the process
environment it is ignored with one warning ``FX_SL_OVERRIDE_IGNORED`` (variable names only, never values) and the
policy stays 25 / 25 pips, 2.0R. The values are not part of ``RuntimeConfig.fingerprint``: changing them is a code
change and a strategy change that needs a flat account (see DEC-FX-SL-3).
"""
from decimal import Decimal
import logging
import os

# Retired overrides (DEC-FX-SL-3): only their presence is checked, to warn that they are ignored.
MIN_PIPS_ENV = "AI_FLOOR_FX_SL_MIN_PIPS"
MAX_PIPS_ENV = "AI_FLOOR_FX_SL_MAX_PIPS"
TP_RR_ENV = "AI_FLOOR_FX_TP_RR"
# DEC-FX-SL-3 (owner, 2026-11-03): EURUSD SL fixed at 25 pips (min == max: no structural range), TP 2R = 50 pips.
DEFAULT_SL_MIN_PIPS = Decimal(25)
DEFAULT_SL_MAX_PIPS = Decimal(25)
DEFAULT_TP_RR = Decimal("2.0")
# Fill-time execution tolerance, same absolute width as DEC-4.7 (fixed 3R plan -> 2.5R fill floor): 0.5R.
FILL_TOLERANCE_R = Decimal("0.5")

FIAT = frozenset({"AUD", "CAD", "CHF", "CNH", "CZK", "DKK", "EUR", "GBP", "HKD", "HUF", "JPY", "MXN", "NOK", "NZD",
                  "PLN", "SEK", "SGD", "TRY", "USD", "ZAR"})
WIDENED, NARROWED, UNCHANGED = "WIDENED", "NARROWED", "UNCHANGED"


def _load(environ):
    """The fixed policy; any retired override key present in ``environ`` is ignored with a warning (names only)."""
    ignored = [name for name in (MIN_PIPS_ENV, MAX_PIPS_ENV, TP_RR_ENV) if name in environ]
    if ignored:
        logging.getLogger("ai_floor.runtime").warning(
            "FX_SL_OVERRIDE_IGNORED: %s; fixed policy %s-%s pips, %sR", ",".join(ignored),
            DEFAULT_SL_MIN_PIPS, DEFAULT_SL_MAX_PIPS, DEFAULT_TP_RR)
    return DEFAULT_SL_MIN_PIPS, DEFAULT_SL_MAX_PIPS, DEFAULT_TP_RR


FX_SL_MIN_PIPS, FX_SL_MAX_PIPS, FX_TP_RR = _load(os.environ)
FX_FILL_RR_FLOOR = FX_TP_RR - FILL_TOLERANCE_R
# R:R contract identity of an FX plan (core.rr_contract.POLICY_FX). The multiple is part of the identity, so a plan or
# a stamped order made under another FX_TP_RR is never judged with this one.
POLICY_FX = f"V2_FX_SL_CLAMP_{FX_TP_RR.normalize()}R"


def is_fx(symbol):
    """True for a six-letter pair of two fiat currencies (EURUSD); False for metals (XAUUSD), indices, anything else."""
    return (isinstance(symbol, str) and len(symbol) == 6 and symbol[:3] != symbol[3:]
            and symbol[:3] in FIAT and symbol[3:] in FIAT)


def pip_size(symbol):
    """Price size of one pip: 0.01 for JPY-quoted pairs, else 0.0001."""
    if not is_fx(symbol):
        raise ValueError(f"{symbol!r} is not an FX symbol")
    return Decimal("0.01") if symbol[3:] == "JPY" else Decimal("0.0001")


def clamp_stop(symbol, side, entry, stop, low=None, high=None):
    """(stop, action, structural_pips) with the SL distance clamped into [low, high] pips (defaults: the config).

    ``entry`` and ``stop`` are Decimals with the stop on the protective side of the entry. A clamped stop is entry -/+
    exactly ``low`` or ``high`` pips, so it stays on any grid as fine as the pip."""
    low = FX_SL_MIN_PIPS if low is None else low
    high = FX_SL_MAX_PIPS if high is None else high
    pip = pip_size(symbol)
    distance = entry - stop if side == "LONG" else stop - entry
    if side not in ("LONG", "SHORT") or distance <= 0:
        raise ValueError("stop must be on the protective side of the entry")
    pips = distance / pip
    if pips < low:
        bound, action = low, WIDENED
    elif pips > high:
        bound, action = high, NARROWED
    else:
        return stop, UNCHANGED, pips
    clamped = bound * pip
    return (entry - clamped if side == "LONG" else entry + clamped), action, pips


__all__ = ["FX_FILL_RR_FLOOR", "FX_SL_MAX_PIPS", "FX_SL_MIN_PIPS", "FX_TP_RR", "NARROWED", "POLICY_FX", "UNCHANGED",
           "WIDENED", "clamp_stop", "is_fx", "pip_size"]
