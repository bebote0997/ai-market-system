"""Round 7b (REPLAY ONLY): the round-7 rotation protocol run separately on GBPUSD, USDJPY, XAUUSD and NAS100USD.

Same pool (E3 / JT[R1+R5] / F2 relaxed TP2, T1, T1F, FLAT), same 6 mappers, same selection / walk-forward / freeze /
holdout / contaminated protocol and pass bar as ``replay.round7`` (EURUSD); per instrument: its own CFTC COT contract,
its own rate driver, its own cost models (FX: C_RAW / C_OANDA / C_RAW_STRESS; XAU and NAS100: IBKR futures-like
C_IBKR / C_IBKR_STRESS + C_OANDA reference) and its own frozen file ``round7_frozen_<SYM>.json`` (+ the committed copy
``replay/round7_frozen_<SYM>.json``; the holdout runner fails closed like round 7).

Run (per instrument, sections append to the round-7 report in order): ``python -m replay.round7b --symbol GBPUSD
--sections merge,data`` then ``select`` (writes the frozen file; commit it), ``holdout``, ``contaminated``; once all
are done ``--sections final`` (summary, ranking, multiple testing, chart). ``--sections declare`` writes the common
declaration (once, before any selection run). Data / outputs stay outside the repository and are never committed.
"""
import argparse
import glob
import json
import math
import os
import subprocess
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from replay import entry_search as es
from replay import fiveyear as fy
from replay import intraday_backtest as bt
from replay import round4 as r4
from replay import round5 as r5
from replay import round7 as r7

UTC = r7.UTC
NY = r7.NY
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RATE_STALE_DAYS = 10  # a rate row older than this at t -> RATE unknown (NO_DATA)
MONTHLY_STALE_DAYS = 92  # a monthly 3M rate usable for longer than this -> no differential that day
RISK_PCT = 0.5  # % of equity per trade (R -> % for the buy-and-hold comparison; simple, not compounded)


@dataclass(frozen=True)
class Spec:
    pip: float
    fx: bool
    break_hours: tuple
    cot_code: str
    cot_name: str
    rate: str  # rate driver (see ``load_driver``)
    rate_side: int  # +1: RATE = UP supports longs of the instrument; -1: UP supports shorts
    costs: str  # "FX" or "IBKR"
    long_biased: bool = False


SPECS = {
    "GBPUSD": Spec(0.0001, True, (), "096742", "BRITISH POUND STERLING (CME)", "US2Y-UK3M", -1, "FX"),
    "USDJPY": Spec(0.01, True, (), "097741", "JAPANESE YEN (CME)", "US2Y-JP3M", +1, "FX"),
    "XAUUSD": Spec(0.1, False, (17,), "088691", "GOLD (COMEX)", "US10Y_REAL", -1, "IBKR", True),
    "NAS100USD": Spec(1.0, False, (17,), "209742", "NASDAQ-100 STOCK INDEX (MINI) (CME)", "US2Y", -1, "IBKR", True),
}
ORDER = tuple(SPECS)
CROSS = {"US2Y-UK3M": "US2Y-BOE", "US2Y-JP3M": "US2Y", "US10Y_REAL": "US2Y", "US2Y": "US10Y_REAL"}
RATE_LABEL = {"US2Y-UK3M": "US 2Y - UK 3M interbank (OECD IR3TIB GBR = FRED IR3TIB01GBM156N)",
              "US2Y-JP3M": "US 2Y - Japan 3M interbank (OECD IR3TIB JPN = FRED IR3TIB01JPM156N)",
              "US10Y_REAL": "US 10Y real yield (Treasury real par curve 10 YR = source of FRED DFII10)",
              "US2Y": "US 2Y (Treasury par curve 2 Yr = source of FRED DGS2)",
              "US2Y-BOE": "US 2Y - BoE Bank Rate (IUDBEDR)"}

# IBKR futures-like costs (points of the instrument pip: XAU 0.1 USD, NAS100 1.0 index point)
IBKR_TICK = {"XAUUSD": 1.0, "NAS100USD": 0.25}  # MGC 0.10 USD/oz; MNQ 0.25 pt
IBKR_COMMISSION = {"XAUUSD": 0.12, "NAS100USD": 0.62}  # price per unit round turn: MGC ~USD 1.2 / 10 oz; MNQ ~USD 1.24 / USD 2 per pt
C_IBKR = r5.CostModel("C_IBKR", spread=dict(IBKR_TICK), slip=dict(IBKR_TICK), commission={k: "ibkr" for k in IBKR_TICK})
C_IBKR_STRESS = r5.CostModel("C_IBKR_STRESS", spread={k: 2 * v for k, v in IBKR_TICK.items()},
                             slip={k: 2 * v for k, v in IBKR_TICK.items()}, commission={k: "ibkr" for k in IBKR_TICK})


def models(symbol):
    """{name: cost model}; the first one is the pass-bar model."""
    if SPECS[symbol].costs == "IBKR":
        return {"C_IBKR": C_IBKR, "C_OANDA": r5.C_OANDA, "C_IBKR_STRESS": C_IBKR_STRESS}
    return {"C_RAW": r5.C_RAW, "C_OANDA": r5.C_OANDA, "C_RAW_STRESS": r5.C_RAW_STRESS}


def bar_name(symbol):
    return next(iter(models(symbol)))


def model_tape(model, symbol, f5, pip, fx):
    if model in (C_IBKR, C_IBKR_STRESS):
        n = len(f5)
        return r4.Tape(es._ns(f5.index), *(f5[x].to_numpy(float) for x in ("Open", "High", "Low", "Close")),
                       np.full(n, model.spread[symbol] * pip), pip, fx, slip=model.slip[symbol] * pip,
                       commission=np.full(n, IBKR_COMMISSION[symbol]))
    return r5.model_tape(model, symbol, f5, pip, fx)


