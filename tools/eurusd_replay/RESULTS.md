# EURUSD replay — proposals C, D and E

Window: 2021-10-01T00:00:00+00:00 → 2026-10-20T00:00:00+00:00 (UTC). Walk-forward split 2024-10-01T00:00:00+00:00 (first three years in-sample, the rest out-of-sample). Session slots considered: 73392. PLAN_READY signals: 10118 (scout audits against the real agents: 51, mismatches: 0).

Starting equity 100,000 USD. Size is the demo router's `broker_units`: 1% of cash, then the 100,000 unit cap. R uses the risk that size actually took. Percent drawdown is mark-to-market on the bid (long) or ask (short).

Of 73,392 session slots, 10,126 had 1h and 15m aligned with 5m not against. The real validator turned 10,118 of those into PLAN_READY (5,005 with all three timeframes aligned, 5,113 only after the neutral-5m rewrite). On a 500-bar window the 15m break/retracement and the 5m equal-high/low lists are almost always present, so those checks remove almost nothing once the timeframes agree. That is the live validator on this window, and it is why the book is close to the alignment-only simulation the proposal already found to be a loser.

## Summary

| Config | Trades | Fill rate | Win rate | PF | E[R] | Max DD R | Max DD % | Lose streak | Trades/week | OOS PF | OOS E[R] | OOS DD R |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0_legacy | 674 | 71.4% | 40.9% | 0.87 | -0.071 | 52.02 | 14.4% | 9 | 2.56 | 0.92 | -0.042 | 28.74 |
| 1_ab_5pip | 890 | 56.9% | 40.3% | 0.86 | -0.074 | 83.77 | 21.4% | 13 | 3.38 | 0.80 | -0.111 | 59.02 |
| 1_ab_market | 1223 | — | 33.4% | 0.87 | -0.080 | 103.59 | 26.5% | 13 | 4.64 | 0.89 | -0.066 | 43.37 |
| 2_ab5_c | 1122 | 57.5% | 42.8% | 0.89 | -0.053 | 75.44 | 19.9% | 15 | 4.26 | 0.84 | -0.085 | 66.81 |
| 3_ab5_d | 891 | 57.0% | 40.1% | 0.86 | -0.074 | 80.77 | 21.0% | 13 | 3.38 | 0.79 | -0.111 | 57.02 |
| 4_ab5_e | 952 | 56.8% | 39.9% | 0.88 | -0.066 | 77.38 | 20.5% | 12 | 3.61 | 0.79 | -0.118 | 65.19 |
| 5_ab5_c_d | 1123 | 57.5% | 42.6% | 0.89 | -0.053 | 72.44 | 19.6% | 15 | 4.26 | 0.84 | -0.085 | 64.81 |
| 5_ab5_c_e | 1175 | 57.4% | 42.0% | 0.90 | -0.053 | 74.83 | 20.4% | 13 | 4.46 | 0.81 | -0.101 | 74.63 |
| 5_ab5_d_e | 953 | 56.8% | 39.6% | 0.87 | -0.067 | 76.38 | 20.6% | 12 | 3.62 | 0.79 | -0.118 | 63.19 |
| 5_ab5_c_d_e | 1176 | 57.4% | 41.8% | 0.89 | -0.054 | 75.68 | 20.7% | 13 | 4.46 | 0.81 | -0.101 | 72.63 |
| 5_abm_d | 1226 | — | 33.1% | 0.87 | -0.084 | 108.59 | 27.6% | 13 | 4.65 | 0.89 | -0.066 | 43.37 |
| 5_abm_e | 1267 | — | 33.1% | 0.88 | -0.079 | 105.10 | 27.2% | 13 | 4.81 | 0.87 | -0.083 | 52.54 |
| 5_abm_c | 1522 | — | 31.7% | 0.85 | -0.099 | 156.18 | 39.2% | 15 | 5.77 | 0.95 | -0.029 | 29.93 |
| 5_abm_c_d_e | 1559 | — | 31.2% | 0.83 | -0.107 | 171.04 | 43.0% | 16 | 5.91 | 0.92 | -0.047 | 36.85 |

Fill rate is filled limit orders divided by limit orders placed. A same-side replace counts the cancelled order as placed and unfilled. Market rows have no limit fill rate.

