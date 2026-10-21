"""Round 6 (REPLAY ONLY): one combined EURUSD system ("COMBO") with a strict train / holdout split.

Modules (candidates exactly as in rounds 2-5): E3 (Asian-range breakout + retest), JT[R1+R5] (JT_SMC without the 1h-FVG
requirement, limit at the 15m OB edge) and F2 (previous-day range mean reversion). A causal D1 regime router sends E3 /
JT to TREND days (D1-trend direction only) and F2 to RANGE days; an ATR14(D1) percentile filter skips the top decile.
Stop = max(structure stop, k x ATR14(D1)) clamped to 40-60 pips; one entry; breakeven at +1R, then a chandelier trail at
t x ATR14(H1) on closed H1 bars; no TP; time exit after H NY trading days; Friday 17:00 NY close; 0.5 % fixed-fractional
risk (R and compounded % equity); 4R (= 2 %) realised daily loss halt.

Protocol: the 16-combo grid is selected on TRAIN (2021-10-01 .. 2024-12-31) only, from frames sliced to < 2025-01-01
(``load_frames(end=TRAIN_END)``), by anchored walk-forward under C_RAW; the chosen config is written to
``round6_frozen.json`` (and committed as ``replay/round6_frozen.json``) before the holdout runner, which reads only that
file, runs once on 2025-01-01 .. data end under C_OANDA / C_RAW / C_RAW_STRESS.

Run (sections append to the report in order):
``python -m replay.round6 --data /workspace/backtest/data5y --out /workspace/backtest/r6
--report /workspace/backtest/report_round6.md --frozen /workspace/backtest/round6_frozen.json --sections declare``;
then ``--sections select`` (writes the frozen file), commit it, then ``--sections holdout`` and ``--sections final``.
Market data and outputs live outside the repository and are never committed.
"""
import argparse
import hashlib
import itertools
import json
import math
import os
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from replay import entry_search as es
from replay import fiveyear as fy
from replay import intraday_backtest as bt
from replay import round4 as r4
from replay import round5 as r5

HOME = "EURUSD"
PIP = bt.PIP
UTC = timezone.utc
TRAIN_START = datetime(2021, 10, 1, tzinfo=UTC)
TRAIN_END = datetime(2025, 1, 1, tzinfo=UTC)  # exclusive: selection data are sliced to bars starting before it
HOLDOUT_START = TRAIN_END
HOLDOUT_LAST_DAY = datetime(2026, 10, 19, tzinfo=UTC)
HOLDOUT_END = datetime(2026, 10, 20, tzinfo=UTC)  # exclusive: holdout frames are sliced to bars starting before it
FOLDS = [  # anchored: train = [TRAIN_START, test start), test = [test start, test end)
    ("F1", datetime(2023, 1, 1, tzinfo=UTC), datetime(2023, 7, 1, tzinfo=UTC)),
    ("F2", datetime(2023, 7, 1, tzinfo=UTC), datetime(2024, 1, 1, tzinfo=UTC)),
    ("F3", datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 7, 1, tzinfo=UTC)),
    ("F4", datetime(2024, 7, 1, tzinfo=UTC), datetime(2025, 1, 1, tzinfo=UTC)),
]
PERIODS = [("2021Q4", datetime(2021, 10, 1, tzinfo=UTC), datetime(2022, 1, 1, tzinfo=UTC)),
           ("2022", datetime(2022, 1, 1, tzinfo=UTC), datetime(2023, 1, 1, tzinfo=UTC)),
           ("2023", datetime(2023, 1, 1, tzinfo=UTC), datetime(2024, 1, 1, tzinfo=UTC)),
           ("2024", datetime(2024, 1, 1, tzinfo=UTC), datetime(2025, 1, 1, tzinfo=UTC)),
           ("2025", datetime(2025, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC)),
           ("2026YTD", datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 10, 20, tzinfo=UTC))]

MODULES = ("E3", "JT[R1+R5]", "F2")  # tie order for candidates on the same 5m close
TREND_MODULES = ("E3", "JT[R1+R5]")
ADX_N, ATR_D1_N, ATR_H1_N, SMA_FAST, SMA_SLOW = 14, 14, 14, 20, 50
SLOPE_BARS = 5  # SMA20 slope = SMA20[k] - SMA20[k - 5] (one trading week of closed D1 bars)
PCT_DAYS, PCT_MIN = 250, 50  # ATR14(D1) percentile among the last 250 values (>= 50 known, else no trade)
CLAMP_PIPS = (40.0, 60.0)
BE_R = 1.0
RISK = 0.005  # 0.5 % of equity per trade
HALT_R = 4.0  # 2 % realised daily loss at 0.5 % risk
MIN_TRAIN_TRADES = 30  # fold selection: combos with fewer purged train trades are ineligible (if any has 30)
GRID = {"a": (20, 25), "k": (1.0, 1.5), "t": (1.5, 2.5), "H": (3, 5)}
V = 0.9
BASE_CFG = {"v": V, "clamp": list(CLAMP_PIPS), "cap_r": None, "friday": True, "risk": RISK, "halt_r": HALT_R,
            "be_r": BE_R, "modules": list(MODULES)}
PASS_BAR = {"avg_r": 0.08, "pf": 1.15, "dd": 12.0, "tpm": 6.0}
PREVIOUS_TESTS = 428
MODELS = {"C_OANDA": r5.C_OANDA, "C_RAW": r5.C_RAW, "C_RAW_STRESS": r5.C_RAW_STRESS}
FROZEN_REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "round6_frozen.json")
INFO_VARIANTS = {"no clamp": {"clamp": None}, "TP cap 4R": {"cap_r": 4.0}}  # reported, never selected on
SEL = "C_RAW"


def combo_name(c):
    return f"a{c['a']}_k{c['k']:g}_t{c['t']:g}_H{c['H']}"


