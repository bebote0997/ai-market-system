"""EURUSD entry search, round 2 (REPLAY ONLY): JT_SMC ablation, 25-30 pip stops, multiple-testing check.

Step 1: JT_SMC with a predeclared grid of relaxations (R1-R6, 12 combos) x two SL models (15-30 / 25-30 pips), TP 2R.
Step 2: SL 25-30 pips, TP 2R for E2, E3 and the two best JT combos by in-sample avg R; cost per R.
Step 3: total variants tried (round 1 + round 2), a strict IS/OOS bar, a 3-fold walk-forward for any passer, G3/G4/G5.

Everything reuses ``replay.entry_search`` (features, sweeps, OBs, split + purge, metrics tables) and
``replay.intraday_backtest`` (causal spread + 0.2 pip slippage, same-bar SL first, I2 2nd-session hard close, Friday
17:00 NY close, MTM drawdown, day-block bootstrap, gates). One position at a time, 2R/day realised halt. Nothing here
is wired into the runtime.

Run: ``python -m replay.round2 --data /workspace/backtest/data --out /workspace/backtest``. Step 3 recomputes the 60
round-1 variants from their trades (full precision) for the strict bar and the walk-forward.
Market data and outputs live outside the repository and are never committed.
"""
import argparse
import math
import os
from collections import Counter, defaultdict
from dataclasses import dataclass, fields
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from replay import entry_search as es
from replay import intraday_backtest as bt

PIP = bt.PIP
LONG = es.LONG

# ---------------------------------------------------------------- predeclared grid (fixed before any run)
ROUND1_VARIANTS = 60
GRID_NAMES = ("R1", "R2", "R4", "R5", "R1+R2", "R1+R4", "R1+R5", "R1+R2+R4", "R1+R2+R5", "R1+R2+R4+R5",
              "R1+R3+R4+R5", "R1+R2+R4+R5+R6")
SL_MODELS = {"SL15": 15.0, "SL25": 25.0}  # minimum stop (pips): narrower structural stops are widened to it
TP_R = 2.0
R4_ZONES = ((0.50, 0.85),)
STAGES = ("sweep", "trend", "ob15", "trigger", "fvg1h", "news", "candidate")
TARGET_TRADES_PER_MONTH = 8.0
STRICT_AVG_R = 0.10
STRICT_MIN_OOS = 30
FOLDS = 3


@dataclass(frozen=True)
class Relax:
    """R1 no unfilled-1h-FVG requirement; R2 trend from H4 only; R3 trend from H1 only; R4 one fib zone 50-85%;
    R5 limit order at the 15m OB proximal edge instead of the 5m trigger; R6 sweeps of PDL/Asian low only, untouched
    requirement dropped."""
    r1: bool = False
    r2: bool = False
    r3: bool = False
    r4: bool = False
    r5: bool = False
    r6: bool = False

    @classmethod
    def parse(cls, name):
        on = set(name.split("+")) if name else set()
        unknown = on - {f"R{i}" for i in range(1, 7)}
        if unknown or ("R2" in on and "R3" in on):
            raise ValueError(f"bad combo {name!r}")
        return cls(**{f.name: f.name.upper() in on for f in fields(cls)})

    @property
    def name(self):
        return "+".join(f.name.upper() for f in fields(self) if getattr(self, f.name)) or "base"


GRID = tuple(Relax.parse(n) for n in GRID_NAMES)


def variant_name(relax, sl):
    return f"JT[{relax.name}]:2R:{sl}"


@dataclass(frozen=True)
class LimitCandidate(es.Candidate):
    """R5: ``t`` = start of the 5m bar that touches the working limit; ``limit`` = limit price (real)."""
    limit: float = None


# ---------------------------------------------------------------- JT_SMC with relaxations
def relaxed_levels(ctx, view, day, asian, relax):
    """Sell-side levels (view lows) and the JT window. R6: Asian low and PDL only, each scanned from the window start
    (a trade-through earlier in the day no longer disqualifies the level)."""
    if not relax.r6:
        return es._levels(ctx, view, day, asian)
    b = view.m5
    win = ctx.window(day, *es.JT_WINDOW)
    if not len(win):
        return [], win
    out = []
    if day in asian:
        out.append((asian[day][0], int(win[0]), "ASIAN"))
    p = view.prior_day(int(b.start[win[0]]))
    if p >= 0:
        out.append((float(view.d1.l[p]), int(win[0]), "PDL"))
    return out, win


def fvg_unfilled(view, t, k):
    """A 1h FVG whose 3rd candle closed in the last 24 h and that no 5m low has filled up to bar ``k``."""
    b = view.m5
    recent = (view.fvg_end > t - es.JT_FVG_HOURS * es.HOUR) & (view.fvg_end <= t)
    for end, bottom in zip(view.fvg_end[recent], view.fvg_bottom[recent]):
        s = int(np.searchsorted(b.start, end))
        if b.l[s:k + 1].min(initial=math.inf) > bottom:
            return True
    return False


