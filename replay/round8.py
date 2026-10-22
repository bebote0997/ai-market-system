"""Round 8 (REPLAY ONLY): single-session scalping and literature families on OANDA M1 candles, 2021-10..2026-10.

Instruments EURUSD, GBPUSD, XAUUSD, NAS100USD. Every family is a fixed, pre-declared rule (no parameter grid); the
base families S1-S3 (ORB / pre-session range breakout, Asia-range sweep reversal, VWAP 2-sigma fade, initial-balance
breakout) are reconstructed from ``scalping_references.md`` because the base section of the round-8 brief was missing
(declared in the report). The literature families L1-L7 follow the brief's LITERATURE ADDITION.

Protocol per instrument (like round 6): TRAIN 2021-10-01..2024-12-31 (frames sliced at the cutoff before any signal is
built) with half-year folds 2023H1..2024H2 as fold tests; one variant (or none) is frozen to
``round8_frozen_<SYM>.json`` + the committed copy ``replay/round8_frozen_<SYM>.json``; the holdout runner fails closed
unless the file equals the committed one; HOLDOUT 2025-01-01..2026-10-19 is run once with the frozen variant.

Run (after the declaration in ``report_round8.md`` section 1 is committed): per symbol ``--symbol EURUSD --sections
select`` (commit the frozen file), then ``--sections holdout``. Data / outputs stay
outside the repository and are never committed.
"""
import argparse
import json
import math
import os
import subprocess
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

NY = "America/New_York"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = "/workspace/backtest"
SYMBOLS = ("EURUSD", "GBPUSD", "XAUUSD", "NAS100USD")
TRAIN = ("2021-10-01", "2025-01-01")  # [a, b) NY session dates
HOLDOUT = ("2025-01-01", "2026-10-20")
FOLDS = (("2023-01-01", "2023-07-01"), ("2023-07-01", "2024-01-01"), ("2024-01-01", "2024-07-01"),
         ("2024-07-01", "2025-01-01"))
MINUTE = 60 * 10**9
ENTRY_TOL = 5  # minutes: a scheduled entry needs a bar starting within [T, T + 5 min)
EXIT_TOL = 10  # minutes: a scheduled exit uses the close of the last bar starting in [T - 10 min, T)
RANGE_MIN_COVER = 0.5  # a reference range needs >= 50 % of its minutes as bars
ATR_N = 14
ATR_STOP_K = 3.0  # "1.0 x ATR14(M5) x k, k fixed 3"
FX_CLAMP_PIPS = (5.0, 15.0)
# ATR-equivalent clamp for XAU / NAS100: EURUSD median ATR14(M5) over TRAIN = 3.3 pips -> 5 / 15 pips = 1.5 / 4.5 ATR,
# applied to the instrument's trailing 20-day median ATR14(M5) (prior days only).
ATR_CLAMP_MULT = (1.5, 4.5)
ATR_MED_DAYS = 20
# pass bar (holdout, main cost model), and the same bar on TRAIN as the selection eligibility filter
BAR = {"avg_r": 0.08, "pf": 1.15, "dd": 12.0, "tr_mo": 3.0}
MIN_POS_FOLDS = 3
PREVIOUS_TESTS, PREVIOUS_CONSERVATIVE = 474, 874
BOOT_N, BOOT_SEED = 2000, 8


@dataclass(frozen=True)
class Spec:
    pip: float
    fx: bool
    open_hm: tuple  # session open (NY)
    end_hm: tuple  # session end (NY)
    range_hm: tuple  # reference range start on the previous calendar day (NY); range ends at the session open
    main: str  # main cost model


SPECS = {
    "EURUSD": Spec(1e-4, True, (3, 0), (11, 0), (19, 0), "C_RAW"),
    "GBPUSD": Spec(1e-4, True, (3, 0), (11, 0), (19, 0), "C_RAW"),
    "XAUUSD": Spec(0.1, False, (3, 0), (11, 0), (19, 0), "C_IBKR"),
    "NAS100USD": Spec(1.0, False, (9, 30), (16, 0), (18, 0), "C_IBKR"),
}


# ---------------------------------------------------------------- cost models (price units)
@dataclass(frozen=True)
class Cost:
    name: str
    spread: dict = field(default_factory=dict)
    slip: dict = field(default_factory=dict)
    commission: dict = field(default_factory=dict)  # round turn
    observed: bool = False  # C_OANDA: half the previous bar's observed bid/ask spread
    slip_floor: dict = field(default_factory=dict)


FX = ("EURUSD", "GBPUSD")
C_RAW = Cost("C_RAW", {s: 0.2e-4 for s in FX}, {s: 0.1e-4 for s in FX}, {s: 0.7e-4 for s in FX})
C_RAW_STRESS = Cost("C_RAW_STRESS", {s: 0.4e-4 for s in FX}, {s: 0.2e-4 for s in FX}, {s: 0.7e-4 for s in FX})
C_IBKR = Cost("C_IBKR", {"XAUUSD": 0.10, "NAS100USD": 0.25}, {"XAUUSD": 0.10, "NAS100USD": 0.25},
              {"XAUUSD": 0.12, "NAS100USD": 0.62})
C_IBKR_STRESS = Cost("C_IBKR_STRESS", {"XAUUSD": 0.20, "NAS100USD": 0.50}, {"XAUUSD": 0.20, "NAS100USD": 0.50},
                     {"XAUUSD": 0.12, "NAS100USD": 0.62})
C_OANDA = Cost("C_OANDA", observed=True, commission={s: 0.0 for s in SYMBOLS},
               slip_floor={"EURUSD": 0.2e-4, "GBPUSD": 0.2e-4, "XAUUSD": 0.02, "NAS100USD": 0.2})
COSTS = {c.name: c for c in (C_RAW, C_RAW_STRESS, C_IBKR, C_IBKR_STRESS, C_OANDA)}


