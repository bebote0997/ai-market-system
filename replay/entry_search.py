"""Intraday entry search on EURUSD, REPLAY ONLY: JT_SMC (Jeferson's plan, mechanical) and E1-E5 x exit models.

Nothing here is wired into the runtime. The simulation reuses ``replay.intraday_backtest``: CSV loader, causal per-trade
spread (30-day trailing median before the fill), 0.2 pip adverse slippage on entry/stop/time exits (0 on TP), same-bar
SL first, gaps through levels, I2 hard close at the end of the 2nd session counted from the fill, Friday 17:00 NY close,
MTM drawdown, 2R/day realised halt, one position at a time.

No-lookahead contract: every signal is decided at the close ``t`` of a 5m (or 15m) bar from bars ENDING at or before
``t`` and fills at the open of the 5m bar starting at ``t`` (no fill when that bar is missing). H4 and D1 are resampled
from closed 1h bars and become visible only at their bucket end (D1 = the 17:00 -> 17:00 New York trading day, H4 =
4h buckets anchored at the 17:00 NY open). Swings are 3-bar pivots confirmed at the close of the following bar.

Every long rule is written once; shorts run the same code on a mirrored price view (O, H, L, C) -> (-O, -L, -H, -C).

Run: ``python -m replay.entry_search --data /workspace/backtest/data --out /workspace/backtest``.
Market data and outputs live outside the repository and are never committed.
"""
import argparse
import math
import os
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone

import numpy as np
import pandas as pd

from replay import intraday_backtest as bt

PIP = bt.PIP
NY = bt.NEW_YORK
MINUTE = 60 * 10**9
HOUR = 60 * MINUTE
FIVE_NS, FIFTEEN_NS = 5 * MINUTE, 15 * MINUTE
DAY_NS = 24 * HOUR

# ---------------------------------------------------------------- fixed parameters (set upfront, never tuned)
EMA_PERIOD = 20
ATR_PERIOD = 14
ADR_DAYS = 20
IS_MONTHS = 8
ASIAN = (19 * 60, 3 * 60)  # 19:00 (prior evening) -> 03:00 ET
LONDON_RANGE = (3 * 60, 8 * 60)
E2_WINDOW = (3 * 60, 6 * 60)  # window on the START of the decision 5m bar (ET minutes)
E3_WINDOW = (3 * 60, 7 * 60)
E3_RETEST_BARS = 12
E4_WINDOW = (8 * 60, 11 * 60)
E5_WINDOW = (3 * 60, 11 * 60)
E5_MAX_SPREAD_PIPS = 1.2
MIN_ASIAN_BARS = 12  # an Asian/London range needs >= 1 h of 5m bars
F80_SHARE = 0.8
ADR_SL, ADR_TP, ADR_RR = 0.30, 0.58, (1.5, 2.0)
FIX_TARGETS = {"FIX2": 2.0, "FIX15": 1.5}
JT_WINDOW = (3 * 60, 11 * 60)  # manipulation (sweep) window
JT_EXPIRY = 12 * 60  # an idea not entered by 12:00 ET is dropped
JT_SWEEP_BARS = 6  # the close back over the level must come within 6 bars (30 min) of the first trade-through
JT_LEVEL_DAYS = 5
JT_FVG_HOURS = 24
JT_OB_LOOKBACK = 12  # bars searched back from a BOS bar for the opposite-colour OB candle
JT_FIB_ZONES = ((0.50, 0.60), (0.75, 0.85))
JT_LIQ_FLOOR_R, JT_LIQ_CAP_ADR = 1.5, 1.0
NEWS_WINDOW = (8 * 60 + 15, 9 * 60)  # decision/fill clock (ET), inclusive
# FOMC statement days (14:00 ET). Outside the 08:15-09:00 news window by construction; listed for the report.
FOMC_DAYS = tuple(date.fromisoformat(d) for d in (
    "2025-10-29", "2025-12-10", "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16",
    "2026-10-28", "2026-12-09"))
MIN_IS_TRADES_FOR_TOP = 20
LONG, SHORT = "LONG", "SHORT"


# ---------------------------------------------------------------- bars, views and resampling
@dataclass
class Bars:
    start: np.ndarray  # int64 ns
    end: np.ndarray  # int64 ns: the bar is visible to decisions at or after ``end``
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray  # noqa: E741
    c: np.ndarray

    def __len__(self):
        return len(self.start)

    def asof(self, t):
        """Index of the last bar ended by ``t`` (-1 if none)."""
        return int(np.searchsorted(self.end, t, side="right")) - 1


def _ns(index):
    return index.as_unit("ns").asi8.copy()


def make_bars(frame, sign=1, duration_ns=None, end=None):
    o, h, l, c = (frame[x].to_numpy(float) for x in ("Open", "High", "Low", "Close"))  # noqa: E741
    if sign < 0:
        o, h, l, c = -o, -l, -h, -c  # noqa: E741
    start = _ns(frame.index)
    return Bars(start, start + duration_ns if end is None else np.asarray(end, dtype=np.int64), o, h, l, c)


def trading_day_open(index):
    """Per bar: UTC ns of the 17:00 New York open of its trading day (17:00 -> 17:00 NY)."""
    opening = (index.tz_convert(NY).tz_localize(None) - pd.Timedelta(hours=17)).normalize()
    lookup = {d: pd.Timestamp(datetime.combine(d.date(), time(17), tzinfo=NY)).value for d in opening.unique()}
    return np.array([lookup[d] for d in opening], dtype=np.int64)


def _next_open(open_ns):
    """UTC ns of the 17:00 NY close of the trading day opened at ``open_ns`` (DST-safe)."""
    lookup = {}
    for value in np.unique(open_ns):
        day = pd.Timestamp(value, tz="UTC").tz_convert(NY).date() + timedelta(days=1)
        lookup[value] = pd.Timestamp(datetime.combine(day, time(17), tzinfo=NY)).value
    return np.array([lookup[v] for v in open_ns], dtype=np.int64)


def resample_closed(frame_1h, hours):
    """H4 (hours=4) or D1 (hours=24) from 1h bars, buckets anchored at the 17:00 NY open of the trading day.
    Column ``available`` = bucket end (UTC ns; H4 capped at the day close): a bucket is visible only from then, once
    all of its 1h bars have closed. Buckets ending after the last 1h bar are dropped (incomplete)."""
    starts = _ns(frame_1h.index)
    opens = trading_day_open(frame_1h.index)
    closes = _next_open(opens)
    if hours >= 24:
        key, available = opens, closes
    else:
        bucket = (starts - opens) // (hours * HOUR)
        key = opens + bucket * hours * HOUR
        available = np.minimum(key + hours * HOUR, closes)
    raw = pd.DataFrame({"key": key, "available": available, "start": starts, "Open": frame_1h["Open"].to_numpy(),
                        "High": frame_1h["High"].to_numpy(), "Low": frame_1h["Low"].to_numpy(),
                        "Close": frame_1h["Close"].to_numpy()})
    out = raw.groupby("key", sort=True).agg(available=("available", "first"), start=("start", "first"),
                                            Open=("Open", "first"), High=("High", "max"), Low=("Low", "min"),
                                            Close=("Close", "last"))
    last_end = starts[-1] + HOUR if len(starts) else 0
    out = out[out["available"] <= last_end]
    out.index = pd.DatetimeIndex(pd.to_datetime(out["start"].to_numpy(), utc=True))
    return out


