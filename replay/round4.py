"""Round 4 (REPLAY ONLY): multi-day swing / trend / mean-reversion families, entry filters and other instruments.

Families on D1 / H4 / H1 bars resampled from closed 1h bars only (``entry_search.resample_closed``: a bucket is
visible once every one of its 1h bars has closed); every indicator at bar ``k`` uses bars <= k; a decision at the
close of bar ``k`` (its ``available`` time) fills at the open of the first 5m bar starting at or after it. Trades are
walked on 5m mid OHLC with bid/ask = mid -/+ the causal half spread: same-bar SL first, gaps through the stop/target
fill at the open, stops ratchet only. Costs: causal 30-day trailing median of the 5m AskClose - BidClose of the
instrument (``intraday_backtest.causal_spread_pips``; no fallback: no causal spread -> no trade) + slippage
0.1 x that spread (min 0.2 pip on FX) on market entries / stops / market exits (none on TP). Financing: none in the
candles -> reported at a fixed 0 % carry (the pass rule) and at a -3 %/yr of notional stress (R per day held =
0.03 / 365 x fill / initial risk). Validation and the pass rule: ``replay.fiveyear`` (yearly blocks Y1-Y5,
day-block CI80, MTM DD, avg cost <= 0.10R). One position at a time per variant, one entry per idea, 2R/day realised
halt, no intraday hard close for the multi-day families, Friday close OFF (one variant ON).

Run (sections append to the report in order):
``python -m replay.round4 --data /workspace/backtest/data5y --out /workspace/backtest/r4
--report /workspace/backtest/report_round4.md --sections declare,a,b,c,d,fri,e,robust,final``.
Market data and outputs live outside the repository and are never committed.
"""
import argparse
import json
import math
import os
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from replay import entry_search as es
from replay import fiveyear as fy
from replay import intraday_backtest as bt
from replay import round2 as r2

# ---------------------------------------------------------------- fixed parameters (declared before any run)
INSTRUMENTS = {  # symbol: (pip / point size, FX)
    "EURUSD": (0.0001, True), "GBPUSD": (0.0001, True), "USDJPY": (0.01, True), "XAUUSD": (0.1, False),
    "US30USD": (1.0, False), "NAS100USD": (1.0, False)}
HOME = "EURUSD"
BREAK_HOURS = (17,)  # XAU / indices: no 1h bar 17:00-18:00 NY (OANDA daily break); FX trades through
SLIP_SHARE = 0.10
MIN_SLIP_FX_PIPS = 0.2
CARRY_STRESS = 0.03  # per year of notional, against the position
MAX_FILL_WAIT_NS = 4 * es.DAY_NS  # weekend + holiday: a decision with no 5m bar within 4 days is not traded
SWING_BUFFER_ATR = 0.2
SWING_CLAMP_ATR = (1.0, 4.0)  # swing stop < 1 ATR widened to 1 ATR, > 4 ATR skipped
PREVIOUS_TESTS = 145
TP, SL, TIME, FRIDAY, END, SIGNAL = bt.TP, bt.SL, bt.TIME, bt.FRIDAY, bt.END, "SIGNAL"
HOUR_NS = es.HOUR

# main parameters per base variant (the +/-25 % neighbourhood is built from these, only for passers)
BASE = {
    "A:ATR:TP2": ("A", {"brk": 20, "sma": 100, "atr": 2.0, "tp": 2.0, "days": 10}),
    "A:ATR:TP3": ("A", {"brk": 20, "sma": 100, "atr": 2.0, "tp": 3.0, "days": 10}),
    "A:SW:TP2": ("A", {"brk": 20, "sma": 100, "tp": 2.0, "days": 10}),
    "A:SW:TP3": ("A", {"brk": 20, "sma": 100, "tp": 3.0, "days": 10}),
    "B1:DON55/20": ("B1", {"entry": 55, "exit": 20, "atr": 2.0}),
    "B2:SMA50/200": ("B2", {"fast": 50, "slow": 200, "atr": 3.0}),
    "B3:TSMOM12": ("B3", {"months": 12, "atr": 3.0}),
    "B4:DON20/10:H4": ("B4", {"entry": 20, "exit": 10, "atr": 2.0}),
    "C1:RSI2:H1": ("C1", {"rsi": 10.0, "adx": 20.0, "atr": 1.5, "hours": 24}),
    "C2:BB20:H4": ("C2", {"period": 20, "width": 2.0, "adx": 20.0, "atr": 1.5, "days": 5}),
    "JT[R1+R5]:SLADR": ("JT", {"lo": 0.25, "hi": 0.5, "tp": 2.0}),
    "E3:SLADR": ("E3", {"lo": 0.25, "hi": 0.5, "tp": 2.0}),
}
INTEGER_PARAMS = {"brk", "sma", "days", "entry", "exit", "fast", "slow", "months", "hours", "period"}
FAMILIES_ABC = ("A", "B1", "B2", "B3", "B4", "C1", "C2")
# d2: long only the higher-yield currency. USD policy rate (Fed funds target) above the ECB deposit rate in every
# month of the data (Oct 2021: 0-0.25 % vs -0.50 %; 2023: 5.25-5.50 % vs 4.00 %; 2025-26: >= 3.5 % vs <= 2.75 %).
# Only this sign is used; a full monthly table (GBP, JPY levels in 2026) is not known reliably -> N/A.
CARRY_SIDE = {"EURUSD": -1}


@contextmanager
def instrument_pip(pip):
    """The replay modules use a module-level ``PIP`` (EURUSD); scale it to ``pip`` for one instrument."""
    from replay import round3 as r3
    mods = [m for m in (bt, es, r2, r3) if hasattr(m, "PIP")]
    saved = [m.PIP for m in mods]
    try:
        for m in mods:
            m.PIP = pip
        yield
    finally:
        for m, value in zip(mods, saved):
            m.PIP = value


_RESAMPLE = es.resample_closed


@contextmanager
def schedule(break_hours):
    """``entry_search.Context`` builds H4 / D1 with ``resample_closed``; make it schedule-aware for one instrument."""
    if not break_hours:
        yield
        return
    original = es.resample_closed
    try:
        es.resample_closed = lambda f, h, complete_only=True: resample(f, h, complete_only, break_hours)
        yield
    finally:
        es.resample_closed = original


# ---------------------------------------------------------------- bars and indicators (closed bars only)
@dataclass
class HBars:
    avail: np.ndarray  # int64 ns: the bar is visible to decisions at or after this time
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray  # noqa: E741
    c: np.ndarray

    def __len__(self):
        return len(self.avail)


def resample(frame_1h, hours, complete_only=True, break_hours=()):
    """``entry_search.resample_closed`` with an instrument schedule: a bucket is complete when it holds every hour
    of its span except the instrument's daily break hours (NY clock; XAU / indices do not trade 17:00-18:00 NY).
    Without break hours it is ``resample_closed`` itself."""
    if not break_hours:
        return _RESAMPLE(frame_1h, hours, complete_only)
    out = _RESAMPLE(frame_1h, hours, complete_only=False)
    starts = es._ns(frame_1h.index)
    opens = es.trading_day_open(frame_1h.index)
    key = opens if hours >= 24 else opens + (starts - opens) // (hours * HOUR_NS) * hours * HOUR_NS
    count = pd.Series(starts).groupby(key).nunique().to_numpy()
    keys = np.unique(key)
    avail = out["available"].to_numpy(np.int64)
    expected = (avail - keys) // HOUR_NS
    for b in range(max(hours, 25)):
        at = keys + b * HOUR_NS
        clock = pd.to_datetime(at, utc=True).tz_convert(es.NY).hour
        expected -= ((at < avail) & np.isin(clock, list(break_hours))).astype(np.int64)
    last_end = starts[-1] + HOUR_NS if len(starts) else 0
    out["complete"] = (count == expected) & (avail <= last_end)
    return out[out["complete"]] if complete_only else out


