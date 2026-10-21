"""EURUSD 5-year rerun + round 3 (REPLAY ONLY): every family x exit on ~5 years with a yearly walk-forward.

Validation (replaces the 1-year IS/OOS split): years Y1..Y5 = consecutive 12-month blocks starting at the first
tradable moment (20 completed NY trading days, ``entry_search.Context.tradable_start``); the last block ends at the
data end. Per year: the trades signalled in it that exit by its end (crossers purged from the yearly figures, kept in
the 5-year totals); avg R and the day-block bootstrap CI80 low (``intraday_backtest.day_block_bootstrap_lower``).
A variant PASSES only if: avg R > +0.10 AND CI80 low > 0 in >= 4 of the 5 years, total R over the 5 years > 0, MTM
max drawdown over the 5 years <= 8R and avg cost <= 0.10R.

Simulation: ``intraday_backtest`` / ``entry_search`` / ``round2`` unchanged (causal 30-day spread + 0.2 pip
slippage, same-bar SL first, I2 hard close at the end of the 2nd session from the fill, Friday 17:00 NY, 2R/day
realised halt, one position at a time, one entry per idea). Exits: FIX2 (structure -/+ 0.2 ATR14(H1), widened to
15, > 30 skipped, TP 2R), FIX15 (same, TP 1.5R), SL25 (widened to 25, > 30 skipped, TP 2R), ADR (SL 0.3 ADR20
clamped 15-30, TP 0.58 ADR20 clamped 1.5-2.0R); F80 (skip when today's range > 0.8 ADR20) on the top families only.

Run (sections append to the report in order; ``final`` reads the per-section summaries):
``python -m replay.fiveyear --data /workspace/backtest/data5y --out /workspace/backtest/5y
--report /workspace/backtest/5y_report.md --sections declare,e,jt,modules,pairs,families,f80,v2,final``.
Market data and outputs live outside the repository and are never committed.
"""
import argparse
import json
import math
import os
from collections import Counter
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from replay import entry_search as es
from replay import intraday_backtest as bt
from replay import round2 as r2
from replay import round3 as r3

YEARS = 5
PASS_AVG_R = 0.10
PASS_YEARS = 4
MAX_DD_R = 8.0
MAX_COST_R = 0.10
ONE_SIDED = 0.10  # CI80 lower bound = a one-sided 10% test per year
EXITS = ("FIX2", "FIX15", "SL25", "ADR")
TOP_FAMILIES_F80 = 3
MIN_TRADES_TOP = 30
PAIR_MODULES = 4
MONTH_S = 365.25 / 12 * 86400
V2_NAMES = {"V2_F3": "V2_F3", "V2_I2": "V2_I2:FIX2", "V2_I2_15": "V2_I2:FIX15", "V2_I2_SL25": "V2_I2:SL25",
            "V2_I2_ADR": "V2_I2:ADR"}


def p_false_pass(years=YEARS, need=PASS_YEARS, alpha=ONE_SIDED):
    """P(a no-edge variant clears >= ``need`` of ``years`` independent one-sided ``alpha`` yearly tests)."""
    return sum(math.comb(years, k) * alpha ** k * (1 - alpha) ** (years - k) for k in range(need, years + 1))


# ---------------------------------------------------------------- years and evaluation
def year_blocks(start, end, n=YEARS):
    """[(a, b)]: ``n`` consecutive 12-month blocks from ``start``; the last one is clipped at ``end``."""
    edges = [pd.Timestamp(start) + pd.DateOffset(months=12 * q) for q in range(n + 1)]
    edges = [e.to_pydatetime() for e in edges]
    edges[-1] = min(edges[-1], end)
    return list(zip(edges[:-1], edges[1:]))


def year_metrics(trades, a, b):
    inside = [t for t in trades if a <= t["signal_at"] < b and t["exit_at"] <= b]
    return bt.metrics(inside, (b - a).total_seconds() / MONTH_S)