## Per year

### 0_legacy

| Year | Trades | Win rate | PF | E[R] | Net R | Max DD R |
|---|---:|---:|---:|---:|---:|---:|
| 2021 | 32 | 40.6% | 0.76 | -0.133 | -4.24 | 7.54 |
| 2022 | 174 | 40.2% | 0.86 | -0.079 | -13.75 | 20.55 |
| 2023 | 125 | 40.8% | 0.98 | -0.012 | -1.53 | 14.24 |
| 2024 | 113 | 44.2% | 0.93 | -0.036 | -4.07 | 19.06 |
| 2025 | 141 | 36.9% | 0.72 | -0.164 | -23.18 | 25.56 |
| 2026 | 89 | 44.9% | 0.97 | -0.012 | -1.11 | 8.91 |

### 1_ab_5pip

| Year | Trades | Win rate | PF | E[R] | Net R | Max DD R |
|---|---:|---:|---:|---:|---:|---:|
| 2021 | 37 | 45.9% | 1.22 | 0.095 | 3.52 | 4.26 |
| 2022 | 228 | 37.7% | 0.82 | -0.098 | -22.35 | 41.59 |
| 2023 | 167 | 45.5% | 1.07 | 0.035 | 5.88 | 11.60 |
| 2024 | 141 | 44.7% | 0.95 | -0.025 | -3.54 | 18.65 |
| 2025 | 191 | 33.5% | 0.64 | -0.221 | -42.13 | 44.66 |
| 2026 | 126 | 42.1% | 0.88 | -0.059 | -7.41 | 21.62 |

### 1_ab_market

| Year | Trades | Win rate | PF | E[R] | Net R | Max DD R |
|---|---:|---:|---:|---:|---:|---:|
| 2021 | 54 | 33.3% | 0.81 | -0.117 | -6.34 | 10.38 |
| 2022 | 310 | 32.3% | 0.84 | -0.107 | -33.08 | 33.08 |
| 2023 | 243 | 34.6% | 0.92 | -0.049 | -11.84 | 29.45 |
| 2024 | 198 | 34.3% | 0.89 | -0.065 | -12.86 | 21.97 |
| 2025 | 264 | 32.2% | 0.87 | -0.087 | -22.90 | 34.63 |
| 2026 | 154 | 34.4% | 0.88 | -0.073 | -11.24 | 28.72 |

### 2_ab5_c

| Year | Trades | Win rate | PF | E[R] | Net R | Max DD R |
|---|---:|---:|---:|---:|---:|---:|
| 2021 | 52 | 38.5% | 0.80 | -0.105 | -5.47 | 9.65 |
| 2022 | 285 | 42.1% | 0.92 | -0.041 | -11.71 | 42.31 |
| 2023 | 220 | 48.6% | 1.12 | 0.056 | 12.39 | 14.98 |
| 2024 | 172 | 43.6% | 0.94 | -0.030 | -5.15 | 22.92 |
| 2025 | 240 | 38.8% | 0.73 | -0.149 | -35.83 | 45.64 |
| 2026 | 153 | 42.5% | 0.81 | -0.092 | -14.13 | 27.24 |

### 3_ab5_d

| Year | Trades | Win rate | PF | E[R] | Net R | Max DD R |
|---|---:|---:|---:|---:|---:|---:|
| 2021 | 37 | 45.9% | 1.22 | 0.095 | 3.52 | 4.26 |
| 2022 | 229 | 37.1% | 0.82 | -0.095 | -21.84 | 38.59 |
| 2023 | 167 | 45.5% | 1.07 | 0.035 | 5.88 | 11.60 |
| 2024 | 141 | 44.0% | 0.92 | -0.039 | -5.54 | 18.65 |
| 2025 | 191 | 33.5% | 0.64 | -0.221 | -42.13 | 44.66 |
| 2026 | 126 | 42.1% | 0.91 | -0.043 | -5.41 | 20.62 |

### 4_ab5_e