def models_for(symbol):
    main = SPECS[symbol].main
    return [main, main + "_STRESS", "C_OANDA"]


class Fills:
    """Per-bar half spread and slippage of one cost model on one market."""

    def __init__(self, cost, symbol, spread_obs):
        n = len(spread_obs)
        self.comm = cost.commission[symbol]
        if cost.observed:
            prev = np.concatenate([[spread_obs[0]], spread_obs[:-1]])  # causal: the previous bar's spread
            self.hs = prev / 2
            self.slip = np.maximum(0.1 * prev, cost.slip_floor[symbol])
        else:
            self.hs = np.full(n, cost.spread[symbol] / 2)
            self.slip = np.full(n, cost.slip[symbol])


# ---------------------------------------------------------------- market
def load_m1(path, end=None):
    df = pd.read_csv(path)
    df.index = pd.DatetimeIndex(pd.to_datetime(df.pop("start"), utc=True)).as_unit("ns")
    if end is not None:
        df = df[df.index < pd.Timestamp(end, tz=NY)]
    return df


class Market:
    """M1 arrays + M5 ATR + D1 (17:00 NY rollover) of one instrument; all lookups causal by construction."""

    def __init__(self, df, symbol):
        self.symbol, self.spec = symbol, SPECS[symbol]
        self.t = df.index.asi8
        self.o, self.h, self.l, self.c = (df[k].to_numpy(float) for k in ("Open", "High", "Low", "Close"))
        self.v = df["Volume"].to_numpy(float)
        self.spread = (df["AskClose"] - df["BidClose"]).to_numpy(float)
        ny = df.index.tz_convert(NY)
        self.days = sorted({d for d, wd in zip(ny.date, ny.weekday) if wd < 5})
        m5 = df.resample("5min", label="left", closed="left").agg(
            {"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
        prev = m5["Close"].shift()
        tr = np.maximum(m5["High"] - m5["Low"], np.maximum((m5["High"] - prev).abs(), (m5["Low"] - prev).abs()))
        self.t5 = m5.index.asi8
        self.end5 = self.t5 + 5 * MINUTE
        self.o5, self.h5, self.l5, self.c5 = (m5[k].to_numpy(float) for k in ("Open", "High", "Low", "Close"))
        self.atr5 = tr.rolling(ATR_N).mean().to_numpy(float)
        key = (ny + pd.Timedelta(hours=7)).date  # D1 bar keyed by the NY date it closes on (17:00 NY)
        g = df.groupby(key)
        d1 = pd.DataFrame({"o": g["Open"].first(), "h": g["High"].max(), "l": g["Low"].min(), "c": g["Close"].last(),
                           "n": g.size()})
        self.d1 = d1[d1["n"] >= 60]
        self.d1_keys = list(self.d1.index)
        self._d1_arr = np.array(self.d1_keys, dtype=object)
        self.month_end_days = month_ends(self.days)
        atr_day = pd.Series(self.atr5, index=m5.index.tz_convert(NY).date).groupby(level=0).median()
        self.atr_med = atr_day.rolling(ATR_MED_DAYS, min_periods=ATR_MED_DAYS).median().shift(1)

    # ---- time helpers
    def ts(self, day, hm, days=0):
        d = pd.Timestamp(day) + pd.Timedelta(days=days)
        return pd.Timestamp(d.year, d.month, d.day, hm[0], hm[1], tz=NY).value

    def entry_at(self, ts):
        k = int(np.searchsorted(self.t, ts, "left"))
        return k if k < len(self.t) and self.t[k] - ts < ENTRY_TOL * MINUTE else None

    def exit_at(self, ts):
        k = int(np.searchsorted(self.t, ts, "left")) - 1
        return k if k >= 0 and ts - self.t[k] <= EXIT_TOL * MINUTE else None

    def span(self, a, b):
        return int(np.searchsorted(self.t, a, "left")), int(np.searchsorted(self.t, b, "left"))

    def range_hl(self, a, b):
        i, j = self.span(a, b)
        if j - i < RANGE_MIN_COVER * (b - a) / MINUTE:
            return None
        return float(self.h[i:j].max()), float(self.l[i:j].min())

    def atr_at(self, ts):
        j = int(np.searchsorted(self.end5, ts, "right")) - 1
        return float(self.atr5[j]) if j >= 0 and not math.isnan(self.atr5[j]) else None

    def prev_d1(self, day, n=1):
        """The last ``n`` completed D1 bars before session day ``day`` (oldest first) or None."""
        k = int(np.searchsorted(self._d1_arr, day, "left")) if self.d1_keys else 0
        if k < n:
            return None
        return self.d1.iloc[k - n:k]

    def clamp(self, day, dist):
        if self.spec.fx:
            lo, hi = (p * self.spec.pip for p in FX_CLAMP_PIPS)
        else:
            med = self.atr_med.get(day)
            if med is None or math.isnan(med):
                return None
            lo, hi = (m * med for m in ATR_CLAMP_MULT)
        return float(min(max(dist, lo), hi))


# ---------------------------------------------------------------- orders
@dataclass
class Leg:
    side: int  # +1 long / -1 short
    kind: str  # "market" | "stop" | "limit"
    level: float  # entry level (market: reference = bar open)
    stop: float = None  # structure stop (absolute) ...
    stop_dist: float = None  # ... or a distance (ATR rule)
    tp_r: float = None
    tp_price: float = None


@dataclass
class Order:
    day: object
    legs: list  # one leg, or two (OCO: the first to trigger wins; both on the same bar -> no trade)
    start: int  # first bar index the order is live (market: the entry bar)
    end: int  # pending orders live on bars [start, end)
    exit: int  # time exit at the close of this bar


def resolve(m, day, leg):
    """Clamped stop and TP of a leg relative to its intended entry ``leg.level``; None if not computable."""
    if leg.stop_dist is not None:
        dist = leg.stop_dist
    elif leg.stop is not None:
        dist = abs(leg.level - leg.stop)
    else:
        return None
    dist = m.clamp(day, dist)
    if not dist:
        return None
    stop = leg.level - leg.side * dist
    if leg.tp_r is not None:
        tp = leg.level + leg.side * leg.tp_r * dist
    elif leg.tp_price is not None:
        tp = leg.tp_price
        if leg.side * (tp - leg.level) <= 0:
            return None
    else:
        tp = None
    return stop, tp


def first_trigger(m, fills, leg, a, b):
    """First bar index in [a, b) where a pending leg fills, and the fill price on its own side (buy orders on the ask
    = mid + half spread, sell orders on the bid = mid - half spread): a buy stop triggers when the ask high reaches the
    level and fills at max(ask open, level); a buy limit needs the ask low to trade through the level and fills at
    min(ask open, level) (never above the limit). Mirror for sells. Slippage is added by ``simulate``."""
    if b <= a:
        return None
    hs = fills.hs[a:b]
    if leg.side > 0:
        hi_s, lo_s, o_s = m.h[a:b] + hs, m.l[a:b] + hs, m.o[a:b] + hs  # ask
    else:
        hi_s, lo_s, o_s = m.h[a:b] - hs, m.l[a:b] - hs, m.o[a:b] - hs  # bid
    if leg.kind == "stop":
        hit = hi_s >= leg.level if leg.side > 0 else lo_s <= leg.level
    else:  # limit: price must trade through
        hit = lo_s < leg.level if leg.side > 0 else hi_s > leg.level
    idx = np.flatnonzero(hit)
    if not len(idx):
        return None
    q = int(idx[0])
    o = o_s[q]
    if leg.kind == "stop":
        px = max(o, leg.level) if leg.side > 0 else min(o, leg.level)
    else:
        px = min(o, leg.level) if leg.side > 0 else max(o, leg.level)
    return a + q, float(px)


def simulate(m, fills, order, busy_until=-1):
    """One trade dict, or None (no fill / overlap / not computable). Every touch is checked on the side that fills it:
    a long exits on the bid (SL when the bid low reaches the stop, TP when the bid high trades through the target),
    a short on the ask. Stop wins a same-bar stop/TP tie; a pending fill bar can stop out but never takes profit."""
    if order.legs[0].kind == "market":
        leg = order.legs[0]
        k, px = order.start, float(m.o[order.start])
        leg = Leg(leg.side, leg.kind, px, leg.stop, leg.stop_dist, leg.tp_r, leg.tp_price)
        entry = px + leg.side * (fills.hs[k] + fills.slip[k])
    else:
        hits = [(first_trigger(m, fills, lg, order.start, order.end), lg) for lg in order.legs]
        hits = [(h, lg) for h, lg in hits if h is not None]
        if not hits:
            return None
        hits.sort(key=lambda x: x[0][0])
        if len(hits) > 1 and hits[0][0][0] == hits[1][0][0]:
            return None
        (k, px), leg = hits[0]
        entry = px + leg.side * (0 if leg.kind == "limit" else fills.slip[k])  # px is already on the ask / bid
    if k <= busy_until or k > order.exit:
        return None
    levels = resolve(m, order.day, leg)
    if levels is None:
        return None
    stop, tp = levels
    side = leg.side
    risk = side * (entry - stop)
    if risk <= 0:
        return None
    hs = fills.hs[k:order.exit + 1]
    exit_side = -side  # a long exits by selling on the bid, a short by buying on the ask
    hi = m.h[k:order.exit + 1] + exit_side * hs
    lo = m.l[k:order.exit + 1] + exit_side * hs
    op = m.o[k:order.exit + 1] + exit_side * hs
    adverse = lo if side > 0 else hi
    s_hit = np.flatnonzero(adverse <= stop if side > 0 else adverse >= stop)
    t_hit = np.array([], dtype=int)
    if tp is not None:
        t_hit = np.flatnonzero(hi > tp if side > 0 else lo < tp)
        if leg.kind != "market":
            t_hit = t_hit[t_hit > 0]
    i_s = int(s_hit[0]) if len(s_hit) else None
    i_t = int(t_hit[0]) if len(t_hit) else None
    if i_s is not None and (i_t is None or i_s <= i_t):
        j = k + i_s
        gap = op[i_s] if i_s > 0 else stop
        xp = min(gap, stop) if side > 0 else max(gap, stop)
        exit_px, reason = xp - side * fills.slip[j], "SL"
        last = i_s
    elif i_t is not None:
        j = k + i_t
        exit_px, reason, last = tp, "TP", i_t
    else:
        j = order.exit
        exit_px, reason, last = m.c[j] - side * (fills.hs[j] + fills.slip[j]), "TIME", order.exit - k
    r = (side * (exit_px - entry) - fills.comm) / risk
    worst_px = adverse[:last + 1].min() if side > 0 else adverse[:last + 1].max()
    if reason == "SL":
        worst_px = max(worst_px, stop) if side > 0 else min(worst_px, stop)
    worst = (side * (worst_px - entry) - fills.comm) / risk
    return {"day": str(order.day), "side": side, "entry_t": int(m.t[k]), "exit_t": int(m.t[j]), "k": k, "j": j,
            "r": float(r), "worst": float(min(worst, r)), "reason": reason, "risk": float(risk)}


def run_orders(m, fills, orders):
    trades, busy = [], -1
    for od in sorted(orders, key=lambda o: o.start):
        tr = simulate(m, fills, od, busy)
        if tr is not None:
            trades.append(tr)
            busy = tr["j"]
    return trades


# ---------------------------------------------------------------- families
def session(m, day):
    sp = m.spec
    return m.ts(day, sp.open_hm), m.ts(day, sp.end_hm)


def pre_range(m, day):
    op, _ = session(m, day)
    return m.range_hl(m.ts(day, m.spec.range_hm, -1), op)


def atr_leg(m, ts, side, kind="market", level=0.0):
    atr = m.atr_at(ts)
    return None if atr is None else Leg(side, kind, level, stop_dist=ATR_STOP_K * atr)


def timed(m, day, t_in, t_out, side_fn):
    """Market order at ``t_in`` (side from ``side_fn(k_in)``; 0 = no trade), ATR stop, exit at ``t_out``."""
    k, x = m.entry_at(t_in), m.exit_at(t_out)
    if k is None or x is None or x < k:
        return None
    side = side_fn(k)
    if not side:
        return None
    leg = atr_leg(m, t_in, side)
    return None if leg is None else Order(day, [leg], k, k + 1, x)


def sign(v):
    return int(np.sign(v)) if v == v else 0


def f_s1a(m, day):
    """S1a 5-min ORB (Zarattini-Aziz): first 5 min of the session; trade its direction at the next minute, stop at the
    opposite OR extreme, TP 10R, exit at session end."""
    op, end = session(m, day)
    i, j = m.span(op, op + 5 * MINUTE)
    k, x = m.entry_at(op + 5 * MINUTE), m.exit_at(end)
    if j - i < 3 or k is None or x is None:
        return []
    side = sign(m.c[j - 1] - m.o[i])
    if not side:
        return []
    stop = float(m.l[i:j].min() if side > 0 else m.h[i:j].max())
    return [Order(day, [Leg(side, "market", 0.0, stop=stop, tp_r=10.0)], k, k + 1, x)]


def stop_pair(m, day, hi, lo, a, b, x, stops, tp_r=None, tp_prices=(None, None)):
    i, j = m.span(a, b)
    legs = [Leg(+1, "stop", hi, stop=stops[0], tp_r=tp_r, tp_price=tp_prices[0]),
            Leg(-1, "stop", lo, stop=stops[1], tp_r=tp_r, tp_price=tp_prices[1])]
    return [Order(day, legs, i, j, x)] if j > i else []


def f_s1b(tp_r):
    def fn(m, day):
        """S1b pre-session range breakout: OCO stops at the range high / low from the open for 3 h, stop at the
        opposite side, TP ``tp_r`` R, exit at session end."""
        rng = pre_range(m, day)
        op, end = session(m, day)
        x = m.exit_at(end)
        if rng is None or x is None:
            return []
        hi, lo = rng
        return stop_pair(m, day, hi, lo, op, op + 180 * MINUTE, x, (lo, hi), tp_r=tp_r)
    return fn


def f_s2(m, day):
    """S2 range sweep reversal: within 3 h of the open an M5 bar trades beyond the pre-session range and closes back
    inside -> fade at the next minute, stop at the extreme since the open, TP 2R, exit at session end."""
    rng = pre_range(m, day)
    op, end = session(m, day)
    x = m.exit_at(end)
    if rng is None or x is None:
        return []
    hi, lo = rng
    a, b = np.searchsorted(m.t5, op, "left"), np.searchsorted(m.t5, op + 180 * MINUTE, "left")
    for q in range(a, b):
        side = -1 if (m.h5[q] > hi and m.c5[q] < hi) else (+1 if (m.l5[q] < lo and m.c5[q] > lo) else 0)
        if side:
            k = m.entry_at(int(m.end5[q]))
            if k is None or k > x:
                return []
            i, _ = m.span(op, op)
            stop = float(m.h[i:k].max() if side < 0 else m.l[i:k].min())
            return [Order(day, [Leg(side, "market", 0.0, stop=stop, tp_r=2.0)], k, k + 1, x)]
    return []


def f_s3a(m, day):
    """S3a VWAP 2-sigma fade: session VWAP / sigma (tick volume) from the open; from open+60 min to end-60 min an M5
    close beyond VWAP +- 2 sigma -> fade at the next minute, TP = VWAP at the signal, stop 1 sigma, exit at end."""
    op, end = session(m, day)
    x = m.exit_at(end)
    if x is None:
        return []
    i, _ = m.span(op, op)
    a, b = np.searchsorted(m.t5, op + 60 * MINUTE, "left"), np.searchsorted(m.t5, end - 60 * MINUTE, "left")
    for q in range(a, b):
        _, jq = m.span(op, int(m.end5[q]))
        tp_ = (m.h[i:jq] + m.l[i:jq] + m.c[i:jq]) / 3
        w = m.v[i:jq]
        if jq - i < 30 or w.sum() <= 0:
            continue
        vwap = float((tp_ * w).sum() / w.sum())
        sig = float(math.sqrt(max(((tp_ - vwap) ** 2 * w).sum() / w.sum(), 0.0)))
        if sig <= 0:
            continue
        side = -1 if m.c5[q] > vwap + 2 * sig else (+1 if m.c5[q] < vwap - 2 * sig else 0)
        if side:
            k = m.entry_at(int(m.end5[q]))
            if k is None or k > x:
                return []
            return [Order(day, [Leg(side, "market", 0.0, stop_dist=sig, tp_price=vwap)], k, k + 1, x)]
    return []


def f_s3b(m, day):
    """S3b initial-balance breakout: IB = first 60 min; OCO stops at IB high / low until end-60 min, stop at the IB
    midpoint, TP one IB range beyond the breakout level, exit at session end."""
    op, end = session(m, day)
    x = m.exit_at(end)
    ib = m.range_hl(op, op + 60 * MINUTE)
    if ib is None or x is None:
        return []
    hi, lo = ib
    mid, w = (hi + lo) / 2, hi - lo
    return stop_pair(m, day, hi, lo, op + 60 * MINUTE, end - 60 * MINUTE, x, (mid, mid),
                     tp_prices=(hi + w, lo - w))


def f_l1(direction):
    def fn(m, day):
        """L1 session-open momentum: sign of the first 30 min of the session (London 03:00-03:30 NY; NAS100 09:30-10:00
        ET; closed bars only) traded at the next bar open (+30 min) in that direction (``direction`` = +1) or reversed (-1), ATR stop, exit at session end."""
        op, end = session(m, day)
        i = m.entry_at(op)
        if i is None:
            return []
        # direction from closed bars only (close of the last bar before +30 min vs the session open), entry at the
        # next bar open
        od = timed(m, day, op + 30 * MINUTE, end, lambda k: direction * sign(m.c[k - 1] - m.o[i]) if k - 1 >= i else 0)
        return [od] if od else []
    return fn


def f_l1b(m, day):
    """L1b last-30-min momentum (Baltussen et al.): sign of prior 17:00 NY -> 16:00 NY (NAS100 15:30 ET), i.e. the
    close of the last bar before the entry, traded at the next bar open to 16:30 (NAS100 16:00), ATR stop."""
    prev = m.prev_d1(day)
    if prev is None:
        return []
    p0 = m.exit_at(m.ts(prev.index[-1], (17, 0)))
    t_in = m.ts(day, (15, 30) if m.symbol == "NAS100USD" else (16, 0))
    if p0 is None:
        return []
    od = timed(m, day, t_in, t_in + 30 * MINUTE, lambda k: sign(m.c[k - 1] - m.c[p0]) if k - 1 > p0 else 0)
    return [od] if od else []


def month_ends(days):
    s = pd.Series(days, index=pd.to_datetime(days))
    return set(s.groupby([s.index.year, s.index.month]).max())


def f_l2(kind):
    def fn(m, day):
        """L2 fix reversal (Krohn et al. / Evans): fade the 60-min move into the fix at the fix, exit 2 h later.
        ``london`` 10:00->11:00 NY (enter 11:00, exit 13:00); ``month_end`` same on the last trading day of the
        month only; ``ecb`` 07:15->08:15 NY (enter 08:15, exit 10:15)."""
        if kind == "month_end" and day not in m.month_end_days:
            return []
        fix = (8, 15) if kind == "ecb" else (11, 0)
        t_fix = m.ts(day, fix)
        i = m.entry_at(t_fix - 60 * MINUTE)
        if i is None:
            return []
        od = timed(m, day, t_fix, t_fix + 120 * MINUTE, lambda k: -sign(m.c[k - 1] - m.o[i]))
        return [od] if od else []
    return fn


def f_l3(n):
    def fn(m, day):
        """L3 Crabel NR``n``: previous D1 range the narrowest of the last ``n``; OCO stops at the session open +-
        stretch (10-day mean of min(high-open, open-low)); the opposite level is the stop; exit at session end."""
        hist = m.prev_d1(day, max(n, 10))
        op, end = session(m, day)
        i, x = m.entry_at(op), m.exit_at(end)
        if hist is None or i is None or x is None:
            return []
        rng = (hist["h"] - hist["l"]).to_numpy()
        if rng[-1] > rng[-n:].min() or (rng[-n:] == rng[-1]).sum() > 1:
            return []
        stretch = float(np.minimum(hist["h"] - hist["o"], hist["o"] - hist["l"]).to_numpy()[-10:].mean())
        if stretch <= 0:
            return []
        p0 = float(m.o[i])
        up, dn = p0 + stretch, p0 - stretch
        _, j = m.span(end, end)
        return [Order(day, [Leg(+1, "stop", up, stop=dn), Leg(-1, "stop", dn, stop=up)], i, j, x)]
    return fn


def fractal_hl(m, k, look):
    return float(m.h[max(k - look, 0):k].max()), float(m.l[max(k - look, 0):k].min())


def is_fvg(m, side, i):
    """3-bar FVG completed at the close of bar ``i`` (bars i-2, i-1, i) in the direction of ``side``."""
    return m.h[i] < m.l[i - 2] if side < 0 else m.l[i] > m.h[i - 2]


def sb_fvg_bar(m, side, ks, j, b):
    """Completion bar of the FVG used after an MSS confirmed at the close of bar ``j`` (sweep at ``ks``): the most
    recent FVG completed at or before ``j`` (bars ks..j, all closed); if none, the first one completing in the next 3
    bars, scanned forward (the order is placed after it completes). None if none, or if placement falls outside the
    window."""
    for i in range(j, ks + 1, -1):
        if is_fvg(m, side, i):
            return i if j + 1 < b else None
    for i in range(j + 1, min(j + 4, b - 1)):
        if is_fvg(m, side, i):
            return i
    return None


def silver_bullet(m, day, w_start, sess_end, liq, strict):
    """L4 one window: liquidity sweep -> market-structure shift (M1 close beyond the 10-bar extreme before the sweep)
    -> 3-bar FVG in the displacement leg completed before the order is placed (``sb_fvg_bar``) -> limit at the FVG
    midpoint from the next bar until the window ends; stop at the sweep extreme known at placement; TP 2R; exit at
    session end."""
    a, b = m.span(w_start, w_start + 60 * MINUTE)
    x = m.exit_at(sess_end)
    if b - a < 30 or x is None:
        return None
    for ks in range(a, b):
        for side in (-1, +1):
            if strict:
                if liq is None:
                    return None
                level = liq[0] if side < 0 else liq[1]
            else:
                hh, ll = fractal_hl(m, ks, 30)
                level = hh if side < 0 else ll
            swept = m.h[ks] > level if side < 0 else m.l[ks] < level
            if not swept:
                continue
            pre_hi, pre_lo = fractal_hl(m, ks, 10)
            mss_level = pre_lo if side < 0 else pre_hi
            for j in range(ks + 1, b):
                if (m.c[j] < mss_level) if side < 0 else (m.c[j] > mss_level):
                    i = sb_fvg_bar(m, side, ks, j, b)
                    if i is None:
                        return None
                    fvg = (m.l[i - 2] + m.h[i]) / 2 if side < 0 else (m.h[i - 2] + m.l[i]) / 2
                    place = max(i, j) + 1
                    ext = float(m.h[ks:place].max() if side < 0 else m.l[ks:place].min())
                    return Order(day, [Leg(side, "limit", float(fvg), stop=ext, tp_r=2.0)], place, b, x)
            return None
    return None


def f_l4(strict):
    def fn(m, day):
        """L4 ICT Silver Bullet (03:00-04:00 and 10:00-11:00 NY): strict = sweep of the session reference range
        (Asia range for 03:00; 03:00-10:00 NY range for 10:00), relaxed = sweep of the 30-bar M1 high / low."""
        out = []
        for w, ref_a, end in (((3, 0), "asia", (11, 0)), ((10, 0), (3, 0), (16, 0))):
            ws = m.ts(day, w)
            if ref_a == "asia":
                liq = m.range_hl(m.ts(day, (19, 0), -1), ws)
            else:
                liq = m.range_hl(m.ts(day, ref_a), ws)
            od = silver_bullet(m, day, ws, m.ts(day, end), liq, strict)
            if od:
                out.append(od)
        return out
    return fn


def f_l5(m, day):
    """L5 XAU London-session short bias: short 03:00 NY, exit 11:00 NY, ATR stop."""
    od = timed(m, day, m.ts(day, (3, 0)), m.ts(day, (11, 0)), lambda k: -1)
    return [od] if od else []


def f_l6(kind):
    def fn(m, day):
        """L6 FX time-of-day (Breedon-Ranaldo): ``short_eu`` short 03:00 -> 11:00 NY; ``long_us`` long 08:00 ->
        17:00 NY; ATR stop."""
        a, b, s = ((3, 0), (11, 0), -1) if kind == "short_eu" else ((8, 0), (17, 0), +1)
        od = timed(m, day, m.ts(day, a), m.ts(day, b), lambda k: s)
        return [od] if od else []
    return fn


def f_l7(m, day):
    """L7 Raschke 80-20: previous D1 opened in its top 20 % and closed in its bottom 20 % (-> long; mirror -> short);
    in the session price first trades beyond the previous low (high) on bar kb; from kb + 1 a stop order back at that
    level (live to session end); stop at the session extreme through kb; exit at session end."""
    prev = m.prev_d1(day)
    op, end = session(m, day)
    x = m.exit_at(end)
    if prev is None or x is None:
        return []
    o, h, lo, c = (float(prev[k].iloc[-1]) for k in ("o", "h", "l", "c"))
    rng = h - lo
    if rng <= 0:
        return []
    if o >= lo + 0.8 * rng and c <= lo + 0.2 * rng:
        side, level = +1, lo
    elif o <= lo + 0.2 * rng and c >= lo + 0.8 * rng:
        side, level = -1, h
    else:
        return []
    i, j = m.span(op, end)
    beyond = np.flatnonzero(m.l[i:j] < level if side > 0 else m.h[i:j] > level)
    if not len(beyond):
        return []
    kb = i + int(beyond[0])
    # the stop order is placed at kb + 1 with the structure known then (session extreme through the breach bar kb);
    # it is not amended by later extremes
    ext = float(m.l[i:kb + 1].min() if side > 0 else m.h[i:kb + 1].max())
    return [Order(day, [Leg(side, "stop", level, stop=ext)], kb + 1, j, x)] if kb + 1 < j else []


def variants(symbol):
    """Pre-declared variants per instrument (name -> signal function). L2 not for NAS100; L5 XAU only; L6 FX only."""
    v = {"S1a:ORB5": f_s1a, "S1b:RANGE_BO_TP1": f_s1b(1.0), "S1b:RANGE_BO_TP2": f_s1b(2.0), "S2:SWEEP_REV": f_s2,
         "S3a:VWAP2S_FADE": f_s3a, "S3b:IB_BO": f_s3b, "L1:OPEN30_MOM": f_l1(+1), "L1:OPEN30_REV": f_l1(-1),
         "L1b:LAST30_MOM": f_l1b}
    if symbol != "NAS100USD":
        v.update({"L2:FIX_LDN": f_l2("london"), "L2:FIX_LDN_MONTHEND": f_l2("month_end"), "L2:FIX_ECB": f_l2("ecb")})
    v.update({"L3:NR7": f_l3(7), "L3:NR4": f_l3(4), "L4:SB_STRICT": f_l4(True), "L4:SB_RELAXED": f_l4(False)})
    if symbol == "XAUUSD":
        v["L5:XAU_LDN_SHORT"] = f_l5
    if symbol in FX:
        v.update({"L6:SHORT_EU": f_l6("short_eu"), "L6:LONG_US": f_l6("long_us")})
    v["L7:RASCHKE_80_20"] = f_l7
    return v


def build_orders(m, fn, a, b):
    lo, hi = pd.Timestamp(a).date(), pd.Timestamp(b).date()
    out = []
    for day in m.days:
        if lo <= day < hi:
            out.extend(fn(m, day))
    return out


# ---------------------------------------------------------------- metrics
def months_between(a, b):
    return (pd.Timestamp(b) - pd.Timestamp(a)).days / 30.4375


def metrics(trades, a, b):
    rs = np.array([t["r"] for t in trades], float)
    n = len(rs)
    out = {"trades": n, "tr_mo": round(n / months_between(a, b), 2)}
    if not n:
        return {**out, "avg_r": 0.0, "total_r": 0.0, "pf": 0.0, "win_pct": 0.0, "mtm_dd_r": 0.0, "ci80_low": 0.0,
                "yearly_total_r": {}, "exits": {}}
    gains, losses = rs[rs > 0].sum(), -rs[rs < 0].sum()
    eq = peak = dd = 0.0
    for t in trades:
        dd = max(dd, peak - (eq + t["worst"]))
        eq += t["r"]
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    years = {}
    for t in trades:
        years.setdefault(t["day"][:4], []).append(t["r"])
    exits = {}
    for t in trades:
        exits[t["reason"]] = exits.get(t["reason"], 0) + 1
    rng = np.random.default_rng(BOOT_SEED)
    boots = rng.choice(rs, size=(BOOT_N, n), replace=True).mean(axis=1)  # one trade per day per variant: day blocks
    return {**out, "avg_r": round(float(rs.mean()), 4), "total_r": round(float(rs.sum()), 2),
            "pf": round(float(gains / losses), 3) if losses > 0 else float("inf"),
            "win_pct": round(100 * float((rs > 0).mean()), 1), "mtm_dd_r": round(dd, 2),
            "ci80_low": round(float(np.percentile(boots, 10)), 4),
            "yearly_total_r": {y: round(float(np.sum(v)), 2) for y, v in sorted(years.items())},
            "yearly_avg_r": {y: round(float(np.mean(v)), 4) for y, v in sorted(years.items())}, "exits": exits}


def window(trades, a, b):
    return [t for t in trades if a <= t["day"] < b]


def passes(mt, years=None):
    ok = (mt["avg_r"] >= BAR["avg_r"] and mt["pf"] > BAR["pf"] and mt["mtm_dd_r"] < BAR["dd"]
          and mt["tr_mo"] >= BAR["tr_mo"])
    if years is not None:
        ok = ok and all(mt["yearly_total_r"].get(y, 0) > 0 for y in years)
    return ok


def select(results):
    """Eligible: TRAIN passes the bar and >= 3 of 4 half-year folds have avg R > 0; pick the best mean fold avg R
    (tie: lower DD). Returns (name or None, table)."""
    table = []
    for name, res in results.items():
        folds = [f["avg_r"] for f in res["folds"]]
        pos = sum(1 for f in res["folds"] if f["trades"] and f["avg_r"] > 0)
        eligible = passes(res["train"]) and pos >= MIN_POS_FOLDS
        table.append({"name": name, "fold_mean": round(float(np.mean(folds)), 4), "pos_folds": pos,
                      "eligible": eligible})
    cands = [r for r in table if r["eligible"]]
    if not cands:
        return None, table
    best = max(cands, key=lambda r: (r["fold_mean"], -results[r["name"]]["train"]["mtm_dd_r"]))
    return best["name"], table


# ---------------------------------------------------------------- frozen file
def frozen_repo(symbol):
    return os.path.join(REPO, "replay", f"round8_frozen_{symbol}.json")


def frozen_path(root, symbol):
    return os.path.join(root, f"round8_frozen_{symbol}.json")


def freeze(root, symbol, payload):
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    for p in (frozen_path(root, symbol), frozen_repo(symbol)):
        with open(p, "w", encoding="utf-8") as handle:
            handle.write(text)
    return text


def committed_frozen(symbol):
    try:
        out = subprocess.run(["git", "-C", REPO, "show", f"HEAD:replay/round8_frozen_{symbol}.json"],
                             capture_output=True, text=True, check=False)
    except OSError:
        return None
    return out.stdout if out.returncode == 0 else None


def load_frozen(root, symbol):
    """Fails closed: the file must equal the committed ``replay/round8_frozen_<SYM>.json`` and name a known variant."""
    with open(frozen_path(root, symbol), encoding="utf-8") as handle:
        text = handle.read()
    committed = committed_frozen(symbol)
    if committed is None:
        raise RuntimeError(f"cannot verify the frozen file: replay/round8_frozen_{symbol}.json not in HEAD")
    if committed != text:
        raise RuntimeError(f"frozen file differs from the committed replay/round8_frozen_{symbol}.json")
    payload = json.loads(text)
    if payload.get("symbol") != symbol or "variant" not in payload:
        raise ValueError("frozen file incomplete or for another symbol")
    if payload["variant"] is not None and payload["variant"] not in variants(symbol):
        raise ValueError(f"unknown frozen variant {payload['variant']}")
    return payload


# ---------------------------------------------------------------- runners
def market(root, symbol, end=None):
    df = load_m1(os.path.join(root, "data_m1", f"{symbol}_1m.csv"), end=end)
    if end is not None:
        assert df.index[-1] < pd.Timestamp(end, tz=NY), "data after the cutoff"
    return Market(df, symbol)


def run_variant(m, symbol, fn, a, b, models):
    orders = build_orders(m, fn, a, b)
    return {mod: run_orders(m, Fills(COSTS[mod], symbol, m.spread), orders) for mod in models}


def selection(root, symbol, log=print):
    m = market(root, symbol, end=TRAIN[1])
    a, b = TRAIN
    results = {}
    for name, fn in variants(symbol).items():
        runs = run_variant(m, symbol, fn, a, b, models_for(symbol))
        res = {mod: metrics(tr, a, b) for mod, tr in runs.items()}
        main = runs[SPECS[symbol].main]
        results[name] = {"train": res[SPECS[symbol].main], "models": res,
                         "folds": [metrics(window(main, fa, fb), fa, fb) for fa, fb in FOLDS]}
        t = results[name]["train"]
        log(f"{symbol} {name}: n={t['trades']} avg={t['avg_r']:+.3f} PF={t['pf']} DD={t['mtm_dd_r']} "
            f"tr/mo={t['tr_mo']}")
    best, table = select(results)
    return results, best, table, str(m.d1_keys[-1])


def holdout(root, symbol, frozen):
    m = market(root, symbol)
    a, b = HOLDOUT
    fn = variants(symbol)[frozen["variant"]]
    runs = run_variant(m, symbol, fn, a, b, models_for(symbol))
    res = {mod: metrics(tr, a, b) for mod, tr in runs.items()}
    return res, runs


def buy_and_hold(root, symbol, a, b):
    df = load_m1(os.path.join(root, "data_m1", f"{symbol}_1m.csv"))
    df = df[(df.index >= pd.Timestamp(a, tz=NY)) & (df.index < pd.Timestamp(b, tz=NY))]
    c = df["Close"]
    ret = 100 * (c.iloc[-1] / df["Open"].iloc[0] - 1)
    dd = 100 * float((1 - c / c.cummax()).max())
    return {"pct": round(float(ret), 1), "max_dd_pct": round(dd, 1)}


def save(root, name, payload):
    os.makedirs(os.path.join(root, "round8"), exist_ok=True)
    with open(os.path.join(root, "round8", name), "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1, default=float)


def load(root, name):
    p = os.path.join(root, "round8", name)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as handle:
        return json.load(handle)


def report_append(root, text):
    with open(os.path.join(root, "report_round8.md"), "a", encoding="utf-8") as handle:
        handle.write(text.rstrip() + "\n\n")


def fmt_row(name, mt):
    yt = " ".join(f"{y}:{v:+.1f}" for y, v in mt.get("yearly_total_r", {}).items())
    return (f"| {name} | {mt['trades']} | {mt['tr_mo']} | {mt['avg_r']:+.3f} | {mt['total_r']:+.1f} | {mt['pf']} | "
            f"{mt['mtm_dd_r']} | {mt['win_pct']} | {mt['ci80_low']:+.3f} | {yt} |")


HEAD = ("| variant | trades | tr/mo | avg R | total R | PF | MTM DD R | win % | CI80 low | yearly total R |\n"
        "|---|---|---|---|---|---|---|---|---|---|")


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--root", default=ROOT)
    p.add_argument("--symbol", choices=SYMBOLS)
    p.add_argument("--sections", required=True)
    args = p.parse_args(argv)
    for sec in args.sections.split(","):
        SECTIONS[sec](args)


def sec_select(args):
    sym = args.symbol
    results, best, table, last = selection(args.root, sym)
    save(args.root, f"{sym}_select.json", {"results": results, "table": table, "best": best, "last_d1": last})
    payload = {"symbol": sym, "variant": best, "main_cost": SPECS[sym].main, "train": list(TRAIN),
               "holdout": list(HOLDOUT), "selection_last_d1": last,
               "train_metrics": results[best]["train"] if best else None}
    freeze(args.root, sym, payload)
    rows = [fmt_row(r["name"] + (" **[frozen]**" if r["name"] == best else ""), results[r["name"]]["train"])
            for r in table]
    folds = "\n".join(f"| {r['name']} | {r['fold_mean']:+.3f} | {r['pos_folds']}/4 | {'yes' if r['eligible'] else 'no'}"
                      f" | " + " | ".join(f"{results[r['name']]['models'][mod]['avg_r']:+.3f}"
                                         for mod in models_for(sym)[1:]) + " |" for r in table)
    report_append(args.root, f"""## {sym} - selection (TRAIN {TRAIN[0]}..2024-12-31, {SPECS[sym].main}; data sliced at
the cutoff, last D1 {last})

{HEAD}
{chr(10).join(rows)}

| variant | mean fold avg R (2023H1..2024H2) | positive folds | eligible | {' | '.join(models_for(sym)[1:])} avg R |
|---|---|---|---|---|---|
{folds}

**Frozen: {best or 'NONE (no variant meets the bar on TRAIN with >= 3/4 positive folds) -> FLAT, no holdout run'}**
(`replay/round8_frozen_{sym}.json`, committed before the holdout).""")


def sec_holdout(args):
    sym = args.symbol
    frozen = load_frozen(args.root, sym)
    a, b = HOLDOUT
    out = {"symbol": sym, "variant": frozen["variant"], "holdout": f"{a}..2026-10-19", "cost_model": SPECS[sym].main}
    sel = load(args.root, f"{sym}_select.json")
    out["train_summary"] = {k: {"n": v["train"]["trades"], "avg_r": v["train"]["avg_r"], "pf": v["train"]["pf"]}
                            for k, v in sel["results"].items()} if sel else None
    if frozen["variant"] is None:
        out.update({"verdict": "FAIL (no TRAIN candidate; FLAT, holdout not run)"})
        save(args.root, f"{sym}.json", out)
        report_append(args.root, f"## {sym} - holdout\n\nNo frozen variant (FLAT): holdout not run. **FAIL**.")
        return
    res, runs = holdout(args.root, sym, frozen)
    mt = res[SPECS[sym].main]
    years = sorted(mt["yearly_total_r"]) or ["2025", "2026"]
    ok = passes(mt, years=["2025", "2026"])
    out.update({"metrics": res, "verdict": "PASS" if ok else "FAIL"})
    if sym in ("XAUUSD", "NAS100USD"):
        out["buy_hold"] = buy_and_hold(args.root, sym, a, b)
    save(args.root, f"{sym}.json", out)
    save(args.root, f"{sym}_holdout_trades.json", runs[SPECS[sym].main])
    rows = "\n".join(fmt_row(mod, r) for mod, r in res.items())
    bh = f"\nBuy-and-hold same window: {out['buy_hold']}." if "buy_hold" in out else ""
    report_append(args.root, f"""## {sym} - holdout (run once, frozen {frozen['variant']}, {a}..2026-10-19)

{HEAD.replace('variant', 'cost model')}
{rows}

Years: {years}. Exit mix ({SPECS[sym].main}): {mt['exits']}.{bh}
**{out['verdict']}** (bar: avg R >= +0.08, PF > 1.15, MTM DD < 12R, >= 3 tr/mo, 2025 and 2026 YTD both positive).""")


SECTIONS = {"select": sec_select, "holdout": sec_holdout}


if __name__ == "__main__":
    main()