def htf_bars(frame_1h, tf, break_hours=()):
    """D1 / H4 from closed 1h bars (complete buckets only) or H1 itself (visible at start + 1h)."""
    if tf == "H1":
        f = frame_1h
        avail = es._ns(f.index) + HOUR_NS
    else:
        f = resample(frame_1h, 24 if tf == "D1" else 4, break_hours=break_hours)
        avail = f["available"].to_numpy(np.int64)
    return HBars(np.asarray(avail, dtype=np.int64), *(f[x].to_numpy(float) for x in ("Open", "High", "Low", "Close")))


def shift(x, n=1):
    out = np.full(len(x), np.nan)
    if n < len(x):
        out[n:] = x[:-n] if n else x
    return out


def sma(x, n):
    return pd.Series(x).rolling(n).mean().to_numpy()


def atr(b, n):
    """Simple mean of the true range over the last ``n`` bars (as ``entry_search``)."""
    prev = shift(b.c)
    tr = np.nanmax(np.vstack([b.h - b.l, np.abs(b.h - prev), np.abs(b.l - prev)]), axis=0)
    return pd.Series(tr).rolling(n).mean().to_numpy()


def wilder(x, n):
    return pd.Series(x).ewm(alpha=1.0 / n, adjust=False).mean().to_numpy()


def rsi(c, n):
    d = np.diff(c, prepend=np.nan)
    up, dn = wilder(np.where(d > 0, d, 0.0)[1:], n), wilder(np.where(d < 0, -d, 0.0)[1:], n)
    with np.errstate(divide="ignore", invalid="ignore"):
        value = np.where(dn == 0, 100.0, 100.0 - 100.0 / (1.0 + up / dn))
    out = np.full(len(c), np.nan)
    out[1:] = value
    out[1:n] = np.nan  # warm-up
    return out


