"""Round 5 (REPLAY ONLY): low-cost broker cost models on every earlier family, a relaxed multi-day exit grid and other
instruments.

Cost models are constant (declared before any result, not read from the OANDA spread): a raw spread (half on each
side; bid/ask = mid -/+ half spread, so limit touches and TP fills see only the raw spread), a round-turn commission
charged once per trade in R (``intraday_backtest.Market.commissions`` / ``round4.Tape.commission``, also on the open
MTM marks, so it is part of the cost = gross mid R - net R) and a fixed slippage on market entries, stops and market
exits (0 on TP and limit fills). ``C_OANDA`` = the earlier causal OANDA costs, for reference.

Prior families run on their original runners (``fiveyear.run_exit``, ``intraday_backtest.run_policy``,
``round4.run``) with a cost-model ``Market`` / ``Tape``; the relaxed grid and the cross-instrument transfers of
intraday families use ``run_rule`` (``round4.walk`` on the 5m tape: same-bar SL first, gaps at the open, limit fills
never credit the target on their fill bar). Validation and the pass rule: ``replay.fiveyear`` / ``round4.evaluate``.

Run (sections append to the report in order):
``python -m replay.round5 --data /workspace/backtest/data5y --out /workspace/backtest/r5
--report /workspace/backtest/report_round5.md --sections declare,prior,fut,relaxed,others,robust,final``.
Market data and outputs live outside the repository and are never committed.
"""
import argparse
import json
import math
import os
import shutil
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, time, timezone

import numpy as np
import pandas as pd

from replay import entry_search as es
from replay import fiveyear as fy
from replay import intraday_backtest as bt
from replay import round2 as r2
from replay import round3 as r3
from replay import round4 as r4