def in_zone(retr, zones):
    return next((z for z in zones if z[0] <= retr <= z[1]), None)


def trend_ok(view, t, relax):
    """(ok, end reason when not ok) at the sweep confirmation ``t``."""
    i1, i4 = view.h1.asof(t), view.h4.asof(t)
    if relax.r2:
        return (i4 >= 0 and view.trend4[i4] > 0), "trend: H4 not bullish (R2)"
    if relax.r3:
        return (i1 >= 0 and view.trend1[i1] > 0), "trend: H1 not bullish (R3)"
    if i1 >= 0 and i4 >= 0 and view.trend1[i1] > 0 and view.trend4[i4] > 0:
        return True, ""
    against = i1 >= 0 and i4 >= 0 and view.trend1[i1] < 0 and view.trend4[i4] < 0
    return False, ("trend: sweep in the trend direction (H1+H4 opposite)" if against
                   else "trend: H1 and H4 disagree / neutral")


def jt_relaxed(ctx, view, relax, news_days, funnel, half_spread, since=0):
    """JT_SMC long rules on one view (``es.jt_candidates`` with ``require_conf=False``) under ``relax``.
    ``half_spread(k)`` = causal half spread (price) for a fill on 5m bar k (R5 touch test). ``funnel`` counts ideas
    confirmed at or after ``since`` reaching each of ``STAGES`` plus their end reasons."""
    b, m15 = view.m5, view.m15
    zones = R4_ZONES if relax.r4 else es.JT_FIB_ZONES
    asian = es.session_ranges(ctx, view, es.ASIAN)
    out = []
    for day in sorted(ctx.days):
        levels, win = relaxed_levels(ctx, view, day, asian, relax)
        if not levels:
            continue
        events = es._sweeps(ctx, view, levels, win)
        if not events:
            continue
        book = es.SweepBook()
        order = None  # R5: limit (view price) working on this bar, placed at the previous close
        for k in ctx.window(day, es.JT_WINDOW[0], es.JT_EXPIRY):
            k = int(k)
            idea = book.active
            if order is not None and idea is not None and not idea.dead and b.l[k] + half_spread(k) <= order:
                if es.news_blocked(int(b.start[k]), news_days):
                    idea.blocked = "news"  # order pulled for this bar
                else:
                    idea.stage = len(STAGES) - 1
                    out.append(LimitCandidate(int(b.start[k]), view.side, view.price(idea.ob15[0]), "JT_SMC",
                                              f"JT:{view.side}:{es._stamp(int(b.start[idea.x]))}",
                                              limit=view.price(order)))
                    idea.dead, idea.why = True, "candidate"
            order = None
            for f, x, extreme, kind in events.get(k, ()):
                if book.join(x, extreme, kind):
                    continue
                j15 = max(0, int(np.searchsorted(m15.start, b.start[f], "right")) - 1)
                active = book.open(es.Idea(f, k, x, extreme, [kind], high=float(b.h[f:k + 1].max()), j15=j15,
                                           j15_0=j15))
                ok, why = trend_ok(view, int(b.end[k]), relax)
                if ok:
                    active.stage = 1
                else:
                    active.dead, active.why = True, why
            idea = book.active
            if idea is None or idea.dead:
                continue
            t = int(b.end[k])
            if k > idea.c:
                if b.c[k] < idea.extreme:
                    idea.dead, idea.why = True, "closed_through_sweep_extreme"
                    continue
                idea.high = max(idea.high, float(b.h[k]))
            if not relax.r5 and view.bos5[k] and k > idea.f:
                ob = es._ob_back(b, k, idea.f - 2)
                if ob is not None:
                    idea.obs5.append((ob, k))
            if idea.ob15 is None:
                while idea.j15 < len(m15) and m15.end[idea.j15] <= t:
                    if view.bos15[idea.j15]:
                        idea.ob15 = es._ob_back(m15, idea.j15, idea.j15_0 - 2) or ()
                        break
                    idea.j15 += 1
                if idea.ob15 == ():
                    idea.dead, idea.why = True, "no_15m_ob_before_bos"
                    continue
                if idea.ob15 is None:
                    continue
                idea.stage = max(idea.stage, 2)
            span = idea.high - idea.extreme
            if relax.r5:  # limit at the OB proximal edge, working on the next bar while it sits in a fib zone
                edge = idea.ob15[1]
                if span <= 0 or in_zone((idea.high - edge) / span, zones) is None:
                    continue
                idea.stage = max(idea.stage, 3)
                if not relax.r1 and not fvg_unfilled(view, t, k):
                    idea.blocked = "no_unfilled_1h_fvg"
                    continue
                idea.stage = max(idea.stage, 4)
                order = edge
                continue
            if not b.c[k] > b.o[k]:
                continue
            zone = in_zone((idea.high - b.c[k]) / span, zones) if span > 0 else None
            if zone is None:
                continue
            z_lo, z_hi = idea.high - zone[1] * span, idea.high - zone[0] * span
            if es._trigger_ob(idea.obs5, k, float(b.c[k]), z_lo, z_hi) is None:
                continue
            idea.stage = max(idea.stage, 3)
            if not relax.r1 and not fvg_unfilled(view, t, k):
                idea.blocked = "no_unfilled_1h_fvg"
                continue
            idea.stage = max(idea.stage, 4)
            if es.news_blocked(t, news_days):
                idea.blocked = "news"
                continue
            idea.stage = len(STAGES) - 1
            out.append(es.Candidate(t, view.side, view.price(idea.ob15[0]), "JT_SMC",
                                    f"JT:{view.side}:{es._stamp(int(b.start[idea.x]))}"))
            idea.dead, idea.why = True, "candidate"
        for idea in book.ideas:
            if b.end[idea.c] < since:
                continue
            funnel["sweep"] += 1
            funnel["end:" + idea.why + (f" (last trigger blocked by {idea.blocked})" if idea.blocked and
                                         idea.why != "candidate" else "")] += 1
            for s in range(1, len(STAGES)):
                if idea.stage >= s:
                    funnel[STAGES[s]] += 1
    return out


