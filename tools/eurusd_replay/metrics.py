"""Closed-trade statistics. R is each trade's pnl divided by the risk the sizer actually used."""
from datetime import datetime, timezone


def _pf(values):
    wins = sum(value for value in values if value > 0)
    losses = sum(-value for value in values if value < 0)
    if losses == 0:
        return None if wins == 0 else float("inf")
    return wins / losses


def _streak(values):
    longest = current = 0
    for value in values:
        if value <= 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _block(trades, weeks):
    values = [trade["r"] for trade in trades]
    wins = sum(1 for value in values if value > 0)
    return {
        "trades": len(values),
        "win_rate": (wins / len(values)) if values else None,
        "pf": _pf(values),
        "expectancy_r": (sum(values) / len(values)) if values else None,
        "net_r": sum(values) if values else 0.0,
        "max_dd_r": None,
        "losing_streak": _streak(values),
        "trades_per_week": (len(values) / weeks) if weeks else None,
    }


def _dd_r(trades):
    """Peak-to-trough of the closed-trade R curve. The book also tracks a mark-to-market R drawdown."""
    equity = peak = 0.0
    worst = 0.0
    for trade in trades:
        equity += trade["r"]
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return worst


def summarize(book, *, start, end, split):
    weeks = max((end - start).total_seconds() / (7 * 24 * 3600), 1e-9)
    ordered = sorted(book.trades, key=lambda trade: trade["exit_at"])
    overall = _block(ordered, weeks)
    overall["max_dd_r_closed"] = _dd_r(ordered)
    overall["max_dd_r"] = float(book.max_dd_r)
    overall["max_dd_pct"] = book.max_dd_pct
    placed = book.placed
    overall["limit_placed"] = placed
    overall["limit_filled"] = book.filled
    overall["limit_fill_rate"] = (book.filled / placed) if placed else None
    overall["limit_expired"] = book.expired
    overall["limit_cancelled_run"] = book.cancelled_run
    overall["limit_replaced"] = book.replaced
    overall["limit_cancelled_ema"] = book.cancelled_ema
    overall["market_sent"] = book.market_sent
    overall["market_refused"] = book.market_refused
    overall["capped_orders"] = book.capped
    overall["ending_equity"] = float(book.cash)

    def subset(trades, label):
        span = weeks if label == "all" else max(weeks * (3 / 5 if label == "is" else 2 / 5), 1e-9)
        block = _block(trades, span)
        block["max_dd_r_closed"] = _dd_r(trades)
        return block

    is_trades = [trade for trade in ordered if trade["entry_at"] < split]
    oos_trades = [trade for trade in ordered if trade["entry_at"] >= split]
    years = {}
    for trade in ordered:
        year = trade["entry_at"].astimezone(timezone.utc).year
        years.setdefault(year, []).append(trade)
    per_year = {year: _block(rows, 52.0) for year, rows in sorted(years.items())}
    for year, rows in years.items():
        per_year[year]["max_dd_r_closed"] = _dd_r(rows)
    return {"name": book.config.name, "all": overall, "is": subset(is_trades, "is"), "oos": subset(oos_trades, "oos"),
            "per_year": per_year, "split": split}


def _fmt(value, digits=2):
    if value is None:
        return "—"
    if value == float("inf"):
        return "inf"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _pct(value):
    if value is None:
        return "—"
    return f"{100 * value:.1f}%"


def markdown_table(results):
    header = ("| Config | Trades | Fill rate | Win rate | PF | E[R] | Max DD R | Max DD % | "
              "Lose streak | Trades/week | OOS PF | OOS E[R] | OOS DD R |")
    sep = "|" + "---|" * 12
    lines = [header, sep]
    for row in results:
        all_, oos = row["all"], row["oos"]
        lines.append(
            f"| {row['name']} | {all_['trades']} | {_pct(all_['limit_fill_rate'])} | {_pct(all_['win_rate'])} | "
            f"{_fmt(all_['pf'])} | {_fmt(all_['expectancy_r'], 3)} | {_fmt(all_['max_dd_r'])} | "
            f"{_pct(all_['max_dd_pct'])} | {all_['losing_streak']} | {_fmt(all_['trades_per_week'], 2)} | "
            f"{_fmt(oos['pf'])} | {_fmt(oos['expectancy_r'], 3)} | {_fmt(oos['max_dd_r_closed'])} |"
        )
    return "\n".join(lines)


def year_table(row):
    lines = ["| Year | Trades | Win rate | PF | E[R] | Net R | Max DD R |", "|---|---:|---:|---:|---:|---:|---:|"]
    for year, block in row["per_year"].items():
        lines.append(f"| {year} | {block['trades']} | {_pct(block['win_rate'])} | {_fmt(block['pf'])} | "
                     f"{_fmt(block['expectancy_r'], 3)} | {_fmt(block['net_r'])} | {_fmt(block['max_dd_r_closed'])} |")
    return "\n".join(lines)


def ab_passes(row, legacy):
    """Limit A+B gate from the proposal: fill rate, profit factor, drawdown not worse than current."""
    fill = row["all"]["limit_fill_rate"]
    return (fill is not None and fill >= 0.50 and (row["all"]["pf"] or 0) >= 1.0
            and row["all"]["max_dd_pct"] <= legacy["all"]["max_dd_pct"] + 1e-12)


def c_passes(row):
    """Proposal gate, plus the owner's out-of-sample requirement."""
    return (row["all"]["pf"] or 0) >= 1.15 and (row["oos"]["pf"] or 0) >= 1.0


def e_passes(row, base):
    """The proposal says to revalidate E and gives no number. The bar used here is PF and OOS PF at or
    above 1.0, and a percent drawdown no worse than the A+B book it is stacked on."""
    return ((row["all"]["pf"] or 0) >= 1.0 and (row["oos"]["pf"] or 0) >= 1.0
            and row["all"]["max_dd_pct"] <= base["all"]["max_dd_pct"] + 1e-12)


def pick_best(results):
    """Highest out-of-sample profit factor among books with PF >= 1 and OOS PF >= 1. C is eligible only
    when it also clears its own 1.15 / 1.0 gate."""
    eligible = []
    for row in results:
        if row["name"] == "0_legacy":
            continue
        pf, oos = row["all"]["pf"], row["oos"]["pf"]
        if pf is None or oos is None or pf < 1.0 or oos < 1.0 or pf == float("inf"):
            continue
        if "c" in row["name"] and not c_passes(row):
            continue
        eligible.append(row)
    if not eligible:
        return None
    return max(eligible, key=lambda row: (row["oos"]["pf"], row["oos"]["expectancy_r"] or -999, -row["all"]["max_dd_pct"]))


def split_at(start):
    """First three years in-sample, the rest out-of-sample. ``start`` is the first 5m bar."""
    return datetime(start.year + 3, start.month, start.day, tzinfo=timezone.utc)