| Year | Trades | Win rate | PF | E[R] | Net R | Max DD R |
|---|---:|---:|---:|---:|---:|---:|
| 2021 | 38 | 44.7% | 1.15 | 0.066 | 2.52 | 5.26 |
| 2022 | 245 | 38.0% | 0.87 | -0.075 | -18.40 | 38.53 |
| 2023 | 177 | 44.6% | 1.07 | 0.033 | 5.83 | 12.14 |
| 2024 | 154 | 44.8% | 1.04 | 0.021 | 3.30 | 17.89 |
| 2025 | 204 | 32.4% | 0.63 | -0.228 | -46.53 | 48.07 |
| 2026 | 134 | 41.8% | 0.86 | -0.070 | -9.37 | 24.39 |

## Verdict

- **0 legacy.** 674 closed trades, limit fill rate 71.4%, PF 0.868, expectancy -0.071R, max drawdown 52.02R / 14.4%. This is the baseline the other rows are judged against.
- **A+B 5 pips.** Fill rate 56.9% against legacy 71.4%. PF 0.861 (legacy 0.868). Drawdown 21.4% against legacy 14.4%. OOS PF 0.797. Does not clear the proposal's gate.
- **A+B market.** No resting limit, so the 50% fill-rate gate does not apply. Market orders sent 1089, refused by the 10-pip anchor 0. PF 0.873, OOS PF 0.895, drawdown 26.5%. The economic half of the A+B gate does not pass. This path does not use the H1 EMA(200) filter; that filter lives inside the limit planner.
- **C.** PF 0.894, OOS PF 0.837, 1122 trades, drawdown 19.9%. Not acceptable. PF is below 1.15 or the out-of-sample PF is below 1.
- **D.** EMA breakeven at +1.5R. PF 0.860 against A+B 0.861, OOS PF 0.794 against 0.797, win rate 40.1% against 40.3%. The proposal keeps this off the live book until 20 practice trades exist, so no flag is added.
- **E.** PF 0.878 against A+B 0.861, OOS PF 0.788 against 0.797, drawdown 20.5% against 21.4%. Does not clear that bar. No live flag.
- **Best combination.** No configuration has profit factor and out-of-sample profit factor both at or above 1 (C combinations also need PF ≥ 1.15). Nothing here is a reason to turn a flag on.

## How the book is driven

- Setup status is `agents.setup_validator.evaluar_setup`. The FX plan is `plan_fx_fixed_rr`. The H1 EMA(200) trend, the limit price, the 25/50 levels, the expiry cap and the partials are the pullback module.
- EMA entries are `execution.ema_cross.step` (M15 cross, H1 filter, 15-minute delay, one shot). SL 20 / TP 40 come from `levels_from_fill` on the bar's ask (long) or bid (short).
- Sessions are `runtime.scheduler.session_names` (London and New York, 08:00–17:00 local). The Friday 16:45 America/New_York ban and the one-week hold are `execution.trade_duration`.
- One position. A resting limit counts. DEC-REVERSAL-1 is on, as in the demo router: an opposite pullback plan closes a pullback that is in profit and then enters. It does not close an EMA position.
- C, inside the harness, rewrites only a **neutral** 5m `structure_state` to the shared 1h/15m side and calls the real validator again. A 5m state that is against the side is not rewritten. 15m break or retracement, 5m liquidity and the invalidation level are still required.
- D moves the EMA stop to the fill when a later 5-minute close is +1.5R. The bar that arms it still uses the original stop.
- E replaces the live UTC [12, 15) window with 07:00–11:00 America/New_York. The live window is not 07:00–10:00 New York all year: it is 07:00–10:00 only while New York is on standard time.

## AI stub and news

The xAI reviewers are not called. A deterministic PLAN_READY is treated as executable. That is a pass-through, not `DeterministicAIProvider`: the echo provider can still return DISAGREE and the live path then marks the cycle AI_CAUTION and does not send the order. The stub therefore **keeps trades a live reviewer would have vetoed**. It biases the book toward more trades, and toward whatever the reviewers have been filtering out.

The candle files have no macro calendar. Every slot uses `NoMacroDataProvider`, and `analizar_macro_news` returns NO_DATA with an empty list. The validator does not block on an empty calendar, and `news_gate('NO_DATA')` allows the EMA entry. High-impact windows are **not** replayed. The book includes trades the live floor would have left in WATCH during those windows. The separate live block that refuses a plan when the macro review is still NO_DATA after a retry is also not applied; applying it with this bundle would have taken zero trades.