def jt_relaxed_all(ctx, relax, news_days, half_spread, since=0):
    funnel = Counter()
    out = []
    for view in ctx.views.values():
        out += jt_relaxed(ctx, view, relax, news_days, funnel, half_spread, since)
    return sorted(out, key=lambda c: (c.t, c.side)), funnel


# ---------------------------------------------------------------- exits and portfolio walk
def plan_2r(candidate, fill, atr, min_sl):
    """(stop, target, None) or (None, None, skip reason): structure -/+ 0.2 ATR14(H1) from the fill, narrower than
    ``min_sl`` pips widened to it, wider than 30 skipped; TP 2R. ``min_sl`` 15 = ``es.plan_levels`` FIX2."""
    s = 1 if candidate.side == LONG else -1
    if not math.isfinite(atr):
        return None, None, "atr_unavailable"
    stop = candidate.struct - s * bt.BUFFER_ATR * atr
    distance = s * (fill - stop) / PIP
    if distance <= 0:
        return None, None, "structure_beyond_fill"
    if distance > bt.MAX_SL_PIPS:
        return None, None, "sl_wider_than_30_pips"
    if distance < min_sl:
        stop, distance = fill - s * min_sl * PIP, min_sl
    return stop, fill + s * TP_R * distance * PIP, None


def limit_fill(market, i, side, limit):
    """Fill price of a limit order touched on bar ``i``: the limit, or the bar's open (ask LONG / bid SHORT) when it
    already opens through it. No slippage on limit fills (like TP)."""
    s = 1 if side == LONG else -1
    at_open = float(market.open[i]) + s * market.half_spread_at(i)[0]
    return at_open if s * (at_open - limit) <= 0 else limit


def simulate_limit(market, i, side, fill, stop, target):
    """I2 trade filled by a limit inside bar ``i`` (``bt.simulate_trade`` semantics otherwise). On the fill bar the
    stop is checked (same-bar SL first) but the target is not credited (the intrabar order is unknown)."""
    if i >= market.limit:
        return {"rejected": "no_fill_data"}
    sign = 1 if side == LONG else -1
    (hs, spread_fallback), slip = market.half_spread_at(i), market.slippage
    risk, reward = sign * (fill - stop), sign * (target - fill)
    if risk <= 0 or reward <= 0:
        return {"rejected": "fill_beyond_levels"}
    opened = market.stamp(i)
    try:
        boundary, boundary_reason = bt.i2_exit(opened)
    except ValueError:
        return {"rejected": "fill_outside_session"}
    five = pd.Timedelta(bt.FIVE).value
    limit = market.limit
    horizon = market.cutoff if market.cutoff is not None else market.stamp(len(market.starts) - 1) + bt.FIVE

    fee = market.commission_at(i) / risk  # round-turn commission in R (0 without one)

    def done(j, exit_at, price, reason, detail=None, stale=False):
        marks = sign * (market.close[i:j] - sign * hs - fill) / risk - fee
        return {"marks": marks, "fill_at": opened, "fill": fill, "mid_fill": fill - sign * hs, "stop": stop,
                "target": target, "exit_at": exit_at, "exit": price, "reason": reason, "detail": detail or reason,
                "r": sign * (price - fill) / risk - fee, "risk_pips": risk / PIP, "tp_pips": reward / PIP,
                "exit_index": j, "stale_exit": stale, "spread_pips": 2 * hs / PIP, "spread_fallback": spread_fallback,
                "entry": "LIMIT"}

    j, event_ns = i, bt._ns(boundary)
    while j < limit and int(market.starts[j]) + five <= event_ns:
        o = float(market.open[j]) - sign * hs
        hi, lo = float(market.high[j]) - sign * hs, float(market.low[j]) - sign * hs
        adverse, favourable = (lo, hi) if sign > 0 else (hi, lo)
        if j > i and sign * (o - stop) <= 0:
            return done(j, market.stamp(j), o - sign * slip, bt.SL, "GAP_SL")
        if j > i and sign * (o - target) >= 0:
            return done(j, market.stamp(j), o, bt.TP, "GAP_TP")
        if sign * (adverse - stop) <= 0:
            return done(j, market.stamp(j) + bt.FIVE, stop - sign * slip, bt.SL)
        if j > i and sign * (favourable - target) >= 0:
            return done(j, market.stamp(j) + bt.FIVE, target, bt.TP)
        j += 1
    if boundary > horizon:
        last = limit - 1
        return done(last, market.stamp(last) + bt.FIVE, float(market.close[last]) - sign * hs - sign * slip, bt.END)
    index, at, stale = (j - 1, boundary, False) if j > i else (j, market.stamp(j) + bt.FIVE, True)
    price = float(market.close[index]) - sign * hs - sign * slip
    return done(index, at, price, boundary_reason, f"I2_{boundary_reason}", stale)


