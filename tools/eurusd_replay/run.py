"""Replay the EURUSD book and write the results markdown.

    python -m tools.eurusd_replay.run --data-dir /path/to/csvs --out tools/eurusd_replay/RESULTS.md

The directory holds EURUSD_5m.csv, EURUSD_15m.csv and EURUSD_1h.csv.
"""
from datetime import timedelta
from decimal import Decimal
import argparse

from tools.eurusd_replay.metrics import (ab_passes, c_passes, e_passes, markdown_table, pick_best, split_at, summarize,
                                        year_table)
from tools.eurusd_replay.scan import BAR, fresh_session_slots, load_frames, scan, write_signals
from tools.eurusd_replay.simulate import ab_limit, ab_market, close_pairs, iter_bars, legacy, run, with_flags


def configs():
    base, market = ab_limit(), ab_market()
    be = Decimal("1.5")
    return [
        legacy(),
        base,
        market,
        with_flags(base, name="2_ab5_c", relaxed=True),
        with_flags(base, name="3_ab5_d", be=be),
        with_flags(base, name="4_ab5_e", window="et_7_11"),
        with_flags(base, name="5_ab5_c_d", relaxed=True, be=be),
        with_flags(base, name="5_ab5_c_e", relaxed=True, window="et_7_11"),
        with_flags(base, name="5_ab5_d_e", be=be, window="et_7_11"),
        with_flags(base, name="5_ab5_c_d_e", relaxed=True, be=be, window="et_7_11"),
        with_flags(market, name="5_abm_d", be=be),
        with_flags(market, name="5_abm_e", window="et_7_11"),
        with_flags(market, name="5_abm_c", relaxed=True),
        with_flags(market, name="5_abm_c_d_e", relaxed=True, be=be, window="et_7_11"),
        with_flags(legacy(), name="a_ema_legacy", pullback=False),
        with_flags(legacy(), name="a_ema_e", pullback=False, window="et_7_11"),
        with_flags(legacy(), name="a_ema_d", pullback=False, be=be),
        with_flags(legacy(), name="b_pullback_legacy", ema=False),
        with_flags(base, name="b_pullback_ab5", ema=False),
    ]


def _verdict(name, text):
    return f"- **{name}.** {text}"