def grid():
    return [dict(zip(GRID, values)) for values in itertools.product(*GRID.values())]


def config(combo, **over):
    return {**BASE_CFG, **combo, "name": combo_name(combo), **over}


def neighbours(combo):
    """All 1-step neighbours in the grid (one parameter moved to its other value)."""
    out = []
    for key, values in GRID.items():
        for v in values:
            if v != combo[key]:
                out.append({**combo, key: v})
    return out


# ---------------------------------------------------------------- data (selection: sliced before TRAIN_END)
def load_frames(data, end=None):
    """EURUSD 5m / 15m / 1h mid frames; ``end``: keep bars starting before it only (they end by it)."""
    frames = bt.load_csv_frames(data, HOME)
    if end is not None:
        frames = {tf: f[f.index < pd.Timestamp(end)] for tf, f in frames.items()}
    return frames


class Regime:
    """Causal D1 features from complete D1 bars (17:00 NY trading days built from closed 1h bars): a bar is used by
    decisions at or after its end. ADX14 (Wilder), SMA20 slope over 5 bars, close vs SMA50, ATR14 (mean true range)
    and its percentile among the last 250 ATR14 values (share <= the current one)."""

    def __init__(self, frames):
        d1 = r4.htf_bars(frames["1h"], "D1")
        self.avail, self.close = d1.avail, d1.c
        self.adx = r4.adx(d1, ADX_N)
        fast = r4.sma(d1.c, SMA_FAST)
        self.slope = fast - r4.shift(fast, SLOPE_BARS)
        self.slow = r4.sma(d1.c, SMA_SLOW)
        self.atr = r4.atr(d1, ATR_D1_N)
        self.pct = trailing_percentile(self.atr, PCT_DAYS, PCT_MIN)

    def index(self, t):
        return int(np.searchsorted(self.avail, t, side="right")) - 1

    def at(self, t, a):
        """(regime, direction, ATR14 D1 price, ATR percentile): regime 'TREND' / 'RANGE' / 'CONFLICT' (ADX >= a but
        slope and close-vs-SMA50 disagree) / 'NO_DATA'."""
        k = self.index(t)
        if k < 0:
            return "NO_DATA", 0, math.nan, math.nan
        values = (self.adx[k], self.slope[k], self.slow[k], self.atr[k], self.pct[k])
        if not all(math.isfinite(x) for x in values):
            return "NO_DATA", 0, math.nan, math.nan
        if self.adx[k] < a:
            return "RANGE", 0, float(self.atr[k]), float(self.pct[k])
        up = self.slope[k] > 0 and self.close[k] > self.slow[k]
        down = self.slope[k] < 0 and self.close[k] < self.slow[k]
        if up or down:
            return "TREND", 1 if up else -1, float(self.atr[k]), float(self.pct[k])
        return "CONFLICT", 0, float(self.atr[k]), float(self.pct[k])


def trailing_percentile(x, days, min_values):
    out = np.full(len(x), np.nan)
    for k in range(len(x)):
        if not math.isfinite(x[k]):
            continue
        w = x[max(0, k - days + 1):k + 1]
        w = w[np.isfinite(w)]
        if len(w) >= min_values:
            out[k] = float((w <= x[k]).mean())
    return out


class System:
    """EURUSD frames -> context, regime, H1 bars and per-cost-model tapes / candidates (built lazily)."""

    def __init__(self, frames):
        self.frames = frames
        self.end = frames["5m"].index[-1].to_pydatetime() + pd.Timedelta(minutes=5)
        with r4.instrument_pip(PIP), r4.schedule(()):
            self.ctx = es.Context(frames)
        self.regime = Regime(frames)
        self.h1 = r4.htf_bars(frames["1h"], "H1")
        self.atr_h1 = r4.atr(self.h1, ATR_H1_N)
        self.start_ns = int(self.ctx.tradable_start())
        self.news = es.high_impact_days(TRAIN_START.date(), self.end.date())
        self._tapes, self._cands = {}, {}

    def tape(self, model):
        key = r5.model_name(model)
        if key not in self._tapes:
            self._tapes[key] = r5.model_tape(model, HOME, self.frames["5m"], PIP, True)
        return self._tapes[key]

    def candidates(self, model):
        """Merged module candidates sorted by (t, module order); only JT R5 limit touches depend on the model."""
        key = r5.model_name(model)
        if key not in self._cands:
            tape = self.tape(model)
            out = []
            with r4.instrument_pip(PIP), r4.schedule(()):
                for q, fam in enumerate(MODULES):
                    for c in r5.family_candidates(self.ctx, fam, self.news, self.start_ns,
                                                  lambda k: float(tape.spread[k]) / 2):
                        out.append((c.t, q, fam, c))
            out.sort(key=lambda x: (x[0], x[1]))
            self._cands[key] = [(fam, c) for _, _, fam, c in out]
        return self._cands[key]


# ---------------------------------------------------------------- the walk
def stop_distance(cfg, s, fill, struct, atr_h1, atr_d1):
    """(distance, None) or (None, skip): max(structure stop, k x ATR14 D1), clamped to ``cfg['clamp']`` pips."""
    if not math.isfinite(atr_h1):
        return None, "atr_unavailable"
    structure = s * (fill - (struct - s * bt.BUFFER_ATR * atr_h1))
    if structure <= 0:
        return None, "structure_beyond_fill"
    dist = max(structure, cfg["k"] * atr_d1)
    if cfg.get("clamp"):
        lo, hi = cfg["clamp"]
        dist = min(max(dist, lo * PIP), hi * PIP)
    return dist, None


