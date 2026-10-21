"""EURUSD entry search, round 3 (REPLAY ONLY): JT_SMC modules alone / in pairs (R3a) and four new families (R3b).

Every definition is fixed before any run (no tuning) and reuses ``replay.entry_search`` (bars, mirrored views for
shorts, H4/D1 resampled from closed 1h bars, 3-bar pivots confirmed by the next bar, Asian range, sweeps, OBs,
ADR20, ATR14(H1)). No-lookahead contract as in ``entry_search``: a decision at the close ``t`` of a 5m (or 1h) bar
uses only bars ended by ``t`` and fills at the open of the 5m bar starting at ``t``.

R3a modules are per-5m-bar arrays on each view (long rules; shorts = the mirrored view):
``cond[k]`` = the module's setup is in force at the close of bar k, ``trig[k]`` = it fires an entry there (always a
subset of ``cond``), ``struct[k]`` = its structural invalidation (view price), ``idea[k]`` = one-entry identity.
A pair / ALL fires at bar k when one member triggers and every other member's ``cond`` holds at k (AND); the stop
structure / idea are those of the triggering member (the lowest structure if several trigger).

Nothing here is wired into the runtime.
"""
import math
from dataclasses import dataclass

import numpy as np

from replay import entry_search as es

HOUR = es.HOUR
LONG = es.LONG

# ---------------------------------------------------------------- fixed parameters (declared before any run)
MOD_WINDOW = (3 * 60, 12 * 60)  # module entries: decision bar START clock in [03:00, 12:00) ET
M1_BOS_BARS = 12  # M1: the 5m BOS must come within 12 bars (1 h) of the close back over the level
M4_WINDOW = (3 * 60, 11 * 60)  # M4: London/NY sweep of the Asian range (bar start clock)
FVG_LIFE = 24 * HOUR  # M2: a 1h FVG is tradable for 24 h after its 3rd candle closes
OB15_LIFE = 24 * HOUR  # M5: a 15m OB is tradable for 24 h after its BOS bar closes
OB1_LIFE = 48 * HOUR  # M5b: a 1h OB counts as overlap for 48 h after its BOS bar closes
FIB_ZONES = es.JT_FIB_ZONES  # M3: (0.50, 0.60), (0.75, 0.85)
MODULES = ("M1", "M2", "M3", "M4", "M5", "M5b")
F1_LONDON = ((3 * 60, 3 * 60 + 30), (3 * 60 + 30, 8 * 60))  # (range window, breakout window), bar start clock
F1_NY = ((9 * 60 + 30, 10 * 60), (10 * 60, 13 * 60))
ORB_BARS = 6  # the 30-minute range needs all six 5m bars
F2_WINDOW = (3 * 60, 12 * 60)
F2_BEYOND_ADR = 0.10
VWAP_SESSIONS = ((3 * 60, 8 * 60), (8 * 60, 12 * 60))  # London anchor 03:00 ET, NY anchor 08:00 ET
VWAP_RUN = 6
F4_BODY_ATR = 1.5


@dataclass
class Module:
    cond: np.ndarray
    trig: np.ndarray
    struct: np.ndarray
    idea: np.ndarray

    @classmethod
    def empty(cls, n):
        return cls(np.zeros(n, bool), np.zeros(n, bool), np.full(n, np.nan), np.full(n, None, dtype=object))


def h4_bull(view):
    """Per 5m bar (at its close): the last closed H4 bar's structure trend is bullish (view direction)."""
    i4 = np.searchsorted(view.h4.end, view.m5.end, side="right") - 1
    return (i4 >= 0) & (view.trend4[np.maximum(i4, 0)] > 0)


def in_window(ctx, rng):
    return (ctx.et_min >= rng[0]) & (ctx.et_min < rng[1])


def _last_index(ctx, day, rng):
    idx = ctx.window(day, *rng)
    return int(idx[-1]) if len(idx) else -1


def _hold(mod, b, k0, last, extreme, idea, struct):
    """cond from k0 while no 5m close below ``extreme``, up to bar ``last`` (inclusive)."""
    for k in range(k0, last + 1):
        if b.c[k] < extreme:
            break
        mod.cond[k] = True
        if mod.idea[k] is None:
            mod.struct[k], mod.idea[k] = struct, idea


