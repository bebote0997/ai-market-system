"""FX stop-loss clamp and fixed take-profit (DEC-FX-SL-1). Pure values and functions; no wall clock, no I/O.

For every FX symbol the engine trades (a pair of two fiat currencies, e.g. EURUSD; never XAUUSD or indices) the
planner keeps the structural invalidation as SL only if its distance from the entry is inside
[FX_SL_MIN_PIPS, FX_SL_MAX_PIPS]; otherwise the SL is moved to the nearest bound (widened below the minimum, narrowed
above the maximum). TP is then FIXED at FX_TP_RR x the (clamped) SL distance (owner: 2R, i.e. 90-120 pips for a
45-60 pip SL), replacing the fixed 3R target for FX symbols. Non-FX symbols are untouched.

Configuration: code defaults below, optionally overridden per key by the process environment (the same mechanism as
every other ``AI_FLOOR_*`` setting; no key is required). Values are read once at import; an invalid value fails
closed to ALL defaults with a warning, so a typo can never produce a half-applied policy. The values are not part of
``RuntimeConfig.fingerprint``: changing them is a strategy change that needs a flat account (see DEC-FX-SL-1).
"""
from decimal import Decimal, InvalidOperation
import logging
import os

MIN_PIPS_ENV = "AI_FLOOR_FX_SL_MIN_PIPS"
MAX_PIPS_ENV = "AI_FLOOR_FX_SL_MAX_PIPS"
TP_RR_ENV = "AI_FLOOR_FX_TP_RR"
DEFAULT_SL_MIN_PIPS = Decimal(45)
DEFAULT_SL_MAX_PIPS = Decimal(60)
DEFAULT_TP_RR = Decimal("2.0")
# Fill-time execution tolerance, same absolute width as DEC-4.7 (fixed 3R plan -> 2.5R fill floor): 0.5R.
FILL_TOLERANCE_R = Decimal("0.5")

FIAT = frozenset({"AUD", "CAD", "CHF", "CNH", "CZK", "DKK", "EUR", "GBP", "HKD", "HUF", "JPY", "MXN", "NOK", "NZD",
                  "PLN", "SEK", "SGD", "TRY", "USD", "ZAR"})
WIDENED, NARROWED, UNCHANGED = "WIDENED", "NARROWED", "UNCHANGED"


def _load(environ):
    def read(name, default):
        raw = environ.get(name)
        return default if raw is None or raw == "" else Decimal(str(raw).strip())
    try:
        low, high, rr = read(MIN_PIPS_ENV, DEFAULT_SL_MIN_PIPS), read(MAX_PIPS_ENV, DEFAULT_SL_MAX_PIPS), \
            read(TP_RR_ENV, DEFAULT_TP_RR)
        if not (low.is_finite() and high.is_finite() and rr.is_finite()) or not (0 < low <= high) \
                or rr - FILL_TOLERANCE_R < 1:
            raise ValueError("FX SL/TP configuration out of range")
    except (InvalidOperation, ValueError):
        logging.getLogger("ai_floor.runtime").warning(
            "FX_SL_POLICY_INVALID: fail closed to defaults (%s-%s pips, %sR)",
            DEFAULT_SL_MIN_PIPS, DEFAULT_SL_MAX_PIPS, DEFAULT_TP_RR)
        return DEFAULT_SL_MIN_PIPS, DEFAULT_SL_MAX_PIPS, DEFAULT_TP_RR
    return low, high, rr


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