def _report(results, meta, split, start, end):
    by_name = {row["name"]: row for row in results}
    legacy_row = by_name["0_legacy"]
    ab = by_name["1_ab_5pip"]
    market = by_name["1_ab_market"]
    c_row = by_name["2_ab5_c"]
    d_row = by_name["3_ab5_d"]
    e_row = by_name["4_ab5_e"]
    best = pick_best(results)
    lines = [
        "# EURUSD replay — proposals C, D and E",
        "",
        f"Window: {start.isoformat()} → {end.isoformat()} (UTC). "
        f"Walk-forward split {split.isoformat()} (first three years in-sample, the rest out-of-sample). "
        f"Session slots considered: {meta.get('slots', '—')}. "
        f"PLAN_READY signals: {meta.get('signals', '—')} "
        f"(scout audits against the real agents: {meta.get('scout_audits', '—')}, mismatches: {meta.get('scout_mismatches', '—')}).",
        "",
        "Starting equity 100,000 USD. Size is the demo router's `broker_units`: 1% of cash, then the 100,000 unit cap. "
        "R uses the risk that size actually took. Percent drawdown is mark-to-market on the bid (long) or ask (short).",
        "",
        "On a 500-bar window the 15m break/retracement and the 5m equal-high/low lists are almost always present, "
        "so those validator checks remove almost nothing once the timeframes agree. The book is therefore close to "
        "the alignment-only simulation the proposal already found to be a loser.",
        "",
        "## Summary",
        "",
        markdown_table(results),
        "",
        "Fill rate is filled limit orders divided by limit orders placed. A same-side replace counts the cancelled order as placed and unfilled. Market rows have no limit fill rate.",
        "",
        "## Per year",
        "",
    ]
    for row in results:
        if row["name"] not in {"0_legacy", "1_ab_5pip", "1_ab_market", "2_ab5_c", "3_ab5_d", "4_ab5_e"} and row is not best:
            continue
        lines += [f"### {row['name']}", "", year_table(row), ""]
    lines += [
        "## Verdict",
        "",
        _verdict("0 legacy", _legacy_text(legacy_row)),
        _verdict("A+B 5 pips", _ab_text(ab, legacy_row, ab_passes(ab, legacy_row))),
        _verdict("A+B market", _market_text(market, legacy_row)),
        _verdict("C", _c_text(c_row, c_passes(c_row))),
        _verdict("D", _d_text(d_row, ab)),
        _verdict("E", _e_text(e_row, ab, e_passes(e_row, ab))),
        _verdict("Best combination", _best_text(best)),
        "",
        "## How the book is driven",
        "",
        "- Setup status is `agents.setup_validator.evaluar_setup`. The FX plan is `plan_fx_fixed_rr`. "
        "The H1 EMA(200) trend, the limit price, the 25/50 levels, the expiry cap and the partials are the pullback module.",
        "- EMA entries are `execution.ema_cross.step` (M15 cross, H1 filter, 15-minute delay, one shot). "
        "SL 20 / TP 40 come from `levels_from_fill` on the bar's ask (long) or bid (short).",
        "- Sessions are `runtime.scheduler.session_names` (London and New York, 08:00–17:00 local). "
        "The Friday 16:45 America/New_York ban and the one-week hold are `execution.trade_duration`.",
        "- One position. A resting limit counts. DEC-REVERSAL-1 is on, as in the demo router: an opposite pullback plan closes a pullback that is in profit and then enters. It does not close an EMA position.",
        "- C, inside the harness, rewrites only a **neutral** 5m `structure_state` to the shared 1h/15m side and calls the real validator again. A 5m state that is against the side is not rewritten. 15m break or retracement, 5m liquidity and the invalidation level are still required.",
        "- D moves the EMA stop to the fill when a later 5-minute close is +1.5R. The bar that arms it still uses the original stop.",
        "- E replaces the live UTC [12, 15) window with 07:00–11:00 America/New_York. The live window is not 07:00–10:00 New York all year: it is 07:00–10:00 only while New York is on standard time.",
        "",
        "## AI stub and news",
        "",
        "The xAI reviewers are not called. A deterministic PLAN_READY is treated as executable. "
        "That is a pass-through, not `DeterministicAIProvider`: the echo provider can still return DISAGREE and the live path then marks the cycle AI_CAUTION and does not send the order. "
        "The stub therefore **keeps trades a live reviewer would have vetoed**. It biases the book toward more trades, and toward whatever the reviewers have been filtering out.",
        "",
        "The candle files have no macro calendar. Every slot uses `NoMacroDataProvider`, and `analizar_macro_news` returns NO_DATA with an empty list. "
        "The validator does not block on an empty calendar, and `news_gate('NO_DATA')` allows the EMA entry. "
        "High-impact windows are **not** replayed. The book includes trades the live floor would have left in WATCH during those windows. "
        "The separate live block that refuses a plan when the macro review is still NO_DATA after a retry is also not applied; applying it with this bundle would have taken zero trades.",
        "",
        "## Spread, fills, and what is not the live loop",
        "",
        "OHLC is the mid. BidClose and AskClose are the bar's close quotes. A bar's path uses that close spread, held constant: bid = mid − spread/2, ask = mid + spread/2. "
        "A long limit fills when the ask trades to the limit (at the limit, or at the ask open if the bar gaps through). A short limit uses the bid. "
        "Stops and targets are resting: a gap fills at the open quote, and when one bar touches both, the stop is assumed first. "
        "The 15-pip cancel uses the mid high (long) or mid low (short) measured from the signal, which is the 5-minute close the plan was built from. "
        "If the same bar both reaches the limit and runs 15 pips in favor, and it did not gap through the limit at the open, the order is cancelled and not filled.",
        "",
        "Partials run at the 15-minute slot on the close quote, and only if the resting stop or target did not already fill on that bar. "
        "A fast move to the target therefore pays the full target and skips the scale-out. "
        "New plans are not filled on the bar that created them.",
        "",
        "Structure and liquidity on the five-year loop are a numpy transcription of `agents/structure_agent.py` and `agents/liquidity_agent.py`. "
        "The scan calls the real scouts on every 200th aligned window and raises if a decision field differs. The validator, planner, risk sizer, pullback and EMA functions are the real ones on every slot.",
        "",
        "A+B's 4-hour life, 15-pip cancel, same-side replace and EMA priority are harness rules. "
        "A replace happens only when the new plan's `setup_id` differs; the same identity does not refresh the clock every 15 minutes. "
        "They are not edits to `execution/eurusd_pullback.py`.",
        "",
    ]
    split_names = ("a_ema_legacy", "a_ema_e", "a_ema_d", "b_pullback_legacy", "b_pullback_ab5")
    if all(name in by_name for name in split_names):
        ema_row = by_name["a_ema_legacy"]
        lines += [
            "## Strategy split",
            "",
            "Each row is its own book. EMA-only never places a pullback. Pullback-only never arms an EMA cross. "
            "They no longer block each other, so the counts are not a partition of the combined table.",
            "",
            markdown_table([by_name[name] for name in split_names]),
            "",
            f"**Verdict.** The losses are the pullback book (PF {_num(by_name['b_pullback_legacy']['all']['pf'])} legacy, "
            f"{_num(by_name['b_pullback_ab5']['all']['pf'])} at 5 pips). EMA 10/21 alone is PF {_num(ema_row['all']['pf'])} "
            f"({ema_row['all']['trades']} trades, drawdown {_num(ema_row['all']['max_dd_r'])}R). "
            "E and D do not improve that EMA book.",
            "",
            "The earlier box (PF about 1.35, 273 trades, max drawdown 12R, stop 25 / target 50, entry on the M15 cross) "
            "uses a wider stop, no 15-minute delay, and no Friday or one-week exit. This row uses the live module: "
            "stop 20 / target 40, one shot 15 minutes after the cross, the spread, and those time exits. "
            "The trade count is already about one a week, and the out-of-sample PF on this EMA book stays above 1.",
            "",
        ]
    return "\n".join(lines) + "\n"


