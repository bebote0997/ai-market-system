"""Round 9 (REPLAY ONLY): one-shot NAS100USD holdout of the round-8b near-misses, on the unchanged round-8b engine.

Loads only the committed freeze ``replay/round9_frozen.json`` (fail closed: the working file must equal the copy in
HEAD, the variants must exist in ``replay/round8.py`` and the engine's git blob must equal the recorded one). Runs
the PRIMARY and SECONDARY variants once on HOLDOUT 2025-01-01..2026-10-19 (orders only on session days inside the
window; earlier M1 data is causal history for the features), under the main cost and C_OANDA.

Run: ``python -m replay.round9`` (after the declaration in ``report_round9.md`` section 1 is committed). Data and
outputs stay outside the repository.
"""
import argparse
import json
import os
import subprocess

import numpy as np
import pandas as pd

from replay import round8 as r8

ROOT = r8.ROOT
FROZEN_NAME = "round9_frozen.json"
FROZEN_REPO = "replay/" + FROZEN_NAME


def git(*args):
    out = subprocess.run(["git", "-C", r8.REPO, *args], capture_output=True, text=True, check=False)
    return out.stdout if out.returncode == 0 else None


def load_frozen(root, committed=None, engine_blob=None):
    """Fails closed unless the freeze equals the committed copy and the engine is the recorded one."""
    with open(os.path.join(root, FROZEN_NAME), encoding="utf-8") as handle:
        text = handle.read()
    if committed is None:
        committed = git("show", f"HEAD:{FROZEN_REPO}")
    if committed is None:
        raise RuntimeError(f"cannot verify the frozen file: {FROZEN_REPO} not in HEAD")
    if committed != text:
        raise RuntimeError(f"frozen file differs from the committed {FROZEN_REPO}")
    payload = json.loads(text)
    if engine_blob is None:
        engine_blob = (git("hash-object", os.path.join(r8.REPO, payload["engine"])) or "").strip()
    if engine_blob != payload["engine_blob"]:
        raise RuntimeError("engine replay/round8.py differs from the frozen blob")
    known = r8.variants(payload["symbol"])
    for name in [payload["primary"], *payload["secondary"]]:
        if name not in known:
            raise ValueError(f"unknown frozen variant {name}")
    return payload


def quarter(day):
    ts = pd.Timestamp(day)
    return f"{ts.year}Q{(ts.month - 1) // 3 + 1}"


def quarters(a, b):
    return sorted({quarter(d) for d in pd.date_range(a, pd.Timestamp(b) - pd.Timedelta(days=1), freq="D")})


def quarter_r(trades, a, b):
    out = {q: 0.0 for q in quarters(a, b)}
    for t in trades:
        out[quarter(t["day"])] += t["r"]
    return {q: round(v, 2) for q, v in out.items()}


def cost_r(trades, fills):
    """Mean cost per trade in R: half spread at both legs, entry slippage, slippage on SL/TIME exits,
    commission; divided by the trade's risk."""
    if not trades:
        return 0.0
    out = []
    for t in trades:
        k, j = t["k"], t["j"]
        entry = fills.hs[k] + fills.slip[k]  # market / stop entries only (checked in run)
        exit_ = fills.hs[j] + (fills.slip[j] if t["reason"] in ("SL", "TIME") else 0.0)
        out.append((entry + exit_ + fills.comm) / t["risk"])
    return round(float(np.mean(out)), 4)


def evaluate(trades, a, b, bar_strict, bar_info, fills):
    mt = r8.metrics(trades, a, b)
    mt.pop("yearly_avg_r", None)
    qr = quarter_r(trades, a, b)
    pos_q = sum(1 for v in qr.values() if v > 0)
    core = mt["avg_r"] >= bar_strict["avg_r"] and mt["pf"] > bar_strict["pf"] and mt["mtm_dd_r"] < bar_strict["dd"]
    mt.update({"quarter_r": qr, "pos_quarters": pos_q, "n_quarters": len(qr), "cost_r_per_trade": cost_r(trades, fills),
               "pass_strict": bool(core and mt["tr_mo"] >= bar_strict["tr_mo"]
                                   and pos_q >= bar_strict["min_pos_quarters"]),
               "pass_r7_info": bool(mt["avg_r"] >= bar_info["avg_r"] and mt["pf"] > bar_info["pf"]
                                    and mt["mtm_dd_r"] < bar_info["dd"] and mt["tr_mo"] >= bar_info["tr_mo"])})
    return mt