# ---------------------------------------------------------------- data
def merge_data(old_dir, new_dir, out_dir, symbol, split=r7.HOLDOUT_END):
    """``old_dir`` bars before ``split`` + ``new_dir`` bars from it -> ``out_dir/<SYM>_<tf>.csv`` (5m / 15m / 1h)."""
    for tf in ("5m", "15m", "1h"):
        old = pd.read_csv(os.path.join(old_dir, f"{symbol}_{tf}.csv"), dtype=str)
        new = pd.read_csv(os.path.join(new_dir, f"{symbol}_{tf}.csv"), dtype=str)
        cut = split.strftime("%Y-%m-%dT%H:%M:%SZ")
        out = pd.concat([old[old["start"] < cut], new[new["start"] >= cut]])
        out = out.drop_duplicates("start").sort_values("start")
        out.to_csv(os.path.join(out_dir, f"{symbol}_{tf}.csv"), index=False)


def drop_break(frame_1h, break_hours):
    """1h bars outside the instrument's daily break hours (NY clock). XAU traded 17:00-18:00 NY on OANDA before 2016;
    the hour is ignored so that every D1 / H4 bucket has the same schedule."""
    if not break_hours:
        return frame_1h
    return frame_1h[~np.isin(frame_1h.index.tz_convert(NY).hour, list(break_hours))]


@contextmanager
def schedule(break_hours):
    """``round4.schedule`` with the break hours dropped before resampling (complete = every non-break hour)."""
    if not break_hours:
        yield
        return
    original = es.resample_closed
    try:
        es.resample_closed = lambda f, h, complete_only=True: r4.resample(drop_break(f, break_hours), h, complete_only,
                                                                          break_hours)
        yield
    finally:
        es.resample_closed = original


def load_frames(data, symbol, end=None):
    frames = bt.load_csv_frames(data, symbol)
    out = {}
    for tf, f in frames.items():
        f = f[~r7.is_weekend(f.index)]
        if end is not None:
            f = f[f.index < pd.Timestamp(end)]
        out[tf] = f
    return out


def d1_bars(frame_1h, break_hours):
    return r4.htf_bars(drop_break(frame_1h, break_hours), "D1", break_hours)


def coverage(data, symbol):
    """Per TF: rows, first / last bar, weekend bars dropped, gaps longer than a weekend, 5m median spread per year."""
    rows = {}
    for tf in ("1h", "15m", "5m"):
        raw = pd.read_csv(os.path.join(data, f"{symbol}_{tf}.csv"), usecols=["start", "AskClose", "BidClose"])
        idx = pd.DatetimeIndex(pd.to_datetime(raw["start"], utc=True))
        weekend = r7.is_weekend(idx)
        keep = idx[~weekend]
        gap = pd.Series(keep[1:] - keep[:-1], index=keep[:-1])
        long_gaps = gap[gap > pd.Timedelta(hours=54)]
        spread = ((raw["AskClose"] - raw["BidClose"]) / SPECS[symbol].pip)[~weekend]
        rows[tf] = {"rows": int(len(keep)), "first": str(keep[0]), "last": str(keep[-1]),
                    "weekend_dropped": int(weekend.sum()), "long_gaps": len(long_gaps),
                    "longest_gap_d": round(float(gap.max().total_seconds() / 86400), 1),
                    "years": {int(k): int(v) for k, v in pd.Series(keep.year).value_counts().sort_index().items()},
                    "median_spread": {int(k): round(float(v), 2) for k, v in
                                      spread.groupby(keep.year).median().items()}}
    return rows


# ---------------------------------------------------------------- macro
def load_cot(raw_dir, code):
    """Weekly legacy futures-only non-commercial net (long - short) of CFTC contract ``code`` with its availability
    (``round7.cot_available``)."""
    parts = []
    for path in sorted(glob.glob(os.path.join(raw_dir, "deacot*.zip"))):
        with zipfile.ZipFile(path) as z:
            with z.open(z.namelist()[0]) as handle:
                d = pd.read_csv(handle, low_memory=False)
        d.columns = [c.strip() for c in d.columns]
        d = d[d["CFTC Contract Market Code"].astype(str).str.strip() == code]
        parts.append(pd.DataFrame({"asof": pd.to_datetime(d["As of Date in Form YYYY-MM-DD"]).dt.date,
                                   "long": d["Noncommercial Positions-Long (All)"].astype(float),
                                   "short": d["Noncommercial Positions-Short (All)"].astype(float)}))
    cot = pd.concat(parts).drop_duplicates("asof").sort_values("asof").reset_index(drop=True)
    cot["net"] = cot["long"] - cot["short"]
    cot["avail"] = [r7.cot_available(d) for d in cot["asof"]]
    return cot


def load_tips(raw_dir):
    parts = []
    for path in sorted(glob.glob(os.path.join(raw_dir, "tips*.csv"))):
        d = pd.read_csv(path)
        parts.append(pd.DataFrame({"day": pd.to_datetime(d["Date"], format="%m/%d/%Y").dt.date,
                                   "value": pd.to_numeric(d["10 YR"], errors="coerce")}))
    return pd.concat(parts).dropna().drop_duplicates("day").sort_values("day").reset_index(drop=True)


def load_oecd(raw_dir, area):
    d = pd.read_csv(os.path.join(raw_dir, "oecd_ir3tib.csv"))
    d = d[d["REF_AREA"] == area]
    out = pd.DataFrame({"day": [date(int(p[:4]), int(p[5:7]), 1) for p in d["TIME_PERIOD"].astype(str)],
                        "value": d["OBS_VALUE"].astype(float).to_numpy()})
    return out.sort_values("day").reset_index(drop=True)


def load_boe(raw_dir):
    d = pd.read_csv(os.path.join(raw_dir, "boe_bank_rate.csv"))
    return pd.DataFrame({"day": pd.to_datetime(d["DATE"], format="%d %b %Y").dt.date,
                         "value": d["IUDBEDR"].astype(float)}).sort_values("day").reset_index(drop=True)


def _asof(days, table_days, values, stale=None):
    """Value of ``table`` at each day (last row with table day <= day); NaN before it or when older than ``stale``."""
    td = np.array(table_days, dtype="datetime64[D]")
    dd = np.array(days, dtype="datetime64[D]")
    pos = np.searchsorted(td, dd, side="right") - 1
    out = np.where(pos >= 0, np.asarray(values, float)[np.maximum(pos, 0)], np.nan)
    if stale is not None:
        age = (dd - td[np.maximum(pos, 0)]).astype(int)
        out = np.where(age > stale, np.nan, out)
    return out