def evaluate(trades, years):
    """5-year metrics + per-year (avg R, CI80 low, n) and the pass rule with the failed conditions."""
    months = (years[-1][1] - years[0][0]).total_seconds() / MONTH_S
    m = bt.metrics(trades, months)
    per = []
    for a, b in years:
        y = year_metrics(trades, a, b)
        per.append({"avg_r": y["avg_r"] if y["trades"] else None, "ci80_low": y["ci80_low"], "n": y["trades"]})
    ok = [p["n"] > 0 and p["avg_r"] > PASS_AVG_R and p["ci80_low"] is not None and p["ci80_low"] > 0 for p in per]
    n = m["trades"]
    fails = [] if n else ["no trades"]
    if n and sum(ok) < PASS_YEARS:
        fails.append(f"years {sum(ok)}/{YEARS}")
    if n and not m["total_r"] > 0:
        fails.append("total R <= 0")
    if n and not m["max_dd_mtm_r"] <= MAX_DD_R:
        fails.append(f"MTM DD {m['max_dd_mtm_r']:.1f}R")
    if n and not m["avg_cost_r"] <= MAX_COST_R:
        fails.append(f"cost {m['avg_cost_r']:.3f}R")
    return {"trades": n, "tpm": m["trades_per_month"], "win": m["win_rate"] if n else None,
            "avg_r": m["avg_r"] if n else None, "total_r": m["total_r"], "dd": m["max_dd_mtm_r"],
            "hold": m["hold_h"][0] if n else None, "exits": [m["exits"][k] for k in (bt.TP, bt.SL, bt.TIME, bt.FRIDAY)],
            "cost": m["avg_cost_r"] if n else None, "sl_pips": m["sl_pips"][0] if n else None, "years": per,
            "years_ok": int(sum(ok)), "pass": not fails, "fails": fails,
            "equity": [[t["exit_at"].isoformat(), round(float(t["r"]), 6)] for t in trades]}


# ---------------------------------------------------------------- runners
def f80_ok(ctx, cand):
    adr = ctx.adr_at(cand.t)
    return math.isfinite(adr) and ctx.range_so_far(cand.t) <= es.F80_SHARE * adr


def run_exit(cands, ctx, market, name, exit_model, f80=False, e5=False):
    """(trades, skips) of one variant. F80/E5 are per-candidate filters (independent of the portfolio state), so
    pre-filtering gives the same trades as ``es.run_variant``'s in-loop checks. SL25 and limit candidates use
    ``round2.run_2r``; FIX2 / FIX15 / ADR use ``es.run_variant``."""
    if e5:
        cands = [c for c in cands if es.e5_filter(ctx, c)]
    if f80:
        cands = [c for c in cands if f80_ok(ctx, c)]
    limit = any(isinstance(c, r2.LimitCandidate) for c in cands)
    if exit_model == "SL25":
        return r2.run_2r(cands, ctx, market, name, 25.0)
    if limit:
        if exit_model != "FIX2":
            raise ValueError("limit candidates only support FIX2 / SL25")
        return r2.run_2r(cands, ctx, market, name, 15.0)
    return es.run_variant(cands, ctx, market, es.Variant(name, name.split(":")[0], exit_model, False))


def v2_records(signals, ctx):
    """Production V2_I2 plans re-exited: ``V2_I2_SL25`` (I2 stop widened to 25 pips from the planned entry, > 30
    skipped, TP 2R) and ``V2_I2_ADR`` (SL 0.3 ADR20 clamped 15-30, TP 0.58 ADR20 clamped 1.5-2.0R, from the planned
    entry; ADR20 of the completed days before the slot). Same VALID_SETUP and setup_id; no extra risk check."""
    out = []
    for record in signals:
        record = dict(record)
        plan = record.get("V2_I2")
        if plan is not None:
            s = 1 if record["side"] == "LONG" else -1
            entry, stop = plan["entry"], plan["stop"]
            dist = s * (entry - stop) / bt.PIP
            if dist > bt.MAX_SL_PIPS:
                record["V2_I2_SL25_skip"] = "sl_wider_than_30_pips"
            else:
                dist = max(dist, 25.0)
                record["V2_I2_SL25"] = {"entry": entry, "stop": entry - s * dist * bt.PIP,
                                        "target": entry + s * 2.0 * dist * bt.PIP}
            adr = ctx.adr_at(int(pd.Timestamp(record["slot"]).value))
            if math.isfinite(adr):
                sl = min(max(es.ADR_SL * adr, bt.MIN_SL_PIPS), bt.MAX_SL_PIPS)
                tp = min(max(es.ADR_TP * adr, es.ADR_RR[0] * sl), es.ADR_RR[1] * sl)
                record["V2_I2_ADR"] = {"entry": entry, "stop": entry - s * sl * bt.PIP,
                                       "target": entry + s * tp * bt.PIP}
            else:
                record["V2_I2_ADR_skip"] = "adr_unavailable"
        else:
            reason = record.get("V2_I2_skip")
            if reason:
                record["V2_I2_SL25_skip"] = record["V2_I2_ADR_skip"] = reason
        out.append(record)
    return out