def stop_events(tape, h1, atr_h1, i, s, fill, dist, hs, horizon_ns, trail, be_r=BE_R, limit_fill=False):
    """Breakeven + trail ratchets of a position filled on 5m bar ``i``: once a 5m bar (bid LONG / ask SHORT; the limit
    fill bar excluded) reaches fill + be_r x R, the stop moves to the fill from the next bar; from then on, at each
    H1 close, to the best H1 close since the fill -/+ ``trail`` x ATR14(H1) of that bar (closed bars only)."""
    five = 5 * es.MINUTE
    lo = i + 1 if limit_fill else i
    hi = int(np.searchsorted(tape.start, horizon_ns - five, side="right"))
    if hi <= lo:
        return []
    favour = (tape.h[lo:hi] if s > 0 else tape.l[lo:hi]) - s * hs
    hit = np.flatnonzero(s * (favour - (fill + s * be_r * dist)) >= 0)
    if not len(hit):
        return []
    be_ns = int(tape.start[lo + int(hit[0])]) + five
    events = [(be_ns, "stop", fill)]
    m0 = int(np.searchsorted(h1.avail, int(tape.start[i]), side="right"))
    m_end = int(np.searchsorted(h1.avail, horizon_ns, side="right"))
    if m_end <= m0:
        return events
    closes = h1.c[m0:m_end]
    best = np.maximum.accumulate(closes) if s > 0 else np.minimum.accumulate(closes)
    for m in range(max(m0, int(np.searchsorted(h1.avail, be_ns, side="left"))), m_end):
        if math.isfinite(atr_h1[m]):
            events.append((int(h1.avail[m]), "stop", float(best[m - m0] - s * trail * atr_h1[m])))
    return events


def exit_kind(trade, s):
    detail = trade["detail"]
    if detail in ("SL", "TRAIL"):
        level = trade["exit"] + s * trade["slip"]
        if detail == "TRAIL" and math.isclose(level, trade["fill"], rel_tol=0, abs_tol=1e-9):
            return "BE"
        return detail
    if detail == "GAP_SL":
        return "GAP"
    if detail in (r4.TP, "GAP_TP"):
        return "TP4R"
    return detail  # TIME / FRIDAY / OPEN_AT_END


def run_system(system, model, cfg, name=None):
    """Sequential walk of COMBO under ``cfg``: (trades, skips). One position at a time, one entry per idea, fill at the
    open of the 5m bar starting at the decision close (R5 limit candidates: ``round2.limit_fill`` semantics, no
    slippage), router + volatility filter at the decision, ``HALT_R`` realised daily loss halt (exit trading day)."""
    tape, ctx, regime = system.tape(model), system.ctx, system.regime
    trades, skips = [], Counter()
    free_at, used, halted = -1, set(), set()
    day_r = defaultdict(float)
    for fam, cand in system.candidates(model):
        idea = f"{fam}|{cand.idea}"
        if idea in used:
            skips["idea_already_traded"] += 1
            continue
        if cand.t < free_at:
            skips["position_open"] += 1
            continue
        if bt.day_key(r4._dt(cand.t)) in halted:
            skips["daily_loss_halt"] += 1
            continue
        s = 1 if cand.side == es.LONG else -1
        state, direction, atr_d1, pct = regime.at(cand.t, cfg["a"])
        if state in ("NO_DATA", "CONFLICT"):
            skips[f"regime_{state.lower()}"] += 1
            continue
        if fam in TREND_MODULES and (state != "TREND" or s != direction):
            skips["trend_module_off" if state != "TREND" else "against_d1_trend"] += 1
            continue
        if fam not in TREND_MODULES and state != "RANGE":
            skips["range_module_off"] += 1
            continue
        if pct > cfg["v"]:
            skips["atr_percentile_high"] += 1
            continue
        i = int(np.searchsorted(tape.start, cand.t))
        if i >= len(tape):
            skips["no_fill_data"] += 1
            continue
        if int(tape.start[i]) != cand.t:
            skips["no_next_bar"] += 1
            continue
        cost = tape.costs(i)
        if cost is None:
            skips["no_causal_spread"] += 1
            continue
        hs, slip = cost
        limit = getattr(cand, "limit", None)
        if limit is None:
            mid_fill = float(tape.o[i])
            fill = mid_fill + s * (hs + slip)
        else:
            at_open = float(tape.o[i]) + s * hs
            fill = at_open if s * (at_open - limit) <= 0 else limit
            mid_fill = fill - s * hs
        dist, why = stop_distance(cfg, s, fill, cand.struct, ctx.atr_at(cand.t), atr_d1)
        if dist is None:
            skips[why] += 1
            continue
        stop = fill - s * dist
        target = fill + s * cfg["cap_r"] * dist if cfg.get("cap_r") else None
        fill_at = tape.stamp(i)
        closes = [(bt._ns(r5.nth_trading_close(fill_at, cfg["H"])), "close", bt.TIME)]
        if cfg.get("friday", True):
            closes.append((bt._ns(bt.friday_close_after(fill_at)), "close", bt.FRIDAY))
        closes = sorted(closes)[:1]
        events = stop_events(tape, system.h1, system.atr_h1, i, s, fill, dist, hs, closes[0][0], cfg["t"],
                             cfg.get("be_r", BE_R), limit is not None) + closes
        events.sort(key=lambda e: (e[0], e[1] != "stop"))
        trade, free_ns = r4.walk_trade(tape, i, s, fill, mid_fill, stop, target, hs, slip, events,
                                       name or cfg["name"], cand.t, idea, no_tp_on_fill=limit is not None)
        trade.update(module=fam, regime=state, entry="LIMIT" if limit is not None else "MARKET",
                     exit_kind=exit_kind(trade, s))
        if trade["exit_kind"] == "BE":
            trade["reason"] = bt.BE
        trades.append(trade)
        used.add(idea)
        free_at = free_ns
        key = bt.day_key(trade["exit_at"])
        day_r[key] += trade["r"]
        if day_r[key] <= -cfg["halt_r"]:
            halted.add(key)
    return trades, skips


# ---------------------------------------------------------------- metrics
def window(trades, a, b, purge=False):
    """Trades whose decision is in [a, b) (``purge``: and that exited by b)."""
    return [t for t in trades if a <= t["signal_at"] < b and (not purge or t["exit_at"] <= b)]