def run_2r(candidates, ctx, market, name, min_sl):
    """``es.run_variant`` for TP 2R with a minimum stop ``min_sl``; ``LimitCandidate`` entries fill at their limit.
    One position at a time, one entry per idea, 2R/day realised halt."""
    trades, skips = [], Counter()
    open_until, used, halted = None, set(), set()
    day_r = defaultdict(float)
    for cand in candidates:
        if cand.idea in used:
            skips["idea_already_traded"] += 1
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
            skips["no_next_bar"] += 1
            continue
        s = 1 if cand.side == LONG else -1
        limit = getattr(cand, "limit", None)
        if limit is None:
            fill = float(market.open[i]) + s * (market.half_spread_at(i)[0] + market.slippage)
        else:
            fill = limit_fill(market, i, cand.side, limit)
        stop, target, why = plan_2r(cand, fill, ctx.atr_at(cand.t), min_sl)
        if stop is None:
            skips[why] += 1
            continue
        if limit is None:
            result = bt.simulate_trade(market, slot, cand.side, stop, target, "I2")
        else:
            result = simulate_limit(market, i, cand.side, fill, stop, target)
        if "rejected" in result:
            skips[result["rejected"]] += 1
            continue
        used.add(cand.idea)
        open_until = result["exit_at"]
        result.update(variant=name, signal_at=slot, side=cand.side, idea=cand.idea)
        trades.append(result)
        key = bt.day_key(result["exit_at"])
        day_r[key] += result["r"]
        if day_r[key] <= -bt.DAILY_HALT_R:
            halted.add(key)
    bt.add_gross(trades, market)
    return trades, skips


# ---------------------------------------------------------------- evaluation
def folds(split, n=FOLDS):
    """n contiguous equal-time folds over the whole tradable period: [(start, end)]."""
    edges = [split.start + (split.end - split.start) * q / n for q in range(n + 1)]
    return list(zip(edges[:-1], edges[1:]))


def fold_metrics(trades, a, b):
    """Trades signalled in [a, b) that exit by ``b`` (crossers purged, as at the IS/OOS split)."""
    inside = [t for t in trades if a <= t["signal_at"] < b and t["exit_at"] <= b]
    return bt.metrics(inside, (b - a).total_seconds() / (365.25 / 12 * 86400))


def strict_pass(a, b):
    """IS and OOS avg R > +0.10, day-block CI80 low > 0 in both, >= 30 OOS trades."""
    return bool(a["trades"] and b["trades"] >= STRICT_MIN_OOS and a["avg_r"] > STRICT_AVG_R
                and b["avg_r"] > STRICT_AVG_R and a["ci80_low"] is not None and a["ci80_low"] > 0
                and b["ci80_low"] is not None and b["ci80_low"] > 0)


def select_best(results, combos, split, n=2):
    """Step 2: the ``n`` combos with the highest IS avg R of their SL25 variant (solely that; combos without an IS
    trade are not ranked). [(combo name, IS trades)], the sample size reported alongside, never used to rank."""
    ranked = []
    for relax in combos:
        a, _ = es.summarise(results[variant_name(relax, "SL25")][0], split)
        if a["trades"]:
            ranked.append((a["avg_r"], relax.name, a["trades"]))
    ranked.sort(key=lambda x: x[0], reverse=True)
    return [(name, n_is) for _, name, n_is in ranked[:n]]


def walk_forward(results, names, split):
    """[(name, fold metrics, all folds avg R > 0)] for ``names`` (strict-bar passers of any round)."""
    out = []
    for name in names:
        rows = [fold_metrics(results[name][0], a, b) for a, b in folds(split)]
        out.append((name, rows, all(m["trades"] and m["avg_r"] > 0 for m in rows)))
    return out


