"""Round 7 (REPLAY ONLY): seasonal / regime rotation over a fixed EURUSD module pool, 2010-2021 data.

Pool (fixed, existing modules): E3 / JT[R1+R5] / F2 under the round-5 relaxed rule with a pre-declared TP2
(``round5.relaxed_rule('TP2')``, run by ``round5.run_rule``), T1 = D1 Donchian swing (first D1 close beyond the prior
20-bar high/low, stop 2 x ATR14(D1) from the fill, chandelier trail best close -/+ 2 x ATR14(D1) ratcheted on D1
closes, time exit at the close of the 20th D1 bar after the signal; ``round4.run``), T1F = T1 only in the direction of
the rate-differential trend, and FLAT.

Regime features (causal: each value carries the time from which it is known, ``Features.state(t)``): quarter; CFTC
EURO FX non-commercial net percentile vs the trailing 3 years (HI >= 0.85 / LO <= 0.15 / MID); 60-observation trend sign
of US 2Y (US Treasury par curve) - ECB deposit facility rate; 20d realised vol of D1 closes vs the median of its last
250 values (HI / LO). Mappers (pre-declared) send each state cell to the module with the best selection-period avg R
(C_RAW, >= 20 trades, > 0) or FLAT.

Protocol: selection 2010-01 .. 2017-12 on frames and macro sliced before 2018-01-01, anchored walk-forward by year
(train up to Y-1, test Y = 2013 .. 2017); the mapper with the best mean yearly test avg R is fitted on 2010-2017 and
frozen to ``round7_frozen.json`` (+ ``replay/round7_frozen.json``, committed before the holdout). The holdout runner
reads only the committed file (fails closed) and runs once on 2018-01-01 .. 2021-09-30 (frames sliced before
2021-10-01); 2021-10 .. 2026-10 is a secondary CONTAMINATED check (earlier rounds selected on it).

Run (sections append to the report in order): ``python -m replay.round7 --sections data,declare`` (data coverage +
declaration), ``pool``, ``select`` (writes the frozen file; commit it), ``holdout``, ``contaminated``, ``final``.
Market / macro data and outputs live outside the repository (``/workspace/backtest``) and are never committed.
"""
import argparse
import glob
import hashlib
import json
import math
import os
import subprocess
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone

import numpy as np
import pandas as pd
from pandas.tseries.holiday import USFederalHolidayCalendar

from replay import entry_search as es
from replay import fiveyear as fy
from replay import intraday_backtest as bt
from replay import round4 as r4
from replay import round5 as r5

HOME = "EURUSD"
PIP = bt.PIP
UTC = timezone.utc
NY = es.NY
SEL_START = datetime(2010, 1, 1, tzinfo=UTC)
SEL_END = datetime(2018, 1, 1, tzinfo=UTC)  # exclusive: selection frames / macro sliced before it
HOLDOUT_START = SEL_END
HOLDOUT_END = datetime(2021, 10, 1, tzinfo=UTC)  # exclusive: holdout frames / macro sliced before it
CONTAM_START = HOLDOUT_END
CONTAM_END = datetime(2026, 10, 20, tzinfo=UTC)
WF_YEARS = (2013, 2014, 2015, 2016, 2017)
HOLDOUT_YEARS = (("2018", 2018), ("2019", 2019), ("2020", 2020), ("2021 (Jan-Sep)", 2021))

INTRADAY = ("E3", "JT[R1+R5]", "F2")
SWING = ("T1", "T1F")
POOL = INTRADAY + SWING  # + FLAT (avg R 0); tie order on the same decision time
RELAXED_TP = "TP2"  # pre-declared, never selected
DONCHIAN_N, T1_ATR_N, T1_STOP_ATR, T1_TRAIL_ATR, T1_HOLD_D = 20, 14, 2.0, 2.0, 20
MIN_CELL_TRADES = 20
COT_YEARS_RELEASES, COT_MIN_RELEASES, COT_HI, COT_LO = 156, 104, 0.85, 0.15
RATE_TREND_OBS = 60
RV_DAYS, RV_REF_DAYS, RV_REF_MIN = 20, 250, 120
ADX_N, TREND_SMA = 14, 100  # descriptive only
COT_CODE = "099741"  # EURO FX - CHICAGO MERCANTILE EXCHANGE
# CFTC releases delayed by US government shutdowns: conservative (late) bounds, never earlier than the real release
COT_SHUTDOWNS = ((date(2013, 10, 1), date(2013, 11, 26), date(2013, 12, 6)),
                 (date(2018, 12, 24), date(2019, 3, 5), date(2019, 3, 15)))
MAPPERS = {"BASE": (), "M_cal": ("Q",), "M_vol": ("VOL",), "M_cot": ("COT",), "M_rate": ("RATE",),
           "M_vol+rate": ("VOL", "RATE")}
STATE_VALUES = {"Q": 4, "VOL": 2, "COT": 3, "RATE": 2}
PASS_BAR = {"avg_r": 0.08, "pf": 1.15, "dd": 12.0, "tpm": 3.0, "years": 3}
PREVIOUS_TESTS = 444
MODELS = {"C_OANDA": r5.C_OANDA, "C_RAW": r5.C_RAW, "C_RAW_STRESS": r5.C_RAW_STRESS}
FROZEN_REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "round7_frozen.json")


# ---------------------------------------------------------------- frames
def is_weekend(index):
    """Bars starting in the FX weekend [Fri 17:00, Sun 17:00) NY (sparse OANDA weekend prints, mostly 2011)."""
    local = index.tz_convert(NY)
    wd, hour = local.dayofweek, local.hour
    return np.asarray(((wd == 4) & (hour >= 17)) | (wd == 5) | ((wd == 6) & (hour < 17)), dtype=bool)


def load_frames(data, end=None):
    """EURUSD 5m / 15m / 1h frames (``intraday_backtest.load_csv_frames``) without weekend bars; ``end``: keep bars
    starting before it only."""
    frames = bt.load_csv_frames(data, HOME)
    out = {}
    for tf, f in frames.items():
        f = f[~is_weekend(f.index)]
        if end is not None:
            f = f[f.index < pd.Timestamp(end)]
        out[tf] = f
    return out


def coverage(data):
    """Per TF: rows, first / last bar, rows per year, weekday gaps > 2 days, weekend bars dropped."""
    rows = {}
    for tf in ("1d", "1h", "15m", "5m"):
        path = os.path.join(data, f"{HOME}_{tf}.csv")
        raw = pd.read_csv(path, usecols=["start", "AskClose", "BidClose"])
        idx = pd.DatetimeIndex(pd.to_datetime(raw["start"], utc=True))
        weekend = is_weekend(idx) if tf != "1d" else np.zeros(len(idx), dtype=bool)
        keep = idx[~weekend]
        gap = pd.Series(keep[1:] - keep[:-1], index=keep[:-1])
        long_gaps = gap[gap > pd.Timedelta(hours=54 if tf != "1d" else 96)]  # longer than a weekend (+ DST hour)
        holiday = [(str(a.date()), round(g.total_seconds() / 86400, 1)) for a, g in long_gaps.items()]
        intraday = gap[(gap > pd.Timedelta(minutes=30)) & (gap < pd.Timedelta(days=1))] if tf in ("5m", "15m") else []
        spread = ((raw["AskClose"] - raw["BidClose"]) / PIP)[~weekend]
        rows[tf] = {"rows": int(len(keep)), "first": str(keep[0]), "last": str(keep[-1]),
                    "years": {int(k): int(v) for k, v in pd.Series(keep.year).value_counts().sort_index().items()},
                    "weekend_dropped": int(weekend.sum()), "long_gaps": holiday,
                    "intraday_gaps_30m": len(intraday),
                    "median_spread": {int(k): round(float(v), 2) for k, v in
                                      spread.groupby(keep.year).median().items()}}
    return rows