def run(root, frozen, log=print):
    sym = frozen["symbol"]
    a, b = frozen["holdout"]
    models = [frozen["main_cost"], *frozen["reference_costs"]]
    m = r8.market(root, sym, end=b)
    days = [d for d in m.days if pd.Timestamp(a).date() <= d < pd.Timestamp(b).date()]
    fills = {mod: r8.Fills(r8.COSTS[mod], sym, m.spread) for mod in models}
    results, trades_main = {}, {}
    for name in [frozen["primary"], *frozen["secondary"]]:
        orders = r8.build_orders(m, r8.variants(sym)[name], a, b)
        if any(lg.kind == "limit" for od in orders for lg in od.legs):
            raise RuntimeError(f"{name}: limit entries not handled by cost_r")  # NR4 / NR7 / ORB5 use stop / market
        res = {}
        for mod in models:
            tr = r8.run_orders(m, fills[mod], orders)
            res[mod] = evaluate(tr, a, b, frozen["bar_strict"], frozen["bar_info_r7"], fills[mod])
            if mod == frozen["main_cost"]:
                trades_main[name] = tr
        results[name] = res
        mt = res[frozen["main_cost"]]
        log(f"{name}: n={mt['trades']} tr/mo={mt['tr_mo']} avg={mt['avg_r']:+.3f} PF={mt['pf']} DD={mt['mtm_dd_r']} "
            f"posQ={mt['pos_quarters']}/{mt['n_quarters']} strict={mt['pass_strict']} r7={mt['pass_r7_info']}")
    window = {"first_day": str(days[0]), "last_day": str(days[-1]), "session_days": len(days),
              "last_bar_utc": str(pd.Timestamp(int(m.t[-1]), tz="UTC"))}
    return results, trades_main, window


def buy_and_hold(root, symbol, a, b):
    df = r8.load_m1(os.path.join(root, "data_m1", f"{symbol}_1m.csv"))
    df = df[(df.index >= pd.Timestamp(a, tz=r8.NY)) & (df.index < pd.Timestamp(b, tz=r8.NY))]
    c = df["Close"]
    bh = r8.buy_and_hold(root, symbol, a, b)
    return bh, c.resample("1D").last().dropna() / df["Open"].iloc[0] * 100 - 100


def chart(root, frozen, trades_main, bh_curve, results):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    ink, muted, surface = "#0b0b0b", "#52514e", "#fcfcfb"
    colors = ("#2a78d6", "#eb6834", "#1f9e6e")
    a, _ = frozen["holdout"]
    fig, (ax, bx) = plt.subplots(2, 1, figsize=(11, 7.2), dpi=120, facecolor=surface, sharex=True,
                                 gridspec_kw={"height_ratios": (2.2, 1)})
    for name, col in zip(trades_main, colors):
        tr = trades_main[name]
        xs = [pd.Timestamp(a, tz=r8.NY)] + [pd.Timestamp(t["exit_t"], tz="UTC") for t in tr]
        ys = np.concatenate([[0.0], np.cumsum([t["r"] for t in tr])])
        mt = results[name][frozen["main_cost"]]
        role = "primary" if name == frozen["primary"] else "secondary"
        ax.step(xs, ys, where="post", color=col, linewidth=2.0 if role == "primary" else 1.4,
                label=f"{name} ({role}): {mt['trades']} tr, {mt['avg_r']:+.3f}R, PF {mt['pf']}, DD {mt['mtm_dd_r']}R")
        ax.annotate(f"{ys[-1]:+.1f}R", (xs[-1], ys[-1]), xytext=(4, 0), textcoords="offset points", va="center",
                    fontsize=9, color=col)
    ax.set_ylabel(f"Cumulative R ({frozen['main_cost']})", color=muted)
    ax.legend(loc="upper left", frameon=False, fontsize=8.5, labelcolor=ink)
    bx.plot(bh_curve.index, bh_curve.to_numpy(), color=muted, linewidth=1.4)
    bx.set_ylabel("NAS100 buy-and-hold %", color=muted)
    for z in (ax, bx):
        z.set_facecolor(surface)
        z.axhline(0, color=muted, linewidth=0.8)
        z.tick_params(colors=muted, labelsize=8)
        z.grid(axis="y", color="#e8e8e6", linewidth=0.8)
        for side in ("top", "right"):
            z.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            z.spines[side].set_color("#c9c8c4")
    fig.suptitle(f"Round 9: NAS100USD one-shot holdout {a}..{frozen['holdout_last_day']} (frozen round-8b rules)",
                 x=0.01, ha="left", color=ink, fontsize=11)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(os.path.join(root, "round9_equity_r.png"), facecolor=surface)
    plt.close(fig)