def table(results, split):
    """Per variant IS / OOS: trades/month, win rate, avg R, total R, MTM DD, median hold, exit mix, avg cost R,
    day-block CI80 low, G3 G4 G5 (``es.gate_flags``). Header names compatible with ``es.parse_report_table``."""
    head = ("| Variant | Trades/mo IS / OOS | Purged | Win % | Avg R | Total R | MTM DD R | Med hold h | "
            "Exits TP/SL/TIME/FRI % IS ; OOS | Avg cost R | CI80 low day-block | G3 G4 G5 |\n" + "|---" * 12 + "|")
    rows = [head]
    for name, (trades, _) in results.items():
        a, b = es.summarise(trades, split)
        purged = len(es.segment(trades, split)[2])

        def pair(key, fmt, scale=1.0):
            return " / ".join(es._f(m[key] * scale if m[key] is not None and m["trades"] else None, fmt)
                              for m in (a, b))

        def exits(m):
            e = m["exits"]
            return "/".join(f"{e[k]:.0f}" for k in (bt.TP, bt.SL, bt.TIME, bt.FRIDAY)) if m["trades"] else "–"
        rows.append(f"| {name} | {a['trades_per_month']:.1f} ({a['trades']}) / {b['trades_per_month']:.1f} "
                    f"({b['trades']}) | {purged} | {pair('win_rate', '.0f', 100)} | {pair('avg_r', '+.3f')} | "
                    f"{pair('total_r', '+.1f')} | {pair('max_dd_mtm_r', '.1f')} | "
                    f"{' / '.join(es._f(m['hold_h'][0], '.1f') for m in (a, b))} | {exits(a)} ; {exits(b)} | "
                    f"{pair('avg_cost_r', '.3f')} | {pair('ci80_low', '+.3f')} | "
                    f"{es.gate_flags(a)} / {es.gate_flags(b)} |")
    return "\n".join(rows)


def cost_table(results, names, split):
    head = ("| Variant | Median SL pips IS / OOS | Median spread pips | Avg gross R IS / OOS | Avg cost R IS / OOS | "
            "Cost % of gross IS / OOS |\n|---|---|---|---|---|---|")
    rows = [head]
    for name in names:
        a, b = es.summarise(results[name][0], split)

        def share(m):
            if not m["trades"] or not m["avg_gross_r"] > 0:
                return "n/a" if m["trades"] else "–"
            return f"{m['avg_cost_r'] / m['avg_gross_r']:.0%}"
        pooled = [t["spread_pips"] for t in results[name][0]]
        rows.append(f"| {name} | {es._f(a['sl_pips'][0], '.1f')} / {es._f(b['sl_pips'][0], '.1f')} | "
                    f"{es._f(bt.pct(pooled, 50), '.2f')} | {es._f(a['avg_gross_r'], '+.3f')} / "
                    f"{es._f(b['avg_gross_r'], '+.3f')} | {es._f(a['avg_cost_r'], '.3f')} / "
                    f"{es._f(b['avg_cost_r'], '.3f')} | {share(a)} / {share(b)} |")
    return "\n".join(rows)


# ---------------------------------------------------------------- report
DECLARATION = f"""\
## 0. Predeclared design (written before any round-2 result)

**Step 1 grid (fixed, 12 combos x 2 SL models = 24 variants)**: {", ".join(GRID_NAMES)}.
Everything not relaxed is the round-1 JT_SMC definition (section 1 of `entries_report.md`), without the 1h-OB
confluence overlay, F80 or E5 filters. TP = 2R on every variant.

- **R1** no unfilled-1h-FVG requirement (the check is skipped; the `fvg1h` funnel stage then passes by definition).
- **R2** trend from H4 only: the sweep needs a bullish H4 structure trend (longs; bearish for shorts); H1 ignored.
- **R3** trend from H1 only (H4 ignored). R2 and R3 are never combined.
- **R4** fib zones widened to one zone 50-85% retracement (instead of 50-60% or 75-85%).
- **R5** entry = limit order at the 15m OB proximal edge (OB high for longs, OB low for shorts), no 5m trigger and no
  5m OB. Mechanics: once the 15m OB exists, at every 5m close while the idea is alive the order is working for the
  next 5m bar iff the edge's retracement (from the running impulse high back to the sweep extreme) lies in a fib zone
  (R4 zones if R4) and, unless R1, an unfilled 1h FVG exists. It fills when that bar's ask low (bid high for shorts)
  reaches the edge, at the edge, or at the bar's open if it opens through it; no slippage on a limit fill. The order
  is pulled for bars starting in the news window and dies with the idea (close through the sweep extreme, deeper
  sweep, 12:00 ET expiry). On the fill bar only the stop is checked (same-bar SL first); the target can be hit from
  the next bar. Portfolio: the touch time is the entry time; if a position is open then (or the day is halted) the
  idea is skipped.
- **R6** liquidity = Asian low and PDL only (prior-day high / Asian high for shorts); the 1h swing levels are dropped
  and so is the untouched rule: the first trade-through inside 03:00-11:00 ET counts even if the level was traded
  through earlier (e.g. during Asia). Interpretation of "drop the untouched-level set".
- **SL models**: stop = 15m OB extreme -/+ 0.2 x ATR14(H1) from the fill (existing buffer); `SL15` = widen to 15 pips,
  skip > 30 (round-1 rule); `SL25` = widen to 25 pips, skip > 30.
- Funnel per combo = ideas reaching each stage (`trigger` = 5m trigger in a fib zone, or for R5 a working limit
  order; `candidate` = trigger passed every filter / limit touched). Target: >= {TARGET_TRADES_PER_MONTH:.0f}
  trades/month.

**Step 2**: `E2:2R:SL25`, `E3:2R:SL25` (round-1 E2/E3 candidates; stop = structure -/+ 0.2 ATR widened to 25, skip
> 30, TP 2R) with the round-1 `E2:FIX2` / `E3:FIX2` as references, plus the two JT combos with the highest IS avg R
of their `SL25` variant (ranked solely by IS avg R among combos with >= 1 IS trade; the IS trade count is reported
next to it, not used to rank; Codex note 1). Selection on IS only. Cost per R = avg (gross mid-price R - net R) per trade.

**Step 3**: variants tried = {ROUND1_VARIANTS} (round 1) + 24 (step 1) + 2 (step 2 E2/E3; the step-2 JT rows are
step-1 variants, not new). Strict bar: IS avg R > +{STRICT_AVG_R:.2f} AND OOS avg R > +{STRICT_AVG_R:.2f} AND day-block
CI80 low > 0 in both AND >= {STRICT_MIN_OOS} OOS trades. Any passer gets a walk-forward with {FOLDS} contiguous
equal-time folds over the whole tradable period (each fold = trades signalled in it that exit by its end; avg R, CI80
low, trades); it passes only if all {FOLDS} folds have avg R > 0. The bar and the walk-forward are also applied to
the 60 round-1 variants, recomputed here at full precision from their trades (same code as `replay.entry_search`;
no rounded report values are parsed; Codex notes 2-3).
"""