# ---------------------------------------------------------------- cost models (declared before any run)
FX_COMMISSION = 7e-5  # USD 7 per 100k round turn: 7e-5 quote units per unit (0.7 pip on EURUSD / GBPUSD)
JPY_COMMISSION = 7e-5  # USD 7 per 100k USD on USDJPY: 7e-5 x price JPY per unit (7 x price / 1000 pips)
XAU_COMMISSION = 0.07  # USD 7 per 100 oz lot
COST_SYMBOLS = ("EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "US30USD", "NAS100USD")


@dataclass(frozen=True)
class CostModel:
    """Constant raw spread / slippage per symbol in pips (points for XAU = 0.1, indices = 1.0) and a commission
    rule: 'fx' (FX_COMMISSION), 'jpy' (JPY_COMMISSION x the previous 5m close), 'xau', 'fut' or None."""
    name: str
    spread: dict
    slip: dict
    commission: dict
    financing: bool = True  # False: no carry stress reported (futures: carry is in the basis, not charged)

    def covers(self, symbol):
        return symbol in self.spread


C_RAW = CostModel(
    "C_RAW",
    spread={"EURUSD": 0.2, "GBPUSD": 0.2, "USDJPY": 0.2, "XAUUSD": 1.0, "US30USD": 1.0, "NAS100USD": 0.8},
    slip={"EURUSD": 0.1, "GBPUSD": 0.1, "USDJPY": 0.1, "XAUUSD": 0.1, "US30USD": 0.1, "NAS100USD": 0.08},
    commission={"EURUSD": "fx", "GBPUSD": "fx", "USDJPY": "jpy", "XAUUSD": "xau"})
C_RAW_STRESS = CostModel(
    "C_RAW_STRESS",
    spread={"EURUSD": 0.4, "GBPUSD": 0.4, "USDJPY": 0.4, "XAUUSD": 2.0, "US30USD": 2.0, "NAS100USD": 1.6},
    slip={"EURUSD": 0.2, "GBPUSD": 0.2, "USDJPY": 0.2, "XAUUSD": 0.2, "US30USD": 0.2, "NAS100USD": 0.16},
    commission={"EURUSD": "fx", "GBPUSD": "fx", "USDJPY": "jpy", "XAUUSD": "xau"})
FUT_TICK_PIPS = 0.5  # 6E: 1 tick = 0.00005
FUT_COMMISSION = 4.5 / 125_000  # USD 4.5 per 125k EUR contract round trip = 0.36 pip
C_FUT = CostModel("C_FUT", spread={"EURUSD": FUT_TICK_PIPS}, slip={"EURUSD": 0.5 * FUT_TICK_PIPS},
                  commission={"EURUSD": "fut"}, financing=False)
C_OANDA = "C_OANDA"  # the earlier causal costs (30-day trailing median spread + slippage), reference only
MODELS = {m.name: m for m in (C_RAW, C_RAW_STRESS, C_FUT)}


def commission_array(model, symbol, f5, pip):
    """Round-turn commission (price per unit) for a trade filled on each 5m bar; USDJPY converts USD 7 with the
    previous bar's close (the fill bar's own open for the first bar): causal."""
    rule = model.commission.get(symbol)
    n = len(f5)
    if rule is None:
        return np.zeros(n)
    if rule == "fx":
        return np.full(n, FX_COMMISSION)
    if rule == "xau":
        return np.full(n, XAU_COMMISSION)
    if rule == "fut":
        return np.full(n, FUT_COMMISSION)
    if rule == "jpy":
        close, first = f5["Close"].to_numpy(float), float(f5["Open"].iloc[0])
        return JPY_COMMISSION * np.concatenate([[first], close[:-1]])
    raise ValueError(rule)


def model_tape(model, symbol, f5, pip, fx):
    """``round4.Tape`` of a constant cost model (or the causal OANDA one for ``C_OANDA``)."""
    if model == C_OANDA:
        return r4.Tape.from_frame(f5, pip, fx)
    n = len(f5)
    return r4.Tape(es._ns(f5.index), *(f5[x].to_numpy(float) for x in ("Open", "High", "Low", "Close")),
                   np.full(n, model.spread[symbol] * pip), pip, fx, slip=model.slip[symbol] * pip,
                   commission=commission_array(model, symbol, f5, pip))


def model_market(model, frames, end, symbol="EURUSD"):
    """``intraday_backtest.Market`` (EURUSD pip) of a cost model; ``C_OANDA`` = the 5-year run's causal market."""
    if model == C_OANDA:
        return bt.Market.from_frames(frames, bt.FALLBACK_SPREAD_PIPS, cutoff=end, causal=True)
    market = bt.Market.from_frames(frames, model.spread[symbol], model.slip[symbol], cutoff=end)
    market.commissions = commission_array(model, symbol, frames["5m"], bt.PIP)
    return market


def model_name(model):
    return model if isinstance(model, str) else model.name


# ---------------------------------------------------------------- relaxed rules and transfers
EURUSD_ADR_PIPS = 74.3  # median ADR20 of EURUSD over the 5-year sample: pip clamps -> ADR multiples elsewhere
RELAX_LO, RELAX_HI = 15.0, 50.0  # pips (EURUSD); other instruments: / EURUSD_ADR_PIPS x ADR20
RELAX_DAYS = 5
RELAX_TPS = {"TP1.5": ("R", 1.5), "TP2": ("R", 2.0), "TP3": ("R", 3.0), "ADR0.6": ("ADR", 0.6)}
RELAX_FAMILIES = ("E3", "JT[R1+R5]", "M1", "M2", "M3", "M4", "M5", "F1", "F1b", "F2", "F3", "F4")
EXIT_RULES = {  # prior intraday exits as rules (pips on EURUSD; ADR multiples elsewhere via EURUSD_ADR_PIPS)
    "FIX2": {"stop": "struct", "lo": 15.0, "hi": 30.0, "tp": ("R", 2.0)},
    "FIX15": {"stop": "struct", "lo": 15.0, "hi": 30.0, "tp": ("R", 1.5)},
    "SL15": {"stop": "struct", "lo": 15.0, "hi": 30.0, "tp": ("R", 2.0)},
    "SL25": {"stop": "struct", "lo": 25.0, "hi": 30.0, "tp": ("R", 2.0)},
    "ADR": {"stop": "adr", "lo": 15.0, "hi": 30.0, "sl_adr": es.ADR_SL, "tp": ("ADRCLAMP", es.ADR_TP, *es.ADR_RR)},
}


def relaxed_rule(tp):
    return {"stop": "struct", "lo": RELAX_LO, "hi": RELAX_HI, "tp": RELAX_TPS[tp], "hold": "DAYS",
            "days": RELAX_DAYS, "friday": True}


def intraday_rule(exit_model):
    return {**EXIT_RULES[exit_model], "hold": "I2", "friday": False}  # I2 already closes at Friday 17:00 NY


def nth_trading_close(fill_at, days):
    """17:00 NY at the close of the ``days``-th NY trading day (17:00 -> 17:00, weekdays) counting the fill's day."""
    day = pd.Timestamp(bt.day_key(fill_at)) + pd.offsets.BDay(days - 1)
    return datetime.combine(day.date(), time(17), tzinfo=es.NY).astimezone(timezone.utc)


def rule_levels(rule, s, fill, struct, atr, adr, scale):
    """(stop, target, None) or (None, None, skip). ``adr`` ADR20 in price; ``scale`` = price per rule unit (pip on
    EURUSD, ADR20 / EURUSD_ADR_PIPS elsewhere)."""
    lo, hi = rule["lo"] * scale, rule["hi"] * scale
    if rule["stop"] == "adr":
        dist = min(max(rule["sl_adr"] * adr, lo), hi)
    else:
        if not math.isfinite(atr):
            return None, None, "atr_unavailable"
        dist = s * (fill - (struct - s * bt.BUFFER_ATR * atr))
        if dist <= 0:
            return None, None, "structure_beyond_fill"
        if dist > hi:
            return None, None, "sl_wider_than_hi"
        dist = max(dist, lo)
    kind, value, *clamp = rule["tp"]
    if kind == "R":
        reward = value * dist
    elif kind == "ADR":
        reward = value * adr
    else:
        reward = min(max(value * adr, clamp[0] * dist), clamp[1] * dist)
    return fill - s * dist, fill + s * reward, None


def run_rule(cands, ctx, tape, name, rule, pip_units=True):
    """Sequential walk of intraday candidates under an exit ``rule``: (trades, skips). Fill at the open of the 5m bar
    starting at the decision close (``LimitCandidate``: ``round2.limit_fill`` semantics, no slippage, target not
    credited on the fill bar); stop/target from the fill (``rule_levels``); hold 'I2' (``bt.i2_exit``) or 'DAYS'
    (close of the ``days``-th NY trading day); Friday 17:00 NY close if ``friday``. One position at a time, one entry
    per idea, 2R/day realised halt, the tape's costs (commission once, in R)."""
    trades, skips = [], Counter()
    free_at, used, halted = -1, set(), set()
    day_r = defaultdict(float)
    for cand in cands:
        if cand.idea in used:
            skips["idea_already_traded"] += 1
            continue
        if cand.t < free_at:
            skips["position_open"] += 1
            continue
        if bt.day_key(r4._dt(cand.t)) in halted:
            skips["daily_loss_halt"] += 1
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
        s = 1 if cand.side == es.LONG else -1
        limit = getattr(cand, "limit", None)
        if limit is None:
            mid_fill = float(tape.o[i])
            fill = mid_fill + s * (hs + slip)
        else:
            at_open = float(tape.o[i]) + s * hs
            fill = at_open if s * (at_open - limit) <= 0 else limit
            mid_fill = fill - s * hs
        adr_pips = ctx.adr_at(cand.t)
        if not math.isfinite(adr_pips):
            skips["adr_unavailable"] += 1
            continue
        adr = adr_pips * tape.pip
        scale = tape.pip if pip_units else adr / EURUSD_ADR_PIPS
        stop, target, why = rule_levels(rule, s, fill, cand.struct, ctx.atr_at(cand.t), adr, scale)
        if stop is None:
            skips[why] += 1
            continue
        fill_at = tape.stamp(i)
        events = []
        if rule["hold"] == "I2":
            try:
                boundary, reason = bt.i2_exit(fill_at)
            except ValueError:
                skips["fill_outside_session"] += 1
                continue
            events.append((bt._ns(boundary), "close", reason))
        else:
            events.append((bt._ns(nth_trading_close(fill_at, rule["days"])), "close", bt.TIME))
        if rule.get("friday"):
            events.append((bt._ns(bt.friday_close_after(fill_at)), "close", bt.FRIDAY))
        events.sort(key=lambda e: e[0])
        events = events[:1]  # the earliest close wins
        trade, free_ns = r4.walk_trade(tape, i, s, fill, mid_fill, stop, target, hs, slip, events, name, cand.t,
                                       cand.idea, no_tp_on_fill=limit is not None)
        trade["entry"] = "LIMIT" if limit is not None else "MARKET"
        trades.append(trade)
        used.add(cand.idea)
        free_at = free_ns
        key = bt.day_key(trade["exit_at"])
        day_r[key] += trade["r"]
        if day_r[key] <= -bt.DAILY_HALT_R:
            halted.add(key)
    return trades, skips


# ---------------------------------------------------------------- candidates
def family_candidates(ctx, fam, news_days, start_ns, half_spread, mods=None):
    """Candidates of a family label (E1-E4 / JT / JT[x] / M* / F*) from ``start_ns``; ``half_spread(k)`` (price) is the
    cost model's (R5 limit touch test)."""
    if fam in ("E1", "E2", "E3", "E4"):
        found = es.entry_candidates(ctx, fam)
    elif fam == "JT" or fam.startswith("JT["):
        relax = r2.Relax() if fam == "JT" else r2.Relax.parse(fam[3:-1])
        found = r2.jt_relaxed_all(ctx, relax, news_days, half_spread, start_ns)[0]
    elif fam in r3.FAMILIES:
        found = r3.family_candidates(ctx, fam)
    elif fam in r3.MODULES:
        found = r3.combine(ctx, mods if mods is not None else r3.modules(ctx), (fam,), fam)
    else:
        raise ValueError(fam)
    return [c for c in found if c.t >= start_ns]


# ---------------------------------------------------------------- the prior EURUSD grid
PRIOR_E = [f"{f}:{x}" for f in ("E1", "E2", "E3", "E4") for x in fy.EXITS]
PRIOR_JT = [f"JT:{x}" for x in fy.EXITS] + [f"JT[{c}]:{sl}" for c in ("R1+R5", "R1+R2", "R1+R2+R4")
                                             for sl in ("SL15", "SL25")]
PRIOR_MODULES = [f"{m}:{x}" for m in r3.MODULES for x in fy.EXITS]
PRIOR_FAMILIES = [f"{f}:{x}" for f in r3.FAMILIES for x in fy.EXITS]
PRIOR_R4 = ["A:SW:TP2", "A:SW:TP3", *r4.GRID_B, *r4.GRID_C]
PRIOR_V2 = ["V2_F3", "V2_I2:FIX2", "V2_I2:FIX15"]
V2_KEYS = {"V2_F3": "V2_F3", "V2_I2:FIX2": "V2_I2", "V2_I2:FIX15": "V2_I2_15"}
PRIOR = PRIOR_V2 + PRIOR_E + PRIOR_JT + PRIOR_MODULES + PRIOR_FAMILIES + PRIOR_R4
TOP_N = 5
OTHERS = r4.OTHERS
RELAX_OTHERS = ("E3", "JT[R1+R5]")
PREVIOUS_TESTS = 182
OANDA_SOURCES = {"5y": ("summary_e", "summary_jt", "summary_modules", "summary_families", "summary_v2"),
                 "r4": ("r4_a", "r4_b", "r4_c")}


def split_name(name):
    """(family, exit) of an intraday prior variant name ('JT[R1+R5]:SL25' -> ('JT[R1+R5]', 'SL25'))."""
    fam, _, exit_model = name.rpartition(":")
    return fam, exit_model


def is_r4(name):
    return name in r4.BASE


def runner_exit(exit_model):
    """``fiveyear.run_exit`` exit of a prior name (JT combos: SL15 = FIX2 geometry)."""
    return "FIX2" if exit_model == "SL15" else exit_model


def family_of(name):
    """Family label for the one-variant-per-family rankings (exits / TPs of a family are siblings)."""
    if is_r4(name):
        return r4.BASE[name][0]
    if name.startswith("RX:"):
        return name.split(":")[1]
    return fy.family_of(name)


def transferable(name):
    return not name.startswith("V2_")


def top_variants(rows, n=TOP_N, keep=lambda name: True):
    """Top ``n`` variants by 5-year avg R with >= 30 trades, one per family."""
    out, seen = [], set()
    for name in sorted((x for x, s in rows.items() if s["trades"] >= fy.MIN_TRADES_TOP and s["avg_r"] is not None
                        and keep(x)), key=lambda x: rows[x]["avg_r"], reverse=True):
        if family_of(name) not in seen:
            seen.add(family_of(name))
            out.append(name)
        if len(out) == n:
            break
    return out


def best_relaxed_tp(rows, fam):
    """Best relaxed TP of ``fam`` by EURUSD C_RAW 5-year avg R (>= 30 trades if any has them)."""
    names = [f"RX:{fam}:{tp}" for tp in RELAX_TPS if f"RX:{fam}:{tp}" in rows]
    for min_trades in (fy.MIN_TRADES_TOP, 1):
        ok = [x for x in names if rows[x]["trades"] >= min_trades and rows[x]["avg_r"] is not None]
        if ok:
            return max(ok, key=lambda x: rows[x]["avg_r"])
    return names[0]


def evaluate(trades, years, model):
    out = r4.evaluate(trades, years)
    if model != C_OANDA and not model.financing:
        out.pop("stress", None)
    return out


# ---------------------------------------------------------------- an instrument under cost models
class Book:
    """One instrument: frames, bars, context and lazily built per-model tapes / markets / candidates."""

    def __init__(self, data, symbol, log=print):
        self.symbol, self.log = symbol, log
        self.ins = r4.Instrument(data, symbol)
        self.pip, self.fx, self.years = self.ins.pip, self.ins.fx, self.ins.years
        self.start_ns = int(self.years[0][0].timestamp() * 1e9)
        self._tapes, self._markets, self._cands, self._mods, self._news = {}, {}, {}, None, None

    @property
    def ctx(self):
        return self.ins.context()

    def tape(self, model):
        key = model_name(model)
        if key not in self._tapes:
            self._tapes[key] = model_tape(model, self.symbol, self.ins.frames["5m"], self.pip, self.fx)
        return self._tapes[key]

    def market(self, model):
        if self.symbol != r4.HOME:
            raise ValueError("prior intraday runners are EURUSD-only")
        key = model_name(model)
        if key not in self._markets:
            self._markets[key] = model_market(model, self.ins.frames, self.ins.end)
        return self._markets[key]

    def candidates(self, fam, model):
        """Family candidates; only JT R5 combos depend on the cost model (limit touch on the ask/bid)."""
        r5 = fam.startswith("JT[") and "R5" in fam[3:-1].split("+")
        key = (fam, model_name(model) if r5 else None)
        if key not in self._cands:
            tape = self.tape(model)
            with r4.instrument_pip(self.pip), r4.schedule(self.ins.break_hours):
                if self._news is None:
                    self._news = es.high_impact_days(self.years[0][0].date(), self.ins.end.date())
                if fam in r3.MODULES and self._mods is None:
                    self._mods = r3.modules(self.ctx)
                self._cands[key] = family_candidates(self.ctx, fam, self._news, self.start_ns,
                                                     lambda k: float(tape.spread[k]) / 2, self._mods)
        return self._cands[key]

    def run(self, name, model, signals=None, rule=None, params=None):
        """(trades, skips) of a variant under ``model``. Names: prior EURUSD names (original runners on EURUSD;
        rule-based transfers elsewhere), 'RX:<family>:<tp>' (relaxed grid), or an explicit ``rule``."""
        home = self.symbol == r4.HOME
        if is_r4(name):
            setups = r4.build_setups(name, self.ins.bars, params)
            return r4.run(setups, self.tape(model), name)
        if name in V2_KEYS:
            if not home or signals is None:
                raise ValueError("V2 production signals exist for EURUSD only")
            from replay import fast_scouts
            market = self.market(model)
            with fast_scouts.patched():
                trades, sk, _, _ = bt.run_policy(signals, market, V2_KEYS[name])
            bt.add_gross(trades, market)
            return trades, sk
        if name.startswith("RX:"):
            _, fam, tp = name.split(":")
            rule = rule or relaxed_rule(tp)
        else:
            fam, exit_model = split_name(name)
            if home and rule is None:
                return fy.run_exit(self.candidates(fam, model), self.ctx, self.market(model), name,
                                   runner_exit(exit_model))
            rule = rule or intraday_rule(exit_model)
        with r4.instrument_pip(self.pip):
            return run_rule(self.candidates(fam, model), self.ctx, self.tape(model), name, rule, pip_units=home)


# ---------------------------------------------------------------- report
DECLARATION = """\
## 0. Predeclared design (written before any round-5 result)

**Data**: `data5y` 5m / 15m / 1h mid OHLC per instrument ({present}; missing: {missing}). EURUSD years (each
instrument: 12-month blocks from its own first tradable moment):
{years}

**Cost models** (constant; not read from the OANDA spread; spread half on each side, so limit touches / TP fills see
the raw spread only; commission = round turn, charged once per trade in R, also on open MTM marks; slippage on
market entries, stops and time / Friday exits, 0 on TP and limit fills):

| Model | EURUSD / GBPUSD | USDJPY | XAUUSD | US30 / NAS100 | Financing |
|---|---|---|---|---|---|
| `C_RAW` | spread 0.2 pip + commission 0.7 pip (USD 7 / 100k) + slippage 0.1 pip; EURUSD market round trip 1.1 pip | spread 0.2 pip + commission 7 x price / 1000 pips (USD 7 / 100k USD at the previous 5m close: ~1.05 pip at 150) + slippage 0.1 pip | spread 0.10 USD + commission 0.07 USD/oz (USD 7 / 100 oz) + slippage 0.01 USD | spread 1.0 / 0.8 pt, no commission, slippage 0.1 / 0.08 pt | 0 % (rule) and -3 %/yr stress shown |
| `C_RAW_STRESS` | spread 0.4 + same commission + slippage 0.2 pip | spread 0.4 + same commission + slippage 0.2 pip | spread 0.20 + 0.07 + slippage 0.02 USD | spread 2.0 / 1.6 pt, slippage 0.2 / 0.16 pt | as C_RAW |
| `C_FUT` (6E, EURUSD only) | spread 1 tick = 0.5 pip + commission USD 4.5 / 125k EUR = 0.36 pip + slippage 0.5 tick = 0.25 pip (market entries and market exits) | – | – | – | none (carry in the futures basis; spot-based replay, **roll gaps not modelled**) |
| `C_OANDA` (reference) | the earlier causal costs: 30-day trailing median OANDA spread + 0.2 pip slippage (intraday runners; fallback 1.6 pip) or + 0.1 x spread, min 0.2 pip (round-4 runners; no fallback) | same rule per instrument | same | same | as round 4 |

Commission is not a spread: on EURUSD a C_RAW stop-out costs 0.2 + 0.7 + 2 x 0.1 = 1.1 pip, a TP 0.9 pip. Interpretation:
the C_FUT slippage is also applied to market entries (they are market orders), not only to market exits.

**Pass rule (unchanged)**: avg R > +0.10 AND day-block CI80 low > 0 in >= 4 of 5 years, total R > 0, MTM max DD
<= 8R, avg cost <= 0.10R (cost = gross mid R - net R, incl. commission). 0 % carry; the -3 %/yr stress is shown for
runs with multi-day holds (round-4 and relaxed runners).

**Grid (fixed parameters)**
- **Step 2 - prior families, EURUSD** (original runners and rules, cost model swapped), {n_prior} variants under
  `C_RAW`: {prior}. C_OANDA figures are the stored 5-year / round-4 results (side by side; not re-counted).
  `C_RAW_STRESS` for every one with C_RAW 5-year total R > 0. `C_FUT` for the top {top} by C_RAW 5-year avg R (>= 30
  trades, one variant per family; family = name before the exit).
- **Step 3 - relaxed rules (Jeferson)**, EURUSD, {n_relax} variants under `C_RAW` + the same {n_relax} under `C_OANDA`
  (new variants, so counted): families {relax_families} (`JT[R1+R5]` = JT_SMC, no 1h-FVG requirement, limit at the
  15m OB edge; modules / F-families as in the 5-year round); entry = the family's candidate (fill at the open of the
  5m bar starting at the decision close; R5 limit fills), stop = structure -/+ 0.2 ATR14(H1) from the fill, < 15 pips
  widened to 15, > 50 pips skipped; TP in {{1.5R, 2R, 3R, 0.6 x ADR20 from the fill (no R clamp)}}; time exit at the
  close (17:00 NY) of the 5th NY trading day counting the fill's day; **Friday 17:00 NY close ON** (so the 5-day exit
  only binds for Monday fills); no I2 session close; one position at a time, one entry per idea, 2R/day halt.
  `C_RAW_STRESS` for those with C_RAW total R > 0. Names `RX:<family>:<TP>`.
- **Step 4 - other instruments** ({others}): the top {top} transferable EURUSD variants by C_RAW 5-year avg R over
  steps 2-3 (>= 30 trades, one per family; V2_* excluded: production signals exist for EURUSD only, the next one
  fills in) + `RX:E3` and `RX:JT[R1+R5]` at their best EURUSD C_RAW TP, under C_RAW; C_RAW_STRESS when total R > 0.
  Round-4 families run unchanged (ATR-based). Pip-denominated stop clamps become ADR-relative: x pips -> x /
  {adr:.1f} x ADR20 (EURUSD median ADR20 over the sample = {adr:.1f} pips; e.g. FIX2 0.20-0.40 ADR20, relaxed
  0.20-0.67 ADR20); intraday exits keep their TP and hold rules (I2 for FIX2/FIX15/SL25/ADR; ADR exit: SL 0.3 ADR20
  clamped to the scaled 15-30, TP 0.58 ADR20 clamped 1.5-2.0R); candidate pip thresholds scale with the instrument
  pip as in round 4.
- **Robustness, only for a passer**: +/-25 % on each main parameter one at a time (rule: lo, hi, TP, days; round-4
  families: `round4.neighbours`), share of neighbours passing and their avg R; per-year table; second half alone;
  C_RAW_STRESS result. Neighbours are not counted as tests.

**Tests declared: {fixed} fixed** ({breakdown}) **+ one C_RAW_STRESS run per variant with total R > 0** (data
dependent, counted when run); cumulative with the 182 earlier tests: >= {cum}. P(no-edge variant clears >= 4 of 5
one-sided 10 % yearly tests) = {p:.5f} -> ~{fp:.3f} expected false passers over the fixed tests if independent
({fpc:.3f} cumulative). The cost models re-test the same entries (strongly correlated), so the effective count is
much lower; a variant that passes only under the cheapest model is one finding, not several.
"""


def declared_counts(present):
    others = [s for s in OTHERS if s in present]
    return {"prior C_RAW": len(PRIOR), "C_FUT": TOP_N, "relaxed C_RAW": len(RELAX_FAMILIES) * len(RELAX_TPS),
            "relaxed C_OANDA": len(RELAX_FAMILIES) * len(RELAX_TPS),
            "others C_RAW": len(others) * (TOP_N + len(RELAX_OTHERS))}


def declaration(home, present, missing):
    years = "\n".join(f"- Y{q + 1}: {a:%Y-%m-%d %H:%M} -> {b:%Y-%m-%d %H:%M} UTC" for q, (a, b) in enumerate(home.years))
    counts = declared_counts(present)
    fixed = sum(counts.values())
    p = fy.p_false_pass()
    return DECLARATION.format(
        present=", ".join(present), missing=", ".join(missing) or "none", years=years, n_prior=len(PRIOR),
        prior=", ".join(PRIOR), top=TOP_N, n_relax=counts["relaxed C_RAW"], relax_families=", ".join(RELAX_FAMILIES),
        others=", ".join(s for s in OTHERS if s in present), adr=EURUSD_ADR_PIPS, fixed=fixed,
        breakdown=" + ".join(f"{k} {v}" for k, v in counts.items()), cum=PREVIOUS_TESTS + fixed, p=p, fp=fixed * p,
        fpc=(PREVIOUS_TESTS + fixed) * p), fixed


def compare_table(names, columns):
    """Side-by-side summary: per model avg R / total R / cost / years ok / result."""
    head = "| Variant | " + " | ".join(f"{m}: avg R, total, cost, yrs, result" for m in columns) + " |\n" + \
        "|---" * (1 + len(columns)) + "|"
    lines = [head]
    for name in names:
        cells = []
        for m in columns:
            s = columns[m].get(name)
            if not s:
                cells.append("–")
                continue
            cells.append(f"{fy._f(s['avg_r'], '+.3f')}, {s['total_r']:+.1f}, {fy._f(s.get('cost'), '.3f')}, "
                         f"{s['years_ok']}/5, {'PASS' if s['pass'] else 'fail'} ({s['trades']})")
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def save(out, key, rows, extra=None):
    with open(os.path.join(out, f"r5_{key}.json"), "w", encoding="utf-8") as handle:
        json.dump({"rows": rows, **(extra or {})}, handle)


def load(out, key):
    path = os.path.join(out, f"r5_{key}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def oanda_reference(backtest_dir):
    """Stored C_OANDA rows of the prior variants (5-year round and round 4), keyed by name."""
    rows = {}
    for sub, keys in OANDA_SOURCES.items():
        for key in keys:
            path = os.path.join(backtest_dir, sub, f"{key}.json")
            if os.path.exists(path):
                with open(path, encoding="utf-8") as handle:
                    rows.update(json.load(handle)["rows"])
    return {n: rows[n] for n in PRIOR if n in rows}


def v2_signals(data, out, book, log, workers):
    """Cached production signals (``fiveyear`` v2 section; reused from ``<backtest>/5y/v2`` when present)."""
    from data.market_evidence import TIMEFRAMES
    from replay.engine import WINDOW
    frames = book.ins.frames
    start, end = book.years[0][0], book.ins.end
    warm = max(f.index[WINDOW - 1] + TIMEFRAMES[tf] for tf, f in frames.items() if tf in TIMEFRAMES).to_pydatetime()
    slots = bt.decision_slots(max(start, warm), end)
    cached = os.path.join(os.path.dirname(os.path.abspath(out)), "5y", "v2")
    v2_dir = os.path.join(out, "v2")
    os.makedirs(v2_dir, exist_ok=True)
    if os.path.isdir(cached):  # copy the 5-year checkpoints (read-only reuse; a mismatch regenerates in ``out``)
        for f in os.listdir(cached):
            if f.startswith("signals_") and f.endswith(".jsonl") and not os.path.exists(os.path.join(v2_dir, f)):
                shutil.copyfile(os.path.join(cached, f), os.path.join(v2_dir, f))
    return fy.v2_records(bt.signals_by_year(data, v2_dir, slots, workers, log), book.ctx)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--sections", default="declare,prior,fut,relaxed,others,robust,final")
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    args = parser.parse_args(argv)
    os.makedirs(args.out, exist_ok=True)
    sections = args.sections.split(",")
    backtest_dir = os.path.dirname(os.path.abspath(args.report))

    def log(message):
        print(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {message}", flush=True)

    present = [s for s in r4.INSTRUMENTS if r4.Instrument.available(args.data, s)]
    missing = [s for s in r4.INSTRUMENTS if s not in present]
    books, cache = {}, {}

    def book(symbol):
        if symbol not in books:
            log(f"loading {symbol}")
            books[symbol] = Book(args.data, symbol, log)
        return books[symbol]

    def signals():
        if "v2" not in cache:
            cache["v2"] = v2_signals(args.data, args.out, book(r4.HOME), log, args.workers)
        return cache["v2"]

    def run_all(jobs):
        """jobs: {label: (symbol, name, model)} -> rows, skips."""
        rows, skips = {}, {}
        for label, (symbol, name, model) in jobs.items():
            b = book(symbol)
            trades, sk = b.run(name, model, signals() if name in V2_KEYS else None)
            rows[label], skips[label] = evaluate(trades, b.years, model), sk
            s = rows[label]
            log(f"  {label}: {s['trades']} trades, avg R {fy._f(s['avg_r'], '+.3f')}, total {s['total_r']:+.1f}, "
                f"cost {fy._f(s['cost'], '.3f')}, {'PASS' if s['pass'] else 'fail'}")
        return rows, skips

    def section(title, rows, skips, notes=(), compare=None):
        lines = [f"## {title}", "", *notes, ""]
        if compare:
            lines += [compare, ""]
        lines += [r4.table(rows), "", "Portfolio skips (top 6 reasons):", "", *fy.skips_lines(skips)]
        append(args.report, "\n".join(lines))
        log(f"{title}: {len(rows)} variants, passers {[n for n, s in rows.items() if s['pass']]}")

    def stress_jobs(rows, symbol_of, name_of):
        return {f"{label}@C_RAW_STRESS": (symbol_of(label), name_of(label), C_RAW_STRESS)
                for label, s in rows.items() if s["trades"] and s["total_r"] > 0}

    if "declare" in sections:
        text, fixed = declaration(book(r4.HOME), present, missing)
        with open(args.report, "w", encoding="utf-8") as handle:
            handle.write(f"# Round 5 - low-cost broker cost models (REPLAY ONLY)\n\nStarted "
                         f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} by `python -m replay.round5` "
                         "(branch `feat/backtest-v2-i2`). Production unchanged. Sections are appended as they "
                         "finish.\n\n")
        append(args.report, text)
        save(args.out, "declared", {}, {"fixed": fixed, "present": present})

    if "prior" in sections:
        raw, sk = run_all({n: (r4.HOME, n, C_RAW) for n in PRIOR})
        stress, sk2 = run_all(stress_jobs(raw, lambda _: r4.HOME, lambda label: label))
        oanda = oanda_reference(backtest_dir)
        check = sanity_check(book(r4.HOME), oanda, signals)
        save(args.out, "prior", raw, {"stress": stress, "oanda": oanda, "check": check})
        stress_by = {k.rsplit("@", 1)[0]: v for k, v in stress.items()}
        notes = [f"{len(PRIOR)} prior variants under C_RAW; C_RAW_STRESS on the {len(stress)} with C_RAW total R > 0. "
                 "C_OANDA = stored 5-year / round-4 rows. Reproduction check (the C_OANDA cost model re-run "
                 "through this module vs the stored rows): " + ", ".join(
                     f"{n} {c['now']:+.4f} vs {fy._f(c['stored'], '+.4f')} ({'same' if c['same'] else 'DIFFERENT'})"
                     for n, c in check.items()) + "."]
        section("Step 2. Prior families on EURUSD under C_RAW (C_OANDA / C_RAW_STRESS side by side)", raw, sk, notes,
                compare_table(PRIOR, {"C_OANDA": oanda, "C_RAW": raw, "C_RAW_STRESS": stress_by}))
        if stress:
            section("Step 2b. C_RAW_STRESS (prior families with C_RAW total R > 0)", stress, sk2)

    if "fut" in sections:
        raw = load(args.out, "prior")["rows"]
        top = top_variants(raw)
        rows, sk = run_all({f"{n}@C_FUT": (r4.HOME, n, C_FUT) for n in top})
        save(args.out, "fut", rows, {"top": top})
        section("Step 2c. C_FUT (6E) on the top 5 by C_RAW 5-year avg R", rows, sk,
                [f"Top {TOP_N} by C_RAW avg R (>= 30 trades, one per family): {', '.join(top)}. Spot-based: futures "
                 "roll gaps and the basis are not modelled; no financing."],
                compare_table(top, {"C_RAW": raw, "C_FUT": {k.rsplit("@", 1)[0]: v for k, v in rows.items()}}))

    if "relaxed" in sections:
        names = [f"RX:{f}:{tp}" for f in RELAX_FAMILIES for tp in RELAX_TPS]
        raw, sk = run_all({n: (r4.HOME, n, C_RAW) for n in names})
        oanda, sko = run_all({f"{n}@C_OANDA": (r4.HOME, n, C_OANDA) for n in names})
        stress, sks = run_all(stress_jobs(raw, lambda _: r4.HOME, lambda label: label))
        save(args.out, "relaxed", raw, {"oanda": oanda, "stress": stress})
        strip = {k: {x.rsplit("@", 1)[0]: v for x, v in d.items()} for k, d in
                 (("C_OANDA", oanda), ("C_RAW_STRESS", stress))}
        section("Step 3. Relaxed rules (5-day hold, SL 15-50 pips, TP 1.5R/2R/3R/0.6 ADR20, Friday close ON) - EURUSD",
                raw, sk, [f"{len(names)} variants under C_RAW (table) and C_OANDA; C_RAW_STRESS on {len(stress)}."],
                compare_table(names, {"C_OANDA": strip["C_OANDA"], "C_RAW": raw,
                                      "C_RAW_STRESS": strip["C_RAW_STRESS"]}))
        section("Step 3b. Relaxed rules under C_OANDA (reference, counted)", oanda, sko)
        if stress:
            section("Step 3c. Relaxed rules under C_RAW_STRESS (C_RAW total R > 0)", stress, sks)

    if "others" in sections:
        pool = {**load(args.out, "prior")["rows"], **load(args.out, "relaxed")["rows"]}
        top = top_variants(pool, keep=transferable)
        extra = [best_relaxed_tp(pool, f) for f in RELAX_OTHERS]
        chosen = top + [x for x in extra if x not in top]
        jobs = {f"{sym}:{n}": (sym, n, C_RAW) for sym in OTHERS if sym in present for n in chosen}
        raw, sk = run_all(jobs)
        stress, sks = run_all(stress_jobs(raw, lambda label: label.split(":", 1)[0],
                                          lambda label: label.split(":", 1)[1]))
        save(args.out, "others", raw, {"stress": stress, "chosen": chosen})
        section("Step 4. Other instruments under C_RAW", raw, sk,
                [f"Variants (selected on EURUSD C_RAW only): top {TOP_N} transferable {', '.join(top)}; relaxed "
                 f"{', '.join(extra)}. Instruments: {', '.join(s for s in OTHERS if s in present)}."],
                compare_table(list(raw), {"C_RAW": raw,
                                          "C_RAW_STRESS": {k.rsplit("@", 1)[0]: v for k, v in stress.items()}}))
        if stress:
            section("Step 4b. Other instruments under C_RAW_STRESS (C_RAW total R > 0)", stress, sks)

    if "robust" in sections:
        robust(args, book, signals, log)
    if "final" in sections:
        final(args, log)


def append(path, text):
    fy.append(path, text)


def sanity_check(home, oanda, signals):
    """Re-run two stored variants under C_OANDA through ``Book.run``: the numbers must match the stored rows."""
    out = {}
    for name in ("E3:FIX2", "A:SW:TP3"):
        if name not in oanda:
            continue
        trades, _ = home.run(name, C_OANDA)
        now = fy.evaluate(trades, home.years)["avg_r"]
        stored = oanda[name]["avg_r"]
        out[name] = {"now": now, "stored": stored, "same": stored is not None and abs(now - stored) < 1e-9}
    return out


# ---------------------------------------------------------------- robustness and final
def all_rows(out):
    """{label: (row, symbol, name, model)} of every counted round-5 run."""
    got = {}
    prior, fut, relaxed, others = (load(out, k) for k in ("prior", "fut", "relaxed", "others"))
    if prior:
        got.update({n: (s, r4.HOME, n, "C_RAW") for n, s in prior["rows"].items()})
        got.update({n: (s, r4.HOME, n.rsplit("@", 1)[0], "C_RAW_STRESS") for n, s in prior["stress"].items()})
    if fut:
        got.update({n: (s, r4.HOME, n.rsplit("@", 1)[0], "C_FUT") for n, s in fut["rows"].items()})
    if relaxed:
        got.update({n: (s, r4.HOME, n, "C_RAW") for n, s in relaxed["rows"].items()})
        for key, model in (("oanda", C_OANDA), ("stress", "C_RAW_STRESS")):
            got.update({n: (s, r4.HOME, n.rsplit("@", 1)[0], model) for n, s in relaxed[key].items()})
    if others:
        for key, model in (("rows", "C_RAW"), ("stress", "C_RAW_STRESS")):
            for n, s in others[key].items():
                sym, name = n.rsplit("@", 1)[0].split(":", 1)
                got[n] = (s, sym, name, model)
    return got


def neighbour_rules(name):
    """[(label, rule)] +/-25 % on each main exit parameter of a rule-based variant (lo, hi, TP, days)."""
    if name.startswith("RX:"):
        base = relaxed_rule(name.split(":")[2])
    else:
        base = intraday_rule(split_name(name)[1])
    out = []
    for key in ("lo", "hi", "tp", "days", "sl_adr"):
        if key not in base:
            continue
        for f in (0.75, 1.25):
            rule = dict(base)
            if key == "tp":
                kind, value, *rest = base["tp"]
                rule["tp"] = (kind, round(value * f, 4), *rest)
                label = f"tp={rule['tp'][1]}"
            elif key == "days":
                rule["days"] = max(1, int(round(base["days"] * f)))
                label = f"days={rule['days']}"
            else:
                rule[key] = round(base[key] * f, 4)
                label = f"{key}={rule[key]}"
            if rule["lo"] <= rule["hi"]:
                out.append((label, rule))
    return out


def robust(args, book, signals, log):
    rows = all_rows(args.out)
    passers = [n for n, (s, *_rest) in rows.items() if s["pass"]]
    lines = ["## Robustness of passers", ""]
    if not passers:
        lines.append("No variant passes, so no neighbourhood / second-half / stress follow-up is triggered "
                     "(predeclared: passers only). Second-half figures of the closest variants are in the shortlist.")
        append(args.report, "\n".join(lines))
        log("robust: no passer")
        return
    result = {}
    for label in passers:
        s, symbol, name, model_key = rows[label]
        model = MODELS.get(model_key, model_key)
        b = book(symbol)
        nrows = {}
        if is_r4(name):
            for nl, params in r4.neighbours(name):
                trades, _ = b.run(name, model, params=params)
                nrows[f"{label}[{nl}]"] = evaluate(trades, b.years, model)
        elif name in V2_KEYS:
            lines.append(f"- {label}: production V2 plans have no free exit parameters here; neighbourhood N/A.")
        else:
            for nl, rule in neighbour_rules(name):
                trades, _ = b.run(name, model, rule=rule)
                nrows[f"{label}[{nl}]"] = evaluate(trades, b.years, model)
        stress_label = f"{label}@C_RAW_STRESS" if "@" not in label else None
        stress = rows.get(stress_label, (None,))[0] if stress_label else None
        if stress is None and model_key == "C_RAW":
            trades, _ = b.run(name, C_RAW_STRESS, signals() if name in V2_KEYS else None)
            stress = evaluate(trades, b.years, C_RAW_STRESS)
        ok = [n for n, x in nrows.items() if x["pass"]]
        avg = [x["avg_r"] for x in nrows.values() if x["avg_r"] is not None]
        h = s["half2"]
        lines += [f"### {label}", "",
                  f"Neighbourhood: {len(ok)}/{len(nrows)} neighbours pass; their avg R: mean "
                  f"{fy._f(float(np.mean(avg)) if avg else None, '+.3f')}, min {fy._f(min(avg) if avg else None, '+.3f')}"
                  f". Second half alone: {h['n']} trades, avg R {fy._f(h['avg_r'], '+.3f')}, total {h['total_r']:+.1f}R,"
                  f" CI80 low {fy._f(h['ci80_low'], '+.3f')}. C_RAW_STRESS: "
                  + ("–" if stress is None else f"avg R {fy._f(stress['avg_r'], '+.3f')}, "
                     f"{'PASS' if stress['pass'] else 'fail: ' + ', '.join(stress['fails'])}") + ".", "",
                  r4.table({label: s, **nrows}), ""]
        result[label] = {"neighbours": len(nrows), "pass": len(ok), "stress_pass": bool(stress and stress["pass"])}
    with open(os.path.join(args.out, "r5_robust.json"), "w", encoding="utf-8") as handle:
        json.dump(result, handle)
    append(args.report, "\n".join(lines))
    log(f"robust: {result}")


def final(args, log):
    rows = all_rows(args.out)
    total = len(rows)
    p = fy.p_false_pass()
    passers = [n for n, (s, *_rest) in rows.items() if s["pass"]]
    by_model = Counter(m for *_x, m in rows.values())

    def closeness(s):
        return (s["years_ok"], s["total_r"] > 0, s["dd"] <= fy.MAX_DD_R,
                s["cost"] is not None and s["cost"] <= fy.MAX_COST_R, s["avg_r"] if s["avg_r"] is not None else -math.inf)

    ranked, seen = [], set()
    for n in sorted((n for n, (s, *_x) in rows.items() if s["trades"] >= fy.MIN_TRADES_TOP),
                    key=lambda n: closeness(rows[n][0]), reverse=True):
        _, symbol, name, _ = rows[n]
        fam = (symbol, family_of(name))
        if fam not in seen:
            seen.add(fam)
            ranked.append(n)
    yearly = Counter(s["years_ok"] for s, *_x in rows.values())
    flat = {n: s for n, (s, *_x) in rows.items()}
    lines = ["## Multiple testing", "",
             f"**Round-5 tests run: {total}** ({', '.join(f'{k} {v}' for k, v in sorted(by_model.items()))}); "
             f"**cumulative {PREVIOUS_TESTS + total}** with the 182 earlier tests. Expected false passers if "
             f"independent: {total * p:.3f} this round, {(PREVIOUS_TESTS + total) * p:.3f} cumulative (p = {p:.5f}). "
             "Most round-5 tests re-price the same entries under another cost model, so they are far from "
             "independent.", "",
             "Years passing (avg R > +0.10 and CI80 low > 0) across round-5 runs: "
             + ", ".join(f"{k}/5: {yearly.get(k, 0)}" for k in range(fy.YEARS, -1, -1)) + ".", "", "## Shortlist", ""]
    if passers:
        lines.append(f"**Passers ({len(passers)}): {', '.join(passers)}.** See the robustness section; any passer still "
                     "needs a fresh pre-registered period (live paper) before a decision.")
        shortlist = passers[:3] + [n for n in ranked if n not in passers][:max(0, 3 - len(passers))]
    else:
        lines.append("**No variant passes.** The 3 closest, one per instrument x family over all cost models (most "
                     "years passing, then total R > 0, DD, cost, avg R; >= 30 trades):")
        shortlist = ranked[:3]
    lines += ["", r4.table({n: flat[n] for n in shortlist}), ""]
    for n in shortlist:
        s, h = flat[n], flat[n]["half2"]
        lines.append(f"- **{n}** ({rows[n][3]}): {'passes' if s['pass'] else 'fails on ' + ', '.join(s['fails'])}; "
                     f"5y avg R {fy._f(s['avg_r'], '+.3f')}, total {s['total_r']:+.1f}R over {s['trades']} trades, MTM "
                     f"DD {s['dd']:.1f}R, cost {fy._f(s['cost'], '.3f')}R, per-year avg R "
                     + ", ".join(fy._f(y['avg_r'], '+.2f') for y in s["years"])
                     + f"; second half alone {h['n']} trades, avg R {fy._f(h['avg_r'], '+.3f')}.")
    years = [(pd.Timestamp(a).to_pydatetime(), pd.Timestamp(b).to_pydatetime()) for a, b in r4._home_years(args.data)]
    plot = shortlist[:5]
    path = os.path.join(os.path.dirname(os.path.abspath(args.report)), "round5_equity_r.png")
    fy.plot_equity(path, flat, plot, years,
                   title="Round 5 replay - cumulative R (net of the stated cost model, 0 % carry): closest variants")
    lines += ["", f"Equity: `round5_equity_r.png` ({', '.join(plot)}; years = EURUSD blocks).", ""]
    append(args.report, "\n".join(lines))
    log(f"final: {total} tests, passers {passers}, shortlist {shortlist}")


if __name__ == "__main__":
    main()
