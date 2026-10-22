# Round 8 (REPLAY ONLY) — single-session scalping + literature families, OANDA M1 2021-10..2026-10

Code: `replay/round8.py` (+ `test_round8.py`), branch `feat/backtest-v2-i2`. Data (outside the repo, never committed):
`/workspace/backtest/data_m1/{EURUSD,GBPUSD,XAUUSD,NAS100USD}_1m.csv` (OANDA practice M1, mid OHLC + bid/ask close,
2021-10-01..2026-10-19). Per-symbol results: `/workspace/backtest/round8/<SYM>.json`.

## 1. Declaration (written and committed BEFORE any selection run)

### 1.0 Decisions taken without asking (brief incomplete)
- **The base section of `spec_round8.md` is missing.** The file holds only the LITERATURE ADDITION (it refers to "fill
  rules as above" and to families S1/S2/S3 named in `scalping_references.md`). Decision: reconstruct the base from the
  round-6/7 protocol (strict train/holdout, freeze committed before holdout, same pass bar) and define S1-S3 from the
  references file ("S1 5-min ORB (NAS100) + London-breakout variants", "S2 Asia sweep reversal", "S3 IB extension /
  VWAP 2-sigma fade"). Everything below is that reconstruction; it is fixed before any result is seen.
- Only a smoke run on 2021-10 (one month, to check that the code executes and fills) was made before this declaration;
  no metric of it was used for any choice.
- Data covers 2021-10..2026-10 only, so the split is the round-6 split: **TRAIN 2021-10-01..2024-12-31, HOLDOUT
  2025-01-01..2026-10-19.**

### 1.1 Common rules
- Time: America/New_York (DST aware). Sessions: FX and XAU London 03:00-11:00 NY, pre-session range 19:00 (previous
  calendar day)-03:00; NAS100 cash 09:30-16:00 ET, overnight range 18:00 (previous day)-09:30. D1 bars = 17:00 NY
  rollover (keyed by the date they close on; D1 with < 60 M1 bars dropped). Mon-Fri session dates only; missing bars at a
  scheduled time -> no trade (NO_DATA; entry needs a bar starting within 5 min after T; a time exit uses the close of the
  last bar starting within 10 min before T). A reference range needs >= 50 % of its minutes as bars.
- **Fills (mid + per-bar costs; the data has mid OHLC but bid/ask only at the close, so bid/ask-side intrabar fills are
  derived from mid + spread):** market entries at the next M1 open; stop entries when the mid high (low) reaches the
  level, filled at max(open, level) (gaps at the open); limit entries only when the mid trades **through** the level
  (touch = no fill), filled at min(open, level); every fill pays half the spread; market / stop entries and stop / time
  exits also pay slippage; TP limit exits pay half spread, no slippage; commission charged once per round turn.
  Same-bar stop and TP -> stop. A stop/limit fill bar can stop out but never takes profit. Stop gaps fill at the bar
  open. OCO pairs: the first leg to trigger wins; both on the same bar -> no trade. One position at a time per
  variant (a signal while a trade is open is skipped). All exits intraday (no overnight risk).
- **Stops:** each family's own rule, **clamped (not skipped) to 5-15 pips (FX)**; ATR-equivalent for XAU / NAS100 =
  [1.5, 4.5] x the instrument's trailing 20-day median ATR14(M5) (prior days only) — the multipliers are the EURUSD
  ratio 5 / 15 pips over its TRAIN median ATR14(M5) of 3.3 pips (computed on all hours). Families without a stop (L1,
  L1b, L2, L5, L6) use 1.0 x ATR14(M5) x k, k = 3, clamped the same way. ATR14(M5) = simple 14-bar mean true range of
  M5 bars resampled from M1, last bar closed at or before the entry. TPs in R are measured from the clamped stop.
- **Costs** (main model first; selection uses the main model only):
  | | main | stress | reference |
  |---|---|---|---|
  | EURUSD, GBPUSD | `C_RAW`: spread 0.2 pip + slippage 0.1 pip per market/stop fill + commission 0.7 pip RT | `C_RAW_STRESS` 0.4 + 0.2 + 0.7 | `C_OANDA`: observed bid/ask spread of the previous M1 bar, slippage max(0.1 x spread, 0.2 pip), no commission |
  | XAUUSD | `C_IBKR` (MGC-like, as round 7b): spread 0.10 + slippage 0.10 USD + commission 0.12 USD/oz RT | `C_IBKR_STRESS` 0.20 + 0.20 + 0.12 | `C_OANDA` (slippage floor 0.02 USD) |
  | NAS100USD | `C_IBKR` (MNQ-like): spread 0.25 + slippage 0.25 pt + commission 0.62 pt RT | `C_IBKR_STRESS` 0.50 + 0.50 + 0.62 | `C_OANDA` (slippage floor 0.2 pt) |

### 1.2 Variants (fixed rules, no grid). "end" = session end.
| id | rule | instruments |
|---|---|---|
| S1a:ORB5 | first 5-min of the session: trade its direction at +5 min, stop at the opposite OR extreme, TP 10R, exit end (Zarattini-Aziz) | all |
| S1b:RANGE_BO_TP1 / TP2 | OCO stop orders at the pre-session range high/low from the open for 3 h; stop = opposite side; TP 1R / 2R; exit end (London breakout) | all |
| S2:SWEEP_REV | within 3 h of the open an M5 bar trades beyond the pre-session range and closes back inside -> fade at the next minute; stop = extreme since the open; TP 2R; exit end | all |
| S3a:VWAP2S_FADE | session VWAP / sigma (tick volume, typical price) from the open; open+60 min .. end-60 min, M5 close beyond VWAP +- 2 sigma -> fade; TP = VWAP at the signal; stop 1 sigma; exit end | all |
| S3b:IB_BO | IB = first 60 min; OCO stops at IB high/low until end-60 min; stop = IB midpoint; TP = one IB range beyond the level; exit end | all |
| L1:OPEN30_MOM / OPEN30_REV | sign of the first 30 min of the session (London 03:00-03:30; NAS100 09:30-10:00) traded at +30 min in the same / reversed direction; exit end (11:00 NY; NAS100 16:00). Both signs tested and counted (Seeck) | all |
| L1b:LAST30_MOM | sign of prior 17:00 NY -> 16:00 NY (NAS100 -> 15:30 ET) traded 16:00-16:30 (NAS100 15:30-16:00) | all |
| L2:FIX_LDN / FIX_LDN_MONTHEND / FIX_ECB | fade the 60-min move into the fix at the fix, exit +2 h: London 4 pm (10:00->11:00 NY, exit 13:00); same on the last trading day of the month only; ECB 08:15 NY (07:15->08:15, exit 10:15) | FX, XAU |
| L3:NR7 / NR4 | previous D1 range narrowest of the last 7 / 4 (strict, unique); OCO stops at session open +- stretch (10-day mean of min(H-O, O-L)); the opposite level is the stop; exit end | all |
| L4:SB_STRICT / SB_RELAXED | ICT Silver Bullet in 03:00-04:00 and 10:00-11:00 NY: sweep -> MSS (M1 close beyond the 10-bar extreme before the sweep) -> 3-bar M1 FVG in the displacement leg -> limit at the FVG midpoint until the window ends; stop beyond the sweep extreme; TP 2R; exit 11:00 (03:00 window) / 16:00 (10:00 window). Strict: the swept level is the reference range (Asia range for 03:00; 03:00-10:00 range for 10:00); relaxed: the 30-bar M1 high/low | all |
| L5:XAU_LDN_SHORT | short 03:00 NY, exit 11:00 NY | XAU |
| L6:SHORT_EU / LONG_US | short 03:00 -> 11:00 NY; long 08:00 -> 17:00 NY (Breedon-Ranaldo) | EURUSD, GBPUSD |
| L7:RASCHKE_80_20 | previous D1 opened in its top 20 % and closed in its bottom 20 % (mirror for shorts); in the session price trades beyond the previous low (high), then a stop order back at that level; stop at the session extreme so far; exit end | all |

Counts: EURUSD 19, GBPUSD 19, XAUUSD 18, NAS100USD 14 = **70 variants** (each under 3 cost models; selection on the
main model only).

### 1.3 Protocol, selection and pass bar
- TRAIN 2021-10-01..2024-12-31: the M1 file is sliced at 2025-01-01 00:00 NY **before** any feature or signal is built.
  Half-year folds 2023H1, 2023H2, 2024H1, 2024H2 (fixed rules, so fold "test" = the variant's result in that half-year).
- **Eligible** = TRAIN (main cost) meets the pass bar (avg R >= +0.08, PF > 1.15, MTM DD < 12R, >= 3 trades/month) AND
  >= 3 of 4 folds with avg R > 0. **Pick** = the eligible variant with the best mean fold avg R (tie: lower DD).
  None eligible -> FLAT: nothing is frozen to trade and the holdout is **not** run for that instrument.
- Freeze `/workspace/backtest/round8_frozen_<SYM>.json` + committed copy `replay/round8_frozen_<SYM>.json`, committed
  BEFORE the holdout; the holdout runner fails closed unless the file equals the committed one in HEAD.
- HOLDOUT 2025-01-01..2026-10-19 run **once** with the frozen variant under main, stress and C_OANDA (earlier data used
  only as causal history for features). **Pass** (main cost): avg R >= +0.08, PF > 1.15, MTM DD < 12R, >= 3 tr/mo, and
  2025 and 2026 YTD both positive. XAU / NAS100 are compared with buy-and-hold over the same window.
- Metrics: trades, tr/mo, avg R, total R, PF, win %, MTM max DD in R (intratrade worst M1 excursion), exit mix, yearly
  totals, bootstrap CI80 low of avg R (2000 resamples of trades; one trade per day per variant, so trade = day block;
  seed 8).
- Multiple testing: 70 new variant tests (+ up to 4 holdouts); cumulative before round 8: 474 (conservative 874).
  Conservative count adds every cost-model run (70 x 3 = 210).

## Round 8b declaration (written and committed BEFORE any 8b selection run)

Brief: `spec_round8b.md`. Decisions taken without asking:

- **Code fixes (Codex r8 findings, chunk 1, commit 36dc749).** (a) Bid/ask sides: buy stop/limit triggers use the ask
  (mid + half spread) and sell triggers the bid. A buy limit needs the ask low to trade through the level and fills at
  min(ask open, level), so never above the limit. Exits are checked on the exit side: a long's SL/TP on the bid, a
  short's on the ask. A TP fills at the target price (it is already on the exit side, so no extra spread is charged).
  SL gaps fill at the exit-side open. Market entries and time exits pay half spread + slippage as before. (b) L7: the
  stop order is placed at kb+1 (the bar after the first breach), with its stop at the session extreme through kb. It
  is not amended and stays live to session end. (c) L4 Silver Bullet: the FVG is the most recent one completed by the
  MSS bar. If there is none, it is the first one completing in the next 3 bars, scanned forward. The order is placed on
  the bar after the later of MSS and FVG. (d) L1/L1b: direction comes from the close of the last bar before the entry
  time, and the entry is at the next bar's open.
- **All 70 round-8 variants are re-run** with the fixed engine. Every round-8 TRAIN number above is **superseded**. The
  re-runs count as **new tests**.
- **New families** (all four instruments, same sessions, fills, clamps, costs and protocol as section 1):

| id | rule |
|---|---|
| S4:M5_MOM_1.5ATR / S4:M5_MOM_2ATR | first M5 bar starting in [open, end-60 min) with body abs(C-O) >= m x ATR14(M5) of the 14 M5 bars **before** it (m = 1.5 / 2) -> market order in the body's direction at the next M1 open; stop 3 x ATR14(M5) known at entry (the common ATR rule), clamped; TP 1.5R; exit at session end |
| S5:MICRO_SMC | previous session's high/low (previous trading day, same session window: London 03:00-11:00 NY; NAS100 09:30-16:00 ET). The first M5 bar starting in [open, end-60 min) that trades beyond the high (low) and closes back inside sets up a fade. Scanning the next 30 M1 bars forward, the first of these sets the level: (1) a 3-bar M1 FVG in the trade direction -> limit at its midpoint; (2) an M1 close beyond the sweep bar's opposite extreme (displacement) -> order block = last opposite-colour M1 bar from the sweep bar's start, limit at its open. The order is placed on the next bar and stays live 60 min (capped at session end). Stop at the sweep extreme from the sweep bar's start to placement, clamped. TP 2R; exit at session end |

  One signal per session for S4 and S5 (the first one). Before this declaration, a smoke run on 2021-10 counted orders
  and trades only (no metric was computed).
- Variant counts for 8b: EURUSD 22, GBPUSD 22, XAUUSD 21, NAS100USD 17 = **82** (70 re-runs + 12 new), each under 3
  cost models.
- Unchanged from section 1.3: TRAIN 2021-10-01..2024-12-31 (sliced before building), folds 2023H1..2024H2,
  eligibility (bar + >= 3/4 positive folds), pick rule, fail-closed freeze, HOLDOUT 2025-01-01..2026-10-19 run once
  **only** if a variant is eligible; otherwise FLAT and no holdout.
- Multiple testing: round 8 counted 70 (cumulative 544, conservative 1084). Round 8b adds 82, so the count goes to
  **626**. Conservative: + 82 x 3 = 246, so **1330**. Any holdout run is added on top.