# ---------------------------------------------------------------- R3a modules (long rules on one view)
def m1(ctx, view):
    """M1 liquidity sweep -> reversal: Asian low / PDL / 1h swing lows of the last 5 days, first traded through in
    03:00-11:00 ET and closed back over within 6 bars (``es._sweeps``, JT definition); trigger = the first 5m BOS up
    (close above the last confirmed 5m swing high) from the close-back bar within 12 bars, before any close below the
    sweep extreme. In force from the trigger until a close below the extreme or 12:00 ET. Structure = extreme."""
    b = view.m5
    mod = Module.empty(len(b))
    asian = es.session_ranges(ctx, view, es.ASIAN)
    for day in sorted(ctx.days):
        levels, win = es._levels(ctx, view, day, asian)
        if not levels:
            continue
        last = _last_index(ctx, day, MOD_WINDOW)
        events = es._sweeps(ctx, view, levels, win)
        seen = set()
        for c in sorted(events):
            for _f, x, extreme, _kind in events[c]:
                if x in seen:
                    continue  # one idea per sweep extreme bar
                seen.add(x)
                for k in range(c, min(c + M1_BOS_BARS, len(b) - 1, last) + 1):
                    if not es._contiguous(b, c, k) or b.c[k] < extreme:
                        break
                    if view.bos5[k]:
                        idea = f"M1:{view.side}:{es._stamp(int(b.start[x]))}"
                        if not mod.trig[k] or extreme < mod.struct[k]:
                            mod.struct[k], mod.idea[k] = extreme, idea
                        mod.trig[k] = True
                        _hold(mod, b, k, last, extreme, idea, extreme)
                        break
    return mod


def m4(ctx, view):
    """M4 Asian accumulation -> London/NY manipulation -> distribution: in 03:00-11:00 ET (bar start) price trades
    below the Asian low (19:00-03:00 ET); trigger = the first later-or-same 5m close back inside the range AND a 5m BOS
    up. One idea per side per day; in force from the trigger until a close below the sweep extreme or 12:00 ET.
    Structure = the sweep extreme (lowest low since the first trade-through)."""
    b = view.m5
    mod = Module.empty(len(b))
    for day, (lo, hi) in es.session_ranges(ctx, view, es.ASIAN).items():
        extreme = None
        for k in ctx.window(day, *M4_WINDOW):
            k = int(k)
            if extreme is None:
                if b.l[k] >= lo:
                    continue
                extreme = b.l[k]
            extreme = min(extreme, b.l[k])
            if lo < b.c[k] < hi and view.bos5[k]:
                idea = f"M4:{view.side}:{day}"
                mod.trig[k], mod.struct[k], mod.idea[k] = True, extreme, idea
                _hold(mod, b, k, _last_index(ctx, day, MOD_WINDOW), extreme, idea, extreme)
                break
    return mod


def _zones_overwrite(b, starts, lives, cut_low, lo):
    """For zones in time order (later overwrite earlier): per 5m bar the most recent zone in force = from the first
    bar starting at/after its availability until ``lives`` later, ended (exclusive) by the first bar whose low
    (``cut_low``: low <= bottom) or close (otherwise: close < bottom) breaks the zone bottom. Returns the zone index
    per bar (-1 = none)."""
    n = len(b)
    owner = np.full(n, -1)
    for z in range(len(starts)):
        s = int(np.searchsorted(b.start, starts[z], side="left"))
        e = int(np.searchsorted(b.end, starts[z] + lives, side="right"))
        if s >= e:
            continue
        probe = b.l[s:e] if cut_low else b.c[s:e]
        broken = np.flatnonzero(probe <= lo[z] if cut_low else probe < lo[z])
        stop = s + int(broken[0]) if len(broken) else e
        owner[s:stop] = z
    return owner