# ---------------------------------------------------------------- report
def _f(x, fmt):
    return es._f(x, fmt)


def table(rows):
    head = ("| Variant | Tr/mo (n) | Win % | Avg R | Total R | MTM DD | Med hold h | TP/SL/TIME/FRI % | Cost R | "
            + " | ".join(f"Y{q + 1}" for q in range(YEARS)) + " | Yrs ok | Result |\n" + "|---" * (12 + YEARS) + "|")
    lines = [head]
    for name, s in rows.items():
        years = " | ".join(f"{_f(y['avg_r'], '+.2f')} / {_f(y['ci80_low'], '+.2f')} ({y['n']})" for y in s["years"])
        exits = "/".join(f"{e:.0f}" for e in s["exits"]) if s["trades"] else "–"
        result = "**PASS**" if s["pass"] else "fail: " + ", ".join(s["fails"])
        lines.append(f"| {name} | {s['tpm']:.1f} ({s['trades']}) | {_f(s['win'] * 100 if s['win'] is not None else None, '.0f')} | "
                     f"{_f(s['avg_r'], '+.3f')} | {s['total_r']:+.1f} | {s['dd']:.1f} | {_f(s['hold'], '.1f')} | "
                     f"{exits} | {_f(s['cost'], '.3f')} | {years} | {s['years_ok']} | {result} |")
    return "\n".join(lines)


def skips_lines(skips):
    return [f"- {name}: " + ", ".join(f"{k} {v}" for k, v in sk.most_common(6)) for name, sk in skips.items()]