def ema(values, period=EMA_PERIOD):
    return pd.Series(values).ewm(span=period, adjust=False).mean().to_numpy()


def confirmed_swings(high, low):
    """3-bar pivots: (last_sh, last_sl) per bar ``k`` = price of the most recent pivot confirmed by the close of bar k
    (pivot ``j`` is confirmed by bar j+1), NaN before any; plus the pivot lists [(pivot_index, price)]."""
    n = len(high)
    sh, sl = np.full(n, np.nan), np.full(n, np.nan)
    ph, pl = [], []
    for j in range(1, n - 1):
        if high[j] > high[j - 1] and high[j] > high[j + 1]:
            sh[j + 1] = high[j]
            ph.append((j, float(high[j])))
        if low[j] < low[j - 1] and low[j] < low[j + 1]:
            sl[j + 1] = low[j]
            pl.append((j, float(low[j])))
    return pd.Series(sh).ffill().to_numpy(), pd.Series(sl).ffill().to_numpy(), ph, pl


def bos_up(bars, last_sh):
    """bar k closes above the last swing high confirmed by the close of bar k-1."""
    prior = np.concatenate([[np.nan], last_sh[:-1]])
    with np.errstate(invalid="ignore"):
        return bars.c > prior


def structure_trend(bars, ph, pl):
    """+1/-1/0 per bar (at its close): last two confirmed swing highs/lows HH+HL -> +1, LH+LL -> -1; otherwise
    (neutral, equal swings or < 2 swings) the sign of the EMA20 slope (close-to-close)."""
    n = len(bars)
    line = ema(bars.c)
    slope = np.sign(np.concatenate([[0.0], np.diff(line)]))
    trend = slope.copy()
    hc = np.array([j + 1 for j, _ in ph], dtype=int)
    lc = np.array([j + 1 for j, _ in pl], dtype=int)
    hp = np.array([p for _, p in ph])
    lp = np.array([p for _, p in pl])
    for k in range(n):
        nh, nl = int(np.searchsorted(hc, k, "right")), int(np.searchsorted(lc, k, "right"))
        if nh >= 2 and nl >= 2:
            dh, dl = hp[nh - 1] - hp[nh - 2], lp[nl - 1] - lp[nl - 2]
            if dh > 0 and dl > 0:
                trend[k] = 1
            elif dh < 0 and dl < 0:
                trend[k] = -1  # equal swings count as neutral (keeps the mirrored view exactly symmetric)
    return trend


class View:
    """All price features of one direction (sign +1: original prices, longs; -1: mirrored prices, shorts)."""

    def __init__(self, frames, sign, h4=None, d1=None):
        self.sign = sign
        self.side = LONG if sign > 0 else SHORT
        self.m5 = make_bars(frames["5m"], sign, FIVE_NS)
        self.m15 = make_bars(frames["15m"], sign, FIFTEEN_NS)
        self.h1 = make_bars(frames["1h"], sign, HOUR)
        h4 = resample_closed(frames["1h"], 4) if h4 is None else h4
        d1 = resample_closed(frames["1h"], 24) if d1 is None else d1
        self.h4 = make_bars(h4, sign, end=h4["available"].to_numpy())
        self.d1 = make_bars(d1, sign, end=d1["available"].to_numpy())
        self.sh5, self.sl5, _, _ = confirmed_swings(self.m5.h, self.m5.l)
        self.bos5 = bos_up(self.m5, self.sh5)
        self.sh15, _, _, _ = confirmed_swings(self.m15.h, self.m15.l)
        self.bos15 = bos_up(self.m15, self.sh15)
        self.sh1, _, self.ph1, self.pl1 = confirmed_swings(self.h1.h, self.h1.l)
        self.bos1 = bos_up(self.h1, self.sh1)
        self.trend1 = structure_trend(self.h1, self.ph1, self.pl1)
        _, _, ph4, pl4 = confirmed_swings(self.h4.h, self.h4.l)
        self.trend4 = structure_trend(self.h4, ph4, pl4)
        self.ema15 = ema(self.m15.c)
        self.ema4 = ema(self.h4.c)
        self.ema_d1 = ema(self.d1.c)
        # 1h FVGs in this direction: 3 consecutive hourly bars a, b, c with low[c] > high[a]
        h = self.h1
        idx = [m for m in range(2, len(h)) if h.start[m] - h.start[m - 2] == 2 * HOUR and h.l[m] > h.h[m - 2]]
        self.fvg_end = np.array([h.end[m] for m in idx], dtype=np.int64)
        self.fvg_bottom = np.array([h.h[m - 2] for m in idx])

    def price(self, p):
        """View price -> real price."""
        return self.sign * p