def m2(ctx, view):
    """M2 1h FVG retrace with the H4 trend: bullish 1h FVG = 3 consecutive 1h bars a, b, c with low[c] > high[a]
    (zone [high[a], low[c]]), usable from the close of c for 24 h while unfilled (no 5m low <= high[a]; the filling
    bar is excluded). In force at bar k when the H4 trend is bullish and the bar trades into the most recent such
    zone (low <= low[c]); trigger = a bullish 5m close inside the zone. Structure = high[a] (zone bottom)."""
    b, h = view.m5, view.h1
    idx = [m for m in range(2, len(h)) if h.start[m] - h.start[m - 2] == 2 * HOUR and h.l[m] > h.h[m - 2]]
    mod = Module.empty(len(b))
    if not idx:
        return mod
    avail = np.array([h.end[m] for m in idx], dtype=np.int64)
    bottom = np.array([h.h[m - 2] for m in idx])
    top = np.array([h.l[m] for m in idx])
    owner = _zones_overwrite(b, avail, FVG_LIFE, True, bottom)
    has = owner >= 0
    z = np.maximum(owner, 0)
    bot, tp = np.where(has, bottom[z], np.nan), np.where(has, top[z], np.nan)
    with np.errstate(invalid="ignore"):
        mod.cond = has & h4_bull(view) & (b.l <= tp)
        mod.trig = mod.cond & (b.c > b.o) & (b.c >= bot) & (b.c <= tp)
    mod.struct = np.where(mod.cond, bot, np.nan)
    for k in np.flatnonzero(mod.cond):
        mod.idea[k] = f"M2:{view.side}:{es._stamp(int(avail[owner[k]]))}"
    return mod


def m3(ctx, view):
    """M3 H4 trend + fib discount of the last H1 impulse: impulse = the most recently confirmed 1h swing high H and
    the last confirmed 1h swing low L before its pivot (H > L); valid from the 5m bar after H's confirmation until the
    next swing high is confirmed, a 5m high above H or a 5m low below L (that bar excluded). In force at bar k when
    the H4 trend is bullish and the close retraces 50-60% or 75-85% of L->H; trigger = a bullish 5m close there.
    Structure = L (100% retracement)."""
    b, h = view.m5, view.h1
    mod = Module.empty(len(b))
    bull = h4_bull(view)
    lows = view.pl1
    low_pivots = np.array([j for j, _ in lows], dtype=int)
    highs = view.ph1
    confirm = [int(h.end[j + 1]) for j, _ in highs]
    for q, (jh, hp) in enumerate(highs):
        p = int(np.searchsorted(low_pivots, jh, side="left")) - 1
        if p < 0 or lows[p][1] >= hp:
            continue
        lp = lows[p][1]
        s = int(np.searchsorted(b.start, confirm[q], side="left"))
        e = int(np.searchsorted(b.start, confirm[q + 1], side="left")) if q + 1 < len(highs) else len(b)
        if s >= e:
            continue
        broken = np.flatnonzero((b.h[s:e] > hp) | (b.l[s:e] < lp))
        e = s + int(broken[0]) if len(broken) else e
        span = hp - lp
        retr = (hp - b.c[s:e]) / span
        zone = np.zeros(e - s, bool)
        for z_lo, z_hi in FIB_ZONES:
            zone |= (retr >= z_lo) & (retr <= z_hi)
        ks = s + np.flatnonzero(zone & bull[s:e])
        mod.cond[ks] = True
        mod.struct[ks] = lp
        idea = f"M3:{view.side}:{es._stamp(int(h.start[jh]))}"
        for k in ks:
            mod.idea[k] = idea
    mod.trig = mod.cond & (b.c > b.o)
    return mod


def _obs(bars, bos):
    """Bullish OBs: for each BOS bar j, the last bearish candle in the 12 bars before it: (available at end[j], low,
    high, id start)."""
    out = []
    for j in np.flatnonzero(bos):
        ob = es._ob_back(bars, int(j), 0)
        if ob is not None:
            out.append((int(bars.end[j]), ob[0], ob[1], int(bars.start[j])))
    return out