def equity_pct(trades, risk=RISK):
    """Compounded % equity at ``risk`` per trade (exit order): (total return %, max realised drawdown %)."""
    eq, peak, dd = 1.0, 1.0, 0.0
    for t in sorted(trades, key=lambda x: x["exit_at"]):
        eq *= 1.0 + risk * t["r"]
        peak = max(peak, eq)
        dd = max(dd, 1.0 - eq / peak)
    return 100.0 * (eq - 1.0), 100.0 * dd


def profit_factor(rs):
    win, loss = sum(r for r in rs if r > 0), -sum(r for r in rs if r < 0)
    return win / loss if loss > 0 else (math.inf if win > 0 else None)


def summarize(trades, a, b, risk=RISK):
    months = (b - a).total_seconds() / fy.MONTH_S
    m = bt.metrics(trades, months)
    n = len(trades)
    rs = [t["r"] for t in trades]
    total_pct, dd_pct = equity_pct(trades, risk)
    modules = {}
    for fam in MODULES:
        mine = [t["r"] for t in trades if t.get("module") == fam]
        modules[fam] = {"n": len(mine), "total_r": float(sum(mine)),
                        "avg_r": float(np.mean(mine)) if mine else None}
    kinds = Counter(t.get("exit_kind", t["reason"]) for t in trades)
    hold_d = [(t["exit_at"] - t["fill_at"]).total_seconds() / 86400 for t in trades]
    return {"trades": n, "tpm": m["trades_per_month"], "win": m["win_rate"] if n else None,
            "avg_r": m["avg_r"] if n else None, "pf": profit_factor(rs), "total_r": m["total_r"],
            "dd": m["max_dd_mtm_r"], "hold_d": float(np.median(hold_d)) if n else None,
            "exits": {k: 100.0 * v / n for k, v in sorted(kinds.items())}, "modules": modules,
            "ci80_low": m["ci80_low"], "cost": m["avg_cost_r"] if n else None,
            "sl_pips": m["sl_pips"][0] if n else None, "pct": total_pct, "dd_pct": dd_pct}


def per_period(trades):
    return {label: summarize(window(trades, a, b), a, b) for label, a, b in PERIODS}


def holdout_pass(s):
    """Holdout bar (C_RAW): avg R >= +0.08, PF > 1.15, MTM DD < 12R, >= 6 trades/month: (pass, failed list)."""
    fails = []
    if not s["trades"]:
        return False, ["no trades"]
    if not s["avg_r"] >= PASS_BAR["avg_r"]:
        fails.append(f"avg R {s['avg_r']:+.3f} < +{PASS_BAR['avg_r']}")
    if not (s["pf"] is not None and s["pf"] > PASS_BAR["pf"]):
        fails.append(f"PF {_f(s['pf'], '.2f')} <= {PASS_BAR['pf']}")
    if not s["dd"] < PASS_BAR["dd"]:
        fails.append(f"MTM DD {s['dd']:.1f}R >= {PASS_BAR['dd']:g}R")
    if not s["tpm"] >= PASS_BAR["tpm"]:
        fails.append(f"{s['tpm']:.1f} trades/month < {PASS_BAR['tpm']:g}")
    return not fails, fails


def _f(x, spec):
    return fy._f(x, spec)


# ---------------------------------------------------------------- selection (TRAIN only)
def fold_rows(trades):
    """Per fold: purged train metrics on [TRAIN_START, test start) and test metrics on [test start, test end)."""
    out = {}
    for label, a, b in FOLDS:
        out[label] = {"train": summarize(window(trades, TRAIN_START, a, purge=True), TRAIN_START, a),
                      "test": summarize(window(trades, a, b), a, b)}
    return out


def select(results):
    """``results``: {combo name: {"folds": fold_rows, "train": summarize}}. Per fold: the combo with the best purged
    train avg R (>= MIN_TRAIN_TRADES if any qualifies); final: best mean fold-test avg R (an empty fold counts 0),
    tie-break lower full-train MTM DD, then name."""
    per_fold = {}
    for label, _, _ in FOLDS:
        rows = {n: r["folds"][label]["train"] for n, r in results.items() if r["folds"][label]["train"]["trades"]}
        ok = {n: s for n, s in rows.items() if s["trades"] >= MIN_TRAIN_TRADES} or rows
        per_fold[label] = max(sorted(ok), key=lambda n: ok[n]["avg_r"]) if ok else None
    means = {n: mean_test(r) for n, r in results.items()}
    final = min(results, key=lambda n: (-means[n], results[n]["train"]["dd"], n))
    return per_fold, means, final


def mean_test(result):
    return float(np.mean([result["folds"][label]["test"]["avg_r"] or 0.0 for label, _, _ in FOLDS]))


def freeze(path, cfg, selection):
    """Write the frozen config (the only input of the holdout runner)."""
    payload = {"system": "COMBO", "symbol": HOME, "config": cfg, "selection": selection,
               "train": [TRAIN_START.isoformat(), TRAIN_END.isoformat()],
               "frozen_at": datetime.now(UTC).isoformat(timespec="seconds")}
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    for p in (path, FROZEN_REPO):
        with open(p, "w", encoding="utf-8") as handle:
            handle.write(text)
    return payload


def load_frozen(path):
    """The frozen config: the holdout runner's only source of parameters. The file must equal the committed
    ``replay/round6_frozen.json`` (fails closed: refuses an edited / uncommitted freeze or an unavailable git)."""
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    payload = json.loads(text)
    committed = committed_frozen()
    if committed is None:
        raise RuntimeError("cannot verify the frozen file: git unavailable or replay/round6_frozen.json not in HEAD")
    if committed != text:
        raise RuntimeError("frozen file differs from the committed replay/round6_frozen.json")
    cfg = payload["config"]
    missing = [k for k in ("a", "k", "t", "H", "v", "clamp", "friday", "risk", "halt_r", "name") if k not in cfg]
    if missing:
        raise ValueError(f"frozen config incomplete: {missing}")
    return cfg