# ---------------------------------------------------------------- shared (direction-free) context
class Context:
    def __init__(self, frames):
        self.frames = frames
        h4, d1 = resample_closed(frames["1h"], 4), resample_closed(frames["1h"], 24)
        self.views = {1: View(frames, 1, h4, d1), -1: View(frames, -1, h4, d1)}
        v = self.views[1]
        self.m5, self.d1, self.h1 = v.m5, v.d1, v.h1
        local = frames["5m"].index.tz_convert(NY)
        self.et_min = (local.hour * 60 + local.minute).to_numpy()
        self.et_day = (local.tz_localize(None).normalize().as_unit("ns").asi8 // DAY_NS).astype(np.int64)
        self.tday = trading_day_open(frames["5m"].index)
        # day high/low so far (closed 5m bars since the 17:00 NY open), at each 5m close
        group = pd.Series(self.tday)
        self.day_hi = pd.Series(self.m5.h).groupby(group).cummax().to_numpy()
        self.day_lo = pd.Series(self.m5.l).groupby(group).cummin().to_numpy()
        f5 = frames["5m"]
        if {"BidClose", "AskClose"} <= set(f5.columns):
            self.spread_now = ((f5["AskClose"] - f5["BidClose"]) / PIP).round(2).to_numpy()
        else:
            self.spread_now = np.full(len(f5), np.nan)
        prev_close = np.concatenate([[np.nan], self.h1.c[:-1]])
        tr = np.nanmax(np.vstack([self.h1.h - self.h1.l, np.abs(self.h1.h - prev_close),
                                  np.abs(self.h1.l - prev_close)]), axis=0)
        self.atr1 = pd.Series(tr).rolling(ATR_PERIOD).mean().to_numpy()
        self.adr = pd.Series(self.d1.h - self.d1.l).rolling(ADR_DAYS).mean().to_numpy() / PIP
        self.days = {int(d): np.flatnonzero(self.et_day == d) for d in np.unique(self.et_day)}

    # causal lookups at a decision time t (ns): only bars ended by t
    def atr_at(self, t):
        i = self.h1.asof(t)
        return float(self.atr1[i]) if i >= 0 else float("nan")

    def adr_at(self, t):
        """ADR20 in pips = mean range of the last 20 completed NY trading days (the current day never included)."""
        i = self.d1.asof(t)
        return float(self.adr[i]) if i >= 0 else float("nan")

    def range_so_far(self, t):
        """Today's high-low in pips from the 17:00 NY open up to t (closed 5m bars of the current trading day)."""
        k = self.m5.asof(t)
        if k < 0:
            return float("nan")
        return float(self.day_hi[k] - self.day_lo[k]) / PIP

    def spread_at(self, t):
        k = self.m5.asof(t)
        return float(self.spread_now[k]) if k >= 0 else float("nan")

    def window(self, day, lo, hi):
        """5m bar indices of ET date ``day`` whose start clock is in [lo, hi) minutes."""
        idx = self.days.get(day, np.array([], dtype=int))
        return idx[(self.et_min[idx] >= lo) & (self.et_min[idx] < hi)]

    def tradable_start(self):
        """First moment with ADR_DAYS completed trading days (ADR20 and the D1 EMA20 defined)."""
        return int(self.d1.end[ADR_DAYS - 1])


def session_ranges(ctx, view, rng):
    """{ET date: (low, high)} of the 5m bars whose start clock is in ``rng`` (Asian: from 19:00 of the prior ET date).
    Complete (all bars closed) at the window end; only used by decisions after it."""
    lo_min, hi_min = rng
    if lo_min > hi_min:  # overnight window
        label = np.where(ctx.et_min >= lo_min, ctx.et_day + 1, np.where(ctx.et_min < hi_min, ctx.et_day, -1))
    else:
        label = np.where((ctx.et_min >= lo_min) & (ctx.et_min < hi_min), ctx.et_day, -1)
    frame = pd.DataFrame({"label": label, "h": view.m5.h, "l": view.m5.l})
    frame = frame[frame["label"] >= 0]
    agg = frame.groupby("label").agg(low=("l", "min"), high=("h", "max"), n=("l", "size"))
    agg = agg[agg["n"] >= MIN_ASIAN_BARS]
    return {int(d): (float(r.low), float(r.high)) for d, r in agg.iterrows()}


def _contiguous(bars, a, b):
    """No missing 5m bar between index a and b."""
    return bars.start[b] - bars.start[a] == (b - a) * FIVE_NS


# ---------------------------------------------------------------- candidates
@dataclass(frozen=True)
class Candidate:
    t: int  # decision time (ns) = close of the last bar used; fill at the open of the 5m bar starting at t
    side: str
    struct: float  # structural level (real price) the stop goes beyond
    family: str
    idea: str
    targets: tuple = ()  # JT: untouched opposite liquidity at t (real prices)
    conf: bool = False  # JT: 1h OB overlaps the 15m OB


def _stamp(t):
    return pd.Timestamp(t, tz="UTC").isoformat()


def e1_candidates(ctx, view):
    """E1 trend pullback (long view): D1 close > EMA20(D1 closes) and H4 EMA20 rising (last two closed H4 bars);
    a 15m close back above EMA20(15m) after >= 1 15m close below it; decision at that 15m close.
    Structure = lowest low of the pullback run (the closes below + the crossing bar)."""
    out, b = [], view.m15
    for j in range(1, len(b)):
        if not (b.c[j] > view.ema15[j] and b.c[j - 1] < view.ema15[j - 1]):
            continue
        t = int(b.end[j])
        d, h = view.d1.asof(t), view.h4.asof(t)
        if d < EMA_PERIOD - 1 or h < EMA_PERIOD:
            continue
        if not (view.d1.c[d] > view.ema_d1[d] and view.ema4[h] > view.ema4[h - 1]):
            continue
        p = j - 1
        while p - 1 >= 0 and b.c[p - 1] < view.ema15[p - 1]:
            p -= 1
        out.append(Candidate(t, view.side, view.price(float(b.l[p:j + 1].min())), "E1", f"E1:{view.side}:{_stamp(t)}"))
    return out


def sweep_fade_candidates(ctx, view, ranges, window, family):
    """E2/E4 (long view): price trades below the range low inside ``window``; then a 5m close back inside the range
    AND a 5m BOS up (close above the last confirmed 5m swing high) -> long. Structure = sweep extreme (lowest low
    since the first trade-through). One idea per side per day."""
    out, b = [], view.m5
    for day, (lo, hi) in ranges.items():
        swept, extreme = None, None
        for k in ctx.window(day, *window):
            if swept is None:
                if b.l[k] < lo:
                    swept, extreme = k, b.l[k]
                else:
                    continue
            extreme = min(extreme, b.l[k])
            if lo < b.c[k] < hi and view.bos5[k]:
                t = int(b.end[k])
                out.append(Candidate(t, view.side, view.price(float(extreme)), family, f"{family}:{view.side}:{day}"))
                break
    return out


def breakout_retest_candidates(ctx, view, ranges):
    """E3 (long view): first 5m close above the Asian high in 03:00-07:00 ET; within the next 12 bars a retest
    (low <= level) and a close back above the level -> long. Structure = lowest low from the retest to the entry bar.
    Cancelled by a close below the Asian low or a missing bar."""
    out, b = [], view.m5
    for day, (lo, hi) in ranges.items():
        idx = ctx.window(day, *E3_WINDOW)
        brk = next((k for k in idx if b.c[k] > hi), None)
        if brk is None:
            continue
        touch = None
        for k in range(brk + 1, min(brk + E3_RETEST_BARS, len(b) - 1) + 1):
            if not _contiguous(b, brk, k) or b.c[k] < lo:
                break
            if touch is None and b.l[k] <= hi:
                touch = k
            if touch is not None and b.c[k] > hi:
                t = int(b.end[k])
                out.append(Candidate(t, view.side, view.price(float(b.l[touch:k + 1].min())), "E3",
                                     f"E3:{view.side}:{day}"))
                break
    return out


def entry_candidates(ctx, family):
    out = []
    for view in ctx.views.values():
        if family == "E1":
            out += e1_candidates(ctx, view)
        elif family == "E2":
            out += sweep_fade_candidates(ctx, view, session_ranges(ctx, view, ASIAN), E2_WINDOW, "E2")
        elif family == "E3":
            out += breakout_retest_candidates(ctx, view, session_ranges(ctx, view, ASIAN))
        elif family == "E4":
            out += sweep_fade_candidates(ctx, view, session_ranges(ctx, view, LONDON_RANGE), E4_WINDOW, "E4")
    return sorted(out, key=lambda c: (c.t, c.side))


def e5_filter(ctx, candidate):
    """E5 overlay: last closed 5m bar starts in 03:00-11:00 ET and its BidClose/AskClose spread <= 1.2 pip."""
    clock = et_clock(candidate.t - FIVE_NS)
    spread = ctx.spread_at(candidate.t)
    return E5_WINDOW[0] <= clock < E5_WINDOW[1] and math.isfinite(spread) and spread <= E5_MAX_SPREAD_PIPS


def et_clock(t):
    local = pd.Timestamp(t, tz="UTC").tz_convert(NY)
    return local.hour * 60 + local.minute


# ---------------------------------------------------------------- news approximation
def first_friday(year, month):
    day = date(year, month, 1)
    return day + timedelta(days=(4 - day.weekday()) % 7)


def cpi_day(year, month):
    """Approximation: the weekday closest to the 12th (Saturday -> Friday 11th, Sunday -> Monday 13th)."""
    day = date(year, month, 12)
    return day - timedelta(days=1) if day.weekday() == 5 else day + timedelta(days=1) if day.weekday() == 6 else day


def high_impact_days(start, end):
    """{ET date: label} for NFP (first Friday), CPI (approx) and FOMC statement days between two dates."""
    days = {}
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        days.setdefault(first_friday(y, m), "NFP")
        days.setdefault(cpi_day(y, m), "CPI")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    for d in FOMC_DAYS:
        days.setdefault(d, "FOMC")
    return days


def news_blocked(t, days):
    local = pd.Timestamp(t, tz="UTC").tz_convert(NY)
    clock = local.hour * 60 + local.minute
    return local.date() in days and NEWS_WINDOW[0] <= clock <= NEWS_WINDOW[1]


# ---------------------------------------------------------------- JT_SMC (Jeferson's plan, mechanical)
JT_STAGES = ("sweep", "trend", "ob15", "trigger", "fvg1h", "news", "ob1h_conf", "candidate")


@dataclass
class Idea:
    f: int  # first trade-through bar
    c: int  # close-back bar (sweep confirmed at its close)
    extreme: float
    kinds: list
    stage: int = 0
    dead: bool = False
    high: float = -math.inf  # impulse extreme after the sweep
    j15: int = 0  # next 15m bar to check for the BOS
    j15_0: int = 0
    ob15: tuple = None
    j1: int = 0
    j1_0: int = 0
    ob1: tuple = None
    obs5: list = field(default_factory=list)
    why: str = "expired_12et"  # how the idea ended
    blocked: str = ""  # last check that failed at a fib/OB trigger


def _levels(ctx, view, day, asian):
    """Sell-side liquidity (lows in the view) for ET date ``day``: (price, scan_from index, kind). Scan_from = first 5m
    bar after the level exists; a level is eligible only if its FIRST trade-through falls in the JT window."""
    b = view.m5
    win = ctx.window(day, *JT_WINDOW)
    if not len(win):
        return [], win
    start_ns = int(b.start[win[0]])
    out = []
    if day in asian:
        out.append((asian[day][0], int(win[0]), "ASIAN"))
    d = view.d1.asof(start_ns)
    if d >= 0:
        out.append((float(view.d1.l[d]), int(np.searchsorted(b.start, view.d1.end[d])), "PDL"))
        first = view.d1.start[max(0, d - JT_LEVEL_DAYS + 1)]
        h = view.h1
        for j, price in view.pl1:
            if first <= h.start[j] and h.end[j + 1] <= start_ns and h.end[j] <= view.d1.end[d]:
                out.append((price, int(np.searchsorted(b.start, h.end[j])), "SWING_1H"))
    return out, win


def _opposite_levels(ctx, view, day, asian, k):
    """Buy-side liquidity (highs in the view) untouched up to bar k: Asian high, PDH, 1h swing highs (last 5 days)."""
    b = view.m5
    t = int(b.end[k])
    win = ctx.window(day, *JT_WINDOW)
    levels = []
    if day in asian and len(win):
        levels.append((asian[day][1], int(win[0])))
    d = view.d1.asof(t)
    if d >= 0:
        levels.append((float(view.d1.h[d]), int(np.searchsorted(b.start, view.d1.end[d]))))
        first = view.d1.start[max(0, d - JT_LEVEL_DAYS + 1)]
        h = view.h1
        for j, price in view.ph1:
            if first <= h.start[j] and h.end[j + 1] <= t:
                levels.append((price, int(np.searchsorted(b.start, h.end[j]))))
    out = []
    for price, scan in levels:
        if scan > k or b.h[scan:k + 1].max(initial=-math.inf) < price:
            if price > b.c[k]:
                out.append(view.price(price))
    return tuple(sorted(set(out), key=lambda p: abs(p - view.price(b.c[k]))))


def _sweeps(ctx, view, levels, win):
    """Sweep events {close-back bar: [(first trade-through, extreme, kind)]} for levels first traded through inside
    the window and closed back over within JT_SWEEP_BARS bars."""
    b = view.m5
    events = defaultdict(list)
    end = int(win[-1]) + 1
    for price, scan, kind in levels:
        if scan >= end:
            continue
        below = np.flatnonzero(b.l[scan:end] < price)
        if not len(below):
            continue
        f = scan + int(below[0])
        if f < win[0]:
            continue  # traded through before the window: not untouched
        for c in range(f, min(f + JT_SWEEP_BARS, len(b) - 1) + 1):
            if not _contiguous(b, f, c):
                break
            if b.c[c] > price:
                events[c].append((f, float(b.l[f:c + 1].min()), kind))
                break
    return events


def _ob_back(bars, bos, floor):
    """Last bearish (close < open) bar in [max(floor, bos-12), bos-1]: (low, high) or None."""
    for q in range(bos - 1, max(floor, bos - JT_OB_LOOKBACK) - 1, -1):
        if bars.c[q] < bars.o[q]:
            return float(bars.l[q]), float(bars.h[q])
    return None


def _in_zone(retr):
    return next((z for z in JT_FIB_ZONES if z[0] <= retr <= z[1]), None)


def jt_candidates(ctx, view, require_conf, news_days, funnel, since=0):
    """JT_SMC long rules on one view (see the report for the definitions). ``funnel`` Counter: ideas reaching each
    stage of ``JT_STAGES`` (stage index stored per idea, counted once) for ideas confirmed at or after ``since``."""
    b, m15, h1 = view.m5, view.m15, view.h1
    asian = session_ranges(ctx, view, ASIAN)
    out = []
    for day in sorted(ctx.days):
        levels, win = _levels(ctx, view, day, asian)
        if not levels:
            continue
        events = _sweeps(ctx, view, levels, win)
        if not events:
            continue
        expiry = ctx.window(day, JT_WINDOW[0], JT_EXPIRY)
        ideas, active = [], None
        for k in expiry:
            for f, extreme, kind in events.get(int(k), ()):
                if active is not None and not active.dead and extreme >= active.extreme:
                    active.kinds.append(kind)  # same move: merged into the live idea
                    continue
                if active is not None and not active.dead:
                    active.dead, active.why = True, "superseded_by_deeper_sweep"
                j15 = max(0, int(np.searchsorted(m15.start, b.start[f], "right")) - 1)  # 15m bar holding f
                j1 = max(0, int(np.searchsorted(h1.start, b.start[f], "right")) - 1)
                active = Idea(f, int(k), extreme, [kind], high=float(b.h[f:k + 1].max()), j15=j15, j15_0=j15,
                              j1=j1, j1_0=j1)
                ideas.append(active)
                t = int(b.end[k])
                i1, i4 = h1.asof(t), view.h4.asof(t)
                if i1 < 0 or i4 < 0 or view.trend1[i1] <= 0 or view.trend4[i4] <= 0:
                    against = i1 >= 0 and i4 >= 0 and view.trend1[i1] < 0 and view.trend4[i4] < 0
                    active.dead, active.why = True, ("trend: sweep in the trend direction (H1+H4 opposite)" if against
                                                     else "trend: H1 and H4 disagree / neutral")
                else:
                    active.stage = 1
            if active is None or active.dead:
                continue
            idea, t = active, int(b.end[k])
            if k > idea.c:
                if b.c[k] < idea.extreme:
                    idea.dead, idea.why = True, "closed_through_sweep_extreme"
                    continue
                idea.high = max(idea.high, float(b.h[k]))
            if view.bos5[k] and k > idea.f:  # 5m OB of each 5m BOS after the sweep (usable from the next bar)
                ob = _ob_back(b, int(k), idea.f - 2)
                if ob is not None:
                    idea.obs5.append((ob, int(k)))
            if idea.ob15 is None:
                while idea.j15 < len(m15) and m15.end[idea.j15] <= t:
                    if view.bos15[idea.j15]:
                        idea.ob15 = _ob_back(m15, idea.j15, idea.j15_0 - 2) or ()
                        break
                    idea.j15 += 1
                if idea.ob15 == ():
                    idea.dead, idea.why = True, "no_15m_ob_before_bos"
                    continue
                if idea.ob15 is None:
                    continue
                idea.stage = max(idea.stage, 2)
            if idea.ob1 is None:
                while idea.j1 < len(h1) and h1.end[idea.j1] <= t:
                    if view.bos1[idea.j1]:
                        idea.ob1 = _ob_back(h1, idea.j1, idea.j1_0 - 2) or ()
                        break
                    idea.j1 += 1
            # trigger: bullish 5m close inside a 5m OB that overlaps the fib zone holding the close
            if not b.c[k] > b.o[k]:
                continue
            span = idea.high - idea.extreme
            zone = _in_zone((idea.high - b.c[k]) / span) if span > 0 else None
            if zone is None:
                continue
            z_lo, z_hi = idea.high - zone[1] * span, idea.high - zone[0] * span
            ob5 = next((ob for ob, formed in reversed(idea.obs5)
                        if formed < k and b.l[k] <= ob[1] and b.c[k] >= ob[0] and ob[0] <= z_hi and ob[1] >= z_lo), None)
            if ob5 is None:
                continue
            idea.stage = max(idea.stage, 3)
            fvg = (view.fvg_end > t - JT_FVG_HOURS * HOUR) & (view.fvg_end <= t)
            unfilled = False
            for end, bottom in zip(view.fvg_end[fvg], view.fvg_bottom[fvg]):
                s = int(np.searchsorted(b.start, end))
                if b.l[s:k + 1].min(initial=math.inf) > bottom:
                    unfilled = True
                    break
            if not unfilled:
                idea.blocked = "no_unfilled_1h_fvg"
                continue
            idea.stage = max(idea.stage, 4)
            if news_blocked(t, news_days):
                idea.blocked = "news"
                continue
            idea.stage = max(idea.stage, 5)
            conf = bool(idea.ob1) and idea.ob1[0] <= idea.ob15[1] and idea.ob1[1] >= idea.ob15[0]
            if require_conf and not conf:
                idea.blocked = "no_1h_ob_overlap"
                continue
            idea.stage = max(idea.stage, 7)
            out.append(Candidate(t, view.side, view.price(idea.ob15[0]), "JT_SMC",
                                 f"JT:{view.side}:{_stamp(int(b.end[idea.c]))}",
                                 _opposite_levels(ctx, view, day, asian, int(k)), conf))
            idea.dead, idea.why = True, "candidate"  # one entry per idea
        for idea in ideas:
            if b.end[idea.c] < since:
                continue
            funnel["sweep"] += 1
            funnel["end:" + idea.why + (f" (last trigger blocked by {idea.blocked})" if idea.blocked and
                                         idea.why != "candidate" else "")] += 1
            for s in range(1, len(JT_STAGES)):
                if idea.stage >= s and (require_conf or JT_STAGES[s] != "ob1h_conf"):
                    funnel[JT_STAGES[s]] += 1
    return out


def jt_all(ctx, require_conf, news_days, since=0):
    funnel = Counter()
    out = []
    for view in ctx.views.values():
        out += jt_candidates(ctx, view, require_conf, news_days, funnel, since)
    return sorted(out, key=lambda c: (c.t, c.side)), funnel


# ---------------------------------------------------------------- exits and portfolio walk
def plan_levels(candidate, exit_model, fill, atr, adr):
    """(stop, target, None) or (None, None, skip_reason). Distances from the fill price (the order is attached at fill).
    FIX*/JT2R/LIQ: structure -/+ 0.2 ATR14(H1), < 15 pips widened to 15, > 30 skipped. ADR: SL 0.3 ADR clamped 15-30,
    TP 0.58 ADR with TP/SL clamped to 1.5-2.0."""
    s = 1 if candidate.side == LONG else -1
    if exit_model == "ADR":
        if not math.isfinite(adr):
            return None, None, "adr_unavailable"
        sl = min(max(ADR_SL * adr, bt.MIN_SL_PIPS), bt.MAX_SL_PIPS)
        tp = min(max(ADR_TP * adr, ADR_RR[0] * sl), ADR_RR[1] * sl)
        return fill - s * sl * PIP, fill + s * tp * PIP, None
    if not math.isfinite(atr):
        return None, None, "atr_unavailable"
    stop = candidate.struct - s * bt.BUFFER_ATR * atr
    distance = s * (fill - stop) / PIP
    if distance <= 0:
        return None, None, "structure_beyond_fill"
    if distance > bt.MAX_SL_PIPS:
        return None, None, "sl_wider_than_30_pips"
    if distance < bt.MIN_SL_PIPS:
        stop, distance = fill - s * bt.MIN_SL_PIPS * PIP, bt.MIN_SL_PIPS
    risk = distance * PIP
    if exit_model == "LIQ":
        ahead = [s * (p - fill) for p in candidate.targets if s * (p - fill) > 0]
        if not ahead or not math.isfinite(adr):
            return None, None, "no_opposite_liquidity"
        reward = min(min(ahead), JT_LIQ_CAP_ADR * adr * PIP)
        if reward < JT_LIQ_FLOOR_R * risk:
            return None, None, "liquidity_below_1_5R"
        return stop, fill + s * reward, None
    k = FIX_TARGETS.get(exit_model, 2.0)
    return stop, fill + s * k * risk, None


@dataclass(frozen=True)
class Variant:
    name: str
    family: str
    exit_model: str  # FIX2 / FIX15 / ADR / LIQ (JT 2R = FIX2)
    f80: bool
    e5: bool = False
    conf: bool = False


def run_variant(candidates, ctx, market, variant):
    """Sequential walk: (trades, skips Counter). One position at a time, one entry per idea, 2R/day realised halt."""
    trades, skips = [], Counter()
    open_until, used, halted = None, set(), set()
    day_r = defaultdict(float)
    for cand in candidates:
        if cand.idea in used:
            skips["idea_already_traded"] += 1
            continue
        if variant.e5 and not e5_filter(ctx, cand):
            skips["e5_session_or_spread"] += 1
            continue
        adr = ctx.adr_at(cand.t)
        if variant.f80 and not (math.isfinite(adr) and ctx.range_so_far(cand.t) <= F80_SHARE * adr):
            skips["f80_day_range"] += 1
            continue
        slot = datetime.fromtimestamp(cand.t / 1e9, timezone.utc)
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
            skips["no_next_bar"] += 1  # the 5m bar starting at the decision close is missing (gap/weekend)
            continue
        s = 1 if cand.side == LONG else -1
        hs, _ = market.half_spread_at(i)
        fill = float(market.open[i]) + s * (hs + market.slippage)  # identical to bt.simulate_trade
        stop, target, why = plan_levels(cand, variant.exit_model, fill, ctx.atr_at(cand.t), adr)
        if stop is None:
            skips[why] += 1
            continue
        result = bt.simulate_trade(market, slot, cand.side, stop, target, "I2")
        if "rejected" in result:
            skips[result["rejected"]] += 1
            continue
        used.add(cand.idea)
        open_until = result["exit_at"]
        result.update(variant=variant.name, signal_at=slot, side=cand.side, idea=cand.idea, conf=cand.conf)
        trades.append(result)
        key = bt.day_key(result["exit_at"])
        day_r[key] += result["r"]
        if day_r[key] <= -bt.DAILY_HALT_R:
            halted.add(key)
    bt.add_gross(trades, market)
    return trades, skips


# ---------------------------------------------------------------- split, metrics, report
@dataclass
class Split:
    start: datetime
    split: datetime
    end: datetime

    @property
    def months(self):
        per = 365.25 / 12 * 86400
        return (self.split - self.start).total_seconds() / per, (self.end - self.split).total_seconds() / per


def make_split(ctx):
    start = pd.Timestamp(ctx.tradable_start(), tz="UTC").to_pydatetime()
    local = (pd.Timestamp(start).tz_convert(NY) + pd.DateOffset(months=IS_MONTHS)).date()
    split = datetime.combine(local, time(17), tzinfo=NY).astimezone(timezone.utc)
    end = datetime.fromtimestamp(int(ctx.m5.end[-1]) / 1e9, timezone.utc)
    return Split(start, split, end)


def segment(trades, split):
    return ([t for t in trades if t["signal_at"] < split.split], [t for t in trades if t["signal_at"] >= split.split])


GATE_ROWS = {"G3": ("G3 mean R", "G3 bootstrap", "G3 worst"), "G4": ("G4 max DD",),
             "G5": ("G5 mean total cost", "G5 costs <=")}


def gate_flags(m):
    """'PFF'-style G3/G4/G5 verdicts from ``bt.gates`` (same thresholds as the backtest report)."""
    if not m["trades"]:
        return "---"
    rows = bt.gates(m)
    flags = ""
    for prefixes in GATE_ROWS.values():
        hits = [v for label, v in rows if label.startswith(prefixes)]
        flags += "P" if hits and all(v.startswith("PASS") for v in hits) else "F"
    return flags


def summarise(trades, split):
    is_t, oos_t = segment(trades, split)
    mi, mo = split.months
    return bt.metrics(is_t, mi), bt.metrics(oos_t, mo)


def _f(x, fmt):
    return "–" if x is None or (isinstance(x, float) and not math.isfinite(x)) else format(x, fmt)


def table(results, split):
    head = ("| Variant | Trades/mo IS / OOS | Win % | Avg R | Total R | MTM DD R | Med hold h | Exits TP/SL/TIME/FRI % "
            "IS ; OOS | CI80 low | G3 G4 G5 |\n|---|---|---|---|---|---|---|---|---|---|")
    rows = [head]
    for name, (trades, _) in results.items():
        a, b = summarise(trades, split)

        def pair(key, fmt, scale=1.0):
            return " / ".join(_f(m[key] * scale if m[key] is not None and m["trades"] else None, fmt) for m in (a, b))

        def exits(m):
            e = m["exits"]
            return "/".join(f"{e[k]:.0f}" for k in (bt.TP, bt.SL, bt.TIME, bt.FRIDAY)) if m["trades"] else "–"
        rows.append(f"| {name} | {a['trades_per_month']:.1f} ({a['trades']}) / {b['trades_per_month']:.1f} "
                    f"({b['trades']}) | {pair('win_rate', '.0f', 100)} | {pair('avg_r', '+.3f')} | "
                    f"{pair('total_r', '+.1f')} | {pair('max_dd_mtm_r', '.1f')} | "
                    f"{' / '.join(_f(m['hold_h'][0], '.1f') for m in (a, b))} | {exits(a)} ; {exits(b)} | "
                    f"{pair('ci80_low', '+.3f')} | {gate_flags(a)} / {gate_flags(b)} |")
    return "\n".join(rows)


def top_variants(results, split, n=4):
    scored = []
    for name, (trades, _) in results.items():
        a, _ = summarise(trades, split)
        if a["trades"] >= MIN_IS_TRADES_FOR_TOP:
            scored.append((a["avg_r"], name))
    return [name for _, name in sorted(scored, reverse=True)[:n]]


def monthly(results, names, split):
    rows = defaultdict(dict)
    for name in names:
        for t in results[name][0]:
            rows[t["exit_at"].astimezone(NY).strftime("%Y-%m")][name] = (
                rows[t["exit_at"].astimezone(NY).strftime("%Y-%m")].get(name, 0.0) + t["r"])
    split_month = (split.split.astimezone(NY) + timedelta(days=1)).strftime("%Y-%m")  # first month mostly OOS
    lines = ["| Month | " + " | ".join(names) + " |", "|---" * (len(names) + 1) + "|"]
    for month in sorted(rows):
        tag = " (OOS)" if month >= split_month else ""
        lines.append(f"| {month}{tag} | " + " | ".join(f"{rows[month].get(n, 0.0):+.2f}" for n in names) + " |")
    return "\n".join(lines)


def oos_positive(results, split):
    names = []
    for name, (trades, _) in results.items():
        _, b = summarise(trades, split)
        if b["trades"] and b["avg_r"] > 0:
            names.append((name, b["avg_r"], b["trades"], b["ci80_low"]))
    return names


def plot_equity(path, results, names, split):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    colors = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]  # categorical slots 1-4, fixed order
    ink, muted, surface = "#0b0b0b", "#52514e", "#fcfcfb"
    fig, ax = plt.subplots(figsize=(10, 5), dpi=120, facecolor=surface)
    ax.set_facecolor(surface)
    ax.axvspan(split.split, split.end, color="#e8e8e6", alpha=0.6, linewidth=0, label="out-of-sample")
    for color, name in zip(colors, names):
        trades = results[name][0]
        if not trades:
            continue
        xs = [split.start] + [t["exit_at"] for t in trades]
        ys = np.concatenate([[0.0], np.cumsum([t["r"] for t in trades])])
        ax.step(xs, ys, where="post", color=color, linewidth=2, label=name)
        ax.annotate(f"{ys[-1]:+.1f}R", (xs[-1], ys[-1]), xytext=(6, 0), textcoords="offset points", va="center",
                    fontsize=9, color=ink)
    ax.axhline(0, color=muted, linewidth=0.8)
    ax.set_title("EURUSD entry search - cumulative R of the top-4 in-sample variants (shaded = out-of-sample)",
                 loc="left", color=ink, fontsize=10)
    ax.set_ylabel("Cumulative R (net of spread + slippage)", color=muted)
    ax.tick_params(colors=muted)
    ax.grid(axis="y", color="#e8e8e6", linewidth=0.8)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#c9c8c4")
    ax.legend(frameon=False, loc="upper left", labelcolor=ink, fontsize=8)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path, facecolor=surface)
    plt.close(fig)