DECLARATION = """\
## 0. Predeclared design (written before any 5-year result)

**Data**: EURUSD 5m/15m/1h mid OHLC + 5m BidClose/AskClose + tick volume, `data5y` ({data_start} -> {data_end}).
**Years** (consecutive 12-month blocks from the first tradable moment = 20 completed NY trading days):
{years}

**Pass rule (per variant)**: avg R > +0.10 AND day-block CI80 low > 0 in >= 4 of 5 years (yearly figures = trades
signalled in the year that exit by its end), total R over 5 years > 0, MTM max DD over 5 years <= 8R, avg cost <=
0.10R. Per variant: trades/month (n), win %, avg R, total R, MTM DD (R), median hold, exit mix TP/SL/TIME/FRI %, avg
cost R (gross mid R - net R), per year `avg R / CI80 low (n)`, years passing, result.

**Rules kept**: causal spread (30-day trailing median, fallback 1.6 pip) + 0.2 pip slippage on market entries /
stops / time exits (0 on TP and limit fills), same-bar SL first, I2 hard close at the end of the 2nd session from the
fill, Friday 17:00 NY, 2R/day realised halt, one position at a time, one entry per idea; fills at the open of the 5m
bar starting at the decision close.

**Exits** (all variants unless stated): `FIX2` structure -/+ 0.2 ATR14(H1), < 15 pips widened to 15, > 30 skipped,
TP 2R; `FIX15` the same with TP 1.5R; `SL25` widened to 25, > 30 skipped, TP 2R; `ADR` SL 0.3 ADR20 clamped 15-30,
TP 0.58 ADR20 clamped to 1.5-2.0R (no structure). `F80` overlay (skip when today's range since 17:00 NY > 0.8 ADR20)
only for the top {top} families, chosen after all base runs by their best 5-year avg R over the four exits (families
with >= {min_trades} trades), on all four exits.

**Grid**
- **R1** `V2_F3` (production fixed 3R + trade-duration rules), `V2_I2:FIX2` / `V2_I2:FIX15` (production VALID_SETUP,
  I2 stop = 15m swing -/+ 0.2 ATR widened to 15 / 5m swing if > 30, TP 2R / 1.5R), `V2_I2:SL25` (I2 stop widened to
  25, > 30 skipped, TP 2R), `V2_I2:ADR` (ADR exit from the planned entry). Signals: the production floor per 15-min
  slot (V2_F3 planner, AI excluded), generated with the replay-only scout accelerator (`replay/fast_scouts.py`, exact
  numpy equivalents of the scout loops, equality tested) and checkpointed per year.
  E1-E4 (round-1 definitions, `replay/entry_search.py`) x 4 exits; E5.E1-E5.E4 (decision in [03:00, 11:00) ET and
  last closed 5m spread <= 1.2 pip) x 4 exits. 37 variants.
- **R2** JT_SMC as round 1 (no 1h-OB overlay) x 4 exits; the 12 predeclared round-2 relaxation combos
  ({grid}) x SL15 (= FIX2 geometry; R5 combos: limit fills) and SL25. 28 variants.
- **R3a JT modules alone** (long rules; shorts mirrored; entries with decision bar start in [03:00, 12:00) ET) x 4
  exits:
  - `M1` sweep of Asian low / PDL / untouched 1h swing lows of the last 5 days (first trade-through in 03:00-11:00
    ET, close back within 6 bars) -> first 5m BOS up within 12 bars of the close-back; structure = sweep extreme.
  - `M2` H4 bullish + bar trading into the most recent unfilled bullish 1h FVG (3rd candle closed <= 24 h ago, no
    5m low <= its bottom since) -> bullish 5m close inside the FVG; structure = FVG bottom.
  - `M3` H4 bullish + close retracing 50-60% or 75-85% of the last H1 impulse (last confirmed 1h swing low -> the
    most recent confirmed 1h swing high; void after a high above it or a low below it) -> bullish 5m close in the
    zone; structure = impulse low.
  - `M4` sweep of the Asian low in 03:00-11:00 ET -> 5m close back inside the range + 5m BOS up; structure = sweep
    extreme (one per side per day).
  - `M5` H4 bullish + bar trading into the most recent 15m OB (last bearish 15m candle before a 15m BOS up, 24 h
    life, void after a 5m close below it) -> bullish 5m close inside the OB; structure = OB low. `M5b` = M5 with a
    1h OB (same rule on 1h, 48 h life) in force and overlapping the 15m OB.
  - Each module's "in force" state: M1/M4 from the trigger until a close below the extreme or 12:00 ET; M2/M3/M5
    the bar conditions above without the bullish-close trigger.
  - **Pairs**: modules ranked by 5-year avg R of their FIX2 variant; the top {pairs} (M5 and M5b never both: the
    better one counts, the next module fills in) give 6 pairs (AND: one member triggers while the other is in force;
    stop structure / idea of the trigger) x 4 exits; then **ALL** (M1-M5 AND) x 4 exits. 6x4 + 6x4 + 4 = 52 variants.
- **R3b new families** x 4 exits (20 variants):
  - `F1` ORB London: range = the six 5m bars 03:00-03:30 ET; first 5m close beyond it with bar start in
    [03:30, 08:00) ET -> enter that way; structure = the other side of the range. `F1b` NY: range 09:30-10:00 ET,
    breakout bars [10:00, 13:00) ET. One idea per side per day.
  - `F2` previous-day range mean reversion: in [03:00, 12:00) ET a 5m low below PDL - 0.1 x ADR20 then the first 5m
    close back above PDL -> long (mirror at PDH -> short); structure = the excursion extreme. One per side per day.
  - `F3` session VWAP pullback: VWAP of typical price x tick volume anchored 03:00 ET (London, entries until 08:00)
    and 08:00 ET (NY, until 12:00); the previous 6 contiguous closes of the session above their VWAP, then a bar whose
    low touches VWAP and closes bullish above it -> long; structure = that bar's low; one idea per run above VWAP.
  - `F4` momentum: a closed 1h candle with body >= 1.5 x ATR14(H1) of the 14 bars before it -> enter in its direction
    at the next 5m open; structure = candle midpoint (high+low)/2 (-/+ 0.2 ATR buffer as every structural stop).
- **F80**: {top} families x 4 exits = 12 variants.

**Total tests declared: {total}** (37 + 28 + 52 + 20 + 12). **Multiple testing**: a no-edge variant passes the
yearly part of the rule with probability P(>= 4 of 5 independent one-sided 10% tests) = {p:.5f}, so ~{fp:.3f}
expected false passers over {total} independent variants (the other conditions only lower it). The variants are
strongly correlated (same candidates under different exits, nested filters, overlapping families), so the effective
number of independent tests is smaller, and so is the expected false-pass count; but a lone passer among many
correlated siblings is still weak evidence.
"""


def declaration(state):
    years = "\n".join(f"- Y{q + 1}: {a:%Y-%m-%d %H:%M} -> {b:%Y-%m-%d %H:%M} UTC" for q, (a, b) in
                      enumerate(state["years"]))
    return DECLARATION.format(data_start=state["data_start"], data_end=state["data_end"], years=years,
                              top=TOP_FAMILIES_F80, min_trades=MIN_TRADES_TOP, pairs=PAIR_MODULES,
                              grid=", ".join(r2.GRID_NAMES), total=DECLARED_TOTAL, p=p_false_pass(),
                              fp=DECLARED_TOTAL * p_false_pass())