## Spread, fills, and what is not the live loop

OHLC is the mid. BidClose and AskClose are the bar's close quotes. A bar's path uses that close spread, held constant: bid = mid − spread/2, ask = mid + spread/2. A long limit fills when the ask trades to the limit (at the limit, or at the ask open if the bar gaps through). A short limit uses the bid. A market order is filled at that same bar's close bid/ask, so the distance from the planned entry is the spread and the 10-pip anchor refusal did not fire in this book. A live quote can sit further from the last close than that. Stops and targets are resting: a gap fills at the open quote, and when one bar touches both, the stop is assumed first. The 15-pip cancel uses the mid high (long) or mid low (short) measured from the signal, which is the 5-minute close the plan was built from. If the same bar both reaches the limit and runs 15 pips in favor, and it did not gap through the limit at the open, the order is cancelled and not filled.

Partials run at the 15-minute slot on the close quote, and only if the resting stop or target did not already fill on that bar. A fast move to the target therefore pays the full target and skips the scale-out. New plans are not filled on the bar that created them.

Structure and liquidity on the five-year loop are a numpy transcription of `agents/structure_agent.py` and `agents/liquidity_agent.py`. The scan calls the real scouts on every 200th aligned window and raises if a decision field differs. The validator, planner, risk sizer, pullback and EMA functions are the real ones on every slot.

A+B's 4-hour life, 15-pip cancel, same-side replace and EMA priority are harness rules. A replace happens only when the new plan's `setup_id` differs; the same identity does not refresh the clock every 15 minutes. They are not edits to `execution/eurusd_pullback.py`.

## Strategy split

Each row is a separate book. EMA-only never places a pullback order. Pullback-only never arms an EMA cross. The two strategies no longer block each other, so these counts are not a partition of the combined table above (legacy combined was 674 trades; the two solo books are 256 + 522).

| Config | Trades | Fill rate | Win rate | PF | E[R] | Max DD R | Max DD % | Lose streak | Trades/week | OOS PF |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| EMA only, legacy window | 256 | — | 40.2% | 1.29 | 0.166 | 15.33 | 3.0% | 10 | 0.97 | 1.48 |
| EMA only, E (07:00–11:00 ET) | 336 | — | 38.1% | 1.18 | 0.109 | 19.21 | 3.8% | 12 | 1.27 | 1.23 |
| EMA only, D (breakeven at +1.5R) | 257 | — | 38.1% | 1.26 | 0.146 | 14.43 | 2.9% | 10 | 0.98 | 1.43 |
| Pullback only, legacy 24h / 12.5 pips | 522 | 70.5% | 41.6% | 0.70 | -0.155 | 83.97 | 20.9% | 9 | 1.98 | 0.73 |
| Pullback only, A+B 5 pips | 714 | 56.0% | 41.5% | 0.76 | -0.123 | 101.09 | 24.9% | 13 | 2.71 | 0.71 |

**Verdict.** The losses are the pullback book; EMA 10/21 alone is profitable, and neither E nor D improves it.

The earlier box (PF about 1.35, 273 trades, max drawdown 12R, stop 25 / target 50, entry on the M15 cross with an H1 trend filter) and this EMA-only legacy row (PF 1.29, 256 trades, max drawdown 15.3R) are the same idea under two contracts. This harness follows the live module: stop 20 / target 40, the order goes out 15 minutes after the cross and only on that first slot, the long pays the ask and the short sells the bid, and Friday 16:45 New York plus the one-week hold can close the trade before the target. A stop five pips tighter is hit by noise a 25-pip stop survives, the delayed fill is a worse price, and the time exit cuts both winners and losers. Those three differences are the gap between 1.35 and 1.29, and between 12R and 15R of drawdown. The trade count is already in the same range, about one a week. `docs/ema_cross.md` records that the 1.35-style pass reused years that had already been used to choose the variant. The out-of-sample half of this run, from 2024-10-01, still prints PF 1.48, so the live EMA rules are not only an in-sample result. The combined book's PF of 0.87 is this EMA book being outnumbered by the pullback book (PF 0.70).
