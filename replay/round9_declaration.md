# Round 9 (REPLAY ONLY) [declaration copy] — one-shot NAS100USD holdout of the round-8b near-misses

Code: `replay/round9.py` (runner) on the round-8b engine `replay/round8.py` (unchanged; git blob
`f78e5f2` recorded in the freeze), branch `feat/backtest-v2-i2`. Data (outside the repo, never committed):
`/workspace/backtest/data_m1/NAS100USD_1m.csv` (OANDA M1, ends 2026-10-19). Results:
`/workspace/backtest/round9/NAS100USD.json`, chart `/workspace/backtest/round9_equity_r.png`.

## 1. Declaration (written and committed BEFORE the holdout is run)

### 1.0 Decisions taken without asking
- **No retuning.** The three variants are the exact round-8b NAS100USD rules (`variants("NAS100USD")` in
  `replay/round8.py` at HEAD): **PRIMARY `L3:NR4`** (the round-8b near-miss: TRAIN +0.454R, PF 1.79, DD 14.3R,
  4.71 tr/mo); **SECONDARY** `L3:NR7` (TRAIN +0.506R, 2.69 tr/mo) and `S1a:ORB5` (TRAIN +0.144R, DD 45R). Secondaries
  are reported and counted as tests but do not decide the verdict. None was eligible in round 8b; this round
  knowingly tests them anyway, and the result is read with that in mind (selection on TRAIN without passing the bar).
- Costs: main `C_IBKR` (MNQ-like: spread 0.25 + slippage 0.25 pt per market/stop fill + commission 0.62 pt RT), as in
  round 8b. `C_OANDA` (observed previous-bar spread, slippage floor 0.2 pt) is reported as reference only.
- Freeze: `/workspace/backtest/round9_frozen.json` = committed `replay/round9_frozen.json`. The runner fails closed
  unless the file equals the copy in HEAD, the variant names exist in the engine, and the engine's git blob equals
  the recorded one.
- **The strict bar's >= 15 tr/mo is far above the TRAIN frequency of NR4 (4.71) and NR7 (2.69)**, so they cannot
  meet it unless their frequency triples. The bar is kept as given; the round-7 bar (>= 3 tr/mo) is reported
  next to it for information only.
- Quarters: 2025Q1..2026Q4 = 8 calendar quarters (2026Q4 = Oct 1-19 only). A quarter is positive if its total R > 0;
  a quarter with no trades is not positive. Need >= 5 of 8.
- Cost R/trade = (entry half spread + entry slippage, exit half spread + exit slippage, commission), using the cost
  model's per-bar values at the entry and exit bars, divided by the trade's risk. Limit entries pay no slippage and
  TP exits none (the TP is already on the exit side); the half spread on those legs is still counted, since it is
  priced into the bid/ask trigger.

### 1.1 Protocol
- HOLDOUT **2025-01-01..2026-10-19** (session dates; `[2025-01-01, 2026-10-20)`), run **once**. The M1 file is cut at
  2026-10-20 00:00 NY. Orders are only built for session days inside the window. Earlier data is used only as causal
  history (D1 NR4/NR7 ranges, 10-day stretch, ATR14(M5), 20-day ATR median), exactly as in round 8b.
- Same engine, fills, clamps and session as round 8b (NAS100 cash 09:30-16:00 ET, all exits intraday).
- **Strict pass (round-8 bar + quarters), main cost, PRIMARY decides:** avg R >= +0.08, PF > 1.15, MTM DD < 12R,
  >= 15 tr/mo, >= 5 of 8 quarters positive. **Informational round-7 bar:** avg R >= +0.08, PF > 1.15, MTM DD < 12R,
  >= 3 tr/mo (does not change the verdict).
- Reported per variant and cost model: trades, tr/mo, avg R, total R, PF, MTM DD R, win %, per-quarter R, cost
  R/trade, exit mix; buy-and-hold NAS100 over the same window (% and max DD %) for comparison.
- Multiple testing: 3 new tests (NR4, NR7, ORB5 holdouts). Cumulative **626 -> 629** (conservative 1330 -> 1336 with
  both cost models counted).