def committed_frozen():
    """HEAD content of replay/round6_frozen.json; None when git is unavailable, outside a checkout or not committed
    (``load_frozen`` then refuses)."""
    repo = os.path.dirname(os.path.dirname(FROZEN_REPO))
    try:
        out = subprocess.run(["git", "-C", repo, "show", "HEAD:replay/round6_frozen.json"], capture_output=True,
                             text=True, check=False)
    except OSError:
        return None
    return out.stdout if out.returncode == 0 else None


def holdout_runs(frozen_path, system, models=MODELS):
    """Run the frozen config once per cost model on ``system`` (frames sliced before HOLDOUT_END); config read only from ``frozen_path``.
    Info variants (Friday close OFF, no clamp, TP cap 4R) under C_RAW, never used for selection."""
    cfg = load_frozen(frozen_path)
    runs = {m: run_system(system, model, cfg)[0] for m, model in models.items()}
    info = {"Friday close OFF": run_system(system, r5.C_RAW, {**cfg, "friday": False})[0]}
    for label, over in INFO_VARIANTS.items():
        info[label] = run_system(system, r5.C_RAW, {**cfg, **over})[0]
    return cfg, runs, info


# ---------------------------------------------------------------- report
DECLARATION = """\
## 0. Predeclared design (written before any round-6 result)

**Pre-step (committed `38dbcfb`)**: round-5 Codex HIGH fixed (the full-sample EURUSD ADR20 reference 74.3 pips ->
causal `round5.ReferenceADR`, median of the last 250 ADR20 values known at the decision) and MED fixed (relaxed SL
> 50 pips clamped, not skipped); round-5 steps 3-4 re-run: still no passer (addendum in `report_round5.md`). Round 6
does not use either piece (EURUSD only, own stop rule).

**Data**: `data5y` EURUSD 5m / 15m / 1h mid OHLC (OANDA), 2021-10-01 .. 2026-10-19.
- **TRAIN** = 2021-10-01 .. 2024-12-31. Selection runs read frames **sliced to bars starting before 2025-01-01 00:00
  UTC** (`load_frames(end=TRAIN_END)`); all features are causal. A position still open at the cut is closed at the last
  train bar (OPEN_AT_END).
- **Anchored walk-forward inside TRAIN** (decision time, UTC): F1 train 2021-10-01..2022-12-31 / test 2023H1; F2 ..2023-06-30
  / 2023H2; F3 ..2023-12-31 / 2024H1; F4 ..2024-06-30 / 2024H2. Fold train metrics are purged (a train trade must exit
  before the test start). One full-train run per combo, split by decision time (no parameter is fitted inside a fold
  except the combo choice).
- **HOLDOUT** = 2025-01-01 .. 2026-10-19 (decisions from 2025-01-01): run **once**, after the frozen config is written
  to `/workspace/backtest/round6_frozen.json` and committed as `replay/round6_frozen.json`; the holdout runner reads
  only that file and refuses one that differs from the committed copy. Holdout run = full data (features warm up on
  train), trades with decision >= 2025-01-01 reported.

**System COMBO** (EURUSD; one position at a time; one entry per signal):
- Modules (candidates unchanged from rounds 2-5): **E3** (Asian breakout + retest, market fill), **JT[R1+R5]** (no 1h-FVG,
  limit at the 15m OB edge; R5 touch on the cost model's bid/ask), **F2** (previous-day range mean reversion, market fill).
  Same-close ties: E3, JT, F2.
- **Router** (closed D1 bars = 17:00 NY trading days from closed 1h bars, used from their end): ADX14 (Wilder) >= a AND
  sign(SMA20[k] - SMA20[k-5]) agrees with sign(close - SMA50) -> **TREND** (direction = that sign): E3 / JT only, only
  in that direction; ADX14 < a -> **RANGE**: F2 only (both sides); ADX >= a with disagreement -> no trade (CONFLICT);
  missing features -> no trade (NO_DATA).
- **Volatility filter**: skip if the ATR14(D1) percentile (share of the last 250 ATR14(D1) values <= the current one; >= 50
  values required, else NO_DATA) > v = 0.9 (fixed).
- **Stop**: max(structure stop = structure -/+ 0.2 ATR14(H1) from the fill, k x ATR14(D1)), clamped to **40-60 pips**.
- **Exit**: at +1R (5m bid LONG / ask SHORT; limit-fill bar excluded) the stop moves to the fill (breakeven) from the next
  5m bar; from then on, at every H1 close, the stop ratchets to the best H1 close since the fill -/+ t x ATR14(H1)
  (closed bars); **no TP**; time exit at the 17:00 NY close of the H-th NY trading day (fill day = 1); **Friday 17:00 NY
  close ON**; gaps fill at the open; same-bar SL first.
- **Sizing**: 0.5 % fixed-fractional risk per trade: results in R and in compounded % equity (exit order); **2 % (= 4R)
  realised daily loss halt** (no new entries for the rest of the NY trading day of the exit).
- **Costs**: `C_RAW` (selection; EURUSD 0.2 pip spread + 0.7 pip commission once in R + 0.1 pip slippage = ~1.1 pip
  round trip), `C_OANDA` (causal 30-day median spread + max(0.1 x spread, 0.2 pip) slippage), `C_RAW_STRESS` (0.4 + 0.7
  + 0.2). 0 % carry.

**Grid (16 combos, fixed)**: a in {{20, 25}} x k in {{1.0, 1.5}} x t in {{1.5, 2.5}} x H in {{3, 5}}; v = 0.9.
- Fold selection: best purged fold-train avg R under C_RAW (>= 30 trades if any combo has them); fold-test results of the
  fold winners are reported (walk-forward OOS of the procedure).
- **Final selection**: best mean of the 4 fold-test avg R (C_RAW; an empty fold counts 0), tie-break lower full-train MTM
  DD. Neighbourhood stability: the 4 one-step grid neighbours' train avg R.
- **Info only (never selected on)**: the selected combo without the 40-60 clamp and with a 4R TP cap (train and holdout,
  C_RAW), Friday close OFF (holdout only, C_RAW).

**Metrics**: avg R, profit factor, total R, MTM max DD R, trades/month, win %, median hold (days), exit mix (SL / BE / TRAIL
/ GAP / TIME / FRIDAY / TP4R / OPEN_AT_END), per-module contribution, per period (2021Q4, 2022, 2023, 2024, 2025, 2026YTD),
day-block bootstrap CI80 low, compounded % equity and its max DD.

**Holdout pass bar (C_RAW)**: avg R >= +0.08 AND PF > 1.15 AND MTM DD < 12R AND >= 6 trades/month. C_OANDA and
C_RAW_STRESS reported alongside, not part of the bar.

**Tests**: 16 combos x 4 folds = 64 fold evaluations (+16 full-train) = **16 new strategy variants on train**; the holdout
is **1 test** of 1 frozen config (3 cost models of the same trades, strongly correlated). Cumulative with the 428 earlier
tests: 444 variants searched. The holdout is the only out-of-sample evidence; nothing is changed after seeing it.
"""