DECLARED_TOTAL = 37 + 28 + 52 + 20 + 12

STEP0 = """\
## Step 0. Round 2 restated after the 3 Codex notes (original 1-year data)

Fixes in `replay/round2.py`: step-2 JT combos ranked solely by IS avg R of their SL25 variant; the 3-fold
walk-forward also covers round-1 strict-bar passers; the round-1 strict bar is computed at full precision from
recomputed round-1 trades (no parsing of rounded report values). Re-run on `/workspace/backtest/data`
(`5y/step0_round2_1y/round2_report.md`): **verdict unchanged, no variant passes the strict bar in round 1 (60) or
round 2 (26)**, so no walk-forward is triggered; the step-2 selection is now R1+R5 (2 IS trades) and R1+R3+R4+R5 (14
IS trades) instead of R1+R2+R4 / R1+R2.
"""


def append(path, text):
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(text.rstrip("\n") + "\n\n")


def save(out, section, rows, extra=None):
    with open(os.path.join(out, f"summary_{section}.json"), "w", encoding="utf-8") as handle:
        json.dump({"rows": rows, **(extra or {})}, handle)


def load(out, section):
    path = os.path.join(out, f"summary_{section}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def compact(rows):
    return {name: {k: v for k, v in s.items() if k != "equity"} for name, s in rows.items()}


def best_by_family(rows, family_of):
    """{family: (best 5y avg R over its variants with >= MIN_TRADES_TOP trades, variant)}."""
    best = {}
    for name, s in rows.items():
        if s["trades"] >= MIN_TRADES_TOP and s["avg_r"] is not None:
            fam = family_of(name)
            if fam not in best or s["avg_r"] > best[fam][0]:
                best[fam] = (s["avg_r"], name)
    return best


def family_of(name):
    return name.split(":")[0]


def pair_members(module_rows):
    """Top PAIR_MODULES modules by FIX2 5-year avg R (M5 / M5b never both)."""
    ranked = []
    for m in r3.MODULES:
        avg = module_rows[f"{m}:FIX2"]["avg_r"]
        ranked.append((avg if avg is not None else -math.inf, m))
    ranked.sort(reverse=True)
    chosen = []
    for _, m in ranked:
        if m in ("M5", "M5b") and {"M5", "M5b"} & set(chosen):
            continue
        chosen.append(m)
        if len(chosen) == PAIR_MODULES:
            break
    return chosen, ranked


# ---------------------------------------------------------------- main
def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--sections", default="declare,e,jt,modules,pairs,families,f80,v2,final")
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    args = parser.parse_args(argv)
    os.makedirs(args.out, exist_ok=True)
    sections = args.sections.split(",")

    def log(message):
        print(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {message}", flush=True)

    frames = bt.load_csv_frames(args.data)
    ctx = es.Context(frames)
    start = pd.Timestamp(ctx.tradable_start(), tz="UTC").to_pydatetime()
    end = datetime.fromtimestamp(int(ctx.m5.end[-1]) / 1e9, timezone.utc)
    years = year_blocks(start, end)
    market = bt.Market.from_frames(frames, bt.FALLBACK_SPREAD_PIPS, cutoff=end, causal=True)
    start_ns = int(start.timestamp() * 1e9)
    news_days = es.high_impact_days(start.date(), end.date())
    state = {"years": years, "data_start": frames["5m"].index[0].isoformat(), "data_end": end.isoformat()}
    log(f"years {[(a.isoformat(), b.isoformat()) for a, b in years]}")

    def evaluate_all(variants):
        rows, skips = {}, {}
        for name, (cands, exit_model, kw) in variants.items():
            trades, sk = run_exit(cands, ctx, market, name, exit_model, **kw)
            rows[name], skips[name] = evaluate(trades, years), sk
        return rows, skips

    def section(key, title, rows, skips, notes=(), extra=None):
        save(args.out, key, rows, extra)
        lines = [f"## {title}", "", *notes, "", table(rows), "", "Portfolio skips (top 6 reasons):", "",
                 *skips_lines(skips)]
        append(args.report, "\n".join(lines))
        passers = [n for n, s in rows.items() if s["pass"]]
        log(f"{key}: {len(rows)} variants, passers {passers}")

    if "declare" in sections:
        with open(args.report, "w", encoding="utf-8") as handle:
            handle.write(f"# EURUSD 5-year rerun + round 3 (REPLAY ONLY)\n\nStarted "
                         f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} by `python -m replay.fiveyear` "
                         "(branch `feat/backtest-v2-i2`). Production unchanged. Sections are appended as they "
                         "finish.\n\n")
        append(args.report, declaration(state))
        append(args.report, STEP0)

    if "e" in sections:
        cands = {f: [c for c in es.entry_candidates(ctx, f) if c.t >= start_ns] for f in ("E1", "E2", "E3", "E4")}
        variants = {}
        for e5 in (False, True):
            for f in ("E1", "E2", "E3", "E4"):
                for x in EXITS:
                    variants[f"{'E5.' if e5 else ''}{f}:{x}"] = (cands[f], x, {"e5": e5})
        rows, skips = evaluate_all(variants)
        spread = (frames["5m"]["AskClose"] - frames["5m"]["BidClose"]).to_numpy() / bt.PIP
        note = (f"Candidates: {', '.join(f'{f} {len(c)}' for f, c in cands.items())}. 5m spread <= "
                f"{es.E5_MAX_SPREAD_PIPS} pip on {np.mean(np.round(spread, 2) <= es.E5_MAX_SPREAD_PIPS):.1%} of bars "
                f"(median {np.median(spread):.2f}).")
        section("e", "R1. E1-E5 x exits (5 years)", rows, skips, [note])

    if "jt" in sections:
        def half_spread(k):
            return market.half_spread_at(k)[0]
        variants, funnels = {}, {}
        base, funnel = r2.jt_relaxed_all(ctx, r2.Relax(), news_days, half_spread, start_ns)
        base = [c for c in base if c.t >= start_ns]
        funnels["base"] = dict(funnel)
        for x in EXITS:
            variants[f"JT:{x}"] = (base, x, {})
        for relax in r2.GRID:
            found, funnel = r2.jt_relaxed_all(ctx, relax, news_days, half_spread, start_ns)
            funnels[relax.name] = dict(funnel)
            found = [c for c in found if c.t >= start_ns]
            variants[f"JT[{relax.name}]:SL15"] = (found, "FIX2", {})
            variants[f"JT[{relax.name}]:SL25"] = (found, "SL25", {})
        rows, skips = evaluate_all(variants)
        note = ["Funnel (ideas reaching each stage, 5 years, both sides): "
                + "; ".join(f"{k}: " + " -> ".join(str(f.get(s, 0)) for s in r2.STAGES) for k, f in funnels.items()),
                f"(stages {' -> '.join(r2.STAGES)}). `JT[x]:SL15` = FIX2 geometry (R5 combos: limit fills)."]
        section("jt", "R2. JT_SMC + 12-combo ablation (5 years)", rows, skips, note)

    mods = None
    if "modules" in sections or "pairs" in sections:
        mods = r3.modules(ctx)
    if "modules" in sections:
        variants = {}
        for m in r3.MODULES:
            cands = [c for c in r3.combine(ctx, mods, (m,), m) if c.t >= start_ns]
            for x in EXITS:
                variants[f"{m}:{x}"] = (cands, x, {})
        rows, skips = evaluate_all(variants)
        section("modules", "R3a. JT modules alone (5 years)", rows, skips,
                [f"Candidates per module: {', '.join(f'{m} {len(variants[m + ':FIX2'][0])}' for m in r3.MODULES)}."])

    if "pairs" in sections:
        module_rows = load(args.out, "modules")["rows"]
        chosen, ranked = pair_members(module_rows)
        variants = {}
        pairs = [(a, b) for i, a in enumerate(chosen) for b in chosen[i + 1:]]
        for a, b in pairs:
            label = f"{a}&{b}"
            cands = [c for c in r3.combine(ctx, mods, (a, b), label) if c.t >= start_ns]
            for x in EXITS:
                variants[f"{label}:{x}"] = (cands, x, {})
        cands = [c for c in r3.combine(ctx, mods, ("M1", "M2", "M3", "M4", "M5"), "ALL") if c.t >= start_ns]
        for x in EXITS:
            variants[f"ALL:{x}"] = (cands, x, {})
        rows, skips = evaluate_all(variants)
        note = [f"Module ranking by 5-year FIX2 avg R: {', '.join(f'{m} {_f(r, '+.3f')}' for r, m in ranked)}. "
                f"Pair members: {', '.join(chosen)}. Candidates: "
                + ", ".join(f"{n.split(':')[0]} {len(v[0])}" for n, v in variants.items() if n.endswith(":FIX2"))]
        section("pairs", "R3a. Module pairs (AND) and ALL (5 years)", rows, skips, note, {"chosen": chosen})

    if "families" in sections:
        variants = {}
        for f in r3.FAMILIES:
            cands = [c for c in r3.family_candidates(ctx, f) if c.t >= start_ns]
            for x in EXITS:
                variants[f"{f}:{x}"] = (cands, x, {})
        rows, skips = evaluate_all(variants)
        section("families", "R3b. New families F1-F4 (5 years)", rows, skips,
                [f"Candidates: {', '.join(f'{f} {len(variants[f + ':FIX2'][0])}' for f in r3.FAMILIES)}."])

    if "f80" in sections:
        everything = {}
        for key in ("e", "jt", "modules", "pairs", "families"):
            got = load(args.out, key)
            everything.update(got["rows"] if got else {})
        best = best_by_family(everything, family_of)
        top = sorted(best.items(), key=lambda kv: kv[1][0], reverse=True)[:TOP_FAMILIES_F80]
        variants = {}
        for fam, _ in top:
            cands = family_cands(ctx, fam, news_days, start_ns, market, mods)
            e5 = fam.startswith("E5.")
            for x in EXITS:
                variants[f"{fam}:{x}:F80"] = (cands, x, {"f80": True, "e5": e5})
        rows, skips = evaluate_all(variants)
        section("f80", "F80 overlay on the top families (5 years)", rows, skips,
                ["Top families by best 5-year avg R (>= 30 trades): "
                 + ", ".join(f"{fam} ({name} {r:+.3f})" for fam, (r, name) in top) + "."])

    if "v2" in sections:
        from data.market_evidence import TIMEFRAMES
        from replay.engine import WINDOW
        warm = max(f.index[WINDOW - 1] + TIMEFRAMES[tf] for tf, f in frames.items()).to_pydatetime()
        slots = bt.decision_slots(max(start, warm), end)  # full 500-bar windows, as intraday_backtest.main
        v2_dir = os.path.join(args.out, "v2")
        os.makedirs(v2_dir, exist_ok=True)
        signals = v2_records(bt.signals_by_year(args.data, v2_dir, slots, args.workers, log), ctx)
        from replay import fast_scouts
        rows, skips = {}, {}
        with fast_scouts.patched():
            for key, name in V2_NAMES.items():
                trades, sk, _, _ = bt.run_policy(signals, market, key)
                bt.add_gross(trades, market)
                rows[name], skips[name] = evaluate(trades, years), sk
        valid = sum(1 for s in signals if s.get("status") == "VALID_SETUP")
        section("v2", "R1. Production signals V2_F3 / V2_I2 (5 years)", rows, skips,
                [f"Slots {len(signals)} (15 min, LONDON/NEW_YORK) from {max(start, warm):%Y-%m-%d %H:%M} UTC, "
                 f"VALID_SETUP {valid}. `V2_I2:SL25` / `V2_I2:ADR` re-exit the production V2_I2 plans (planned entry, "
                 "no extra risk check)."])

    if "final" in sections:
        final(args, ctx, years, log)
    return state


def family_cands(ctx, fam, news_days, start_ns, market, mods):
    """Candidates of a family label used in the summaries (E1-E4 / E5.Ex / JT / M* / pairs / ALL / F*)."""
    base = fam[3:] if fam.startswith("E5.") else fam
    if base in ("E1", "E2", "E3", "E4"):
        found = es.entry_candidates(ctx, base)
    elif base == "JT" or base.startswith("JT["):
        relax = r2.Relax() if base == "JT" else r2.Relax.parse(base[3:-1])
        found = r2.jt_relaxed_all(ctx, relax, news_days, lambda k: market.half_spread_at(k)[0], start_ns)[0]
    elif base in r3.FAMILIES:
        found = r3.family_candidates(ctx, base)
    else:
        mods = mods or r3.modules(ctx)
        members = ("M1", "M2", "M3", "M4", "M5") if base == "ALL" else tuple(base.split("&"))
        found = r3.combine(ctx, mods, members, base)
    return [c for c in found if c.t >= start_ns]


def final(args, ctx, years, log):
    everything, groups = {}, {}
    for key in ("v2", "e", "jt", "modules", "pairs", "families", "f80"):
        got = load(args.out, key)
        if got:
            everything.update(got["rows"])
            groups[key] = len(got["rows"])
    total = len(everything)
    p = p_false_pass()
    passers = [n for n, s in everything.items() if s["pass"]]

    def closeness(s):
        return (s["years_ok"], s["total_r"] > 0, s["dd"] <= MAX_DD_R, s["cost"] is not None and s["cost"] <= MAX_COST_R,
                s["avg_r"] if s["avg_r"] is not None else -math.inf)
    ranked = sorted((n for n, s in everything.items() if s["trades"] >= MIN_TRADES_TOP),
                    key=lambda n: closeness(everything[n]), reverse=True)
    yearly = Counter(s["years_ok"] for s in everything.values())
    lines = ["## Multiple testing", "",
             f"**Tests run: {total}** ({', '.join(f'{k} {v}' for k, v in groups.items())}; declared "
             f"{DECLARED_TOTAL}). P(no-edge variant clears >= {PASS_YEARS} of {YEARS} one-sided 10% yearly tests) = "
             f"{p:.5f} -> **{total * p:.3f} expected false passers** if the tests were independent. They are not: "
             "the four exits of a family share the same entries, E5/F80 are subsets of their base, pairs/ALL are "
             "subsets of modules and JT combos are nested; the effective number of independent tests is well below "
             f"{total}, which lowers the expected false-pass count further but also means one passer and its "
             "siblings are one finding, not several.", "",
             "Years passing (avg R > +0.10 and CI80 low > 0) across all variants: "
             + ", ".join(f"{k}/5: {yearly.get(k, 0)}" for k in range(YEARS, -1, -1)) + ".", "",
             "## Shortlist", ""]
    if passers:
        lines.append(f"**Passers ({len(passers)}): {', '.join(passers)}.** These still need a fresh pre-registered "
                     "period (live paper) before any decision.")
        shortlist = passers[:3] + [n for n in ranked if n not in passers][:max(0, 3 - len(passers))]
    else:
        lines.append("**No variant passes.** The 3 closest (most years passing, then total R > 0, DD, cost, avg "
                     "R; >= 30 trades):")
        shortlist = ranked[:3]
    lines += ["", table({n: everything[n] for n in shortlist}), ""]
    for n in shortlist:
        s = everything[n]
        lines.append(f"- **{n}**: {'passes' if s['pass'] else 'fails on ' + ', '.join(s['fails'])}; 5y avg R "
                     f"{_f(s['avg_r'], '+.3f')}, total {s['total_r']:+.1f}R over {s['trades']} trades, MTM DD "
                     f"{s['dd']:.1f}R, cost {_f(s['cost'], '.3f')}R, per-year avg R "
                     + ", ".join(_f(y['avg_r'], '+.2f') for y in s["years"]) + ".")
    plot = shortlist + [n for n in ("V2_F3", "JT:FIX2") if n in everything and n not in shortlist]
    plot_equity(os.path.join(os.path.dirname(args.report), "5y_equity_r.png"), everything, plot[:5], years)
    lines += ["", f"Equity: `5y_equity_r.png` ({', '.join(plot[:5])}).", ""]
    append(args.report, "\n".join(lines))
    log(f"final: {total} tests, passers {passers}, shortlist {shortlist}")


def plot_equity(path, rows, names, years):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    colors = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]  # categorical slots 1-5, fixed order
    ink, muted, surface = "#0b0b0b", "#52514e", "#fcfcfb"
    fig, ax = plt.subplots(figsize=(11, 5), dpi=120, facecolor=surface)
    ax.set_facecolor(surface)
    for q, (a, b) in enumerate(years):
        if q % 2:
            ax.axvspan(a, b, color="#e8e8e6", alpha=0.5, linewidth=0)
        ax.text(a, 1.0, f" Y{q + 1}", transform=ax.get_xaxis_transform(), va="top", fontsize=8, color=muted)
    for color, name in zip(colors, names):
        eq = rows[name]["equity"]
        if not eq:
            continue
        xs = [years[0][0]] + [pd.Timestamp(t).to_pydatetime() for t, _ in eq]
        ys = np.concatenate([[0.0], np.cumsum([r for _, r in eq])])
        ax.step(xs, ys, where="post", color=color, linewidth=2, label=name)
        ax.annotate(f"{name} {ys[-1]:+.1f}R", (xs[-1], ys[-1]), xytext=(6, 0), textcoords="offset points",
                    va="center", fontsize=9, color=ink)
    ax.axhline(0, color=muted, linewidth=0.8)
    ax.set_title("EURUSD 5-year replay - cumulative R (net of spread + slippage): shortlist and references",
                 loc="left", color=ink, fontsize=10)
    ax.set_ylabel("Cumulative R", color=muted)
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


if __name__ == "__main__":
    main()