def m5(ctx, view, overlap_1h=False):
    """M5 15m order block in the H4 trend + 5m trigger: OB = last bearish 15m candle in the 12 bars before a 15m BOS
    up, usable from the BOS bar's close for 24 h until a 5m close below the OB low (that bar excluded); the most
    recent OB in force counts. In force at bar k when the H4 trend is bullish and the bar trades into the OB
    (low <= OB high); trigger = a bullish 5m close inside the OB (OB low <= close <= OB high). Structure = OB low.
    M5b (``overlap_1h``): additionally a 1h OB (same rule on 1h bars, 48 h life, ended by a 5m close below its low)
    in force at k overlapping the 15m OB."""
    b = view.m5
    obs = _obs(view.m15, view.bos15)
    mod = Module.empty(len(b))
    if not obs:
        return mod
    avail, low, high, ids = (np.array([o[i] for o in obs]) for i in range(4))
    owner = _zones_overwrite(b, avail, OB15_LIFE, False, low)
    has = owner >= 0
    z = np.maximum(owner, 0)
    lo, hi = np.where(has, low[z], np.nan), np.where(has, high[z], np.nan)
    with np.errstate(invalid="ignore"):
        cond = has & h4_bull(view) & (b.l <= hi)
        if overlap_1h:
            obs1 = _obs(view.h1, view.bos1)
            if obs1:
                a1, l1, h1, _ = (np.array([o[i] for o in obs1]) for i in range(4))
                own1 = _zones_overwrite(b, a1, OB1_LIFE, False, l1)
                z1 = np.maximum(own1, 0)
                cond &= (own1 >= 0) & (l1[z1] <= hi) & (h1[z1] >= lo)
            else:
                cond &= False
        mod.cond = cond
        mod.trig = cond & (b.c > b.o) & (b.c >= lo) & (b.c <= hi)
    mod.struct = np.where(cond, lo, np.nan)
    tag = "M5b" if overlap_1h else "M5"
    for k in np.flatnonzero(cond):
        mod.idea[k] = f"{tag}:{view.side}:{es._stamp(int(ids[owner[k]]))}"
    return mod


BUILDERS = {"M1": m1, "M2": m2, "M3": m3, "M4": m4, "M5": m5, "M5b": lambda ctx, view: m5(ctx, view, True)}


def modules(ctx, names=MODULES):
    """{name: {view sign: Module}} on both views."""
    return {name: {s: BUILDERS[name](ctx, v) for s, v in ctx.views.items()} for name in names}


def combine(ctx, mods, members, label):
    """Candidates of the AND of ``members`` (one member alone = the module): fires at bar k (decision bar start in
    [03:00, 12:00) ET) when a member triggers and every other member is in force; structure / idea of the trigger."""
    window = in_window(ctx, MOD_WINDOW)
    out = []
    for sign, view in ctx.views.items():
        b = view.m5
        parts = [mods[m][sign] for m in members]
        allc = np.logical_and.reduce([p.cond for p in parts])
        fire = window & allc & np.logical_or.reduce([p.trig for p in parts])
        for k in np.flatnonzero(fire):
            trig = [p for p in parts if p.trig[k]]
            best = min(trig, key=lambda p: p.struct[k])
            out.append(es.Candidate(int(b.end[k]), view.side, view.price(float(best.struct[k])), label,
                                    f"{label}:{best.idea[k]}"))
    return sorted(out, key=lambda c: (c.t, c.side))


# ---------------------------------------------------------------- R3b families (long rules on one view)
def orb(ctx, view, ranges, family):
    """F1 / F1b opening-range breakout: range = the six 5m bars starting in the range window (all present); first 5m
    close above the range high with bar start in the breakout window -> long. Structure = range low (other side).
    One idea per side per day."""
    (r_lo, r_hi), (w_lo, w_hi) = ranges
    b = view.m5
    out = []
    for day in sorted(ctx.days):
        rng = ctx.window(day, r_lo, r_hi)
        if len(rng) != ORB_BARS:
            continue
        top, bottom = float(b.h[rng].max()), float(b.l[rng].min())
        for k in ctx.window(day, w_lo, w_hi):
            if b.c[k] > top:
                out.append(es.Candidate(int(b.end[k]), view.side, view.price(bottom), family,
                                        f"{family}:{view.side}:{day}"))
                break
    return out


def f2(ctx, view):
    """F2 previous-day range mean reversion (fade toward the range): in 03:00-12:00 ET (bar start) a 5m low below
    PDL - 0.1 x ADR20, then the first 5m close back above PDL -> long. PDL/ADR20 from completed trading days before
    the window (``View.prior_day``, ``Context.adr_at``). Structure = the lowest low since the excursion. One idea per
    side per day."""
    b = view.m5
    out = []
    for day in sorted(ctx.days):
        win = ctx.window(day, *F2_WINDOW)
        if not len(win):
            continue
        t0 = int(b.start[win[0]])
        p, adr = view.prior_day(t0), ctx.adr_at(t0)
        if p < 0 or not math.isfinite(adr):
            continue
        pdl = float(view.d1.l[p])
        level = pdl - F2_BEYOND_ADR * adr * es.PIP
        extreme = None
        for k in win:
            if extreme is None:
                if b.l[k] >= level:
                    continue
                extreme = b.l[k]
            extreme = min(extreme, b.l[k])
            if b.c[k] > pdl:
                out.append(es.Candidate(int(b.end[k]), view.side, view.price(float(extreme)), "F2",
                                        f"F2:{view.side}:{day}"))
                break
    return out