def driver_series(raw_dir, driver):
    """Daily (day, value) of a rate driver: US 2Y / US 10Y real daily; minus a monthly 3M rate (month m usable from
    the 1st of m + 2: one-month publication lag) or the BoE Bank Rate (step series by date of change)."""
    if driver == "US10Y_REAL":
        return load_tips(raw_dir)
    ust = r7.load_ust2y(raw_dir).rename(columns={"y2": "value"})
    if driver == "US2Y":
        return ust
    if driver in ("US2Y-UK3M", "US2Y-JP3M"):
        m = load_oecd(raw_dir, "GBR" if driver == "US2Y-UK3M" else "JPN")
        usable = [r7.month_plus(d, 2) for d in m["day"]]
        other = _asof([r7.next_business_day(d) for d in ust["day"]], usable, m["value"], MONTHLY_STALE_DAYS)
    elif driver == "US2Y-BOE":
        b = load_boe(raw_dir)
        other = _asof(ust["day"], b["day"], b["value"])
    else:
        raise ValueError(driver)
    out = ust.assign(value=ust["value"] - other)
    return out.dropna(subset=["value"]).reset_index(drop=True)


def load_driver(raw_dir, driver):
    """Daily rate driver with availability (value of day d used from 17:00 NY of the next business day) and the
    60-observation trend (value[k] - value[k - 60])."""
    d = driver_series(raw_dir, driver)
    d["avail"] = [r7._ns_ny(r7.next_business_day(x), 17) for x in d["day"]]
    d["trend"] = d["value"] - d["value"].shift(r7.RATE_TREND_OBS)
    return d


def trend_agreement(raw_dir, a, b):
    x, y = load_driver(raw_dir, a), load_driver(raw_dir, b)
    m = x.merge(y, on="day").dropna(subset=["trend_x", "trend_y"])
    return float((np.sign(m["trend_x"]) == np.sign(m["trend_y"])).mean()) if len(m) else math.nan


# ---------------------------------------------------------------- features / system
class Features(r7.Features):
    """``round7.Features`` with the instrument's D1 schedule, COT contract and rate driver; RATE is unknown when the
    latest known driver row is older than ``RATE_STALE_DAYS``."""

    def __init__(self, frames, macro_dir, cutoff, symbol):
        spec = SPECS[symbol]
        cut = int(pd.Timestamp(cutoff).value)
        raw = os.path.join(macro_dir, "raw")
        d1 = d1_bars(frames["1h"], spec.break_hours)
        self.d1 = d1
        logret = np.diff(np.log(d1.c), prepend=np.nan)
        self.rv = pd.Series(logret).rolling(r7.RV_DAYS).std().to_numpy() * math.sqrt(252)
        self.rv_ref = pd.Series(self.rv).rolling(r7.RV_REF_DAYS, min_periods=r7.RV_REF_MIN).median().to_numpy()
        self.adx = r4.adx(d1, r7.ADX_N)
        self.sma = r4.sma(d1.c, r7.TREND_SMA)
        cot = load_cot(raw, spec.cot_code)
        cot = cot[cot["avail"] < cut].reset_index(drop=True)
        cot["pct"] = r7.trailing_rank(cot["net"].to_numpy(), r7.COT_YEARS_RELEASES, r7.COT_MIN_RELEASES)
        self.cot = cot
        rates = load_driver(raw, spec.rate)
        self.rates = rates[rates["avail"] < cut].reset_index(drop=True)
        self._cot_avail = self.cot["avail"].to_numpy(np.int64)
        self._rate_avail = self.rates["avail"].to_numpy(np.int64)

    def state(self, t):
        out = super().state(t)
        j = self._at(self._rate_avail, t)
        if j >= 0 and int(t) - int(self._rate_avail[j]) > RATE_STALE_DAYS * es.DAY_NS:
            out["RATE"] = None
        return out


class System(r7.System):
    """``round7.System`` for one instrument: pip, D1 / H4 schedule, cost models, causal EURUSD reference ADR."""

    def __init__(self, frames, macro_dir, cutoff, symbol, ref_frames=None):
        spec = SPECS[symbol]
        self.symbol, self.spec, self.pip = symbol, spec, spec.pip
        self.frames, self.cutoff = frames, cutoff
        self.end = frames["5m"].index[-1].to_pydatetime() + timedelta(minutes=5)
        with r4.instrument_pip(self.pip), schedule(spec.break_hours):
            self.ctx = es.Context(frames)
        self.features = Features(frames, macro_dir, cutoff, symbol)
        self.start_ns = int(self.ctx.tradable_start())
        self.news = es.high_impact_days(r7.SEL_START.date(), self.end.date())
        for d in r7.load_fomc(os.path.join(macro_dir, "raw")):
            if d < self.end.date():
                self.news.setdefault(d, "FOMC")
        self.d1 = self.features.d1
        self._tapes, self._items, self._states = {}, {}, {}
        self.rule = r5.relaxed_rule(r7.RELAXED_TP)
        self.ref = None
        if ref_frames is not None:  # EURUSD frames sliced at the same cutoff
            with r4.instrument_pip(r7.PIP):
                self.ref = r5.ReferenceADR(es.Context(ref_frames))

    def tape(self, model):
        key = r5.model_name(model)
        if key not in self._tapes:
            self._tapes[key] = model_tape(model, self.symbol, self.frames["5m"], self.pip, self.spec.fx)
        return self._tapes[key]

    def items(self, model):
        key = r5.model_name(model)
        if key not in self._items:
            tape = self.tape(model)
            out = []
            with r4.instrument_pip(self.pip), schedule(self.spec.break_hours):
                for q, fam in enumerate(r7.INTRADAY):
                    for c in r5.family_candidates(self.ctx, fam, self.news, self.start_ns,
                                                  lambda k: float(tape.spread[k]) / 2):
                        out.append((c.t, q, fam, c))
            for q, fam in enumerate(r7.SWING, len(r7.INTRADAY)):
                for st in r7.t1_setups(self.d1, fam):
                    if st.t >= self.start_ns:
                        out.append((st.t, q, fam, st))
            out.sort(key=lambda x: (x[0], x[1]))
            self._items[key] = [(t, fam, c) for t, _, fam, c in out]
        return self._items[key]

    def t1f_allowed(self, state, side):
        return t1f_allowed(state, side, self.spec.rate_side)