def fold_table(results, per_fold, means):
    head = "| Combo | " + " | ".join(f"{f} train avg R (n) / test avg R (n)" for f, _, _ in FOLDS) + \
        " | Mean test avg R | Train avg R | Train PF | Train total R | Train MTM DD | Train tr/mo |\n" + \
        "|---" * (7 + len(FOLDS)) + "|"
    lines = [head]
    for n in sorted(results, key=lambda x: -means[x]):
        r = results[n]
        cells = []
        for f, _, _ in FOLDS:
            tr, te = r["folds"][f]["train"], r["folds"][f]["test"]
            star = " *" if per_fold.get(f) == n else ""
            cells.append(f"{_f(tr['avg_r'], '+.3f')} ({tr['trades']}){star} / {_f(te['avg_r'], '+.3f')} ({te['trades']})")
        t = r["train"]
        lines.append(f"| {n} | " + " | ".join(cells) + f" | {means[n]:+.3f} | {_f(t['avg_r'], '+.3f')} | "
                     f"{_f(t['pf'], '.2f')} | {t['total_r']:+.1f} | {t['dd']:.1f} | {t['tpm']:.1f} |")
    return "\n".join(lines)


def summary_table(rows):
    head = ("| Run | Trades (tr/mo) | Win % | Avg R | PF | Total R | MTM DD R | Med hold d | CI80 low | Cost R | "
            "% equity (max DD %) | Exit mix % | E3 / JT / F2: n, total R |\n" + "|---" * 13 + "|")
    lines = [head]
    for name, s in rows.items():
        exits = ", ".join(f"{k} {v:.0f}" for k, v in s["exits"].items()) or "–"
        mods = " / ".join(f"{m['n']}, {m['total_r']:+.1f}" for m in s["modules"].values())
        lines.append(f"| {name} | {s['trades']} ({s['tpm']:.1f}) | "
                     f"{_f(s['win'] * 100 if s['win'] is not None else None, '.0f')} | {_f(s['avg_r'], '+.3f')} | "
                     f"{_f(s['pf'], '.2f')} | {s['total_r']:+.1f} | {s['dd']:.1f} | {_f(s['hold_d'], '.1f')} | "
                     f"{_f(s['ci80_low'], '+.3f')} | {_f(s['cost'], '.3f')} | {s['pct']:+.1f} ({s['dd_pct']:.1f}) | "
                     f"{exits} | {mods} |")
    return "\n".join(lines)


def period_table(by_run, labels=None):
    labels = labels or [p for p, _, _ in PERIODS]
    head = "| Run | " + " | ".join(labels) + " |\n" + "|---" * (1 + len(labels)) + "|"
    lines = [head]
    for name, periods in by_run.items():
        cells = [f"{_f(periods[p]['avg_r'], '+.3f')} / {periods[p]['total_r']:+.1f}R ({periods[p]['trades']})"
                 for p in labels]
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def skips_lines(skips):
    return [f"- {k}: {v}" for k, v in skips.most_common(10)]


def plot(path, curves, holdout_start, title):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    colors = ["#2a78d6", "#eb6834", "#1baf7a"]
    ink, muted, surface = "#0b0b0b", "#52514e", "#fcfcfb"
    fig, ax = plt.subplots(figsize=(11, 5), dpi=120, facecolor=surface)
    ax.set_facecolor(surface)
    end = max((t["exit_at"] for trades in curves.values() for t in trades), default=holdout_start)
    ax.axvspan(holdout_start, end, color="#e8e8e6", alpha=0.7, linewidth=0)
    ax.text(holdout_start, 1.0, " HOLDOUT (frozen config, run once)", transform=ax.get_xaxis_transform(), va="top",
            fontsize=8, color=muted)
    ax.text(TRAIN_START, 1.0, " TRAIN (selection)", transform=ax.get_xaxis_transform(), va="top", fontsize=8,
            color=muted)
    for color, (name, trades) in zip(colors, curves.items()):
        trades = sorted(trades, key=lambda t: t["exit_at"])
        if not trades:
            continue
        xs = [TRAIN_START] + [t["exit_at"] for t in trades]
        ys = np.concatenate([[0.0], np.cumsum([t["r"] for t in trades])])
        ax.step(xs, ys, where="post", color=color, linewidth=2, label=name)
        ax.annotate(f"{name} {ys[-1]:+.1f}R", (xs[-1], ys[-1]), xytext=(6, 0), textcoords="offset points",
                    va="center", fontsize=9, color=ink)
    ax.axhline(0, color=muted, linewidth=0.8)
    ax.set_title(title, loc="left", color=ink, fontsize=10)
    ax.set_ylabel("Cumulative R (0.5 % risk per trade)", color=muted)
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