def session_vwap(ctx, sessions=VWAP_SESSIONS):
    """Per 5m bar (at its close, real prices): VWAP of the bars of the same ET date and session from the anchor up to
    and including this bar, typical price (H+L+C)/3 weighted by tick volume; NaN outside the sessions or with zero
    cumulative volume. Also the session label per bar (-1 outside)."""
    f = ctx.frames["5m"]
    typical = ((f["High"] + f["Low"] + f["Close"]) / 3).to_numpy()
    volume = f["Volume"].to_numpy(float) if "Volume" in f else np.full(len(f), np.nan)
    label = np.full(len(f), -1, dtype=np.int64)
    for q, rng in enumerate(sessions):
        mask = in_window(ctx, rng)
        label[mask] = ctx.et_day[mask] * len(sessions) + q
    vwap = np.full(len(f), np.nan)
    for value in np.unique(label[label >= 0]):
        idx = np.flatnonzero(label == value)
        vol = np.cumsum(volume[idx])
        with np.errstate(invalid="ignore", divide="ignore"):
            vwap[idx] = np.where(vol > 0, np.cumsum(typical[idx] * volume[idx]) / vol, np.nan)
    return vwap, label


def f3(ctx, view, vwap=None, label=None):
    """F3 session VWAP pullback: in a session (London from 03:00 ET until 08:00, NY from 08:00 until 12:00) the
    previous 6 contiguous 5m closes of the session are above their VWAP (trend), then a bar whose low touches the
    VWAP (low <= VWAP at its close) and closes bullish above it -> long. Structure = that bar's low. One idea per
    run of closes above the VWAP (id = the run's first bar)."""
    if vwap is None:
        vwap, label = session_vwap(ctx)
    b = view.m5
    v = view.sign * vwap
    out = []
    with np.errstate(invalid="ignore"):
        above = b.c > v
    for k in range(VWAP_RUN, len(b)):
        if label[k] < 0 or not math.isfinite(v[k]):
            continue
        if k >= VWAP_RUN and label[k - VWAP_RUN] == label[k] and es._contiguous(b, k - VWAP_RUN, k) \
                and above[k - VWAP_RUN:k].all() and b.l[k] <= v[k] and b.c[k] > v[k] and b.c[k] > b.o[k]:
            first = k - VWAP_RUN
            while first - 1 >= 0 and label[first - 1] == label[k] and above[first - 1] \
                    and es._contiguous(b, first - 1, k):
                first -= 1
            out.append(es.Candidate(int(b.end[k]), view.side, view.price(float(b.l[k])), "F3",
                                    f"F3:{view.side}:{es._stamp(int(b.start[first]))}"))
    return out


def f4(ctx, view):
    """F4 momentum after a large H1 candle: a closed bullish 1h bar with body >= 1.5 x ATR14(H1) of the 14 bars
    ending at the previous 1h bar -> long at the open of the 5m bar starting at its close. Structure = the candle's
    midpoint (high+low)/2."""
    h = view.h1
    out = []
    for m in range(1, len(h)):
        atr = ctx.atr1[m - 1]
        if not math.isfinite(atr) or not h.c[m] - h.o[m] >= F4_BODY_ATR * atr:
            continue
        out.append(es.Candidate(int(h.end[m]), view.side, view.price(float((h.h[m] + h.l[m]) / 2)), "F4",
                                f"F4:{view.side}:{es._stamp(int(h.start[m]))}"))
    return out


FAMILIES = ("F1", "F1b", "F2", "F3", "F4")


def family_candidates(ctx, family):
    out = []
    vwap = session_vwap(ctx) if family == "F3" else (None, None)
    for view in ctx.views.values():
        if family == "F1":
            out += orb(ctx, view, F1_LONDON, "F1")
        elif family == "F1b":
            out += orb(ctx, view, F1_NY, "F1b")
        elif family == "F2":
            out += f2(ctx, view)
        elif family == "F3":
            out += f3(ctx, view, *vwap)
        elif family == "F4":
            out += f4(ctx, view)
    return sorted(out, key=lambda c: (c.t, c.side))