def fmt(name, mod, mt):
    q = " ".join(f"{k}:{v:+.1f}" for k, v in mt["quarter_r"].items())
    return (f"| {name} | {mod} | {mt['trades']} | {mt['tr_mo']} | {mt['avg_r']:+.3f} | {mt['total_r']:+.1f} | "
            f"{mt['pf']} | {mt['mtm_dd_r']} | {mt['win_pct']} | {mt['cost_r_per_trade']:.3f} | "
            f"{mt['pos_quarters']}/{mt['n_quarters']} | {mt['exits']} | {q} |")


def report(root, frozen, results, bh, window, verdict):
    main = frozen["main_cost"]
    rows = "\n".join(fmt(n, mod, res[mod]) for n, res in results.items() for mod in res)
    bars = "\n".join(f"| {n} | {'PASS' if res[main]['pass_strict'] else 'FAIL'} | "
                     f"{'pass' if res[main]['pass_r7_info'] else 'fail'} |" for n, res in results.items())
    text = f"""## 2. Holdout result (run once, {frozen['holdout'][0]}..{frozen['holdout_last_day']})

Window: {window['session_days']} session days {window['first_day']}..{window['last_day']} (last M1 bar
{window['last_bar_utc']}). Cost R/trade = spread + slippage + commission paid, / risk.

| variant | cost | trades | tr/mo | avg R | total R | PF | MTM DD R | win % | cost R/trade | pos. quarters | exits | quarter R |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
{rows}

| variant ({main}) | strict bar (decides for PRIMARY) | round-7 bar (info) |
|---|---|---|
{bars}

Buy-and-hold NAS100USD over the same window: {bh['pct']:+.1f} % (max DD {bh['max_dd_pct']} %).
Chart: `round9_equity_r.png`. Multiple testing: 3 new tests, cumulative {frozen['tests_before']} ->
{frozen['tests_after']}.

**Verdict: {verdict}** (PRIMARY {frozen['primary']}, strict bar, {main})."""
    with open(os.path.join(root, "report_round9.md"), "a", encoding="utf-8") as handle:
        handle.write("\n" + text.rstrip() + "\n")


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--root", default=ROOT)
    args = p.parse_args(argv)
    frozen = load_frozen(args.root)
    out_path = os.path.join(args.root, "round9", f"{frozen['symbol']}.json")
    if os.path.exists(out_path):
        raise RuntimeError(f"{out_path} exists: the round-9 holdout is run once")
    results, trades_main, window = run(args.root, frozen)
    a, b = frozen["holdout"]
    bh, bh_curve = buy_and_hold(args.root, frozen["symbol"], a, b)
    verdict = "PASS" if results[frozen["primary"]][frozen["main_cost"]]["pass_strict"] else "FAIL"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump({"frozen": frozen, "window": window, "results": results, "buy_hold": bh, "verdict": verdict,
                   "trades_main": trades_main}, handle, indent=1, default=float)
    chart(args.root, frozen, trades_main, bh_curve, results)
    report(args.root, frozen, results, bh, window, verdict)
    print(f"verdict {verdict}; buy-and-hold {bh}")


if __name__ == "__main__":
    main()