def header(state):
    split, adr = state["split"], state["adr"]
    mi, mo = split.months
    return [
        "# EURUSD intraday entry search — round 2 (REPLAY ONLY)", "",
        f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')} by `python -m replay.round2` (branch "
        f"`feat/backtest-v2-i2`). Production unchanged. Status: **{state['status']}**.", "",
        f"- Data: EURUSD 5m/15m/1h mid OHLC + 5m BidClose/AskClose, {state['data_start']} -> {state['data_end']} "
        "(same files as round 1).",
        f"- **In-sample** {split.start:%Y-%m-%d} -> {split.split:%Y-%m-%d} ({mi:.2f} months), **out-of-sample** "
        f"{split.split:%Y-%m-%d} -> {split.end:%Y-%m-%d} ({mo:.2f} months); trades signalled before the split and "
        "open at it are purged from both (column `Purged`). Continuous portfolio walk.",
        "- Costs and exits as round 1: causal per-trade spread (30-day trailing median, fallback 1.6 pip), 0.2 pip "
        "slippage on market entries / stops / time exits, 0 on TP and limit fills; same-bar SL first; I2 hard close "
        "at the end of the 2nd session from the fill; Friday 17:00 NY close; 2R/day realised halt; one position.",
        f"- ADR20 median {adr['median']:.1f} pips. Columns IS / OOS; MTM DD in R; CI80 low = day-block bootstrap "
        f"(circular {bt.BOOTSTRAP_BLOCK_DAYS}-trading-day blocks, empty days included); gates G3 (mean > +0.15R, CI "
        "low > 0, worst >= -1.2R), G4 (MTM DD <= 8R), G5 (avg cost <= 0.10R and <= 25% of gross); P/F.", "",
        DECLARATION]