def adr_stats(ctx, split):
    values = [ctx.adr[i] for i in range(len(ctx.d1)) if split.start.timestamp() * 1e9 <= ctx.d1.end[i]
              and math.isfinite(ctx.adr[i])]
    return {"median": float(np.median(values)), "mean": float(np.mean(values)), "p10": float(np.percentile(values, 10)),
            "p90": float(np.percentile(values, 90)), "n": len(values)}


JT_DEFINITIONS = """\
Mechanical definitions (fixed before any run; long rules shown, shorts mirror them):
- **Trend**: H1 and H4 (H4 resampled from closed 1h bars, 4h buckets anchored at 17:00 NY) must both be bullish at the
  sweep confirmation. Per timeframe: last two confirmed swing highs AND lows HH+HL = bullish, LH+LL = bearish; if
  neither (neutral / < 2 swings) the sign of the EMA20 slope of the last two closes. Longs only in a bullish trend.
- **Liquidity (sell-side for longs)**: Asian low (19:00-03:00 ET), PDL (low of the prior NY 17:00-17:00 trading day),
  confirmed 1h swing lows whose pivot lies in the last 5 completed trading days. A level is eligible only if its FIRST
  trade-through after it formed happens in 03:00-11:00 ET (untouched until then).
- **Sweep**: that first trade-through bar wicks below the level and a 5m close back above it comes within 6 bars
  (30 min). Sweep extreme = lowest low from the trade-through to the close back. Simultaneous / shallower sweeps
  merge into the live idea; a deeper one supersedes it; a 5m close below the extreme kills it; ideas expire at
  12:00 ET. One entry per idea.
- **15m OB**: the first closed 15m bar after the sweep that closes above the last confirmed 15m swing high (BOS);
  the OB = last bearish 15m candle in the up-to-12 bars before that BOS bar (not earlier than 2 bars before the
  sweep), full candle range. No such candle -> idea dropped.
- **1h OB confluence (variant `1hOB`)**: same definition on 1h bars (first 1h BOS after the sweep, last bearish 1h
  candle before it); required to overlap the 15m OB range at entry.
- **Fib**: from the sweep extreme to the impulse extreme (highest high since the trade-through, running, closed
  bars). The entry close must retrace 50-60% or 75-85%.
- **5m OB / trigger**: every 5m BOS after the sweep (close above the last confirmed 5m swing high) defines a 5m OB =
  last bearish 5m candle before it (up to 12 bars back). Trigger = a bullish 5m close with low <= OB high and
  close >= OB low, for an OB formed before that bar and overlapping the fib zone that holds the close. Fill at the
  next 5m open.
- **1h FVG**: a bullish 3-candle 1h FVG (low[c] > high[a], consecutive hours) whose 3rd candle closed within the last
  24 h and that is unfilled (no 5m low <= high[a] since) at the trigger.
- **News (approximation, no calendar file)**: skip triggers with the decision/fill clock in 08:15-09:00 ET on
  NFP = first Friday of the month, CPI = the weekday closest to the 12th, FOMC statement days (2025-10-29,
  2025-12-10, 2026-01-28, 2026-03-18, 2026-04-29, 2026-06-17, 2026-07-29, 2026-09-16). Limitations: real NFP/CPI
  dates differ in some months (and were shifted by the Oct-Nov 2025 US government shutdown); FOMC statements are at
  14:00 ET so the 08:15-09:00 window never blocks them (entries end by 12:00 ET but positions may be held through
  14:00 ET); other high-impact events (ECB, GDP, PCE, retail sales...) are not modeled.
- **SL**: 15m OB low - 0.2 x ATR14(H1), measured from the fill; < 15 pips widened to 15, > 30 pips skipped; fixed
  once set (no break-even, no trailing).
- **TP**: `2R` fixed; `LIQ` = nearest untouched buy-side level above the fill (Asian high, PDH, 1h swing highs of
  the last 5 days), capped at 1.0 x ADR20 from the fill, skipped if < 1.5R; `ADR` = X_ADR (SL 0.3 ADR clamped 15-30,
  TP 0.58 ADR with TP/SL clamped to 1.5-2.0R; the structural stop is not used).
"""