# ---------------------------------------------------------------- macro (public, no keys)
def _ns_ny(day, hh, mm=0):
    return int(pd.Timestamp(datetime.combine(day, time(hh, mm)), tz=NY).tz_convert(UTC).value)


_HOLIDAYS = None


def us_holidays():
    global _HOLIDAYS
    if _HOLIDAYS is None:
        _HOLIDAYS = set(USFederalHolidayCalendar().holidays("2005-01-01", "2027-12-31").date)
    return _HOLIDAYS


def next_business_day(d):
    d = d + timedelta(days=1)
    while d.weekday() >= 5 or d in us_holidays():
        d += timedelta(days=1)
    return d


def cot_available(asof):
    """COT (Tuesday as-of) is released Friday 15:30 ET (as-of + 3 days); used from 17:00 NY of the next business day
    after that Friday (absorbs holiday-shifted Monday releases); shutdown backlogs: the declared late bound."""
    release = asof + timedelta(days=3)
    for a, b, late in COT_SHUTDOWNS:
        if a <= asof <= b:
            release = max(release, late)
    return _ns_ny(next_business_day(release), 17)


def load_cot(raw_dir):
    """Weekly EURO FX (CFTC 099741) legacy futures-only non-commercial net (long - short) with its availability."""
    parts = []
    for path in sorted(glob.glob(os.path.join(raw_dir, "deacot*.zip"))):
        with zipfile.ZipFile(path) as z:
            with z.open(z.namelist()[0]) as handle:
                d = pd.read_csv(handle, low_memory=False)
        d.columns = [c.strip() for c in d.columns]
        d = d[d["CFTC Contract Market Code"].astype(str).str.strip() == COT_CODE]
        parts.append(pd.DataFrame({"asof": pd.to_datetime(d["As of Date in Form YYYY-MM-DD"]).dt.date,
                                   "long": d["Noncommercial Positions-Long (All)"].astype(float),
                                   "short": d["Noncommercial Positions-Short (All)"].astype(float)}))
    cot = pd.concat(parts).drop_duplicates("asof").sort_values("asof").reset_index(drop=True)
    cot["net"] = cot["long"] - cot["short"]
    cot["avail"] = [cot_available(d) for d in cot["asof"]]
    return cot


def trailing_rank(x, window, min_values):
    """Share of the last ``window`` values (current included) <= the current one; NaN with < ``min_values``."""
    out = np.full(len(x), np.nan)
    for k in range(len(x)):
        w = x[max(0, k - window + 1):k + 1]
        if len(w) >= min_values:
            out[k] = float((w <= x[k]).mean())
    return out


def load_ust2y(raw_dir):
    parts = []
    for path in sorted(glob.glob(os.path.join(raw_dir, "ust*.csv"))):
        d = pd.read_csv(path)
        parts.append(pd.DataFrame({"day": pd.to_datetime(d["Date"], format="%m/%d/%Y").dt.date,
                                   "y2": pd.to_numeric(d["2 Yr"], errors="coerce")}))
    out = pd.concat(parts).dropna().drop_duplicates("day").sort_values("day").reset_index(drop=True)
    return out


def load_ecb(path, monthly=False):
    d = pd.read_csv(path)
    out = pd.DataFrame({"period": d["TIME_PERIOD"].astype(str), "value": d["OBS_VALUE"].astype(float)})
    if monthly:
        out["day"] = [date(int(p[:4]), int(p[5:7]), 1) for p in out["period"]]
    else:
        out["day"] = pd.to_datetime(out["period"]).dt.date
    return out.sort_values("day").reset_index(drop=True)[["day", "value"]]