# ---------------------------------------------------------------- persistence
def _ser(trades):
    keep = ("signal_at", "fill_at", "exit_at", "side", "module", "regime", "entry", "r", "gross_r", "risk_pips",
            "exit_kind", "reason", "fill", "stop", "exit", "commission_r")
    return [{k: (t[k].isoformat() if isinstance(t[k], datetime) else t[k]) for k in keep} for t in trades]


def save(out, key, payload):
    with open(os.path.join(out, f"r6_{key}.json"), "w", encoding="utf-8") as handle:
        json.dump(payload, handle, default=float)


def load(out, key):
    with open(os.path.join(out, f"r6_{key}.json"), encoding="utf-8") as handle:
        return json.load(handle)


def file_sha(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


# ---------------------------------------------------------------- main
def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", default="/workspace/backtest/data5y")
    parser.add_argument("--out", default="/workspace/backtest/r6")
    parser.add_argument("--report", default="/workspace/backtest/report_round6.md")
    parser.add_argument("--frozen", default="/workspace/backtest/round6_frozen.json")
    parser.add_argument("--png", default="/workspace/backtest/round6_equity_r.png")
    parser.add_argument("--sections", default="declare")
    args = parser.parse_args(argv)
    sections = args.sections.split(",")
    os.makedirs(args.out, exist_ok=True)

    def log(message):
        print(f"{datetime.now(UTC).isoformat(timespec='seconds')} {message}", flush=True)

    if "declare" in sections:
        with open(args.report, "w", encoding="utf-8") as handle:
            handle.write("# Round 6 - one combined EURUSD system (COMBO) with a strict holdout (REPLAY ONLY)\n\n"
                         f"Started {datetime.now(UTC).isoformat(timespec='seconds')} by `python -m replay.round6` "
                         "(branch `feat/backtest-v2-i2`). Production unchanged. Sections are appended as they "
                         "finish.\n\n")
        fy.append(args.report, DECLARATION.format())
        log("declared")

    if "select" in sections:
        select_section(args, log)

    if "holdout" in sections:
        holdout_section(args, log)

    if "final" in sections:
        final_section(args, log)


def select_section(args, log):
    log("selection: loading frames sliced before 2025-01-01")
    frames = load_frames(args.data, end=TRAIN_END)
    assert frames["5m"].index[-1] < pd.Timestamp(TRAIN_END), "holdout data in the selection frames"
    system = System(frames)
    train_end = min(TRAIN_END, system.end)
    results, skips, trades_by = {}, {}, {}
    for combo in grid():
        cfg = config(combo)
        trades, sk = run_system(system, r5.C_RAW, cfg)
        results[cfg["name"]] = {"combo": combo, "folds": fold_rows(trades),
                                "train": summarize(window(trades, TRAIN_START, TRAIN_END), TRAIN_START, train_end)}
        skips[cfg["name"]], trades_by[cfg["name"]] = sk, trades
        t = results[cfg["name"]]["train"]
        log(f"  {cfg['name']}: {t['trades']} trades, avg R {_f(t['avg_r'], '+.3f')}, PF {_f(t['pf'], '.2f')}, "
            f"DD {t['dd']:.1f}, mean fold test {mean_test(results[cfg['name']]):+.3f}")
    per_fold, means, final = select(results)
    combo = results[final]["combo"]
    cfg = config(combo)
    wf = [window(trades_by[per_fold[f]], a, b) if per_fold[f] else [] for f, a, b in FOLDS]
    wf_trades = [t for part in wf for t in part]
    wf_sum = summarize(wf_trades, FOLDS[0][1], FOLDS[-1][2])
    neigh = {combo_name(c): results[combo_name(c)]["train"] for c in neighbours(combo)}
    sel_trades = trades_by[final]
    extra = {"C_RAW (selected)": sel_trades}
    for m in ("C_OANDA", "C_RAW_STRESS"):
        extra[m] = run_system(system, MODELS[m], cfg)[0]
    for label, over in INFO_VARIANTS.items():
        extra[f"info: {label} (C_RAW)"] = run_system(system, r5.C_RAW, {**cfg, **over})[0]
    train_rows = {k: summarize(window(v, TRAIN_START, TRAIN_END), TRAIN_START, train_end) for k, v in extra.items()}
    selection = {"selected": final, "mean_fold_test_avg_r": means[final], "per_fold_winner": per_fold,
                 "train_avg_r_c_raw": results[final]["train"]["avg_r"], "train_trades": results[final]["train"]["trades"],
                 "selection_cost_model": SEL, "data_last_bar": frames["5m"].index[-1].isoformat()}
    payload = freeze(args.frozen, cfg, selection)
    save(args.out, "select", {"results": results, "per_fold": per_fold, "means": means, "final": final,
                              "wf": wf_sum, "neigh": neigh, "train_rows": train_rows,
                              "skips": {k: dict(v) for k, v in skips.items()}})
    lines = ["## 1. Selection on TRAIN (data sliced before 2025-01-01; C_RAW)", "",
             f"Selection frames end at {frames['5m'].index[-1]:%Y-%m-%d %H:%M} UTC (last 5m bar start). "
             "16 combos, one run each; fold tables split by decision time. `*` = the fold winner (best purged train "
             "avg R).", "", fold_table(results, per_fold, means), "",
             "**Walk-forward OOS of the procedure** (fold winners on their own test halves, concatenated 2023-2024): "
             f"{', '.join(f'{f} -> {per_fold[f]}' for f, _, _ in FOLDS)}.", "",
             summary_table({"WF OOS (fold winners)": wf_sum}), "",
             f"**Final selection: `{final}`** (mean fold-test avg R {means[final]:+.3f}; full-train avg R "
             f"{_f(results[final]['train']['avg_r'], '+.3f')}, MTM DD {results[final]['train']['dd']:.1f}R).", "",
             "**Neighbourhood stability** (the 4 one-step grid neighbours, full-train C_RAW):", "",
             "| Neighbour | Trades | Avg R | PF | Total R | MTM DD |", "|---|---|---|---|---|---|",
             *[f"| {n} | {s['trades']} | {_f(s['avg_r'], '+.3f')} | {_f(s['pf'], '.2f')} | {s['total_r']:+.1f} | "
               f"{s['dd']:.1f} |" for n, s in neigh.items()],
             "", f"Neighbours with avg R > 0: {sum(1 for s in neigh.values() if (s['avg_r'] or 0) > 0)}/4.", "",
             "**Selected combo on TRAIN** (2021-10-01 .. 2024-12-31; info rows never selected on):", "",
             summary_table(train_rows), "",
             period_table({k: per_period(v) for k, v in extra.items()}, [p for p, _, _ in PERIODS[:4]]), "",
             "Selected combo skips (C_RAW, top 10):", "", *skips_lines(skips[final]), "",
             f"**FROZEN** -> `{args.frozen}` (sha256 `{file_sha(args.frozen)}`), copy `replay/round6_frozen.json`; "
             f"config: `{json.dumps(payload['config'], sort_keys=True)}`. Committed before the holdout runner."]
    fy.append(args.report, "\n".join(lines))
    log(f"selected {final}; per fold {per_fold}; frozen -> {args.frozen}")


def holdout_section(args, log):
    log("holdout: loading frames sliced before 2026-10-20 (frozen config only)")
    frames = load_frames(args.data, end=HOLDOUT_END)
    assert frames["5m"].index[-1] < pd.Timestamp(HOLDOUT_END), "data after the holdout cutoff"
    system = System(frames)
    cfg, runs, info = holdout_runs(args.frozen, system)
    end = min(system.end, HOLDOUT_END)
    rows = {m: summarize(window(t, HOLDOUT_START, end), HOLDOUT_START, end) for m, t in runs.items()}
    info_rows = {k: summarize(window(t, HOLDOUT_START, end), HOLDOUT_START, end) for k, t in info.items()}
    verdict, fails = holdout_pass(rows["C_RAW"])
    save(args.out, "holdout", {"cfg": cfg, "rows": rows, "info": info_rows,
                               "trades": {m: _ser(t) for m, t in runs.items()},
                               "frozen_sha": file_sha(args.frozen)})
    periods = {m: per_period(t) for m, t in runs.items()}
    lines = ["## 2. HOLDOUT (2025-01-01 .. 2026-10-19) - frozen config, run once", "",
             f"Config read from `{args.frozen}` (sha256 `{file_sha(args.frozen)}`, equal to the committed copy): "
             f"`{cfg['name']}`. Frames sliced before 2026-10-20 (features causal), trades with decision >= 2025-01-01.", "",
             summary_table(rows), "",
             f"**Holdout pass bar (C_RAW: avg R >= +0.08, PF > 1.15, MTM DD < 12R, >= 6 trades/month): "
             f"{'PASS' if verdict else 'FAIL'}**" + ("" if verdict else f" ({'; '.join(fails)})") + ".", "",
             "Per period (avg R / total R (n); 2021Q4-2024 = train part of the same frozen run):", "",
             period_table(periods), "",
             "**Info only (C_RAW; not used for any decision)**:", "", summary_table(info_rows)]
    fy.append(args.report, "\n".join(lines))
    log(f"holdout C_RAW: {rows['C_RAW']['trades']} trades avg R {_f(rows['C_RAW']['avg_r'], '+.3f')} "
        f"PF {_f(rows['C_RAW']['pf'], '.2f')} DD {rows['C_RAW']['dd']:.1f} tpm {rows['C_RAW']['tpm']:.1f} -> "
        f"{'PASS' if verdict else 'FAIL'} {fails}")


def final_section(args, log):
    sel, hold = load(args.out, "select"), load(args.out, "holdout")
    curves = {}
    for m in ("C_RAW", "C_OANDA"):
        curves[m] = [{**t, "exit_at": pd.Timestamp(t["exit_at"]).to_pydatetime()} for t in hold["trades"][m]]
    plot(args.png, curves, HOLDOUT_START,
         f"EURUSD COMBO {hold['cfg']['name']} - cumulative R, frozen config (train selection, shaded = holdout)")
    raw = hold["rows"]["C_RAW"]
    verdict, fails = holdout_pass(raw)
    lines = ["## 3. Multiple testing and verdict", "",
             f"- Round 6: 16 combos x 4 folds = 64 fold evaluations on train (16 variants, + their 16 full-train runs); "
             f"selected `{sel['final']}`; the holdout is 1 test of 1 frozen config (C_OANDA / C_RAW / C_RAW_STRESS = "
             f"the same trades under 3 cost models). Cumulative variants searched: {PREVIOUS_TESTS} + 16 = "
             f"{PREVIOUS_TESTS + 16}.",
             f"- Holdout C_RAW: {raw['trades']} trades ({raw['tpm']:.1f}/month), avg R {_f(raw['avg_r'], '+.3f')}, "
             f"PF {_f(raw['pf'], '.2f')}, MTM DD {raw['dd']:.1f}R, CI80 low {_f(raw['ci80_low'], '+.3f')} -> "
             f"**{'PASS' if verdict else 'FAIL'}**" + ("" if verdict else f" ({'; '.join(fails)})") + ".",
             f"- Chart: `{args.png}` (C_RAW and C_OANDA, train + shaded holdout)."]
    fy.append(args.report, "\n".join(lines))
    log(f"final: {'PASS' if verdict else 'FAIL'} {fails}")


if __name__ == "__main__":
    main()