def t1f_allowed(state, side, rate_side):
    """T1F: trade only in the direction the rate driver's trend supports (``rate_side``: +1 = RATE UP supports longs)."""
    if state.get("RATE") is None:
        return False
    favoured = rate_side if state["RATE"] == "UP" else -rate_side
    return side == favoured


def build_system(args, symbol, cutoff):
    frames = load_frames(args.data, symbol, end=cutoff)
    assert frames["5m"].index[-1] < pd.Timestamp(cutoff), "data after the cutoff"
    ref = r7.load_frames(args.data, end=cutoff)
    return System(frames, args.macro, cutoff, symbol, ref_frames=ref), frames


# ---------------------------------------------------------------- frozen file
def frozen_repo(symbol):
    return os.path.join(REPO, "replay", f"round7_frozen_{symbol}.json")


def frozen_path(args, symbol):
    return os.path.join(args.root, f"round7_frozen_{symbol}.json")


def freeze(path, symbol, payload):
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    for p in (path, frozen_repo(symbol)):
        with open(p, "w", encoding="utf-8") as handle:
            handle.write(text)
    return text


def committed_frozen(symbol):
    try:
        out = subprocess.run(["git", "-C", REPO, "show", f"HEAD:replay/round7_frozen_{symbol}.json"],
                             capture_output=True, text=True, check=False)
    except OSError:
        return None
    return out.stdout if out.returncode == 0 else None


def load_frozen(path, symbol):
    """Fails closed: the file must equal the committed ``replay/round7_frozen_<SYM>.json`` and match this module."""
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    committed = committed_frozen(symbol)
    if committed is None:
        raise RuntimeError(f"cannot verify the frozen file: git unavailable or replay/round7_frozen_{symbol}.json not "
                           "in HEAD")
    if committed != text:
        raise RuntimeError(f"frozen file differs from the committed replay/round7_frozen_{symbol}.json")
    payload = json.loads(text)
    missing = [k for k in ("symbol", "mapper", "keys", "mapping", "rule_tp", "pool") if k not in payload]
    if missing:
        raise ValueError(f"frozen file incomplete: {missing}")
    if payload["symbol"] != symbol or tuple(payload["pool"]) != r7.POOL or payload["rule_tp"] != r7.RELAXED_TP:
        raise ValueError("frozen symbol / pool / rule differ from this run")
    return payload


# ---------------------------------------------------------------- persistence
def save(args, symbol, key, payload):
    with open(os.path.join(args.out, f"r7b_{symbol}_{key}.json"), "w", encoding="utf-8") as handle:
        json.dump(payload, handle, default=float)