def month_plus(d, months):
    m = d.month - 1 + months
    return date(d.year + m // 12, m % 12 + 1, 1)


def load_rates(raw_dir):
    """Daily US 2Y (Treasury par curve) - ECB DFR (step series, date of change, forward filled); value of day d used
    from 17:00 NY of the next business day. Cross-check: 2Y - 3M Euribor (monthly average, month m used from the 1st
    of m + 2 = one-month publication lag)."""
    ust = load_ust2y(raw_dir)
    dfr = load_ecb(os.path.join(raw_dir, "ecb_dfr.csv"))
    pos = np.searchsorted(np.array(dfr["day"], dtype="datetime64[D]"), np.array(ust["day"], dtype="datetime64[D]"),
                          side="right") - 1
    ust["dfr"] = np.where(pos >= 0, dfr["value"].to_numpy()[np.maximum(pos, 0)], np.nan)
    ust["diff"] = ust["y2"] - ust["dfr"]
    eur = load_ecb(os.path.join(raw_dir, "ecb_euribor3m.csv"), monthly=True)
    eur["usable"] = [month_plus(d, 2) for d in eur["day"]]
    pos = np.searchsorted(np.array(eur["usable"], dtype="datetime64[D]"),
                          np.array([next_business_day(d) for d in ust["day"]], dtype="datetime64[D]"), side="right") - 1
    ust["euribor"] = np.where(pos >= 0, eur["value"].to_numpy()[np.maximum(pos, 0)], np.nan)
    ust["diff_eur"] = ust["y2"] - ust["euribor"]
    ust = ust.dropna(subset=["diff"]).reset_index(drop=True)
    ust["avail"] = [_ns_ny(next_business_day(d), 17) for d in ust["day"]]
    ust["trend"] = ust["diff"] - ust["diff"].shift(RATE_TREND_OBS)
    ust["trend_eur"] = ust["diff_eur"] - ust["diff_eur"].shift(RATE_TREND_OBS)
    return ust


def load_fomc(raw_dir):
    """Scheduled FOMC statement days (last day of each scheduled meeting) parsed from the Fed's historical pages
    (2009-2020) and the current calendar page (2021+); conference calls, notation votes, unscheduled and cancelled
    meetings excluded (the ``entry_search.FOMC_DAYS`` convention)."""
    import re
    months = {m: i for i, m in enumerate(("January", "February", "March", "April", "May", "June", "July", "August",
                                          "September", "October", "November", "December"), 1)}
    short = {m[:3]: i for m, i in months.items()}
    days = set()
    for path in sorted(glob.glob(os.path.join(raw_dir, "fomc", "h*.htm"))):
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
        for head in re.findall(r"<h5[^>]*>([^<]*)</h5>", text):
            m = re.match(r"([A-Za-z]+)(?:/([A-Za-z]+))? (\d+)(?:-(?:([A-Za-z]+) )?(\d+))? Meeting - (\d{4})$",
                         " ".join(head.split()))
            if m:  # "(unscheduled)" / "(cancelled)" / calls / notation votes never match
                first, slash, d1, month2, d2, year = m.groups()
                last = month2 or slash or first
                days.add(date(int(year), months.get(last) or short[last[:3]], int(d2 or d1)))
    path = os.path.join(raw_dir, "fomc", "cal.htm")
    if os.path.exists(path):
        with open(path, encoding="utf-8", errors="replace") as handle:
            parts = re.split(r"(\d{4}) FOMC Meetings", handle.read())
        for k in range(1, len(parts) - 1, 2):
            for month, span in re.findall(r"fomc-meeting__month[^>]*>(?:<strong>)?([^<]+)<.*?"
                                          r"fomc-meeting__date[^>]*>([^<]+)<", parts[k + 1], re.S):
                last, dd = month.split("/")[-1].strip(), re.findall(r"\d+", span)
                if dd and not any(w in span.lower() for w in ("cancel", "notation", "unscheduled")):
                    days.add(date(int(parts[k]), months.get(last) or short[last[:3]], int(dd[-1])))
    return sorted(days)


# ---------------------------------------------------------------- features
class Features:
    """Causal regime state at a decision time t (ns): every series value carries the first time it is known."""

    def __init__(self, frames, macro_dir, cutoff):
        cut = int(pd.Timestamp(cutoff).value)
        d1 = r4.htf_bars(frames["1h"], "D1")
        self.d1 = d1
        logret = np.diff(np.log(d1.c), prepend=np.nan)
        self.rv = pd.Series(logret).rolling(RV_DAYS).std().to_numpy() * math.sqrt(252)
        self.rv_ref = pd.Series(self.rv).rolling(RV_REF_DAYS, min_periods=RV_REF_MIN).median().to_numpy()
        self.adx = r4.adx(d1, ADX_N)
        self.sma = r4.sma(d1.c, TREND_SMA)
        cot = load_cot(os.path.join(macro_dir, "raw"))
        cot = cot[cot["avail"] < cut].reset_index(drop=True)
        cot["pct"] = trailing_rank(cot["net"].to_numpy(), COT_YEARS_RELEASES, COT_MIN_RELEASES)
        self.cot = cot
        rates = load_rates(os.path.join(macro_dir, "raw"))
        self.rates = rates[rates["avail"] < cut].reset_index(drop=True)
        self._cot_avail = self.cot["avail"].to_numpy(np.int64)
        self._rate_avail = self.rates["avail"].to_numpy(np.int64)

    @staticmethod
    def _at(avail, t):
        return int(np.searchsorted(avail, t, side="right")) - 1

    def state(self, t):
        """{'Q', 'VOL', 'COT', 'RATE', + descriptive 'MONTH', 'ADX', 'TREND'}; a component unknown at t is None."""
        local = pd.Timestamp(int(t), tz=UTC).tz_convert(NY)
        day = bt.day_key(local.to_pydatetime(warn=False))
        out = {"Q": f"Q{(day.month - 1) // 3 + 1}", "MONTH": f"{day.month:02d}"}
        k = self._at(self.d1.avail, t)
        rv, ref = (self.rv[k], self.rv_ref[k]) if k >= 0 else (math.nan, math.nan)
        out["VOL"] = ("HI" if rv > ref else "LO") if math.isfinite(rv) and math.isfinite(ref) else None
        adx = self.adx[k] if k >= 0 else math.nan
        out["ADX"] = ("ADX>=25" if adx >= 25 else "ADX<25") if math.isfinite(adx) else None
        sma = self.sma[k] if k >= 0 else math.nan
        out["TREND"] = ("UP" if self.d1.c[k] > sma else "DOWN") if math.isfinite(sma) else None
        j = self._at(self._cot_avail, t)
        pct = float(self.cot["pct"].iloc[j]) if j >= 0 else math.nan
        out["COT"] = (("HI" if pct >= COT_HI else "LO" if pct <= COT_LO else "MID") if math.isfinite(pct) else None)
        j = self._at(self._rate_avail, t)
        tr = float(self.rates["trend"].iloc[j]) if j >= 0 else math.nan
        out["RATE"] = ("UP" if tr > 0 else "DOWN") if math.isfinite(tr) else None
        return out


def n_cells():
    return sum(math.prod(STATE_VALUES[k] for k in keys) for keys in MAPPERS.values())


def cell_key(state, keys):
    """Mapper cell of a state ('ALL' for the single-module baseline); None if a component is unknown."""
    if not keys:
        return "ALL"
    parts = []
    for k in keys:
        if state.get(k) is None:
            return None
        parts.append(f"{k}={state[k]}")
    return "|".join(parts)


# ---------------------------------------------------------------- the pool
def t1_setups(d1, family="T1"):
    """T1 Donchian swing on closed D1 bars: first close beyond the prior 20-bar high / low; stop 2 x ATR14(D1) from
    the fill; chandelier best close -/+ 2 x ATR14 at each later D1 close; time exit at the close of the 20th D1 bar
    after the signal bar (market, last 5m bar ending by it). Sorted by decision time."""
    a = r4.atr(d1, T1_ATR_N)
    out = []
    for side in (1, -1):
        if side > 0:
            enter = r4.first_cross(r4.gt(d1.c, r4.prior_max(d1.h, DONCHIAN_N)))
        else:
            enter = r4.first_cross(r4.lt(d1.c, r4.prior_min(d1.l, DONCHIAN_N)))
        for k in np.flatnonzero(enter):
            if not math.isfinite(a[k]):
                continue
            end = k + T1_HOLD_D
            out.append(r4.Setup(int(d1.avail[k]), side, family, f"{family}:{side}:{k}", stop_dist=T1_STOP_ATR * a[k],
                                events=r4._trail(d1, k, side, a, T1_TRAIL_ATR, end),
                                deadline=int(d1.avail[end]) if end < len(d1) else None))
    out.sort(key=lambda s: s.t)
    return out


class System:
    """EURUSD frames (sliced at ``cutoff``) + features + per-cost-model tapes / merged module candidates."""

    def __init__(self, frames, macro_dir, cutoff):
        self.frames, self.cutoff = frames, cutoff
        self.end = frames["5m"].index[-1].to_pydatetime() + timedelta(minutes=5)
        with r4.instrument_pip(PIP), r4.schedule(()):
            self.ctx = es.Context(frames)
        self.features = Features(frames, macro_dir, cutoff)
        self.start_ns = int(self.ctx.tradable_start())
        self.news = es.high_impact_days(SEL_START.date(), self.end.date())
        for d in load_fomc(os.path.join(macro_dir, "raw")):
            if d < self.end.date():
                self.news.setdefault(d, "FOMC")
        self.d1 = r4.htf_bars(frames["1h"], "D1")
        self._tapes, self._items, self._states = {}, {}, {}
        self.rule = r5.relaxed_rule(RELAXED_TP)

    def tape(self, model):
        key = r5.model_name(model)
        if key not in self._tapes:
            self._tapes[key] = r5.model_tape(model, HOME, self.frames["5m"], PIP, True)
        return self._tapes[key]

    def state(self, t):
        if t not in self._states:
            self._states[t] = self.features.state(t)
        return self._states[t]

    def items(self, model):
        """[(t, module, candidate / setup)] of every pool module, sorted by (t, pool order)."""
        key = r5.model_name(model)
        if key not in self._items:
            tape = self.tape(model)
            out = []
            with r4.instrument_pip(PIP), r4.schedule(()):
                for q, fam in enumerate(INTRADAY):
                    for c in r5.family_candidates(self.ctx, fam, self.news, self.start_ns,
                                                  lambda k: float(tape.spread[k]) / 2):
                        out.append((c.t, q, fam, c))
            for q, fam in enumerate(SWING, len(INTRADAY)):
                for st in t1_setups(self.d1, fam):
                    if st.t >= self.start_ns:
                        out.append((st.t, q, fam, st))
            out.sort(key=lambda x: (x[0], x[1]))
            self._items[key] = [(t, fam, c) for t, _, fam, c in out]
        return self._items[key]


def t1f_allowed(state, side):
    """T1F: long only when the rate differential (US 2Y - ECB DFR) trend is DOWN (EUR-supportive), short only UP."""
    if state.get("RATE") is None:
        return False
    return (side > 0 and state["RATE"] == "DOWN") or (side < 0 and state["RATE"] == "UP")


def run_book(system, model, route, a, b):
    """Sequential routed walk of the pool on decisions in [a, b): a candidate of module m is taken only if
    ``route(state(t)) == m``; one position at a time across modules, one entry per idea, 2R/day realised halt
    (trading day of the exit). Intraday modules: ``round5.run_rule`` (relaxed TP2 rule); T1 / T1F: ``round4.run``.
    Returns (trades, skips); each trade carries ``module`` and its decision ``state``."""
    tape, ctx = system.tape(model), system.ctx
    a_ns, b_ns = bt._ns(a), bt._ns(b)
    trades, skips = [], Counter()
    free_at, used, halted = -1, set(), set()
    day_r = defaultdict(float)
    for t, fam, cand in system.items(model):
        if t < a_ns or t >= b_ns:
            continue
        state = system.state(t)
        if route(state) != fam:
            continue
        side = cand.side if fam in SWING else (1 if cand.side == es.LONG else -1)
        if fam == "T1F" and not t1f_allowed(state, side):
            skips["t1f_against_rate_trend"] += 1
            continue
        idea = f"{fam}|{cand.idea}"
        if idea in used:
            skips["idea_already_traded"] += 1
            continue
        if t < free_at:
            skips["position_open"] += 1
            continue
        if bt.day_key(r4._dt(t)) in halted:
            skips["daily_loss_halt"] += 1
            continue
        if fam in SWING:
            got, sk = r4.run([cand], tape, fam)
        else:
            with r4.instrument_pip(PIP):
                got, sk = r5.run_rule([cand], ctx, tape, fam, system.rule)
        if not got:
            skips.update(sk)
            continue
        trade = got[0]
        trade.update(module=fam, state=state)
        trades.append(trade)
        used.add(idea)
        free_at = bt._ns(trade["exit_at"])
        key = bt.day_key(trade["exit_at"])
        day_r[key] += trade["r"]
        if day_r[key] <= -bt.DAILY_HALT_R:
            halted.add(key)
    return trades, skips


def fixed(module):
    return lambda state: module


def mapped(keys, mapping):
    return lambda state: mapping.get(cell_key(state, keys), "FLAT")


# ---------------------------------------------------------------- fitting and walk-forward
def window(trades, a, b, purge=False):
    return [t for t in trades if a <= t["signal_at"] < b and (not purge or t["exit_at"] <= b)]


def fit(keys, standalone, a, b):
    """Cell -> module: per mapper cell, the pool module with the best avg R over standalone trades decided in [a, b)
    (purged: exited by b) with >= MIN_CELL_TRADES trades and avg R > 0 (else FLAT). Returns (mapping, stats)."""
    stats = defaultdict(dict)
    for fam, trades in standalone.items():
        groups = defaultdict(list)
        for t in window(trades, a, b, purge=True):
            cell = cell_key(t["state"], keys)
            if cell is not None:
                groups[cell].append(t["r"])
        for cell, rs in groups.items():
            stats[cell][fam] = (len(rs), float(np.mean(rs)))
    mapping = {}
    for cell in sorted(stats):
        ok = [(avg, fam) for fam, (n, avg) in stats[cell].items() if n >= MIN_CELL_TRADES and avg > 0]
        mapping[cell] = max(ok, key=lambda x: (x[0], -POOL.index(x[1])))[1] if ok else "FLAT"
    return mapping, {c: dict(v) for c, v in stats.items()}


def year_bounds(y):
    return datetime(y, 1, 1, tzinfo=UTC), datetime(y + 1, 1, 1, tzinfo=UTC)


def walk_forward(system, standalone, keys):
    """Anchored yearly walk-forward: fit on [SEL_START, Y) (purged), test on Y with a routed book (C_RAW)."""
    out = {}
    for y in WF_YEARS:
        a, b = year_bounds(y)
        mapping, _ = fit(keys, standalone, SEL_START, a)
        trades, _ = run_book(system, r5.C_RAW, mapped(keys, mapping), a, min(b, system.end))
        out[y] = {"mapping": mapping, "trades": trades}
    return out


def wf_score(wf):
    """Mean of the yearly test avg R (a year without trades counts 0)."""
    return float(np.mean([np.mean([t["r"] for t in v["trades"]]) if v["trades"] else 0.0 for v in wf.values()]))


def choose(scores, mappings):
    """Best mean walk-forward test avg R; ties: fewer cells, then name."""
    return min(scores, key=lambda n: (-scores[n], len(mappings[n]), n))


# ---------------------------------------------------------------- metrics
def profit_factor(rs):
    win, loss = sum(r for r in rs if r > 0), -sum(r for r in rs if r < 0)
    return win / loss if loss > 0 else (math.inf if win > 0 else None)


def summarize(trades, a, b):
    months = max((b - a).total_seconds() / fy.MONTH_S, 1e-9)
    n = len(trades)
    if not n:
        return {"trades": 0, "tpm": 0.0, "win": None, "avg_r": None, "pf": None, "total_r": 0.0, "dd": 0.0,
                "ci80_low": None, "cost": None, "modules": {}}
    m = bt.metrics(trades, months)
    mods = defaultdict(list)
    for t in trades:
        mods[t["module"]].append(t["r"])
    return {"trades": n, "tpm": m["trades_per_month"], "win": m["win_rate"], "avg_r": m["avg_r"],
            "pf": profit_factor([t["r"] for t in trades]), "total_r": m["total_r"], "dd": m["max_dd_mtm_r"],
            "ci80_low": m["ci80_low"], "cost": m["avg_cost_r"],
            "modules": {k: {"n": len(v), "total_r": float(sum(v)), "avg_r": float(np.mean(v))}
                        for k, v in sorted(mods.items())}}


def yearly(trades, years):
    out = {}
    for label, y in years:
        a, b = year_bounds(y)
        out[label] = summarize(window(trades, a, b), a, b)
    return out


def by_cell(trades, keys):
    groups = defaultdict(list)
    for t in trades:
        groups[(cell_key(t["state"], keys), t["module"])].append(t["r"])
    return {f"{c} -> {m}": {"n": len(v), "avg_r": float(np.mean(v)), "total_r": float(sum(v))}
            for (c, m), v in sorted(groups.items(), key=lambda x: (str(x[0][0]), x[0][1]))}


def holdout_pass(s, years):
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
    positive = sum(1 for v in years.values() if v["total_r"] > 0)
    if positive < PASS_BAR["years"]:
        fails.append(f"positive years {positive}/{len(years)} < {PASS_BAR['years']}")
    return not fails, fails


def _f(x, spec):
    return fy._f(x, spec)


# ---------------------------------------------------------------- frozen file
def freeze(path, payload):
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    for p in (path, FROZEN_REPO):
        with open(p, "w", encoding="utf-8") as handle:
            handle.write(text)
    return text


def committed_frozen():
    """HEAD content of replay/round7_frozen.json; None if git is unavailable or the file is not committed."""
    repo = os.path.dirname(os.path.dirname(FROZEN_REPO))
    try:
        out = subprocess.run(["git", "-C", repo, "show", "HEAD:replay/round7_frozen.json"], capture_output=True,
                             text=True, check=False)
    except OSError:
        return None
    return out.stdout if out.returncode == 0 else None


def load_frozen(path):
    """The frozen mapper: the holdout runner's only parameter source; must equal the committed copy (fails closed)."""
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    committed = committed_frozen()
    if committed is None:
        raise RuntimeError("cannot verify the frozen file: git unavailable or replay/round7_frozen.json not in HEAD")
    if committed != text:
        raise RuntimeError("frozen file differs from the committed replay/round7_frozen.json")
    payload = json.loads(text)
    missing = [k for k in ("mapper", "keys", "mapping", "rule_tp", "pool") if k not in payload]
    if missing:
        raise ValueError(f"frozen file incomplete: {missing}")
    if tuple(payload["pool"]) != POOL or payload["rule_tp"] != RELAXED_TP:
        raise ValueError("frozen pool / rule differ from this module")
    return payload


def file_sha(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


# ---------------------------------------------------------------- persistence
KEEP = ("signal_at", "fill_at", "exit_at", "side", "module", "r", "gross_r", "risk_pips", "reason", "commission_r")


def _ser(trades):
    return [{**{k: (t[k].isoformat() if isinstance(t[k], datetime) else t[k]) for k in KEEP}, "state": t["state"]}
            for t in trades]


def _de(rows):
    return [{**t, **{k: pd.Timestamp(t[k]).to_pydatetime() for k in ("signal_at", "fill_at", "exit_at")}}
            for t in rows]


def save(out, key, payload):
    with open(os.path.join(out, f"r7_{key}.json"), "w", encoding="utf-8") as handle:
        json.dump(payload, handle, default=float)


def load(out, key):
    with open(os.path.join(out, f"r7_{key}.json"), encoding="utf-8") as handle:
        return json.load(handle)


# ---------------------------------------------------------------- report
DECLARATION = """\
## 1. Predeclared design, protocol and grid (written and committed before any selection run)

**Question**: does routing a fixed pool of existing EURUSD modules by a few slow, causal regime states (season, CFTC
positioning, rate-differential trend, volatility) produce an edge that survives a 2018-2021 holdout?

**Data** (section 2): OANDA practice EURUSD 5m / 15m / 1h (mid OHLC + bid/ask close) 2010-01-03 .. 2021-09-30 (new,
`dl10y.py`, resumable monthly chunks) merged with `data5y` (2021-10 .. 2026-10); D1 bars are built from closed 1h bars
(17:00 NY trading days; the downloaded D1 file is for coverage only). Bars starting in the FX weekend
[Fri 17:00, Sun 17:00) NY are dropped (sparse OANDA weekend prints, mostly 2011). Macro (public, no keys) with explicit
availability: CFTC COT legacy futures-only EURO FX (099741) non-commercial net (long - short), as-of Tuesday, released
Friday 15:30 ET, **used from 17:00 NY of the next business day after that Friday** (absorbs holiday-shifted releases);
2013 / 2018-19 shutdown backlogs use declared late bounds (as-of 2013-10-01..2013-11-26 -> 2013-12-06; as-of
2018-12-24..2019-03-05 -> 2019-03-15). US 2Y: US Treasury daily par yield curve "2 Yr" (the source of FRED DGS2; FRED
itself was unreachable from this host) - ECB deposit facility rate (ECB Data Portal `FM.D.U2.EUR.4F.KR.DFR.LEV`, =
FRED ECBDFR, step series by date of change); value of day d **used from 17:00 NY of the next business day**.
Cross-check only: 3M Euribor monthly average (ECB `FM.M.U2.EUR.RT.MM.EURIBOR3MD_.HSTA`, substitute for OECD
IR3TIB01DEM156N), month m used from the 1st of m + 2 (one-month publication lag). OANDA retail sentiment: **skipped**
(no history). FOMC statement days 2010-2021 (scheduled meetings, Fed historical pages) are added to the existing news
calendar (NFP / CPI approx) used by JT.

**Regime states** (at the decision time t; a component unknown at t -> NO_DATA -> FLAT for mappers using it):
- `Q`: calendar quarter of the NY trading day (Q1..Q4).
- `COT`: percentile of the latest known net among the last 156 releases (3 years, current included, >= 104 known):
  HI >= 0.85, LO <= 0.15, else MID.
- `RATE`: sign of (US2Y - DFR)[k] - (US2Y - DFR)[k - 60 observations] at the latest known day k: UP (> 0, USD-supportive)
  / DOWN.
- `VOL`: annualised std of the last 20 D1 log returns vs the median of its last 250 values (>= 120 known): HI (>) / LO.
- Descriptive only (reported on selection, never used by a mapper): month, ADX14(D1) >= 25, D1 close vs SMA100.

**Pool (fixed)**: E3 (Asian breakout + retest), JT[R1+R5] (limit at the 15m OB edge), F2 (previous-day range mean
reversion) - candidates unchanged from rounds 2-5 - under the round-5 relaxed rule with a **pre-declared TP2** (structure
stop clamped to 15-50 pips, TP 2R, time exit at the 5th NY trading-day close, Friday 17:00 NY close; never selected);
**T1** D1 Donchian swing: first D1 close beyond the prior 20-bar high / low -> market at the next 5m open; stop 2 x
ATR14(D1) from the fill; chandelier stop best D1 close since the signal -/+ 2 x ATR14(D1), ratcheted at each D1 close;
time exit at the close of the 20th D1 bar after the signal; no Friday close; **T1F** = T1 only long when RATE = DOWN /
short when RATE = UP (the one declared direction-filter variant, entered into the pool); **FLAT** (avg R 0). All TFs
exist from 2010 (section 2), so no module is dropped for any period.

**Book**: one position at a time across modules, one entry per idea, fill at the open of the 5m bar starting at the
decision close (JT limit semantics unchanged), same-bar SL first, gaps at the open, round-5 cost semantics (commission
once, in R), 2R realised daily halt. A candidate of module m is taken only if the router sends the decision state to m.
Standalone module trades (router = always m) are the fitting data.

**Mappers (6, fixed)**: `BASE` (always-best-single-module, 1 cell), `M_cal` (Q: 4 cells), `M_vol` (VOL: 2), `M_cot`
(COT: 3), `M_rate` (RATE: 2), `M_vol+rate` (VOL x RATE: 4). Fit: per cell, the pool module with the best avg R over
the standalone C_RAW trades of the fit window whose decision state falls in the cell (purged: exited by the window
end), requiring >= 20 trades and avg R > 0; otherwise FLAT.

**Selection (2010-01 .. 2017-12 only)**: frames sliced to bars starting before 2018-01-01 and macro to values known before
it. Anchored walk-forward by year: fit on [2010-01-01, Y-01-01), test = routed book on Y (C_RAW), Y = 2013..2017.
Score = mean of the 5 yearly test avg R (a year without trades counts 0); choose the best mapper (ties: fewer cells,
then name), refit it on 2010-2017 and freeze the cell -> module map to `/workspace/backtest/round7_frozen.json` +
`replay/round7_frozen.json`, **committed before the holdout runner**.

**Holdout (once)**: 2018-01-01 .. 2021-09-30 decisions, frames sliced before 2021-10-01 and macro to values known before
it (features warm up on 2010-2017); the runner loads only the committed frozen file (fails closed: no git / not in HEAD
/ different content). Cost models: `C_RAW` (0.2 pip spread + 0.1 pip slippage + 0.7 pip commission ~ 1.0-1.1 pip round
trip; the bar), `C_OANDA` (causal 30-day median OANDA spread + slippage), `C_RAW_STRESS` (0.4 + 0.2 + 0.7). Then a
secondary run 2021-10-01 .. 2026-10-19 labelled **CONTAMINATED** (rounds 1-6 selected on these years).

**Metrics**: avg R, total R, PF, MTM max DD R, trades, trades/month, win %, yearly breakdown, per-module and per-cell
contribution, day-block bootstrap CI80 low, avg cost R.

**Pass bar (holdout, C_RAW)**: avg R >= +0.08 AND PF > 1.15 AND MTM DD < 12R AND >= 3 trades/month AND total R > 0
in >= 3 of the 4 years (2018, 2019, 2020, 2021 Jan-Sep).

**Multiple testing**: 6 mappers (selection candidates) built from 16 cells x 5 modules = 80 cell-module comparisons;
counted as 6 (mapper level) and conservatively 86. Cumulative before this round: 444.

Note: before this declaration was committed, a smoke run on the selection frames printed T1 (90 trades, +5.7R) and the
E3 trade count while checking the code; no design element was changed after it.
"""


def coverage_section(cov, cot, rates, fomc):
    lines = ["## 2. Data: coverage, gaps and macro availability", "",
             "| TF | Rows | First bar | Last bar | Weekend bars dropped | Gaps longer than a weekend (holidays) | "
             "Gaps 30 min-1 d |", "|---|---|---|---|---|---|---|"]
    for tf, c in cov.items():
        gaps = ", ".join(f"{d} ({g}d)" for d, g in c["long_gaps"]) or "none"
        lines.append(f"| {tf} | {c['rows']} | {c['first']} | {c['last']} | {c['weekend_dropped']} | {gaps} | "
                     f"{c['intraday_gaps_30m'] if tf in ('5m', '15m') else '-'} |")
    years = sorted(cov["5m"]["years"])
    lines += ["", "Rows per year (after the weekend filter) and median 5m bid/ask spread (pips):", "",
              "| TF | " + " | ".join(str(y) for y in years) + " |", "|---" * (1 + len(years)) + "|"]
    for tf, c in cov.items():
        lines.append(f"| {tf} | " + " | ".join(str(c["years"].get(y, 0)) for y in years) + " |")
    lines.append("| spread 5m | " + " | ".join(f"{cov['5m']['median_spread'].get(y, math.nan):.1f}" for y in years) + " |")
    agree = rates.dropna(subset=["trend", "trend_eur"])
    same = float((np.sign(agree["trend"]) == np.sign(agree["trend_eur"])).mean()) if len(agree) else math.nan
    lines += ["", "**Macro**:", "",
              f"- COT EURO FX (099741): {len(cot)} weekly reports, as-of {cot['asof'].iloc[0]} .. {cot['asof'].iloc[-1]}; "
              f"net range {cot['net'].min():+,.0f} .. {cot['net'].max():+,.0f} contracts. Lag: used from 17:00 NY of the "
              "next business day after the Friday release (typically Monday 17:00 NY = 6 days after as-of).",
              f"- US 2Y (Treasury par curve) - ECB DFR: {len(rates)} daily values {rates['day'].iloc[0]} .. "
              f"{rates['day'].iloc[-1]}; DFR range {rates['dfr'].min():+.2f} .. {rates['dfr'].max():+.2f} %, "
              f"differential {rates['diff'].min():+.2f} .. {rates['diff'].max():+.2f} pp. Lag: next business day "
              "17:00 NY.",
              f"- Cross-check 2Y - 3M Euribor (monthly, 1-month publication lag): 60-obs trend sign agrees with the DFR "
              f"version on {100 * same:.0f} % of days (not used by any mapper).",
              f"- FOMC scheduled statement days parsed: {len(fomc)} ({fomc[0]} .. {fomc[-1]}).",
              "- FRED (`fredgraph.csv`) timed out from this host (HTTP/2 stream error, then no response in 90 s); the same "
              "underlying public series were taken from the US Treasury and the ECB Data Portal (no keys).",
              "- OANDA retail sentiment: skipped (no history available).",
              "- Realised vol, ADX14 and SMA100 trend are computed from the D1 bars built from closed 1h bars."]
    return "\n".join(lines)


def pool_section(system, label):
    counts = defaultdict(Counter)
    for t, fam, _ in system.items(r5.C_RAW):
        counts[fam][pd.Timestamp(int(t), tz=UTC).year] += 1
    years = sorted({y for c in counts.values() for y in c})
    lines = ["## 3. Pool availability (candidate counts only, no results)", "",
             f"{label}. 5m / 15m / 1h exist for every year from 2010 (section 2), so every module runs from its warm-up "
             f"(tradable from {pd.Timestamp(system.start_ns, tz=UTC):%Y-%m-%d}: 20 complete D1 bars); **no module is "
             "dropped**. T1F has the same candidates as T1 (the rate-direction filter applies at the routed decision).",
             "", "| Module | " + " | ".join(str(y) for y in years) + " |", "|---" * (1 + len(years)) + "|"]
    for fam in POOL:
        lines.append(f"| {fam} | " + " | ".join(str(counts[fam].get(y, 0)) for y in years) + " |")
    return "\n".join(lines)


def summary_table(rows):
    head = ("| Run | Trades (tr/mo) | Win % | Avg R | PF | Total R | MTM DD R | CI80 low | Cost R | Modules: n, total R |\n"
            + "|---" * 10 + "|")
    lines = [head]
    for name, s in rows.items():
        mods = "; ".join(f"{k} {v['n']}, {v['total_r']:+.1f}" for k, v in s["modules"].items()) or "-"
        lines.append(f"| {name} | {s['trades']} ({s['tpm']:.1f}) | "
                     f"{_f(s['win'] * 100 if s['win'] is not None else None, '.0f')} | {_f(s['avg_r'], '+.3f')} | "
                     f"{_f(s['pf'], '.2f')} | {s['total_r']:+.1f} | {s['dd']:.1f} | {_f(s['ci80_low'], '+.3f')} | "
                     f"{_f(s['cost'], '.3f')} | {mods} |")
    return "\n".join(lines)


def year_table(by_run):
    labels = list(next(iter(by_run.values())))
    lines = ["| Run | " + " | ".join(labels) + " |", "|---" * (1 + len(labels)) + "|"]
    for name, ys in by_run.items():
        lines.append(f"| {name} | " + " | ".join(
            f"{_f(ys[y]['avg_r'], '+.3f')} / {ys[y]['total_r']:+.1f}R ({ys[y]['trades']})" for y in labels) + " |")
    return "\n".join(lines)


def cell_table(cells):
    lines = ["| Cell -> module | Trades | Avg R | Total R |", "|---|---|---|---|"]
    for k, v in cells.items():
        lines.append(f"| {k} | {v['n']} | {v['avg_r']:+.3f} | {v['total_r']:+.1f} |")
    return "\n".join(lines)


def descriptive(standalone, feature):
    """Standalone module avg R (n) by a descriptive state component (selection data; diagnostics only)."""
    values = sorted({t["state"].get(feature) for ts in standalone.values() for t in ts} - {None})
    lines = [f"| Module \\ {feature} | " + " | ".join(values) + " |", "|---" * (1 + len(values)) + "|"]
    for fam, ts in standalone.items():
        cells = []
        for v in values:
            rs = [t["r"] for t in ts if t["state"].get(feature) == v]
            cells.append(f"{np.mean(rs):+.3f} ({len(rs)})" if rs else "-")
        lines.append(f"| {fam} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def plot(path, curves, spans, title):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    colors = {"C_RAW": "#2a78d6", "C_OANDA": "#eb6834"}
    ink, muted, surface = "#0b0b0b", "#52514e", "#fcfcfb"
    fig, ax = plt.subplots(figsize=(12, 5.2), dpi=120, facecolor=surface)
    ax.set_facecolor(surface)
    for (label, a, b, color, hatch) in spans:
        ax.axvspan(a, b, facecolor=color, alpha=0.8, linewidth=0, hatch=hatch, edgecolor="#c9c8c4")
        ax.text(a, 1.0, f" {label}", transform=ax.get_xaxis_transform(), va="top", fontsize=8, color=muted)
    for name, trades in curves.items():
        trades = sorted(trades, key=lambda t: t["exit_at"])
        if not trades:
            continue
        xs = [spans[0][1]] + [t["exit_at"] for t in trades]
        ys = np.concatenate([[0.0], np.cumsum([t["r"] for t in trades])])
        ax.step(xs, ys, where="post", color=colors.get(name, ink), linewidth=1.8, label=name)
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
    ax.legend(frameon=False, loc="lower left", labelcolor=ink, fontsize=8)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path, facecolor=surface)
    plt.close(fig)


# ---------------------------------------------------------------- main
def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", default="/workspace/backtest/data10y")
    parser.add_argument("--macro", default="/workspace/backtest/macro")
    parser.add_argument("--out", default="/workspace/backtest/r7")
    parser.add_argument("--report", default="/workspace/backtest/report_round7.md")
    parser.add_argument("--frozen", default="/workspace/backtest/round7_frozen.json")
    parser.add_argument("--png", default="/workspace/backtest/round7_equity_r.png")
    parser.add_argument("--sections", default="declare")
    args = parser.parse_args(argv)
    sections = args.sections.split(",")
    os.makedirs(args.out, exist_ok=True)

    def log(message):
        print(f"{datetime.now(UTC).isoformat(timespec='seconds')} {message}", flush=True)

    if "declare" in sections:
        with open(args.report, "w", encoding="utf-8") as handle:
            handle.write("# Round 7 - seasonal / regime rotation, EURUSD 2010-2021 (REPLAY ONLY)\n\n"
                         f"Started {datetime.now(UTC).isoformat(timespec='seconds')} by `python -m replay.round7` "
                         "(branch `feat/backtest-v2-i2`). Production unchanged. Sections are appended as they "
                         "finish.\n\n")
        fy.append(args.report, DECLARATION)
        log("declared")
    for name, fn in (("data", data_section), ("pool", pool_run), ("select", select_section),
                     ("holdout", holdout_section), ("contaminated", contaminated_section), ("final", final_section)):
        if name in sections:
            fn(args, log)


def data_section(args, log):
    raw = os.path.join(args.macro, "raw")
    cov, cot, rates, fomc = coverage(args.data), load_cot(raw), load_rates(raw), load_fomc(raw)
    fy.append(args.report, coverage_section(cov, cot, rates, fomc))
    log("data section written")


def pool_run(args, log):
    frames = load_frames(args.data, end=SEL_END)
    system = System(frames, args.macro, SEL_END)
    fy.append(args.report, pool_section(system, "Selection frames (bars before 2018-01-01)"))
    log("pool section written")


def select_section(args, log):
    log("selection: frames and macro sliced before 2018-01-01")
    frames = load_frames(args.data, end=SEL_END)
    assert frames["5m"].index[-1] < pd.Timestamp(SEL_END), "holdout data in the selection frames"
    system = System(frames, args.macro, SEL_END)
    end = min(SEL_END, system.end)
    standalone = {fam: run_book(system, r5.C_RAW, fixed(fam), SEL_START, end)[0] for fam in POOL}
    for fam, ts in standalone.items():
        log(f"  standalone {fam}: {len(ts)} trades")
    wfs, scores, mappings = {}, {}, {}
    for name, keys in MAPPERS.items():
        wfs[name] = walk_forward(system, standalone, keys)
        scores[name] = wf_score(wfs[name])
        mappings[name], _ = fit(keys, standalone, SEL_START, SEL_END)
        log(f"  {name}: WF mean test avg R {scores[name]:+.3f}")
    chosen = choose(scores, mappings)
    keys = MAPPERS[chosen]
    mapping, stats = fit(keys, standalone, SEL_START, SEL_END)
    insample = {m: run_book(system, model, mapped(keys, mapping), SEL_START, end)[0] for m, model in MODELS.items()}
    payload = {"system": "ROUND7_ROTATION", "symbol": HOME, "mapper": chosen, "keys": list(keys), "mapping": mapping,
               "pool": list(POOL), "rule_tp": RELAXED_TP, "min_cell_trades": MIN_CELL_TRADES,
               "selection": {"window": [SEL_START.isoformat(), SEL_END.isoformat()], "wf_years": list(WF_YEARS),
                             "scores": scores, "data_last_bar": frames["5m"].index[-1].isoformat(),
                             "macro_cutoff": SEL_END.isoformat()},
               "frozen_at": datetime.now(UTC).isoformat(timespec="seconds")}
    freeze(args.frozen, payload)
    wf_rows = {}
    for name, wf in wfs.items():
        pooled = [t for v in wf.values() for t in v["trades"]]
        wf_rows[name] = summarize(pooled, year_bounds(WF_YEARS[0])[0], min(year_bounds(WF_YEARS[-1])[1], end))
    save(args.out, "select", {"scores": scores, "chosen": chosen, "mapping": mapping, "stats": stats,
                              "mappings": mappings,
                              "standalone": {k: _ser(v) for k, v in standalone.items()},
                              "insample": {k: _ser(v) for k, v in insample.items()}})
    sel_years = [(str(y), y) for y in range(2010, 2018)]
    lines = ["## 4. Selection 2010-2017 (frames and macro sliced before 2018-01-01; C_RAW)", "",
             f"Selection frames end at {frames['5m'].index[-1]:%Y-%m-%d %H:%M} UTC. Standalone pool modules (router = "
             "always that module; the fitting data):", "",
             summary_table({f: summarize(ts, SEL_START, end) for f, ts in standalone.items()}), "",
             year_table({f: yearly(ts, sel_years) for f, ts in standalone.items()}), "",
             "**Anchored walk-forward by year** (fit on 2010..Y-1, routed book on Y; avg R / total R (n)):", "",
             "| Mapper | " + " | ".join(str(y) for y in WF_YEARS) + " | Score (mean yearly avg R) | WF pooled: trades "
             "(tr/mo), avg R, PF, MTM DD |", "|---" * (3 + len(WF_YEARS)) + "|"]
    for name, wf in wfs.items():
        cells = []
        for y in WF_YEARS:
            rs = [t["r"] for t in wf[y]["trades"]]
            cells.append(f"{np.mean(rs):+.3f} / {sum(rs):+.1f}R ({len(rs)})" if rs else "0 (0)")
        p = wf_rows[name]
        lines.append(f"| {name} | " + " | ".join(cells) + f" | **{scores[name]:+.3f}** | {p['trades']} "
                     f"({p['tpm']:.1f}), {_f(p['avg_r'], '+.3f')}, {_f(p['pf'], '.2f')}, {p['dd']:.1f} |")
    lines += ["", "Yearly fitted maps (cell -> module):", ""]
    for name, wf in wfs.items():
        lines.append(f"- {name}: " + "; ".join(f"{y}: {json.dumps(wf[y]['mapping'], sort_keys=True)}" for y in WF_YEARS))
    lines += ["", f"**Chosen mapper: `{chosen}`** (WF score {scores[chosen]:+.3f}). Refit on 2010-2017 -> frozen map "
              f"`{json.dumps(mapping, sort_keys=True)}`.", "",
              "Cell statistics of the final fit (standalone trades, n / avg R):", "",
              "| Cell | " + " | ".join(POOL) + " | -> |", "|---" * (2 + len(POOL)) + "|"]
    for cell in sorted(stats):
        row = [f"{stats[cell][f][0]} / {stats[cell][f][1]:+.3f}" if f in stats[cell] else "-" for f in POOL]
        lines.append(f"| {cell} | " + " | ".join(row) + f" | {mapping[cell]} |")
    lines += ["", "**Frozen map in-sample 2010-2017** (fitted on these years: in-sample, NOT evidence):", "",
              summary_table({m: summarize(ts, SEL_START, end) for m, ts in insample.items()}), "",
              "Descriptive only (standalone module avg R (n) by state on 2010-2017; never used for selection):", ""]
    for feature in ("VOL", "RATE", "COT", "Q", "ADX", "TREND", "MONTH"):
        lines += [descriptive(standalone, feature), ""]
    lines.append(f"**FROZEN** -> `{args.frozen}` (sha256 `{file_sha(args.frozen)}`), copy `replay/round7_frozen.json`; "
                 "committed before the holdout runner.")
    fy.append(args.report, "\n".join(lines))
    log(f"chosen {chosen} map {mapping}")


def run_frozen(args, cutoff, windows, log):
    """Frozen map on frames / macro sliced before ``cutoff``: {label: {cost model: trades}} per (label, a, b)."""
    frames = load_frames(args.data, end=cutoff)
    assert frames["5m"].index[-1] < pd.Timestamp(cutoff), "data after the cutoff"
    payload = load_frozen(args.frozen)
    system = System(frames, args.macro, cutoff)
    route = mapped(tuple(payload["keys"]), payload["mapping"])
    out = {}
    for label, a, b in windows:
        out[label] = {m: run_book(system, model, route, a, min(b, system.end))[0] for m, model in MODELS.items()}
        log(f"frozen {payload['mapper']} {label} {a:%Y-%m-%d}..{min(b, system.end):%Y-%m-%d}")
    return payload, out, min(cutoff, system.end)


def result_lines(title, payload, runs, a, b, years, args):
    rows = {m: summarize(t, a, b) for m, t in runs.items()}
    ys = {m: yearly(t, years) for m, t in runs.items()}
    verdict, fails = holdout_pass(rows["C_RAW"], ys["C_RAW"])
    carry = r4.with_carry(runs["C_RAW"])
    for t, u in zip(runs["C_RAW"], carry):
        u["module"], u["state"] = t["module"], t["state"]
    info = {"C_RAW + 3 %/yr carry stress (info)": summarize(carry, a, b)}
    lines = [title, "",
             f"Map read from `{args.frozen}` (sha256 `{file_sha(args.frozen)}`, equal to the committed copy): mapper "
             f"`{payload['mapper']}`, `{json.dumps(payload['mapping'], sort_keys=True)}`.", "",
             summary_table({**rows, **info}), "", "Yearly (avg R / total R (n)):", "", year_table(ys), "",
             f"**Pass bar (C_RAW: avg R >= +0.08, PF > 1.15, MTM DD < 12R, >= 3 trades/month, >= 3 positive years): "
             f"{'PASS' if verdict else 'FAIL'}**" + ("" if verdict else f" ({'; '.join(fails)})") + ".", "",
             "Per cell / module contribution (C_RAW):", "", cell_table(by_cell(runs["C_RAW"], tuple(payload["keys"])))]
    return lines, rows, ys, verdict, fails


def holdout_section(args, log):
    log("holdout: frames and macro sliced before 2021-10-01, frozen map only")
    payload, out, b = run_frozen(args, HOLDOUT_END, [("holdout", HOLDOUT_START, HOLDOUT_END)], log)
    runs = out["holdout"]
    lines, rows, ys, verdict, fails = result_lines(
        "## 5. HOLDOUT 2018-01-01 .. 2021-09-30 (frozen map, run once)", payload, runs, HOLDOUT_START, b,
        HOLDOUT_YEARS, args)
    save(args.out, "holdout", {"rows": rows, "years": ys, "verdict": verdict, "fails": fails,
                               "trades": {m: _ser(t) for m, t in runs.items()}, "frozen_sha": file_sha(args.frozen)})
    fy.append(args.report, "\n".join(lines))
    raw = rows["C_RAW"]
    log(f"holdout C_RAW {raw['trades']} trades avg R {_f(raw['avg_r'], '+.3f')} PF {_f(raw['pf'], '.2f')} "
        f"DD {raw['dd']:.1f} tpm {raw['tpm']:.1f} -> {'PASS' if verdict else 'FAIL'} {fails}")


def contaminated_section(args, log):
    log("contaminated: full frames, frozen map")
    payload, out, b = run_frozen(args, CONTAM_END, [("contaminated", CONTAM_START, CONTAM_END),
                                                    ("full", SEL_START, CONTAM_END)], log)
    runs, curves = out["contaminated"], out["full"]
    years = [("2021Q4", 2021), ("2022", 2022), ("2023", 2023), ("2024", 2024), ("2025", 2025), ("2026YTD", 2026)]
    lines, rows, ys, verdict, fails = result_lines(
        "## 6. Secondary check 2021-10-01 .. 2026-10-19 - CONTAMINATED (rounds 1-6 selected on these years)",
        payload, runs, CONTAM_START, b, years, args)
    lines[-3] = lines[-3].replace("**Pass bar", "**(Reference only, not a test) bar")
    save(args.out, "contaminated", {"rows": rows, "years": ys, "trades": {m: _ser(t) for m, t in runs.items()},
                                    "curves": {m: _ser(curves[m]) for m in ("C_RAW", "C_OANDA")}})
    fy.append(args.report, "\n".join(lines))
    log(f"contaminated C_RAW {rows['C_RAW']['trades']} trades avg R {_f(rows['C_RAW']['avg_r'], '+.3f')}")


def final_section(args, log):
    sel, hold, con = load(args.out, "select"), load(args.out, "holdout"), load(args.out, "contaminated")
    curves = {m: _de(con["curves"][m]) for m in ("C_RAW", "C_OANDA")}
    spans = [("SELECTION 2010-2017 (frozen map in-sample)", SEL_START, SEL_END, "#fcfcfb", None),
             ("HOLDOUT 2018-2021Q3 (run once)", HOLDOUT_START, HOLDOUT_END, "#e3e9f3", None),
             ("CONTAMINATED 2021Q4-2026", CONTAM_START, CONTAM_END, "#f3e9e3", "//")]
    plot(args.png, curves, spans, f"EURUSD round-7 rotation `{sel['chosen']}` - cumulative R, frozen map, "
                                  "one continuous causal run")
    raw = hold["rows"]["C_RAW"]
    n_new, n_cons = len(MAPPERS), len(MAPPERS) + n_cells() * len(POOL)
    lines = ["## 7. Multiple testing and verdict", "",
             f"- Round 7: {len(MAPPERS)} mappers x 5 walk-forward years (+ the refit), built from {n_cells()} cells x "
             f"{len(POOL)} modules = {n_cells() * len(POOL)} cell-module comparisons; the holdout is 1 test of 1 frozen map (3 cost models "
             f"of the same trades). Cumulative: {PREVIOUS_TESTS} + {n_new} = {PREVIOUS_TESTS + n_new} (mapper level); "
             f"conservative {PREVIOUS_TESTS} + {n_cons} = {PREVIOUS_TESTS + n_cons}.",
             f"- Holdout C_RAW: {raw['trades']} trades ({raw['tpm']:.1f}/month), avg R {_f(raw['avg_r'], '+.3f')}, "
             f"PF {_f(raw['pf'], '.2f')}, MTM DD {raw['dd']:.1f}R, CI80 low {_f(raw['ci80_low'], '+.3f')} -> "
             f"**{'PASS' if hold['verdict'] else 'FAIL'}**" + ("" if hold["verdict"] else f" ({'; '.join(hold['fails'])})")
             + ".",
             f"- Chart: `{args.png}` (C_RAW and C_OANDA; selection unshaded = in-sample, holdout shaded blue, "
             "contaminated hatched)."]
    fy.append(args.report, "\n".join(lines))
    log(f"final: {'PASS' if hold['verdict'] else 'FAIL'}")


if __name__ == "__main__":
    main()