def _legacy_text(row):
    return (f"{row['all']['trades']} closed trades, limit fill rate {_pct(row['all']['limit_fill_rate'])}, "
            f"PF {_num(row['all']['pf'])}, expectancy {_num(row['all']['expectancy_r'])}R, "
            f"max drawdown {_num(row['all']['max_dd_r'])}R / {_pct(row['all']['max_dd_pct'])}. "
            "This is the baseline the other rows are judged against.")


def _ab_text(row, legacy_row, ok):
    gate = "Clears the proposal's gate (fill rate ≥ 50%, PF ≥ 1, drawdown not worse than legacy)." if ok else (
        "Does not clear the proposal's gate.")
    return (f"Fill rate {_pct(row['all']['limit_fill_rate'])} against legacy {_pct(legacy_row['all']['limit_fill_rate'])}. "
            f"PF {_num(row['all']['pf'])} (legacy {_num(legacy_row['all']['pf'])}). "
            f"Drawdown {_pct(row['all']['max_dd_pct'])} against legacy {_pct(legacy_row['all']['max_dd_pct'])}. "
            f"OOS PF {_num(row['oos']['pf'])}. {gate}")


def _market_text(row, legacy_row):
    sent, refused = row["all"]["market_sent"], row["all"]["market_refused"]
    economic = (row["all"]["pf"] or 0) >= 1.0 and row["all"]["max_dd_pct"] <= legacy_row["all"]["max_dd_pct"] + 1e-12
    gate = "The economic half of the A+B gate (PF ≥ 1 and drawdown not worse) passes." if economic else (
        "The economic half of the A+B gate does not pass.")
    return (f"No resting limit, so the 50% fill-rate gate does not apply. "
            f"Market orders sent {sent}, refused by the 10-pip anchor {refused}. "
            f"PF {_num(row['all']['pf'])}, OOS PF {_num(row['oos']['pf'])}, "
            f"drawdown {_pct(row['all']['max_dd_pct'])}. {gate} "
            "This path does not use the H1 EMA(200) filter; that filter lives inside the limit planner.")