def write_report(path, state):
    split = state["split"]
    mi, mo = split.months
    lines = header(state)
    if "s1" in state:
        results, funnels, cands = state["s1"], state["s1_funnel"], state["s1_cands"]
        lines += ["## 1. JT_SMC ablation", "", "### Funnel and trade rate per combo", "",
                  "| Combo | " + " | ".join(STAGES) + " | Trades/mo all (SL15 / SL25) | >= 8/mo |",
                  "|---" * (len(STAGES) + 3) + "|"]
        for relax in GRID:
            f = funnels[relax.name]
            rates = []
            for sl in SL_MODELS:
                n = len(results[variant_name(relax, sl)][0])
                rates.append(n / (mi + mo))
            lines.append(f"| {relax.name} | " + " | ".join(str(f.get(s, 0)) for s in STAGES)
                         + f" | {rates[0]:.1f} / {rates[1]:.1f} | "
                         + ("yes" if max(rates) >= TARGET_TRADES_PER_MONTH else "no") + " |")
        lines += ["", "Funnel over the whole tradable period, both sides. For R5 combos `trigger` = a limit order was "
                  "working at least once (OB edge in a fib zone), `fvg1h` = working with the FVG check passed, `news` / "
                  "`candidate` = the order was touched outside the news window. `Trades/mo all` = trades / "
                  f"{mi + mo:.2f} months (IS + OOS).",
                  "", "Round-1 JT_SMC (no relaxation) for reference: 439 sweeps -> 81 trend -> 21 ob15 -> 12 trigger "
                  "-> 4 fvg1h -> 4 candidates (0.4 trades/month).", "",
                  "### Results (TP 2R)", "", table(results, split), "",
                  "Main idea end reasons per combo (top 4):", ""]
        for relax in GRID:
            ends = Counter({k[4:]: v for k, v in funnels[relax.name].items() if k.startswith("end:")})
            lines.append(f"- {relax.name}: " + "; ".join(f"{k} {v}" for k, v in ends.most_common(4)))
        lines += ["", "Portfolio skips per variant:", ""]
        for name, (trades, skips) in results.items():
            lines.append(f"- {name}: traded {len(trades)} of {cands[name]} candidates; "
                         + ", ".join(f"{k} {v}" for k, v in skips.most_common()))
        lines.append("")
    if "s2" in state:
        best = state["s2_best"]
        lines += ["## 2. 25-30 pip stops, TP 2R", "",
                  "JT combos selected solely on IS avg R of their SL25 variant: " + (", ".join(
                      f"{n} ({k} IS trades)" for n, k in best) or "none")
                  + ". Their SL15 rows and the round-1 E2/E3 FIX2 rows are shown for comparison.", "",
                  table(state["s2"], split), "", "### Cost per R", "", cost_table(state["s2"], list(state["s2"]), split),
                  ""]
        for name, (trades, skips) in state["s2"].items():
            lines.append(f"- {name}: traded {len(trades)}; " + ", ".join(f"{k} {v}" for k, v in skips.most_common()))
        lines.append("")
    if "s3" in state:
        s3 = state["s3"]
        lines += ["## 3. Multiple testing and the strict bar", "",
                  f"**Variants tried: {s3['total']}** = {ROUND1_VARIANTS} (round 1) + {s3['round2']} (round 2: 24 JT "
                  "ablation + 2 E2/E3 SL25). With an 80% two-sided CI (10% one-sided per test) about "
                  f"{0.10 * s3['total']:.0f} variants would show 'CI low > 0' in one segment by chance alone with no edge "
                  "(fewer in practice: the variants are correlated).", "",
                  f"Strict bar (IS and OOS avg R > +{STRICT_AVG_R:.2f}, day-block CI80 low > 0 in both, >= "
                  f"{STRICT_MIN_OOS} OOS trades):", "",
                  f"- Round 2 ({s3['round2']} variants): **{', '.join(s3['passers']) or 'none pass'}**.",
                  f"- Round 1 ({s3['round1_n']} variants recomputed at full precision): "
                  f"**{', '.join(s3['round1_passers']) or 'none pass'}**.", "",
                  "Closest round-2 variants (the bar's conditions met, of 5):", "",
                  "| Variant | IS avg R | OOS avg R | IS CI80 low | OOS CI80 low | OOS n | conditions met |",
                  "|---|---|---|---|---|---|---|"]
        for row in s3["closest"]:
            lines.append("| " + " | ".join(row) + " |")
        lines += ["", "### Walk-forward (strict-bar passers of round 1 and round 2)", ""]
        if s3["walk"]:
            fold_head = " | ".join(f"Fold {q + 1} {a:%Y-%m-%d}->{b:%Y-%m-%d} avg R / CI80 low / n"
                                   for q, (a, b) in enumerate(folds(split)))
            lines += [f"| Variant | {fold_head} | pass |", "|---" * (FOLDS + 2) + "|"]
            for name, rows, ok in s3["walk"]:
                cells = " | ".join(f"{es._f(m['avg_r'] if m['trades'] else None, '+.3f')} / "
                                   f"{es._f(m['ci80_low'], '+.3f')} / {m['trades']}" for m in rows)
                lines.append(f"| {name} | {cells} | {'PASS' if ok else 'FAIL'} |")
        else:
            lines.append("No variant passed the strict bar, so no walk-forward was run.")
        lines += ["", "### Gates G3 / G4 / G5 per round-2 variant (IS / OOS)", "",
                  "| Variant | G3 G4 G5 IS | G3 G4 G5 OOS |", "|---|---|---|"]
        for name, (trades, _) in state["all"].items():
            a, b = es.summarise(trades, split)
            lines.append(f"| {name} | {es.gate_flags(a)} | {es.gate_flags(b)} |")
        lines += ["", f"Equity: `round2_equity_r.png` ({', '.join(state['plot'])}).", "",
                  "### Verdict", "", s3["verdict"], ""]
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


def closest_rows(results, split, n=6):
    scored = []
    for name, (trades, _) in results.items():
        a, b = es.summarise(trades, split)
        if not a["trades"]:
            continue
        checks = [a["avg_r"] > STRICT_AVG_R, b["trades"] > 0 and b["avg_r"] > STRICT_AVG_R,
                  a["ci80_low"] is not None and a["ci80_low"] > 0, b["ci80_low"] is not None and b["ci80_low"] > 0,
                  b["trades"] >= STRICT_MIN_OOS]
        scored.append((sum(checks), a["avg_r"], name, a, b))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [[name, es._f(a["avg_r"], "+.3f"), es._f(b["avg_r"] if b["trades"] else None, "+.3f"),
             es._f(a["ci80_low"], "+.3f"), es._f(b["ci80_low"], "+.3f"), str(b["trades"]), str(met)]
            for met, _, name, a, b in scored[:n]]