def adx(b, n=14):
    up, dn = np.diff(b.h, prepend=np.nan), -np.diff(b.l, prepend=np.nan)
    plus = np.where((up > dn) & (up > 0), up, 0.0)[1:]
    minus = np.where((dn > up) & (dn > 0), dn, 0.0)[1:]
    prev = b.c[:-1]
    tr = np.maximum.reduce([b.h[1:] - b.l[1:], np.abs(b.h[1:] - prev), np.abs(b.l[1:] - prev)])
    atr_w = wilder(tr, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi, mdi = 100 * wilder(plus, n) / atr_w, 100 * wilder(minus, n) / atr_w
        dx = 100 * np.abs(pdi - mdi) / (pdi + mdi)
    value = wilder(np.nan_to_num(dx), n)
    out = np.full(len(b), np.nan)
    out[1:] = value
    out[1:2 * n] = np.nan  # warm-up
    return out


def prior_max(x, n):
    """max of bars k-n..k-1 (the current bar excluded)."""
    return shift(pd.Series(x).rolling(n).max().to_numpy())


def prior_min(x, n):
    return shift(pd.Series(x).rolling(n).min().to_numpy())


def first_cross(cond):
    """cond[k] and not cond[k-1] (NaN comparisons are False)."""
    prev = np.concatenate([[False], cond[:-1]])
    return cond & ~prev


def gt(a, b):
    with np.errstate(invalid="ignore"):
        return np.asarray(a > b, dtype=bool)


def lt(a, b):
    with np.errstate(invalid="ignore"):
        return np.asarray(a < b, dtype=bool)


# ---------------------------------------------------------------- setups
@dataclass
class Setup:
    t: int  # decision time (ns) = close of the last bar used; fill at the open of the first 5m bar starting >= t
    side: int  # +1 long, -1 short
    family: str
    idea: str
    stop_dist: float = math.nan  # price distance from the fill
    stop_level: float = math.nan  # structural stop (absolute), distance clamped to ``clamp``
    clamp: tuple = None
    tp_r: float = None
    tp_price: float = None
    events: list = field(default_factory=list)  # (t ns, 'stop', level) ratchet / (t ns, 'exit', reason) next open
    deadline: int = None  # absolute time exit (ns): market exit at the close of the last 5m bar ending by it
    hold_ns: int = None  # time exit relative to the fill


def _trail(b, k, side, a, mult, until):
    """Chandelier stop levels at each bar m in (k, until]: best close of k..m -/+ mult x ATR[m]."""
    out = []
    best = b.c[k]
    for m in range(k + 1, min(until, len(b) - 1) + 1):
        best = max(best, b.c[m]) if side > 0 else min(best, b.c[m])
        if math.isfinite(a[m]):
            out.append((int(b.avail[m]), "stop", best - side * mult * a[m]))
    return out


def _first(mask, k, limit=None):
    """First index m > k with mask[m] (None if none)."""
    idx = np.flatnonzero(mask[k + 1:limit])
    return int(idx[0]) + k + 1 if len(idx) else None


def swing_setups(b, p, mode):
    """(a) D1 close beyond the 20-day high/low (first close) in the direction of the SMA100 slope (today vs
    yesterday). Stop: ATR mode 2 x ATR14(D1) from the fill; SW mode the last confirmed D1 swing low/high -/+ 0.2 ATR,
    distance clamped to 1-4 ATR. TP ``tp`` R, time exit at the close of the 10th D1 bar after the signal bar."""
    d = b["D1"]
    a, ma = atr(d, 14), sma(d.c, p["sma"])
    slope = ma - shift(ma)
    up = first_cross(gt(d.c, prior_max(d.h, p["brk"])) & gt(slope, 0))
    dn = first_cross(lt(d.c, prior_min(d.l, p["brk"])) & lt(slope, 0))
    sh, sl_, _, _ = es.confirmed_swings(d.h, d.l)
    out = []
    for side, mask in ((1, up), (-1, dn)):
        for k in np.flatnonzero(mask):
            if not math.isfinite(a[k]):
                continue
            end = k + p["days"]
            s = Setup(int(d.avail[k]), side, "A", f"A:{side}:{k}", tp_r=p["tp"],
                      deadline=int(d.avail[end]) if end < len(d) else None)
            if mode == "ATR":
                s.stop_dist = p["atr"] * a[k]
            else:
                level = sl_[k] - SWING_BUFFER_ATR * a[k] if side > 0 else sh[k] + SWING_BUFFER_ATR * a[k]
                if not math.isfinite(level):
                    continue
                s.stop_level, s.clamp = level, (SWING_CLAMP_ATR[0] * a[k], SWING_CLAMP_ATR[1] * a[k])
            out.append(s)
    return out


def donchian_setups(bars, p, family, atr_n=20):
    """(b1 D1 / b4 H4) first close beyond the prior ``entry``-bar high/low; exit at the next open after a close
    beyond the prior ``exit``-bar opposite extreme; fixed stop ``atr`` x ATR20 from the fill; no target."""
    a = atr(bars, atr_n)
    out = []
    for side in (1, -1):
        if side > 0:
            enter = first_cross(gt(bars.c, prior_max(bars.h, p["entry"])))
            leave = lt(bars.c, prior_min(bars.l, p["exit"]))
        else:
            enter = first_cross(lt(bars.c, prior_min(bars.l, p["entry"])))
            leave = gt(bars.c, prior_max(bars.h, p["exit"]))
        for k in np.flatnonzero(enter):
            if not math.isfinite(a[k]):
                continue
            m = _first(leave, k)
            events = [(int(bars.avail[m]), "exit", SIGNAL)] if m is not None else []
            out.append(Setup(int(bars.avail[k]), side, family, f"{family}:{side}:{k}", stop_dist=p["atr"] * a[k],
                             events=events))
    return out


def sma_cross_setups(b, p):
    """(b2) D1 SMA50 crossing SMA200 -> enter that way; exit at the opposite cross (next open) or on the chandelier
    stop: best close since entry -/+ 3 x ATR14(D1), ratcheted on each D1 close; initial stop 3 x ATR14 from the fill."""
    d = b["D1"]
    a, fast, slow = atr(d, 14), sma(d.c, p["fast"]), sma(d.c, p["slow"])
    above, below = gt(fast, slow), lt(fast, slow)
    out = []
    for side, enter, leave in ((1, first_cross(above), below), (-1, first_cross(below), above)):
        for k in np.flatnonzero(enter):
            if k == 0 or not (math.isfinite(slow[k - 1]) and math.isfinite(a[k])):
                continue
            m = _first(leave, k)
            until = m if m is not None else len(d) - 1
            events = _trail(d, k, side, a, p["atr"], until)
            if m is not None:
                events.append((int(d.avail[m]), "exit", SIGNAL))
            out.append(Setup(int(d.avail[k]), side, "B2", f"B2:{side}:{k}", stop_dist=p["atr"] * a[k], events=events))
    return out


def month_ends(d):
    """D1 indices whose NY close date is the last weekday of its calendar month (calendar only, no price data)."""
    dates = pd.to_datetime(d.avail, utc=True).tz_convert(es.NY).tz_localize(None).normalize()
    nxt = dates + pd.offsets.BDay(1)
    return np.flatnonzero(np.asarray(nxt.month != dates.month))


def tsmom_setups(b, p):
    """(b3) at each month-end D1 close: side = sign of the return over the last ``months`` months (close vs the last
    D1 close at or before the same moment ``months`` earlier). The position is held while the monthly sign agrees
    (the same-side month-end setup is skipped as position_open) and exits at the next open after the first month-end
    with the opposite sign; chandelier stop 3 x ATR20(D1) from the best close, ratcheted daily; after a stop-out the
    next month-end re-enters."""
    d = b["D1"]
    a = atr(d, 20)
    ends = month_ends(d)
    when = pd.to_datetime(d.avail, utc=True)
    sign = np.zeros(len(d))
    for k in ends:
        back = int(np.searchsorted(d.avail, (when[k] - pd.DateOffset(months=p["months"])).value, side="right")) - 1
        if back >= 0 and when[k] - when[back] >= pd.Timedelta(days=28 * p["months"]):
            sign[k] = np.sign(d.c[k] - d.c[back])
    out = []
    for q, k in enumerate(ends):
        side = int(sign[k])
        if side == 0 or not math.isfinite(a[k]):
            continue
        flip = next((m for m in ends[q + 1:] if sign[m] == -side), None)
        until = flip if flip is not None else len(d) - 1
        events = _trail(d, k, side, a, p["atr"], until)
        if flip is not None:
            events.append((int(d.avail[flip]), "exit", SIGNAL))
        out.append(Setup(int(d.avail[k]), side, "B3", f"B3:{k}", stop_dist=p["atr"] * a[k], events=events))
    return out


def rsi2_setups(b, p):
    """(c1) H1 close with RSI(2) < 10 (long) / > 90 (short) and ADX14(H1) < 20; exit at the next open after the first
    H1 close with RSI(2) beyond 50, or 24 h after the fill; stop 1.5 x ATR14(H1) from the fill; no target."""
    h = b["H1"]
    r, x, a = rsi(h.c, 2), adx(h, 14), atr(h, 14)
    calm = lt(x, p["adx"])
    out = []
    horizon = p["hours"] * HOUR_NS + MAX_FILL_WAIT_NS
    for side, enter, leave in ((1, lt(r, p["rsi"]) & calm, gt(r, 50)), (-1, gt(r, 100 - p["rsi"]) & calm, lt(r, 50))):
        for k in np.flatnonzero(enter):
            if not math.isfinite(a[k]):
                continue
            limit = int(np.searchsorted(h.avail, h.avail[k] + horizon, side="right"))
            m = _first(leave, k, limit)
            events = [(int(h.avail[m]), "exit", SIGNAL)] if m is not None else []
            out.append(Setup(int(h.avail[k]), side, "C1", f"C1:{side}:{k}", stop_dist=p["atr"] * a[k], events=events,
                             hold_ns=p["hours"] * HOUR_NS))
    return out


def bollinger_setups(b, p):
    """(c2) H4 close outside Bollinger(20, 2) with ADX14(H4) < 20 -> fade: TP = the middle band at the signal close
    (fixed limit), stop 1.5 x ATR14(H4) from the fill, time exit 5 days after the fill."""
    h = b["H4"]
    mid = sma(h.c, p["period"])
    sd = pd.Series(h.c).rolling(p["period"]).std(ddof=0).to_numpy()
    x, a = adx(h, 14), atr(h, 14)
    calm = lt(x, p["adx"])
    out = []
    for side, enter in ((1, lt(h.c, mid - p["width"] * sd) & calm), (-1, gt(h.c, mid + p["width"] * sd) & calm)):
        for k in np.flatnonzero(enter):
            if math.isfinite(a[k]):
                out.append(Setup(int(h.avail[k]), side, "C2", f"C2:{side}:{k}", stop_dist=p["atr"] * a[k],
                                 tp_price=float(mid[k]), hold_ns=int(p["days"] * es.DAY_NS)))
    return out


def build_setups(base, bars, params=None):
    family, default = BASE[base]
    p = {**default, **(params or {})}
    if family == "A":
        found = swing_setups(bars, p, base.split(":")[1])
    elif family == "B1":
        found = donchian_setups(bars["D1"], p, "B1")
    elif family == "B2":
        found = sma_cross_setups(bars, p)
    elif family == "B3":
        found = tsmom_setups(bars, p)
    elif family == "B4":
        found = donchian_setups(bars["H4"], p, "B4")
    elif family == "C1":
        found = rsi2_setups(bars, p)
    elif family == "C2":
        found = bollinger_setups(bars, p)
    else:
        raise ValueError(base)
    return sorted(found, key=lambda s: (s.t, s.side))


# ---------------------------------------------------------------- 5m tape, costs and the trade walk
@dataclass
class Tape:
    start: np.ndarray  # int64 ns
    o: np.ndarray  # mid
    h: np.ndarray
    l: np.ndarray  # noqa: E741
    c: np.ndarray
    spread: np.ndarray  # causal spread (price) for a fill / exit priced on bar i; NaN when unavailable
    pip: float
    fx: bool
    slip: float = None  # fixed slippage (price) of a constant cost model; None -> 0.1 x spread (min 0.2 pip FX)
    commission: np.ndarray = None  # round-turn commission (price) for a trade filled on bar i; None -> 0

    @classmethod
    def from_frame(cls, f5, pip, fx):
        with instrument_pip(pip):
            spread = bt.causal_spread_pips(f5).to_numpy() * pip
        return cls(es._ns(f5.index), *(f5[x].to_numpy(float) for x in ("Open", "High", "Low", "Close")), spread, pip,
                   fx)

    def __len__(self):
        return len(self.start)

    def costs(self, i):
        """(half spread, slippage) in price for a trade filled on bar ``i``, or None (no causal spread)."""
        sp = float(self.spread[i])
        if not math.isfinite(sp):
            return None
        if self.slip is not None:
            return sp / 2, self.slip
        return sp / 2, max(SLIP_SHARE * sp, MIN_SLIP_FX_PIPS * self.pip if self.fx else 0.0)

    def commission_at(self, i):
        return 0.0 if self.commission is None else float(self.commission[i])

    def stamp(self, i):
        return datetime.fromtimestamp(int(self.start[i]) / 1e9, timezone.utc)


def walk(tape, i, side, fill, stop, target, hs, slip, events, no_tp_on_fill=False):
    """Walk a position filled on 5m bar ``i`` (``no_tp_on_fill``: a limit fill inside bar ``i``, whose target is not
    credited on that bar since the intrabar order is unknown; its stop is). ``events`` (sorted): (t, 'stop', level) ratchets the stop from the
    bar starting at t; (t, 'exit', reason) = market exit at the open of the first bar starting at or after t;
    (t, 'close', reason) = market exit at the close of the last bar ending by t. Between events: gap through the
    stop / target -> filled at the open; else same-bar SL first, then TP. Returns the exit fields."""
    s, n, five = side, len(tape), 5 * es.MINUTE
    cur, j = stop, i
    for t_ev, kind, value in list(events) + [(None, "end", END)]:
        jj = n if t_ev is None else int(np.searchsorted(tape.start, t_ev - five, side="right"))
        if jj > j:
            o = tape.o[j:jj] - s * hs
            adverse = (tape.l[j:jj] if s > 0 else tape.h[j:jj]) - s * hs
            favour = (tape.h[j:jj] if s > 0 else tape.l[j:jj]) - s * hs
            hit = s * (adverse - cur) <= 0
            if target is not None:
                tp_hit = s * (favour - target) >= 0
                if no_tp_on_fill and j == i:
                    tp_hit[0] = False
                hit |= tp_hit
            idx = np.flatnonzero(hit)
            if len(idx):
                x = j + int(idx[0])
                ox = float(o[idx[0]])
                if x > i and s * (ox - cur) <= 0:
                    return x, int(tape.start[x]), ox - s * slip, SL, "GAP_SL", int(tape.start[x])
                if x > i and target is not None and s * (ox - target) >= 0:
                    return x, int(tape.start[x]), ox, TP, "GAP_TP", int(tape.start[x])
                if s * (float(adverse[idx[0]]) - cur) <= 0:
                    return x, int(tape.start[x]) + five, cur - s * slip, SL, "SL" if cur == stop else "TRAIL", \
                        int(tape.start[x]) + five
                return x, int(tape.start[x]) + five, target, TP, TP, int(tape.start[x]) + five
            j = jj
        if kind == "stop":
            cur = max(cur, value) if s > 0 else min(cur, value)
        elif kind == "exit" and j < n:
            return j, int(tape.start[j]), float(tape.o[j]) - s * hs - s * slip, value, value, int(t_ev)
        elif kind in ("close", "end") or (kind == "exit" and j >= n):
            last = j - 1 if j > i else j
            last = min(last, n - 1)
            at = int(t_ev) if (kind == "close" and j > i) else int(tape.start[last]) + five
            reason = END if kind != "close" else value
            return last, at, float(tape.c[last]) - s * hs - s * slip, reason, reason, at
    raise AssertionError("unreachable")


def walk_trade(tape, i, s, fill, mid_fill, stop, target, hs, slip, events, name, signal_ns, idea,
               no_tp_on_fill=False):
    """(trade dict, free_ns) of a position filled on bar ``i`` (``walk`` + R, gross mid R, the tape's round-turn
    commission charged once in R, also on the open marks)."""
    x, exit_ns, price, reason, detail, free_ns = walk(tape, i, s, fill, stop, target, hs, slip, events, no_tp_on_fill)
    risk = s * (fill - stop)
    if reason == TP:
        mid_exit = price + s * hs
    else:
        mid_exit = price + s * (hs + slip)
    fee = tape.commission_at(i) / risk
    trade = {"variant": name, "signal_at": _dt(signal_ns), "fill_at": tape.stamp(i), "exit_at": _dt(exit_ns),
             "side": "LONG" if s > 0 else "SHORT", "idea": idea, "fill": fill, "mid_fill": mid_fill, "stop": stop,
             "target": target, "exit": price, "reason": reason, "detail": detail, "r": s * (price - fill) / risk - fee,
             "gross_r": s * (mid_exit - mid_fill) / risk, "risk_price": risk, "risk_pips": risk / tape.pip,
             "tp_pips": abs(target - fill) / tape.pip if target is not None else math.nan,
             "spread_pips": 2 * hs / tape.pip, "spread_fallback": False, "slip": slip, "commission_r": fee,
             "marks": s * (tape.c[i:x] - s * hs - fill) / risk - fee, "mark_ns": tape.start[i:x] + 5 * es.MINUTE}
    return trade, free_ns


def plan_stop(setup, fill):
    """(stop, None) or (None, skip reason)."""
    s = setup.side
    if math.isfinite(setup.stop_dist):
        return (fill - s * setup.stop_dist, None) if setup.stop_dist > 0 else (None, "stop_unavailable")
    dist = s * (fill - setup.stop_level)
    lo, hi = setup.clamp
    if dist <= 0:
        return None, "structure_beyond_fill"
    if dist > hi:
        return None, "swing_stop_wider_than_4_atr"
    return fill - s * max(dist, lo), None


def _dt(ns):
    return datetime.fromtimestamp(int(ns) / 1e9, timezone.utc)


def run(setups, tape, name, entry_filter=None, friday=False):
    """Sequential walk of one variant: (trades, skips). One position at a time (the next entry is allowed from the
    exit decision time: a signal exit and an opposite entry on the same close share the same fill bar), one entry
    per idea, 2R/day realised halt (trading day of the exit)."""
    trades, skips = [], Counter()
    free_at, used, halted = -1, set(), set()
    day_r = defaultdict(float)
    for st in setups:
        if st.idea in used:
            skips["idea_already_traded"] += 1
            continue
        if st.t < free_at:
            skips["position_open"] += 1
            continue
        if bt.day_key(_dt(st.t)) in halted:
            skips["daily_loss_halt"] += 1
            continue
        i = int(np.searchsorted(tape.start, st.t, side="left"))
        if i >= len(tape):
            skips["no_fill_data"] += 1
            continue
        if int(tape.start[i]) - st.t > MAX_FILL_WAIT_NS:
            skips["no_next_bar"] += 1
            continue
        fill_at = tape.stamp(i)
        if entry_filter is not None:
            why = entry_filter(st, fill_at)
            if why:
                skips[why] += 1
                continue
        cost = tape.costs(i)
        if cost is None:
            skips["no_causal_spread"] += 1
            continue
        hs, slip = cost
        s = st.side
        mid_fill = float(tape.o[i])
        fill = mid_fill + s * (hs + slip)
        stop, why = plan_stop(st, fill)
        if stop is None:
            skips[why] += 1
            continue
        risk = s * (fill - stop)
        target = None
        if st.tp_r is not None:
            target = fill + s * st.tp_r * risk
        elif st.tp_price is not None:
            if s * (st.tp_price - fill) <= 0:
                skips["target_beyond_fill"] += 1
                continue
            target = st.tp_price
        events = [e for e in st.events if e[0] > st.t]
        if st.deadline is not None:
            events.append((st.deadline, "close", TIME))
        if st.hold_ns is not None:
            events.append((int(tape.start[i]) + st.hold_ns, "close", TIME))
        if friday:
            events.append((bt._ns(bt.friday_close_after(fill_at)), "close", FRIDAY))
        events.sort(key=lambda e: (e[0], e[1] != "stop"))
        trade, free_ns = walk_trade(tape, i, s, fill, mid_fill, stop, target, hs, slip, events, name, st.t, st.idea)
        trades.append(trade)
        used.add(st.idea)
        free_at = free_ns
        key = bt.day_key(trade["exit_at"])
        day_r[key] += trade["r"]
        if day_r[key] <= -bt.DAILY_HALT_R:
            halted.add(key)
    return trades, skips


def with_carry(trades, rate=CARRY_STRESS):
    """Copies with a financing charge of ``rate`` per year of notional against the position, in R: rate x years
    held x fill / initial risk (also on the MTM marks); gross R unchanged, so the charge shows in the cost."""
    out = []
    year_ns = 365 * es.DAY_NS
    for t in trades:
        per_ns = rate * t["fill"] / t["risk_price"] / year_ns
        fill_ns = bt._ns(t["fill_at"])
        held = bt._ns(t["exit_at"]) - fill_ns
        u = dict(t)
        u["r"] = t["r"] - per_ns * held
        u["carry_r"] = per_ns * held
        if len(t["marks"]):
            u["marks"] = t["marks"] - per_ns * (t["mark_ns"] - fill_ns)
        out.append(u)
    return out


# ---------------------------------------------------------------- intraday (e): JT[R1+R5] / E3 with ADR-relative stops
def run_intraday(cands, ctx, market, tape, name, p):
    """``round2.run_2r`` with an ATR/ADR-relative stop: structure -/+ 0.2 ATR14(H1) from the fill, narrower than
    ``lo`` x ADR20 widened to it, wider than ``hi`` x ADR20 skipped; TP ``tp`` R; I2 hard close, Friday 17:00 NY.
    Per-trade causal spread and slippage (instrument pip, ``Tape.costs``); no trade without a causal spread."""
    trades, skips = [], Counter()
    open_until, used, halted = None, set(), set()
    day_r = defaultdict(float)
    for cand in cands:
        if cand.idea in used:
            skips["idea_already_traded"] += 1
            continue
        slot = _dt(cand.t)
        if open_until is not None and slot < open_until:
            skips["position_open"] += 1
            continue
        if bt.day_key(slot) in halted:
            skips["daily_loss_halt"] += 1
            continue
        i = int(np.searchsorted(market.starts, cand.t))
        if i >= market.limit:
            skips["no_fill_data"] += 1
            continue
        if int(market.starts[i]) != cand.t:
            skips["no_next_bar"] += 1
            continue
        cost = tape.costs(i)
        if cost is None:
            skips["no_causal_spread"] += 1
            continue
        hs, slip = cost
        market.slippage = slip
        s = 1 if cand.side == es.LONG else -1
        limit = getattr(cand, "limit", None)
        fill = float(market.open[i]) + s * (hs + slip) if limit is None else r2.limit_fill(market, i, cand.side, limit)
        adr, a = ctx.adr_at(cand.t) * tape.pip, ctx.atr_at(cand.t)
        if not (math.isfinite(adr) and math.isfinite(a)):
            skips["adr_atr_unavailable"] += 1
            continue
        dist = s * (fill - (cand.struct - s * bt.BUFFER_ATR * a))
        if dist <= 0:
            skips["structure_beyond_fill"] += 1
            continue
        if dist > p["hi"] * adr:
            skips["sl_wider_than_hi_adr"] += 1
            continue
        dist = max(dist, p["lo"] * adr)
        stop, target = fill - s * dist, fill + s * p["tp"] * dist
        if limit is None:
            result = bt.simulate_trade(market, slot, cand.side, stop, target, "I2")
        else:
            result = r2.simulate_limit(market, i, cand.side, fill, stop, target)
        if "rejected" in result:
            skips[result["rejected"]] += 1
            continue
        used.add(cand.idea)
        open_until = result["exit_at"]
        if result["reason"] == TP:
            mid_exit = result["exit"] + s * hs
        elif result["reason"] in (SL, bt.BE, TIME, FRIDAY, END):
            mid_exit = result["exit"] + s * (hs + slip)
        else:
            mid_exit = result["exit"]
        result.update(variant=name, signal_at=slot, side=cand.side, idea=cand.idea, slip=slip,
                      gross_r=s * (mid_exit - result["mid_fill"]) / (s * (result["fill"] - stop)),
                      risk_price=s * (result["fill"] - stop),
                      mark_ns=market.starts[i:i + len(result["marks"])] + 5 * es.MINUTE)
        trades.append(result)
        key = bt.day_key(result["exit_at"])
        day_r[key] += result["r"]
        if day_r[key] <= -bt.DAILY_HALT_R:
            halted.add(key)
    return trades, skips


# ---------------------------------------------------------------- filters (d)
def skip_mon_fri(st, fill_at):
    """d1: no entry whose fill falls in a Monday or Friday trading day (17:00 -> 17:00 NY)."""
    return "d1_monday_friday" if bt.day_key(fill_at).weekday() in (0, 4) else None


def carry_filter(symbol):
    side = CARRY_SIDE.get(symbol)
    if side is None:
        return None

    def f(st, fill_at):
        return None if st.side == side else "d2_against_carry"
    return f


# ---------------------------------------------------------------- evaluation
def evaluate(trades, years):
    """``fiveyear.evaluate`` + the signal-exit share, the carry stress and the second-half-of-data result."""
    out = fy.evaluate(trades, years)
    n = len(trades)
    out["sig"] = 100 * sum(t["reason"] == SIGNAL for t in trades) / n if n else 0.0
    out["end"] = 100 * sum(t["reason"] == END for t in trades) / n if n else 0.0
    stressed = fy.evaluate(with_carry(trades), years) if trades and "risk_price" in trades[0] else None
    if stressed is not None:
        out["stress"] = {k: stressed[k] for k in ("avg_r", "total_r", "dd", "cost", "years_ok", "pass", "fails")}
    mid = years[0][0] + (years[-1][1] - years[0][0]) / 2
    late = [t for t in trades if t["signal_at"] >= mid]
    m = bt.metrics(late, (years[-1][1] - mid).total_seconds() / fy.MONTH_S)
    out["half2"] = {"n": m["trades"], "avg_r": m["avg_r"] if late else None, "total_r": m["total_r"],
                    "ci80_low": m["ci80_low"]}
    return out


def table(rows):
    head = ("| Variant | Tr/mo (n) | Win % | Avg R | Total R | MTM DD | Med hold h | TP/SL/TIME/SIG/FRI % | Cost R | "
            + " | ".join(f"Y{q + 1}" for q in range(fy.YEARS)) + " | Yrs ok | Result | Carry -3 %/yr: avg R, "
            "total, cost, result |\n" + "|---" * (13 + fy.YEARS) + "|")
    lines = [head]
    for name, s in rows.items():
        years = " | ".join(f"{fy._f(y['avg_r'], '+.2f')} / {fy._f(y['ci80_low'], '+.2f')} ({y['n']})"
                           for y in s["years"])
        exits = "/".join(f"{e:.0f}" for e in (*s["exits"][:3], s.get("sig", 0.0), s["exits"][3])) if s["trades"] \
            else "–"
        result = "**PASS**" if s["pass"] else "fail: " + ", ".join(s["fails"])
        st = s.get("stress")
        stress = "–" if not st else (f"{fy._f(st['avg_r'], '+.3f')}, {st['total_r']:+.1f}, {fy._f(st['cost'], '.3f')}, "
                                     + ("PASS" if st["pass"] else "fail"))
        lines.append(f"| {name} | {s['tpm']:.1f} ({s['trades']}) | "
                     f"{fy._f(s['win'] * 100 if s['win'] is not None else None, '.0f')} | {fy._f(s['avg_r'], '+.3f')} | "
                     f"{s['total_r']:+.1f} | {s['dd']:.1f} | {fy._f(s['hold'], '.1f')} | {exits} | "
                     f"{fy._f(s['cost'], '.3f')} | {years} | {s['years_ok']} | {result} | {stress} |")
    return "\n".join(lines)


def neighbours(base, params=None):
    """+/-25 % on each main parameter, one at a time (integers rounded, RSI threshold kept below 50)."""
    _, default = BASE[base]
    p = {**default, **(params or {})}
    out = []
    for key, value in p.items():
        for f in (0.75, 1.25):
            v = value * f
            v = int(round(v)) if key in INTEGER_PARAMS else round(v, 4)
            if v != value:
                out.append((f"{key}={v}", {**p, key: v}))
    return out


# ---------------------------------------------------------------- instruments
class Instrument:
    def __init__(self, data, symbol):
        self.symbol = symbol
        self.pip, self.fx = INSTRUMENTS[symbol]
        self.frames = bt.load_csv_frames(data, symbol)
        self.break_hours = () if self.fx else BREAK_HOURS
        self.bars = {tf: htf_bars(self.frames["1h"], tf, self.break_hours) for tf in ("D1", "H4", "H1")}
        self.tape = Tape.from_frame(self.frames["5m"], self.pip, self.fx)
        start = _dt(self.bars["D1"].avail[es.ADR_DAYS - 1])  # = entry_search.Context.tradable_start
        self.end = _dt(self.tape.start[-1] + 5 * es.MINUTE)
        self.years = fy.year_blocks(start, self.end)
        self._ctx = None

    @staticmethod
    def available(data, symbol):
        return all(os.path.exists(os.path.join(data, f"{symbol}_{tf}.csv")) for tf in ("5m", "15m", "1h"))

    def context(self):
        if self._ctx is None:
            with instrument_pip(self.pip), schedule(self.break_hours):
                self._ctx = es.Context(self.frames)
        return self._ctx

    def run_spec(self, spec):
        """(trades, skips) of a variant spec {base, params, filter, friday}."""
        base, params = spec["base"], spec.get("params")
        family, default = BASE[base]
        if family in ("JT", "E3"):
            return self.run_intraday(base, {**default, **(params or {})})
        filt = {None: None, "d1": skip_mon_fri, "d2": carry_filter(self.symbol)}[spec.get("filter")]
        if spec.get("filter") == "d2" and filt is None:
            raise ValueError(f"d2 N/A for {self.symbol}")
        setups = build_setups(base, self.bars, params)
        return run(setups, self.tape, spec["name"], filt, spec.get("friday", False))

    def run_intraday(self, base, p):
        ctx = self.context()
        with instrument_pip(self.pip):
            market = bt.Market.from_frames(self.frames, math.nan, cutoff=self.end, causal=True)
            start_ns = int(ctx.tradable_start())
            if base.startswith("JT"):
                news = es.high_impact_days(self.years[0][0].date(), self.end.date())
                cands = r2.jt_relaxed_all(ctx, r2.Relax.parse("R1+R5"), news,
                                          lambda k: market.half_spread_at(k)[0], start_ns)[0]
            else:
                cands = es.entry_candidates(ctx, "E3")
            cands = [c for c in cands if c.t >= start_ns]
            return run_intraday(cands, ctx, market, self.tape, base, p)


# ---------------------------------------------------------------- report
GRID_A = ["A:ATR:TP2", "A:ATR:TP3", "A:SW:TP2", "A:SW:TP3"]
GRID_B = ["B1:DON55/20", "B2:SMA50/200", "B3:TSMOM12", "B4:DON20/10:H4"]
GRID_C = ["C1:RSI2:H1", "C2:BB20:H4"]
OTHERS = ("GBPUSD", "USDJPY", "XAUUSD", "US30USD", "NAS100USD")
DECLARED = {"a": 4, "b": 4, "c": 2, "d": 4, "fri": 1, "e_families": 2 * len(OTHERS), "e_intraday": 2 * len(INSTRUMENTS)}

DECLARATION = """\
## 0. Predeclared design (written before any round-4 result)

**Data**: `data5y` 5m / 15m / 1h mid OHLC + 5m BidClose/AskClose per instrument; present: {present}; missing:
{missing}. Pip / point: EURUSD, GBPUSD 0.0001; USDJPY 0.01; XAUUSD 0.1; US30USD, NAS100USD 1.0.
**Years** ({home}; each instrument uses the same rule = 12-month blocks from its first tradable moment, 20
completed NY trading days):
{years}
Multi-day families need warm-up (SMA100 + 20-day: ~5 months; SMA200: ~10 months; 12-month return: 12 months), so Y1
is partly or fully empty for them: the >= 4 of 5 years rule then needs every later year. Stated, not changed.

**Pass rule (unchanged from the 5-year round)**: avg R > +0.10 AND day-block CI80 low > 0 in >= 4 of 5 years
(yearly figures = trades signalled in the year that exit by its end), total R > 0, MTM max DD <= 8R, avg cost <=
0.10R, all in R (1R = initial stop distance from the fill). Evaluated at **0 % carry**; every row also shows the
**-3 %/yr financing stress** (R per day = 0.03/365 x fill / initial risk, also on MTM marks, counted in the cost);
a passer is reported as robust to financing only if it also passes under the stress.

**Mechanics**: D1 = 17:00 -> 17:00 NY trading days and H4 = 4h buckets from that open, both resampled from closed
1h bars (complete buckets only = every hour of the span except the instrument's daily break, 17:00-18:00 NY for
XAU / indices, none for FX; visible once their last 1h bar has closed; the intraday Context uses the same rule); H1 = the 1h bars (visible at start +
1h). Indicators on closed bars only (ATR = simple mean of the true range; RSI, ADX Wilder; Bollinger = SMA +/- 2
population SD). A decision at a bar close fills at the open of the first 5m bar starting at or after it (no fill
if none within 4 days). Walk on 5m mid OHLC, bid/ask = mid -/+ half spread: gap through stop/target -> open; same-bar
SL first; trailing stops only ratchet and apply from the bar after the close that sets them; signal exits at the
next 5m open; time exits at the close of the last 5m bar ending by the deadline. **Costs**: causal 30-day trailing
median spread of the instrument (none -> no trade; no fallback) + slippage 0.1 x that spread (min 0.2 pip on FX) on
market entries, stops and market exits, 0 on TP; the spread used is the 30-day median, not the (wider) 17:00 NY
rollover spread at which D1 decisions fill: a limitation, it flatters D1 entries slightly. One position at a time per
variant (a signal exit and an opposite entry on the same close share the fill bar), one entry per idea, 2R/day
realised halt, no intraday hard close, **Friday close OFF** except the one variant below.

**Grid (EURUSD first; fixed parameters)**
- **(a) Swing** (4): D1 close beyond the prior 20-day high/low (first close beyond) in the direction of the SMA100
  slope (today's SMA100 vs yesterday's). Stop `ATR` = 2 x ATR14(D1) from the fill; `SW` = last confirmed 3-bar D1
  swing low/high -/+ 0.2 ATR14, < 1 ATR widened to 1 ATR, > 4 ATR skipped. TP 2R / 3R. Time exit at the close of
  the 10th D1 bar after the signal bar. `A:ATR:TP2`, `A:ATR:TP3`, `A:SW:TP2`, `A:SW:TP3`.
- **(b) Trend** (4): `B1:DON55/20` first D1 close beyond the prior 55-day high/low, exit after a close beyond the
  prior 20-day opposite extreme, fixed stop 2 x ATR20(D1); `B2:SMA50/200` D1 SMA50 crossing SMA200, exit at the
  opposite cross, chandelier stop (best close since entry -/+ 3 x ATR14(D1), ratcheted each D1 close; initial
  3 x ATR14 from the fill); `B3:TSMOM12` at each month-end D1 close (last weekday of the month) side = sign of the
  12-month return (close vs the last D1 close 12 months earlier), held while the sign agrees, exit at the first
  opposite month-end, chandelier stop 3 x ATR20(D1), re-entry at the next month-end after a stop-out;
  `B4:DON20/10:H4` B1 on H4 with 20/10 bars and stop 2 x ATR20(H4). No targets.
- **(c) Mean reversion** (2): `C1:RSI2:H1` H1 close with RSI(2) < 10 (long) / > 90 (short) and ADX14(H1) < 20, exit
  after the first H1 close with RSI(2) beyond 50 or 24 h after the fill, stop 1.5 x ATR14(H1), no target;
  `C2:BB20:H4` H4 close outside Bollinger(20, 2) with ADX14(H4) < 20 -> fade, TP = middle band at the signal close,
  stop 1.5 x ATR14(H4), time exit 5 days after the fill.
- **(d) Filters** (4) on the best 2 families of (a)-(c) (families A, B1-B4, C1, C2; ranked by the 5-year avg R of
  their best variant with >= 30 trades; that variant is used): `+d1` no entry filled in a Monday or Friday trading
  day; `+d2` carry direction = EURUSD short only. Policy rates: USD (Fed funds target) is above EUR (ECB deposit
  rate) in every month of the data (0-0.25 % vs -0.50 % in Oct 2021, 5.25-5.50 % vs 4.00 % in 2023, >= 3.5 % vs
  <= 2.75 % in 2025-26), so only this sign is used; a full monthly table for USD/EUR/GBP/JPY is **N/A** (I do not
  know every 2026 level reliably), so d2 is not applied to GBP or JPY crosses.
- **Friday close ON** (1): the best family's best variant with every position closed at Friday 17:00 NY.
- **(e) Other instruments**: the 2 best families' best variants on {others} ({n_fam}); plus intraday
  `JT[R1+R5]:SLADR` (JT_SMC with R1 no 1h-FVG requirement + R5 limit at the 15m OB edge) and `E3:SLADR` (Asian
  breakout-retest) on all six instruments incl. EURUSD ({n_intra}): stop = structure -/+ 0.2 ATR14(H1) from the fill,
  < 0.25 x ADR20 widened, > 0.5 x ADR20 skipped (instead of 15-30 pips), TP 2R, I2 hard close + Friday 17:00 NY,
  candidate rules as in round 2 / round 1 with pip-denominated thresholds scaled by the instrument pip; same costs.
  Missing instruments are skipped and their tests not counted.
- **Robustness, only for a passer**: +/-25 % on each main parameter one at a time (A: 20-day, SMA100, ATR mult, TP R,
  10 days; B1/B4: entry, exit, ATR mult; B2: 50, 200, ATR mult; B3: 12 months, ATR mult; C1: RSI threshold, ADX,
  ATR mult, 24 h; C2: 20, 2.0, ADX, ATR mult, 5 days; intraday: 0.25, 0.5, TP) - share of neighbours that also pass
  and their avg R; per-year table; second half of the data alone. Neighbours are not counted as new tests.

**Tests declared: {total}** ({breakdown}); cumulative with the 145 earlier tests: **{cum}**. P(no-edge variant
clears >= 4 of 5 one-sided 10 % yearly tests) = {p:.5f} -> ~{fp:.3f} expected false passers over this round's tests
if independent ({fpc:.3f} cumulative); the variants are correlated (shared entries, the d/e runs reuse the best
variants), so the effective count is lower.
"""


def append(path, text):
    fy.append(path, text)


def declaration(home, present, missing):
    years = "\n".join(f"- Y{q + 1}: {a:%Y-%m-%d %H:%M} -> {b:%Y-%m-%d %H:%M} UTC" for q, (a, b) in enumerate(home.years))
    others = [s for s in OTHERS if s in present]
    declared = {**DECLARED, "e_families": 2 * len(others), "e_intraday": 2 * len(present)}
    total = sum(declared.values())
    p = fy.p_false_pass()
    return DECLARATION.format(present=", ".join(present), missing=", ".join(missing) or "none", home=home.symbol,
                              years=years, others=", ".join(others), n_fam=declared["e_families"],
                              n_intra=declared["e_intraday"], total=total,
                              breakdown=" + ".join(f"{k} {v}" for k, v in declared.items()),
                              cum=PREVIOUS_TESTS + total, p=p, fp=total * p, fpc=(PREVIOUS_TESTS + total) * p), total


def save(out, key, rows, specs, extra=None):
    with open(os.path.join(out, f"r4_{key}.json"), "w", encoding="utf-8") as handle:
        json.dump({"rows": rows, "specs": specs, **(extra or {})}, handle)


def load(out, key):
    path = os.path.join(out, f"r4_{key}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def best_families(rows, specs, n=2):
    """Top ``n`` families of (a)-(c) by the 5-year avg R of their best variant with >= 30 trades (fewer eligible
    families -> the best variants with any trades fill in)."""
    best = {}
    for min_trades in (fy.MIN_TRADES_TOP, 1):
        for name, s in rows.items():
            fam = BASE[specs[name]["base"]][0]
            if fam in best or s["trades"] < min_trades or s["avg_r"] is None:
                continue
            cands = [(rows[x]["avg_r"], x) for x in rows if BASE[specs[x]["base"]][0] == fam
                     and rows[x]["trades"] >= min_trades and rows[x]["avg_r"] is not None]
            best[fam] = max(cands)
        if len(best) >= n:
            break
    return sorted(best.items(), key=lambda kv: kv[1][0], reverse=True)[:n]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--sections", default="declare,a,b,c,d,fri,e,robust,final")
    args = parser.parse_args(argv)
    os.makedirs(args.out, exist_ok=True)
    sections = args.sections.split(",")

    def log(message):
        print(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {message}", flush=True)

    present = [s for s in INSTRUMENTS if Instrument.available(args.data, s)]
    missing = [s for s in INSTRUMENTS if s not in present]
    cache = {}

    def inst(symbol):
        if symbol not in cache:
            log(f"loading {symbol}")
            cache[symbol] = Instrument(args.data, symbol)
        return cache[symbol]

    def run_specs(specs):
        rows, skips = {}, {}
        for name, spec in specs.items():
            ins = inst(spec["symbol"])
            trades, sk = ins.run_spec(spec)
            rows[name], skips[name] = evaluate(trades, ins.years), sk
            log(f"  {name}: {rows[name]['trades']} trades, avg R {fy._f(rows[name]['avg_r'], '+.3f')}, "
                f"{'PASS' if rows[name]['pass'] else 'fail'}")
        return rows, skips

    def section(key, title, specs, notes=()):
        rows, skips = run_specs(specs)
        save(args.out, key, {n: {k: v for k, v in s.items()} for n, s in rows.items()}, specs)
        lines = [f"## {title}", "", *notes, "", table(rows), "", "Portfolio skips (top 6 reasons):", "",
                 *fy.skips_lines(skips)]
        append(args.report, "\n".join(lines))
        log(f"{key}: {len(rows)} variants, passers {[n for n, s in rows.items() if s['pass']]}")
        return rows

    def spec(name, base, symbol=HOME, **kw):
        return {"name": name, "base": base, "symbol": symbol, **kw}

    if "declare" in sections:
        text, total = declaration(inst(HOME), present, missing)
        with open(args.report, "w", encoding="utf-8") as handle:
            handle.write(f"# Round 4 - multi-day families, filters, other instruments (REPLAY ONLY)\n\nStarted "
                         f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} by `python -m replay.round4` "
                         "(branch `feat/backtest-v2-i2`). Production unchanged. Sections are appended as they "
                         "finish.\n\n")
        append(args.report, text)
        with open(os.path.join(args.out, "r4_declared.json"), "w", encoding="utf-8") as handle:
            json.dump({"total": total, "present": present}, handle)

    for key, title, grid in (("a", "(a) Swing 2-10 days - EURUSD", GRID_A), ("b", "(b) Trend - EURUSD", GRID_B),
                             ("c", "(c) Mean reversion - EURUSD", GRID_C)):
        if key in sections:
            section(key, title, {g: spec(g, g) for g in grid})

    def abc():
        rows, specs = {}, {}
        for key in ("a", "b", "c"):
            got = load(args.out, key)
            rows.update(got["rows"])
            specs.update(got["specs"])
        return rows, specs

    if "d" in sections:
        rows, specs = abc()
        top = best_families(rows, specs)
        variants = {}
        for _, (_, name) in top:
            variants[f"{name}+d1"] = spec(f"{name}+d1", specs[name]["base"], filter="d1")
            variants[f"{name}+d2"] = spec(f"{name}+d2", specs[name]["base"], filter="d2")
        section("d", "(d) Filters on the best 2 families - EURUSD", variants,
                ["Best 2 families by 5-year avg R of their best variant: "
                 + ", ".join(f"{fam} ({name} {r:+.3f})" for fam, (r, name) in top) + ". `+d1` = no Monday/Friday "
                 "trading-day entries; `+d2` = EURUSD short only (USD policy rate > EUR throughout)."])

    if "fri" in sections:
        rows, specs = abc()
        (_, (_, name)), = best_families(rows, specs, 1)
        section("fri", "Friday close ON - best family, EURUSD", {f"{name}+FRI": spec(f"{name}+FRI", specs[name]["base"],
                                                                                   friday=True)},
                [f"Best family's best variant: {name}; every position closed at Friday 17:00 NY (reason FRI)."])

    if "e" in sections:
        rows, specs = abc()
        top = best_families(rows, specs)
        variants = {}
        for symbol in (s for s in OTHERS if s in present):
            for _, (_, name) in top:
                variants[f"{symbol}:{name}"] = spec(f"{symbol}:{name}", specs[name]["base"], symbol)
        for symbol in present:
            for base in ("JT[R1+R5]:SLADR", "E3:SLADR"):
                variants[f"{symbol}:{base}"] = spec(f"{symbol}:{base}", base, symbol)
        section("e", "(e) Other instruments: best 2 families + intraday JT[R1+R5] / E3 with ADR-relative stops",
                variants, [f"Families: {', '.join(name for _, (_, name) in top)}. Intraday stops: 0.25-0.5 x ADR20 "
                           f"clamp, TP 2R, I2 hard close. Instruments: {', '.join(present)}; missing: "
                           f"{', '.join(missing) or 'none'}."])

    if "robust" in sections:
        robust(args, run_specs, log)
    if "final" in sections:
        final(args, log)


def all_rows(out):
    rows, specs, groups = {}, {}, {}
    for key in ("a", "b", "c", "d", "fri", "e"):
        got = load(out, key)
        if got:
            rows.update(got["rows"])
            specs.update(got["specs"])
            groups[key] = len(got["rows"])
    return rows, specs, groups


def robust(args, run_specs, log):
    rows, specs, _ = all_rows(args.out)
    passers = [n for n, s in rows.items() if s["pass"]]
    lines = ["## Robustness of passers", ""]
    if not passers:
        lines.append("No variant passes, so no neighbourhood / second-half analysis is triggered (predeclared: "
                     "passers only). The second-half-of-data figure of every variant is in the final shortlist.")
        append(args.report, "\n".join(lines))
        return
    out = {}
    for name in passers:
        sp = specs[name]
        nb = {f"{name}[{label}]": {**sp, "name": f"{name}[{label}]", "params": p}
              for label, p in neighbours(sp["base"], sp.get("params"))}
        nrows, _ = run_specs(nb)
        ok = [n for n, s in nrows.items() if s["pass"]]
        avg = [s["avg_r"] for s in nrows.values() if s["avg_r"] is not None]
        s = rows[name]
        h = s["half2"]
        lines += [f"### {name}", "",
                  f"Neighbourhood: {len(ok)}/{len(nrows)} neighbours pass; their avg R: mean "
                  f"{fy._f(float(np.mean(avg)) if avg else None, '+.3f')}, min {fy._f(min(avg) if avg else None, '+.3f')}"
                  f". Second half of the data alone: {h['n']} trades, avg R {fy._f(h['avg_r'], '+.3f')}, total "
                  f"{h['total_r']:+.1f}R, CI80 low {fy._f(h['ci80_low'], '+.3f')}. Carry stress: "
                  f"{'pass' if s.get('stress', {}).get('pass') else 'fail'}.", "", table({name: s, **nrows}), ""]
        out[name] = {"neighbours": len(nrows), "pass": len(ok)}
    with open(os.path.join(args.out, "r4_robust.json"), "w", encoding="utf-8") as handle:
        json.dump(out, handle)
    append(args.report, "\n".join(lines))
    log(f"robust: {out}")


def final(args, log):
    rows, specs, groups = all_rows(args.out)
    total = len(rows)
    p = fy.p_false_pass()
    passers = [n for n, s in rows.items() if s["pass"]]

    def closeness(s):
        return (s["years_ok"], s["total_r"] > 0, s["dd"] <= fy.MAX_DD_R,
                s["cost"] is not None and s["cost"] <= fy.MAX_COST_R, s["avg_r"] if s["avg_r"] is not None else -math.inf)

    def family(n):
        sp = specs[n]
        return (sp["symbol"], BASE[sp["base"]][0])
    ranked, seen = [], set()
    for n in sorted((n for n, s in rows.items() if s["trades"] >= fy.MIN_TRADES_TOP),
                    key=lambda n: closeness(rows[n]), reverse=True):
        if family(n) not in seen:
            seen.add(family(n))
            ranked.append(n)
    yearly = Counter(s["years_ok"] for s in rows.values())
    lines = ["## Multiple testing", "",
             f"**Round-4 tests run: {total}** ({', '.join(f'{k} {v}' for k, v in groups.items())}); **cumulative "
             f"{PREVIOUS_TESTS + total}** with the 145 earlier tests. Expected false passers if independent: "
             f"{total * p:.3f} this round, {(PREVIOUS_TESTS + total) * p:.3f} cumulative (p = {p:.5f}).", "",
             "Years passing (avg R > +0.10 and CI80 low > 0) across round-4 variants: "
             + ", ".join(f"{k}/5: {yearly.get(k, 0)}" for k in range(fy.YEARS, -1, -1)) + ".", "", "## Shortlist", ""]
    if passers:
        lines.append(f"**Passers ({len(passers)}): {', '.join(passers)}.** See the robustness section; any passer still "
                     "needs a fresh pre-registered period (live paper) before a decision.")
        shortlist = passers[:3] + [n for n in ranked if n not in passers][:max(0, 3 - len(passers))]
    else:
        lines.append("**No variant passes.** The 3 closest, one per instrument x family (most years passing, then total "
                     "R > 0, DD, cost, avg R; >= 30 trades):")
        shortlist = ranked[:3]
    lines += ["", table({n: rows[n] for n in shortlist}), ""]
    for n in shortlist:
        s, h = rows[n], rows[n]["half2"]
        lines.append(f"- **{n}**: {'passes' if s['pass'] else 'fails on ' + ', '.join(s['fails'])}; 5y avg R "
                     f"{fy._f(s['avg_r'], '+.3f')}, total {s['total_r']:+.1f}R over {s['trades']} trades, MTM DD "
                     f"{s['dd']:.1f}R, cost {fy._f(s['cost'], '.3f')}R, per-year avg R "
                     + ", ".join(fy._f(y['avg_r'], '+.2f') for y in s["years"])
                     + f"; second half alone {h['n']} trades, avg R {fy._f(h['avg_r'], '+.3f')}; carry stress "
                     f"avg R {fy._f(s.get('stress', {}).get('avg_r'), '+.3f')}.")
    years = [(pd.Timestamp(a).to_pydatetime(), pd.Timestamp(b).to_pydatetime()) for a, b in
             _home_years(args.data)]
    plot = shortlist[:5]
    path = os.path.join(os.path.dirname(args.report), "round4_equity_r.png")
    fy.plot_equity(path, rows, plot, years,
                   title="Round 4 replay - cumulative R (net of spread + slippage, 0 % carry): closest variants")
    lines += ["", f"Equity: `round4_equity_r.png` ({', '.join(plot)}; years = EURUSD blocks).", ""]
    append(args.report, "\n".join(lines))
    log(f"final: {total} tests, passers {passers}, shortlist {shortlist}")


def _home_years(data):
    d1 = htf_bars(bt.load_csv_frames(data, HOME)["1h"], "D1")
    f5 = pd.read_csv(os.path.join(data, f"{HOME}_5m.csv"), usecols=["start"]).iloc[-1, 0]
    end = pd.Timestamp(f5).to_pydatetime() + bt.FIVE
    return fy.year_blocks(_dt(d1.avail[es.ADR_DAYS - 1]), end)


if __name__ == "__main__":
    main()