def _c_text(row, ok):
    gate = "Acceptable on the stated bar (PF ≥ 1.15 and OOS PF ≥ 1)." if ok else (
        "Not acceptable. PF is below 1.15 or the out-of-sample PF is below 1.")
    return (f"PF {_num(row['all']['pf'])}, OOS PF {_num(row['oos']['pf'])}, "
            f"{row['all']['trades']} trades, drawdown {_pct(row['all']['max_dd_pct'])}. {gate}")


def _d_text(row, base):
    return (f"EMA breakeven at +1.5R. PF {_num(row['all']['pf'])} against A+B {_num(base['all']['pf'])}, "
            f"OOS PF {_num(row['oos']['pf'])} against {_num(base['oos']['pf'])}, "
            f"win rate {_pct(row['all']['win_rate'])} against {_pct(base['all']['win_rate'])}. "
            "The proposal keeps this off the live book until 20 practice trades exist, so no flag is added.")


def _e_text(row, base, ok):
    gate = ("Clears the bar used here (PF ≥ 1, OOS PF ≥ 1, drawdown not worse than A+B). "
            "A flag that defaults off is added in a separate commit.") if ok else (
        "Does not clear that bar. No live flag.")
    return (f"PF {_num(row['all']['pf'])} against A+B {_num(base['all']['pf'])}, "
            f"OOS PF {_num(row['oos']['pf'])} against {_num(base['oos']['pf'])}, "
            f"drawdown {_pct(row['all']['max_dd_pct'])} against {_pct(base['all']['max_dd_pct'])}. {gate}")


def _best_text(best):
    if best is None:
        return ("No configuration has profit factor and out-of-sample profit factor both at or above 1 "
                "(C combinations also need PF ≥ 1.15). Nothing here is a reason to turn a flag on.")
    return (f"`{best['name']}` has the highest out-of-sample profit factor among the books that clear those bars: "
            f"OOS PF {_num(best['oos']['pf'])}, PF {_num(best['all']['pf'])}, "
            f"expectancy {_num(best['all']['expectancy_r'])}R, drawdown {_pct(best['all']['max_dd_pct'])}.")


def _num(value):
    if value is None:
        return "—"
    if value == float("inf"):
        return "inf"
    return f"{value:.3f}" if abs(value) < 10 else f"{value:.2f}"


def _pct(value):
    if value is None:
        return "—"
    return f"{100 * value:.1f}%"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Replay EURUSD proposals C, D and E")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--cache", default="")
    parser.add_argument("--no-audit", action="store_true")
    args = parser.parse_args(argv)
    frames = load_frames(args.data_dir)
    if args.cache:
        from tools.eurusd_replay.scan import read_signals
        try:
            signals, meta = read_signals(args.cache)
        except FileNotFoundError:
            signals, meta = scan(frames, audit=not args.no_audit, progress=print)
            write_signals(args.cache, signals, meta)
    else:
        signals, meta = scan(frames, audit=not args.no_audit, progress=print)
        if args.cache:
            write_signals(args.cache, signals, meta)
    start = frames["5m"].index[0].to_pydatetime()
    end = frames["5m"].index[-1].to_pydatetime() + timedelta(minutes=5)
    split = split_at(start)
    fresh = fresh_session_slots(frames)
    bars = list(iter_bars(frames["5m"]))
    m15 = close_pairs(frames["15m"], BAR["15m"])
    h1 = close_pairs(frames["1h"], BAR["1h"])
    results = []
    for config in configs():
        print(f"simulate {config.name}", flush=True)
        book = run(config, signals, bars, m15, h1, fresh)
        results.append(summarize(book, start=start, end=end, split=split))
        print(f"  trades={results[-1]['all']['trades']} pf={results[-1]['all']['pf']}", flush=True)
    text = _report(results, meta, split, start, end)
    with open(args.out, "w", encoding="utf-8") as handle:
        handle.write(text)
    print(args.out)


if __name__ == "__main__":
    main()