E_DEFINITIONS = """\
- **E1** trend pullback: D1 close > EMA20(D1) and H4 EMA20 rising; a 15m close back above EMA20(15m) after >= 1
  15m close below it; structure = lowest low of the pullback run (incl. the crossing bar). Any hour; fills outside
  the London/NY sessions are rejected by the I2 exit rule (`fill_outside_session`).
- **E2** London-open Asian sweep: in 03:00-06:00 ET (decision bar start) price trades below the Asian low
  (19:00-03:00 ET), then a 5m close back inside the range AND a 5m BOS up -> long; structure = sweep extreme.
- **E3** Asian breakout + retest: first 5m close above the Asian high in 03:00-07:00 ET; within 12 bars a retest
  (low <= level) and a close back above -> long; structure = lowest low from the retest to entry.
- **E4** NY-open London sweep: London range 03:00-08:00 ET; in 08:00-11:00 ET same rule as E2.
- **E5.x** = Ex restricted to decision bars starting 03:00-11:00 ET with the last closed 5m bar's
  AskClose-BidClose <= 1.2 pip.
- Exits: `FIX2`/`FIX15` = structure -/+ 0.2 x ATR14(H1), < 15 pips widened, > 30 pips skipped, TP 2R / 1.5R;
  `ADR` = SL 0.3 x ADR20 clamped 15-30 pips, TP 0.58 x ADR20, TP/SL clamped to 1.5-2.0R. `F80` = skip when today's
  range (since 17:00 NY) > 0.8 x ADR20. One idea = one setup per side per day (E2-E4) / per 15m cross (E1).
"""