def load(args, symbol, key):
    path = os.path.join(args.out, f"r7b_{symbol}_{key}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


# ---------------------------------------------------------------- buy and hold
def buy_and_hold(frame_5m, a, b):
    """Mid open of the first 5m bar >= a to the last close < b: % return and max drawdown % (5m closes)."""
    f = frame_5m[(frame_5m.index >= pd.Timestamp(a)) & (frame_5m.index < pd.Timestamp(b))]
    if f.empty:
        return None
    first = float(f["Open"].iloc[0])
    path = f["Close"].to_numpy(float) / first
    dd = float((1 - path / np.maximum.accumulate(path)).max())
    return {"ret_pct": 100 * (path[-1] - 1), "dd_pct": 100 * dd, "r_equiv": 100 * (path[-1] - 1) / RISK_PCT}


# ---------------------------------------------------------------- report
DECLARATION = """\
## 8. Multi-instrument (scope addition): GBPUSD, USDJPY, XAUUSD, NAS100USD

### 8.0 Predeclared design (written and committed before any multi-instrument selection run)

The EURUSD protocol of sections 1-7 is repeated **separately and unchanged** per instrument (`replay/round7b.py` reuses
`replay/round7.py`): same pool (E3, JT[R1+R5], F2 relaxed TP2; T1 D1 Donchian; T1F; FLAT), same 6 mappers (BASE,
M_cal, M_vol, M_cot, M_rate, M_vol+rate), same cell fit (>= 20 trades, avg R > 0, purged), same anchored walk-forward
2013-2017 on frames / macro sliced before 2018-01-01, same choice rule, one frozen map per instrument
(`/workspace/backtest/round7_frozen_<SYM>.json` + `replay/round7_frozen_<SYM>.json`, **committed before that
instrument's holdout**; the runner fails closed), holdout once 2018-01-01 .. 2021-09-30 (frames / macro sliced before
2021-10-01), then 2021-10-01 .. 2026-10-19 labelled CONTAMINATED (rounds 4-6 looked at these instruments on 2021-2026).

**Data**: OANDA practice 5m / 15m / 1h (mid OHLC + bid/ask close) 2010-01 .. 2021-09 (`dl10y_multi.py`) merged with
`data5y` from 2021-10-01 into `data10y/<SYM>_<tf>.csv`; weekend bars [Fri 17:00, Sun 17:00) NY dropped. **D1 = 17:00 NY
trading days resampled from closed 1h bars** (no downloaded D1). XAUUSD / NAS100USD: the 17:00-18:00 NY hour is the
daily break (OANDA XAU traded through it before 2016); it is dropped before resampling so every D1 / H4 bucket has the
same schedule (complete = every non-break hour present). Pip / point: GBPUSD 0.0001, USDJPY 0.01, XAUUSD 0.1,
NAS100USD 1.0. The relaxed rule's 15-50 pip stop clamp is scaled on non-EURUSD instruments by ADR20 / causal EURUSD
reference ADR20 (round-5 `ReferenceADR`, EURUSD frames sliced at the same cutoff).

**Macro per instrument** (availability as in section 1: COT used from 17:00 NY of the next business day after the
Friday release, shutdown late bounds; daily rates used from 17:00 NY of the next business day; monthly OECD 3M rates
month m used from the 1st of m + 2 and ignored once more than 92 days stale; any driver row older than 10 days at t ->
RATE unknown):

| Instrument | COT contract (legacy futures-only, non-commercial net) | RATE driver (60-obs trend sign) | RATE = UP favours (T1F) | Cross-check (not used) |
|---|---|---|---|---|
| GBPUSD | 096742 BRITISH POUND STERLING | US 2Y - UK 3M interbank (OECD `IR3TIB` GBR = FRED IR3TIB01GBM156N; FRED down, OECD SDMX used) | short | US 2Y - BoE Bank Rate |
| USDJPY | 097741 JAPANESE YEN | US 2Y - Japan 3M interbank (OECD `IR3TIB` JPN = FRED IR3TIB01JPM156N) | long | US 2Y trend |
| XAUUSD | 088691 GOLD (COMEX) | US 10Y real yield (Treasury real par curve 10 YR = source of FRED DFII10) | short | US 2Y trend |
| NAS100USD | 209742 NASDAQ-100 E-mini (continuous since before 2010; the consolidated 20974+ series starts mid-2010 and would leave COT NO_DATA until 2012) | US 2Y (Treasury par curve = source of DGS2) | short | US 10Y real trend |

All series exist 2007-2026, so **no mapper is dropped** for any instrument (if a series were missing, its mapper would
be dropped and declared). COT percentile, VOL, Q states as in section 1.

**Costs** (pass bar on the first model):

| Instrument | Bar model | Others |
|---|---|---|
| GBPUSD, USDJPY | `C_RAW` (0.2 pip spread + 0.1 pip slippage + USD 7 / 100k commission; USDJPY 7 x price / 1000 pips) | `C_OANDA` (causal 30-day median OANDA spread), `C_RAW_STRESS` (0.4 + 0.2 + same commission) |
| XAUUSD | `C_IBKR` MGC-like: spread 1 tick 0.10 USD + slippage 1 tick 0.10 USD per market fill / stop + commission 0.12 USD/oz round turn | `C_IBKR_STRESS` (2 ticks + 2 ticks), `C_OANDA` reference |
| NAS100USD | `C_IBKR` MNQ-like: spread 1 tick 0.25 pt + slippage 1 tick 0.25 pt + commission 0.62 pt round turn (USD 1.24 / USD 2 per pt) | `C_IBKR_STRESS` (2 + 2 ticks), `C_OANDA` reference |

Futures-like costs carry no financing (in the basis); the 3 %/yr carry stress row is still shown for information.

**Pass bar (holdout, bar model)**: avg R >= +0.08, PF > 1.15, MTM DD < 12R, >= 3 trades/month, total R > 0 in >= 3 of
the 4 years (2018, 2019, 2020, 2021 Jan-Sep). Passers are ranked by holdout avg R. XAUUSD / NAS100USD are compared to
buy-and-hold over the same holdout (mid, % return and max DD; R-equivalent = % / 0.5 % risk per trade; strategy % =
total R x 0.5 %, simple).

**Multiple testing**: per instrument 6 mappers (conservative 6 + 16 cells x 5 modules = 86); 4 instruments -> 24
(conservative 344); cumulative after EURUSD round 7: 450 (conservative 530) -> 474 (conservative 874). Holdouts: 4
frozen maps, one test each.
"""


def data_lines(args, symbol):
    spec = SPECS[symbol]
    cov = coverage(args.data, symbol)
    raw = os.path.join(args.macro, "raw")
    cot = load_cot(raw, spec.cot_code)
    drv = load_driver(raw, spec.rate)
    agree = trend_agreement(raw, spec.rate, CROSS[spec.rate])
    lines = [f"### 8.{ORDER.index(symbol) + 1} {symbol}", "", f"#### 8.{ORDER.index(symbol) + 1}.a Data and pool", "",
             "| TF | Rows | First bar | Last bar | Weekend bars dropped | Gaps > weekend | Longest gap (d) |",
             "|---|---|---|---|---|---|---|"]
    for tf, c in cov.items():
        lines.append(f"| {tf} | {c['rows']} | {c['first']} | {c['last']} | {c['weekend_dropped']} | {c['long_gaps']} | "
                     f"{c['longest_gap_d']} |")
    years = sorted(cov["5m"]["years"])
    lines += ["", "| 5m | " + " | ".join(str(y) for y in years) + " |", "|---" * (1 + len(years)) + "|",
              "| rows | " + " | ".join(str(cov["5m"]["years"].get(y, 0)) for y in years) + " |",
              f"| median spread ({'pip' if spec.fx else 'pt'}) | "
              + " | ".join(f"{cov['5m']['median_spread'].get(y, math.nan):.1f}" for y in years) + " |", "",
              f"- COT {spec.cot_code} {spec.cot_name}: {len(cot)} weekly reports, as-of {cot['asof'].iloc[0]} .. "
              f"{cot['asof'].iloc[-1]}; net {cot['net'].min():+,.0f} .. {cot['net'].max():+,.0f} contracts.",
              f"- RATE driver {RATE_LABEL[spec.rate]}: {len(drv)} daily values {drv['day'].iloc[0]} .. "
              f"{drv['day'].iloc[-1]}, range {drv['value'].min():+.2f} .. {drv['value'].max():+.2f}; 60-obs trend sign "
              f"agrees with {RATE_LABEL[CROSS[spec.rate]]} on {100 * agree:.0f} % of days (cross-check only)."]
    return lines


def pool_lines(system):
    counts = {}
    for t, fam, _ in system.items(models(system.symbol)[bar_name(system.symbol)]):
        y = pd.Timestamp(int(t), tz=UTC).year
        counts.setdefault(fam, {}).setdefault(y, 0)
        counts[fam][y] += 1
    years = sorted({y for c in counts.values() for y in c})
    lines = ["", f"Pool candidates on the selection frames (tradable from "
                 f"{pd.Timestamp(system.start_ns, tz=UTC):%Y-%m-%d}; no module dropped):", "",
             "| Module | " + " | ".join(str(y) for y in years) + " |", "|---" * (1 + len(years)) + "|"]
    for fam in r7.POOL:
        lines.append(f"| {fam} | " + " | ".join(str(counts.get(fam, {}).get(y, 0)) for y in years) + " |")
    return lines


def select_section(args, symbol, log):
    n = ORDER.index(symbol) + 1
    bar = bar_name(symbol)
    bar_model = models(symbol)[bar]
    log(f"{symbol} selection: frames and macro sliced before 2018-01-01")
    system, frames = build_system(args, symbol, r7.SEL_END)
    end = min(r7.SEL_END, system.end)
    standalone = {fam: r7.run_book(system, bar_model, r7.fixed(fam), r7.SEL_START, end)[0] for fam in r7.POOL}
    for fam, ts in standalone.items():
        log(f"  standalone {fam}: {len(ts)} trades")
    wfs, scores, mappings = {}, {}, {}
    for name, keys in r7.MAPPERS.items():
        wfs[name] = r7.walk_forward(system, standalone, keys, bar_model)
        scores[name] = r7.wf_score(wfs[name])
        mappings[name], _ = r7.fit(keys, standalone, r7.SEL_START, r7.SEL_END)
        log(f"  {name}: WF mean test avg R {scores[name]:+.3f}")
    chosen = r7.choose(scores, mappings)
    keys = r7.MAPPERS[chosen]
    mapping, stats = r7.fit(keys, standalone, r7.SEL_START, r7.SEL_END)
    insample = {m: r7.run_book(system, model, r7.mapped(keys, mapping), r7.SEL_START, end)[0]
                for m, model in models(symbol).items()}
    path = frozen_path(args, symbol)
    payload = {"system": "ROUND7_ROTATION", "symbol": symbol, "mapper": chosen, "keys": list(keys),
               "mapping": mapping, "pool": list(r7.POOL), "rule_tp": r7.RELAXED_TP,
               "min_cell_trades": r7.MIN_CELL_TRADES, "bar_model": bar, "cot_code": SPECS[symbol].cot_code,
               "rate_driver": SPECS[symbol].rate, "rate_side": SPECS[symbol].rate_side,
               "selection": {"window": [r7.SEL_START.isoformat(), r7.SEL_END.isoformat()],
                             "wf_years": list(r7.WF_YEARS), "scores": scores,
                             "data_last_bar": frames["5m"].index[-1].isoformat(),
                             "macro_cutoff": r7.SEL_END.isoformat()},
               "frozen_at": datetime.now(UTC).isoformat(timespec="seconds")}
    freeze(path, symbol, payload)
    pooled = {name: r7.summarize([t for v in wf.values() for t in v["trades"]], r7.year_bounds(r7.WF_YEARS[0])[0],
                                 min(r7.year_bounds(r7.WF_YEARS[-1])[1], end)) for name, wf in wfs.items()}
    save(args, symbol, "select", {"scores": scores, "chosen": chosen, "mapping": mapping, "stats": stats,
                                  "mappings": mappings, "standalone": {k: r7._ser(v) for k, v in standalone.items()},
                                  "insample": {k: r7._ser(v) for k, v in insample.items()}})
    sel_years = [(str(y), y) for y in range(2010, 2018)]
    lines = data_lines(args, symbol) + pool_lines(system)
    lines += ["", f"#### 8.{n}.b Selection 2010-2017 (frames and macro sliced before 2018-01-01; {bar})", "",
              f"Selection frames end at {frames['5m'].index[-1]:%Y-%m-%d %H:%M} UTC. Standalone pool modules:", "",
              r7.summary_table({f: r7.summarize(ts, r7.SEL_START, end) for f, ts in standalone.items()}), "",
              r7.year_table({f: r7.yearly(ts, sel_years) for f, ts in standalone.items()}), "",
              "Anchored walk-forward by year (avg R / total R (n)):", "",
              "| Mapper | " + " | ".join(str(y) for y in r7.WF_YEARS) + " | Score | WF pooled: trades (tr/mo), avg R, "
              "PF, MTM DD |", "|---" * (3 + len(r7.WF_YEARS)) + "|"]
    for name, wf in wfs.items():
        cells = []
        for y in r7.WF_YEARS:
            rs = [t["r"] for t in wf[y]["trades"]]
            cells.append(f"{np.mean(rs):+.3f} / {sum(rs):+.1f}R ({len(rs)})" if rs else "0 (0)")
        p = pooled[name]
        lines.append(f"| {name} | " + " | ".join(cells) + f" | **{scores[name]:+.3f}** | {p['trades']} "
                     f"({p['tpm']:.1f}), {r7._f(p['avg_r'], '+.3f')}, {r7._f(p['pf'], '.2f')}, {p['dd']:.1f} |")
    lines += ["", "Yearly fitted maps: " + "; ".join(
        f"{name} " + ", ".join(f"{y}: {json.dumps(wf[y]['mapping'], sort_keys=True)}" for y in r7.WF_YEARS)
        for name, wf in wfs.items() if name != "BASE") + "; BASE " + ", ".join(
        f"{y}: {wfs['BASE'][y]['mapping'].get('ALL', 'FLAT')}" for y in r7.WF_YEARS) + ".", "",
        f"**Chosen mapper: `{chosen}`** (WF score {scores[chosen]:+.3f}); refit 2010-2017 -> frozen map "
        f"`{json.dumps(mapping, sort_keys=True)}`.", "",
        "| Cell | " + " | ".join(r7.POOL) + " | -> |", "|---" * (2 + len(r7.POOL)) + "|"]
    for cell in sorted(stats):
        row = [f"{stats[cell][f][0]} / {stats[cell][f][1]:+.3f}" if f in stats[cell] else "-" for f in r7.POOL]
        lines.append(f"| {cell} | " + " | ".join(row) + f" | {mapping[cell]} |")
    lines += ["", "Frozen map in-sample 2010-2017 (in-sample, NOT evidence):", "",
              r7.summary_table({m: r7.summarize(ts, r7.SEL_START, end) for m, ts in insample.items()}), "",
              f"**FROZEN** -> `{path}` (sha256 `{r7.file_sha(path)}`), copy `replay/round7_frozen_{symbol}.json`; "
              "committed before this instrument's holdout."]
    fy.append(args.report, "\n".join(lines))
    log(f"{symbol} chosen {chosen} map {mapping}")


def run_frozen(args, symbol, cutoff, windows, log):
    payload = load_frozen(frozen_path(args, symbol), symbol)
    system, _ = build_system(args, symbol, cutoff)
    route = r7.mapped(tuple(payload["keys"]), payload["mapping"])
    out = {}
    for label, a, b in windows:
        out[label] = {m: r7.run_book(system, model, route, a, min(b, system.end))[0]
                      for m, model in models(symbol).items()}
        log(f"{symbol} frozen {payload['mapper']} {label} {a:%Y-%m-%d}..{min(b, system.end):%Y-%m-%d}")
    return payload, out, min(cutoff, system.end), system


def result_lines(args, symbol, payload, runs, a, b, years, bar_label):
    bar = bar_name(symbol)
    rows = {m: r7.summarize(t, a, b) for m, t in runs.items()}
    ys = {m: r7.yearly(t, years) for m, t in runs.items()}
    verdict, fails = r7.holdout_pass(rows[bar], ys[bar])
    carry = r4.with_carry(runs[bar])
    for t, u in zip(runs[bar], carry):
        u["module"], u["state"] = t["module"], t["state"]
    info = {f"{bar} + 3 %/yr carry stress (info)": r7.summarize(carry, a, b)}
    path = frozen_path(args, symbol)
    lines = [f"Map read from `{path}` (sha256 `{r7.file_sha(path)}`, equal to the committed copy): mapper "
             f"`{payload['mapper']}`, `{json.dumps(payload['mapping'], sort_keys=True)}`.", "",
             r7.summary_table({**rows, **info}), "", "Yearly (avg R / total R (n)):", "", r7.year_table(ys), "",
             f"**{bar_label} ({bar}: avg R >= +0.08, PF > 1.15, MTM DD < 12R, >= 3 trades/month, >= 3 positive "
             f"years): {'PASS' if verdict else 'FAIL'}**" + ("" if verdict else f" ({'; '.join(fails)})") + ".", "",
             "Per cell / module contribution:", "", r7.cell_table(r7.by_cell(runs[bar], tuple(payload["keys"])))]
    return lines, rows, ys, verdict, fails


def bh_line(system, a, b, total_r):
    bh = buy_and_hold(system.frames["5m"], a, b)
    if bh is None:
        return []
    return ["", f"Buy-and-hold {system.symbol} (mid) over the same window: {bh['ret_pct']:+.1f} % (max DD "
                f"{bh['dd_pct']:.1f} %) = {bh['r_equiv']:+.1f}R-equivalent at {RISK_PCT} % risk; strategy "
                f"{total_r:+.1f}R = {total_r * RISK_PCT:+.1f} % (simple)."]


def holdout_section(args, symbol, log):
    n = ORDER.index(symbol) + 1
    payload, out, b, system = run_frozen(args, symbol, r7.HOLDOUT_END,
                                         [("holdout", r7.HOLDOUT_START, r7.HOLDOUT_END)], log)
    runs = out["holdout"]
    lines, rows, ys, verdict, fails = result_lines(args, symbol, payload, runs, r7.HOLDOUT_START, b,
                                                   r7.HOLDOUT_YEARS, "Pass bar")
    bh = buy_and_hold(system.frames["5m"], r7.HOLDOUT_START, b) if SPECS[symbol].long_biased else None
    if bh is not None:
        lines += bh_line(system, r7.HOLDOUT_START, b, rows[bar_name(symbol)]["total_r"])
    save(args, symbol, "holdout", {"rows": rows, "years": ys, "verdict": verdict, "fails": fails, "bh": bh,
                                   "trades": {m: r7._ser(t) for m, t in runs.items()},
                                   "frozen_sha": r7.file_sha(frozen_path(args, symbol))})
    fy.append(args.report, "\n".join([f"#### 8.{n}.c HOLDOUT 2018-01-01 .. 2021-09-30 (frozen map, run once)", ""]
                                     + lines))
    s = rows[bar_name(symbol)]
    log(f"{symbol} holdout {s['trades']} trades avg R {r7._f(s['avg_r'], '+.3f')} PF {r7._f(s['pf'], '.2f')} "
        f"-> {'PASS' if verdict else 'FAIL'} {fails}")


CONTAM_YEARS = [("2021Q4", 2021), ("2022", 2022), ("2023", 2023), ("2024", 2024), ("2025", 2025), ("2026YTD", 2026)]


def contaminated_section(args, symbol, log):
    n = ORDER.index(symbol) + 1
    payload, out, b, system = run_frozen(args, symbol, r7.CONTAM_END,
                                         [("contaminated", r7.CONTAM_START, r7.CONTAM_END),
                                          ("full", r7.SEL_START, r7.CONTAM_END)], log)
    runs, curves = out["contaminated"], out["full"]
    lines, rows, ys, verdict, fails = result_lines(args, symbol, payload, runs, r7.CONTAM_START, b, CONTAM_YEARS,
                                                   "Reference only (not a test): bar")
    if SPECS[symbol].long_biased:
        lines += bh_line(system, r7.CONTAM_START, b, rows[bar_name(symbol)]["total_r"])
    save(args, symbol, "contaminated", {"rows": rows, "years": ys,
                                        "curves": {m: r7._ser(curves[m]) for m in (bar_name(symbol), "C_OANDA")}})
    fy.append(args.report, "\n".join([f"#### 8.{n}.d Secondary check 2021-10-01 .. 2026-10-19 - CONTAMINATED", ""]
                                     + lines))
    log(f"{symbol} contaminated {rows[bar_name(symbol)]['trades']} trades")


def final_section(args, log):
    done = {s: (load(args, s, "select"), load(args, s, "holdout"), load(args, s, "contaminated")) for s in ORDER}
    done = {s: v for s, v in done.items() if all(x is not None for x in v)}
    plot_multi(args.png, done)
    lines = ["### 8.5 Multi-instrument summary (holdout 2018-01 .. 2021-09, bar model, run once per instrument)", "",
             "| Instrument | Frozen map | Bar model | Avg R | Total R | PF | MTM DD R | Trades | Tr/mo | 2018 | 2019 | "
             "2020 | 2021 (Jan-Sep) | Verdict |", "|---" * 14 + "|"]
    passers = []
    for s, (sel, hold, _) in done.items():
        bar = bar_name(s)
        r, ys = hold["rows"][bar], hold["years"][bar]
        yc = " | ".join(f"{ys[k]['total_r']:+.1f}R ({ys[k]['trades']})" for k, _ in r7.HOLDOUT_YEARS)
        lines.append(f"| {s} | {sel['chosen']} {json.dumps(sel['mapping'], sort_keys=True)} | {bar} | "
                     f"{r7._f(r['avg_r'], '+.3f')} | {r['total_r']:+.1f} | {r7._f(r['pf'], '.2f')} | {r['dd']:.1f} | "
                     f"{r['trades']} | {r['tpm']:.1f} | {yc} | **{'PASS' if hold['verdict'] else 'FAIL'}** |")
        if hold["verdict"]:
            passers.append((r["avg_r"], s))
    lines += ["", "Failed criteria: " + "; ".join(f"{s}: {', '.join(h['fails'])}" for s, (_, h, _) in done.items()
                                                  if not h["verdict"]) + "."]
    for s, (_, hold, _) in done.items():
        if hold.get("bh"):
            bh = hold["bh"]
            tr = hold["rows"][bar_name(s)]["total_r"]
            lines.append(f"- {s} vs buy-and-hold (holdout): strategy {tr:+.1f}R = {tr * RISK_PCT:+.1f} %; buy-and-hold "
                         f"{bh['ret_pct']:+.1f} % (max DD {bh['dd_pct']:.1f} %) = {bh['r_equiv']:+.1f}R-equivalent.")
    lines += ["", "**Ranking of passers** (holdout avg R): " + (", ".join(
        f"{k}. {s} ({a:+.3f})" for k, (a, s) in enumerate(sorted(passers, reverse=True), 1)) or "none - no instrument "
        "passed the bar."), ""]
    n_ins = len(done)
    per, cons = len(r7.MAPPERS), len(r7.MAPPERS) + r7.n_cells() * len(r7.POOL)
    base, base_cons = r7.PREVIOUS_TESTS + per, r7.PREVIOUS_TESTS + cons
    lines += [f"**Multiple testing (all instruments)**: EURUSD round 7 {per} mappers (conservative {cons}); "
              f"{n_ins} more instruments x {per} = {n_ins * per} (conservative {n_ins * cons}); round 7 total "
              f"{(n_ins + 1) * per} (conservative {(n_ins + 1) * cons}); cumulative {base} + {n_ins * per} = "
              f"{base + n_ins * per} (conservative {base_cons} + {n_ins * cons} = {base_cons + n_ins * cons}). "
              f"Holdouts: {n_ins + 1} frozen maps (EURUSD + {n_ins}), one test each; at 5 holdout tests one "
              "chance pass at the 5 % level is ~23 %.", "",
              f"Chart: `{args.png}` (one panel per instrument: bar model and C_OANDA, one continuous causal run of "
              "the frozen map; selection = in-sample, holdout shaded blue, contaminated hatched)."]
    fy.append(args.report, "\n".join(lines))
    log(f"final: passers {passers}")


def plot_multi(path, done):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    ink, muted, surface = "#0b0b0b", "#52514e", "#fcfcfb"
    colors = {"C_RAW": "#2a78d6", "C_IBKR": "#2a78d6", "C_OANDA": "#eb6834"}
    n = max(len(done), 1)
    fig, axes = plt.subplots(n, 1, figsize=(12, 3.4 * n), dpi=110, facecolor=surface, sharex=True, squeeze=False)
    spans = [("SELECTION (in-sample)", r7.SEL_START, r7.SEL_END, surface, None),
             ("HOLDOUT (run once)", r7.HOLDOUT_START, r7.HOLDOUT_END, "#e3e9f3", None),
             ("CONTAMINATED", r7.CONTAM_START, r7.CONTAM_END, "#f3e9e3", "//")]
    for ax, (s, (sel, _, con)) in zip(axes[:, 0], done.items()):
        ax.set_facecolor(surface)
        for label, a, b, color, hatch in spans:
            ax.axvspan(a, b, facecolor=color, alpha=0.8, linewidth=0, hatch=hatch, edgecolor="#c9c8c4")
            ax.text(a, 1.0, f" {label}", transform=ax.get_xaxis_transform(), va="top", fontsize=7, color=muted)
        for k, (name, rows) in enumerate(con["curves"].items()):
            trades = sorted(r7._de(rows), key=lambda t: t["exit_at"])
            if not trades:
                continue
            xs = [r7.SEL_START] + [t["exit_at"] for t in trades]
            ys = np.concatenate([[0.0], np.cumsum([t["r"] for t in trades])])
            ax.step(xs, ys, where="post", color=colors.get(name, ink), linewidth=1.5, label=name)
            ax.annotate(f"{name} {ys[-1]:+.1f}R", (xs[-1], ys[-1]), xytext=(6, 6 - 13 * k),
                        textcoords="offset points", va="center", fontsize=8, color=ink)
        ax.axhline(0, color=muted, linewidth=0.8)
        ax.set_title(f"{s} - frozen `{sel['chosen']}` {json.dumps(sel['mapping'], sort_keys=True)}", loc="left",
                     color=ink, fontsize=9)
        ax.set_ylabel("Cumulative R", color=muted)
        ax.tick_params(colors=muted)
        ax.grid(axis="y", color="#e8e8e6", linewidth=0.8)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        ax.legend(frameon=False, loc="lower left", fontsize=7)
    fig.tight_layout()
    fig.savefig(path, facecolor=surface)
    plt.close(fig)


# ---------------------------------------------------------------- main
def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--symbol", choices=ORDER)
    parser.add_argument("--root", default="/workspace/backtest")
    parser.add_argument("--data", default="/workspace/backtest/data10y")
    parser.add_argument("--multi", default="/workspace/backtest/data10y_multi")
    parser.add_argument("--recent", default="/workspace/backtest/data5y")
    parser.add_argument("--macro", default="/workspace/backtest/macro")
    parser.add_argument("--out", default="/workspace/backtest/r7")
    parser.add_argument("--report", default="/workspace/backtest/report_round7.md")
    parser.add_argument("--png", default="/workspace/backtest/round7_multi_equity_r.png")
    parser.add_argument("--sections", default="")
    args = parser.parse_args(argv)
    sections = args.sections.split(",")
    os.makedirs(args.out, exist_ok=True)

    def log(message):
        print(f"{datetime.now(UTC).isoformat(timespec='seconds')} {message}", flush=True)

    if "declare" in sections:
        fy.append(args.report, DECLARATION)
        log("declared")
    if "merge" in sections:
        merge_data(args.multi, args.recent, args.data, args.symbol)
        log(f"{args.symbol} merged")
    for name, fn in (("select", select_section), ("holdout", holdout_section),
                     ("contaminated", contaminated_section)):
        if name in sections:
            fn(args, args.symbol, log)
    if "final" in sections:
        final_section(args, log)


if __name__ == "__main__":
    main()