# ---------------------------------------------------------------- main
def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--steps", type=int, default=3, help="run steps 1..N")
    args = parser.parse_args(argv)
    os.makedirs(args.out, exist_ok=True)
    report = os.path.join(args.out, "round2_report.md")

    def log(message):
        print(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {message}", flush=True)

    frames = bt.load_csv_frames(args.data)
    ctx = es.Context(frames)
    split = es.make_split(ctx)
    market = bt.Market.from_frames(frames, bt.FALLBACK_SPREAD_PIPS, cutoff=split.end, causal=True)
    start_ns = int(split.start.timestamp() * 1e9)
    news_days = es.high_impact_days(split.start.date(), split.end.date())
    state = {"split": split, "adr": es.adr_stats(ctx, split), "status": "PARTIAL (grid declared, step 1 running)",
             "data_start": frames["5m"].index[0].isoformat(), "data_end": split.end.isoformat()}
    write_report(report, state)  # the full design is on disk before any result

    def half_spread(k):
        return market.half_spread_at(k)[0]

    results, funnels, ncands, jt_cands = {}, {}, {}, {}
    for relax in GRID:
        found, funnels[relax.name] = jt_relaxed_all(ctx, relax, news_days, half_spread, start_ns)
        jt_cands[relax.name] = [c for c in found if c.t >= start_ns]
        for sl, min_sl in SL_MODELS.items():
            name = variant_name(relax, sl)
            results[name] = run_2r(jt_cands[relax.name], ctx, market, name, min_sl)
            ncands[name] = len(jt_cands[relax.name])
        log(f"{relax.name}: {len(jt_cands[relax.name])} candidates, trades "
            f"{[len(results[variant_name(relax, sl)][0]) for sl in SL_MODELS]}")
    state.update(s1=results, s1_funnel=funnels, s1_cands=ncands, status="PARTIAL (step 1 done)")
    write_report(report, state)
    if args.steps < 2:
        return state

    s2 = {}
    for family in ("E2", "E3"):
        cands = [c for c in es.entry_candidates(ctx, family) if c.t >= start_ns]
        s2[f"{family}:2R:SL25"] = run_2r(cands, ctx, market, f"{family}:2R:SL25", SL_MODELS["SL25"])
        s2[f"{family}:FIX2 (round 1)"] = es.run_variant(cands, ctx, market, es.Variant(f"{family}:FIX2", family,
                                                                                         "FIX2", False))
    best = select_best(results, GRID, split)
    for name, _ in best:
        for sl in ("SL25", "SL15"):
            s2[variant_name(Relax.parse(name), sl)] = results[variant_name(Relax.parse(name), sl)]
    state.update(s2=s2, s2_best=best, status="PARTIAL (steps 1-2 done)")
    write_report(report, state)
    log(f"step 2: best JT combos {best}")
    if args.steps < 3:
        return state

    new = {**results, "E2:2R:SL25": s2["E2:2R:SL25"], "E3:2R:SL25": s2["E3:2R:SL25"]}
    passers = [n for n, (trades, _) in new.items() if strict_pass(*es.summarise(trades, split))]
    round1 = es.round1_results(ctx, market, news_days, start_ns)
    passers1 = [n for n, (trades, _) in round1.items() if strict_pass(*es.summarise(trades, split))]
    walk = walk_forward({**round1, **new}, passers1 + passers, split)
    survivors = [n for n, _, ok in walk if ok]
    every = passers1 + passers
    if survivors:
        verdict = (f"**{len(survivors)} variant(s) pass the strict bar and the walk-forward: {', '.join(survivors)}.** "
                   f"Still {len(new) + ROUND1_VARIANTS} variants were tried on the same year; a fresh pre-registered "
                   "period (live paper) is required before any decision.")
    elif every:
        verdict = (f"{len(every)} variant(s) pass the strict bar ({', '.join(every)}) but none passes the "
                   "walk-forward (every fold avg R > 0). **No passer.**")
    else:
        verdict = "**No variant passes the strict bar. No passer.**"
    plot = [variant_name(Relax.parse(n), "SL25") for n, _ in best] + ["E2:2R:SL25", "E3:2R:SL25"]
    plot = [n for n in dict.fromkeys(passers + plot)][:5]
    state.update(all=new, plot=plot, status="COMPLETE", s3={
        "total": ROUND1_VARIANTS + len(new), "round2": len(new), "passers": passers,
        "round1_n": len(round1), "round1_passers": passers1,
        "closest": closest_rows(new, split), "walk": walk, "verdict": verdict})
    es.plot_equity(os.path.join(args.out, "round2_equity_r.png"), new, plot, split,
                   title="EURUSD entry search round 2 - cumulative R, TP 2R, SL 25-30 pips (shaded = out-of-sample)")
    write_report(report, state)
    log(f"done: passers {passers}, walk-forward survivors {survivors}")
    return state


if __name__ == "__main__":
    main()