def write_report(path, state):
    split, adr = state["split"], state["adr"]
    mi, mo = split.months
    lines = [
        "# EURUSD intraday entry search (REPLAY ONLY)", "",
        f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')} by `python -m replay.entry_search` "
        f"(branch `feat/backtest-v2-i2`). Production unchanged. Status: **{state['status']}**.", "",
        f"- Data: EURUSD 5m/15m/1h mid OHLC + 5m BidClose/AskClose, {state['data_start']} -> {state['data_end']}. "
        "H4/D1 resampled causally from closed 1h bars.",
        f"- Tradable from {split.start:%Y-%m-%d %H:%M} UTC (20 completed trading days for ADR20 / D1 EMA20). "
        f"**In-sample** {split.start:%Y-%m-%d} -> {split.split:%Y-%m-%d} ({mi:.2f} months), **out-of-sample** "
        f"{split.split:%Y-%m-%d} -> {split.end:%Y-%m-%d} ({mo:.2f} months). Trades are assigned by signal time; "
        "the portfolio walk is continuous. All parameters fixed upfront (no tuning).",
        "- Costs: causal per-trade spread (median AskClose-BidClose of the 30 days before the fill bar, fallback 1.6 "
        "pip), 0.2 pip adverse slippage on entry/stop/time exits, 0 on TP. Same-bar SL first. Exit at TP/SL, the end "
        "of the 2nd session counted from the fill (TIME) or Friday 17:00 NY (FRIDAY). 2R/day realised halt, one "
        "position at a time.",
        f"- ADR20 (pips) over the tradable period ({adr['n']} trading days): median {adr['median']:.1f}, mean "
        f"{adr['mean']:.1f}, p10 {adr['p10']:.1f}, p90 {adr['p90']:.1f}.",
        "- Columns: IS / OOS. Trades/mo (n). MTM DD = mark-to-market max drawdown in R. CI80 low = lower bound of the "
        "80% bootstrap CI of the mean R. Gates G3 (mean > +0.15R, CI low > 0, worst >= -1.2R), G4 (MTM DD <= 8R), G5 "
        "(avg cost <= 0.10R and <= 25% of gross) from `replay.intraday_backtest.gates`; P = pass, F = fail.", ""]
    if "jt" in state:
        lines += ["## 1. JT_SMC — Jeferson's plan, mechanical (priority)", "", JT_DEFINITIONS,
                  "### Results", "", table(state["jt"], split), ""]
        lines += ["### Funnel (ideas reaching each stage, both sides, tradable period)", "",
                  "| Stage | no 1h-OB requirement | with 1h-OB confluence |", "|---|---|---|"]
        for stage in JT_STAGES:
            if stage == "ob1h_conf":
                lines.append(f"| {stage} | n/a | {state['jt_funnel'][True].get(stage, 0)} |")
            else:
                lines.append(f"| {stage} | {state['jt_funnel'][False].get(stage, 0)} | "
                             f"{state['jt_funnel'][True].get(stage, 0)} |")
        ends = sorted({k for f in state["jt_funnel"].values() for k in f if k.startswith("end:")})
        lines += ["", "How ideas ended (`end:` reason; 'last trigger blocked by' = a fib/5m-OB trigger happened but "
                  "a later check failed):", "", "| End reason | no 1h-OB req. | with 1h-OB |", "|---|---|---|"]
        lines += [f"| {k[4:]} | {state['jt_funnel'][False].get(k, 0)} | {state['jt_funnel'][True].get(k, 0)} |"
                  for k in ends]
        lines += ["", "Portfolio stage (after `candidate`, per variant):", ""]
        for name, (trades, skips) in state["jt"].items():
            lines.append(f"- {name}: traded {len(trades)}; " + ", ".join(f"{k} {v}" for k, v in skips.most_common()))
        lines += ["", state["jt_note"], ""]
    if state.get("e"):
        lines += ["## 2. E1-E5 x exits x F80", "", E_DEFINITIONS, f"Families finished: {', '.join(state['e_done'])}.",
                  "", table(state["e"], split), ""]
        if "E5" in state["e_done"]:
            lines += [state["spread_note"], ""]
        lines += ["Skips per variant (portfolio):", ""]
        for name, (trades, skips) in state["e"].items():
            lines.append(f"- {name}: traded {len(trades)}; " + ", ".join(f"{k} {v}" for k, v in skips.most_common()))
        lines.append("")
    if state.get("final"):
        everything = {**state["jt"], **state["e"]}
        top = top_variants(everything, split)
        lines += ["## 3. Top-4 variants by in-sample avg R", "",
                  f"Ranked over all {len(everything)} variants with >= {MIN_IS_TRADES_FOR_TOP} in-sample trades: "
                  f"{', '.join(top) or 'none'}. Equity: `entries_equity_r.png`.", "",
                  "Monthly net R (by exit month, NY):", "", monthly(everything, top, split), ""]
        positive = oos_positive(everything, split)
        lines += ["## 4. Out-of-sample verdict", ""]
        if positive:
            lines.append(f"**{len(positive)} of {len(everything)} variants have a positive OOS mean R**: " + "; ".join(
                f"{n} ({r:+.3f}R, {k} trades, CI80 low {_f(c, '+.3f')})" for n, r, k, c in positive) + ".")
        else:
            lines.append(f"**No variant is positive out-of-sample** (0 of {len(everything)}).")
        both = [n for n, *_ in positive if summarise(everything[n][0], split)[0]["avg_r"] > 0]
        lines.append(f"Positive in BOTH IS and OOS: {', '.join(both) or 'none'}.")
        lines += ["", f"**Multiple-testing warning**: {len(everything)} variants were evaluated on the same year. With a "
                  "one-sided 10% error per test (the 80% CI lower bound), ~"
                  f"{0.10 * len(everything):.0f} variants would show 'CI lower > 0' by chance alone if none had an "
                  "edge (fewer in practice because the variants are strongly correlated, but the point stands). A "
                  "positive OOS result in one variant among many is NOT evidence of an edge; it needs a fresh, "
                  "pre-registered out-of-sample period (e.g. live paper) before any decision.", ""]
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


# ---------------------------------------------------------------- main
def jt_variants():
    out = []
    for conf in (False, True):
        for tp in ("FIX2", "LIQ", "ADR"):
            for f80 in (False, True):
                label = {"FIX2": "2R", "LIQ": "LIQ", "ADR": "ADR"}[tp]
                out.append(Variant(f"JT:{label}{':F80' if f80 else ''}{':1hOB' if conf else ''}", "JT", tp, f80,
                                   conf=conf))
    return out


def e_variants(family, e5=False):
    name = f"E5.{family}" if e5 else family
    return [Variant(f"{name}:{x}{':F80' if f80 else ''}", family, x, f80, e5)
            for x in ("FIX2", "FIX15", "ADR") for f80 in (False, True)]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    os.makedirs(args.out, exist_ok=True)
    report = os.path.join(args.out, "entries_report.md")

    def log(message):
        print(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {message}", flush=True)

    frames = bt.load_csv_frames(args.data)
    ctx = Context(frames)
    split = make_split(ctx)
    end = split.end
    market = bt.Market.from_frames(frames, bt.FALLBACK_SPREAD_PIPS, cutoff=end, causal=True)
    start_ns = int(split.start.timestamp() * 1e9)
    news_days = high_impact_days(split.start.date(), end.date())
    state = {"split": split, "adr": adr_stats(ctx, split), "status": "PARTIAL (JT_SMC running)",
             "data_start": frames["5m"].index[0].isoformat(), "data_end": end.isoformat()}
    log(f"split {split.start} | {split.split} | {split.end}; ADR20 {state['adr']}")

    jt, funnels = {}, {}
    cands = {}
    for conf in (False, True):
        found, funnel = jt_all(ctx, conf, news_days, start_ns)
        cands[conf] = [c for c in found if c.t >= start_ns]
        funnels[conf] = funnel
        log(f"JT conf={conf}: {len(cands[conf])} candidates, funnel {dict(funnel)}")
    for variant in jt_variants():
        jt[variant.name] = run_variant(cands[variant.conf], ctx, market, variant)
        log(f"{variant.name}: {len(jt[variant.name][0])} trades")
    spread = np.round((frames["5m"]["AskClose"] - frames["5m"]["BidClose"]).to_numpy() / PIP, 2)
    state["spread_note"] = (
        f"**E5 note**: the 5m BidClose/AskClose spread in this data is never below {spread.min():.1f} pip (median "
        f"{np.median(spread):.1f}, {np.mean(spread <= E5_MAX_SPREAD_PIPS):.1%} of bars <= {E5_MAX_SPREAD_PIPS} pip), "
        "so the fixed E5 spread condition (<= 1.2 pip) rejects every candidate: E5 is empty by construction on this "
        "feed (OANDA practice quotes), not because of the entries. Not re-parameterised (no tuning).")
    state.update(jt=jt, jt_funnel=funnels, status="PARTIAL (JT_SMC done, E families running)", e={}, e_done=[])
    rates = [summarise(trades, split)[0]["trades_per_month"] for trades, _ in jt.values()]
    state["jt_note"] = (f"In-sample trades/month across JT variants: {min(rates):.1f}-{max(rates):.1f}. "
                        + ("**Sample too small (< 4 trades/month): JT results are not statistically meaningful.**"
                           if max(rates) < 4 else "Some JT variants trade >= 4/month; others may not."))
    write_report(report, state)

    for family in ("E1", "E2", "E3", "E4", "E5"):
        if family == "E5":
            for base in ("E1", "E2", "E3", "E4"):
                for variant in e_variants(base, e5=True):
                    state["e"][variant.name] = run_variant(cands[base], ctx, market, variant)
        else:
            cands[family] = [c for c in entry_candidates(ctx, family) if c.t >= start_ns]
            log(f"{family}: {len(cands[family])} candidates")
            for variant in e_variants(family):
                state["e"][variant.name] = run_variant(cands[family], ctx, market, variant)
        state["e_done"].append(family)
        log(f"{family} done")
        write_report(report, state)

    state.update(final=True, status="COMPLETE")
    everything = {**jt, **state["e"]}
    plot_equity(os.path.join(args.out, "entries_equity_r.png"), everything, top_variants(everything, split), split)
    write_report(report, state)
    log("done")
    return state


if __name__ == "__main__":
    main()
