"""Read-only data of the animated "Floor" page (``ui/views/floor_view.py``). Pure reads; no writes, no orders.

``build_floor_payload`` only READS what the engine already stores: the PAPER sqlite DB (``mode=ro`` through
``ui.state.dashboard_database``) with the same helpers the dashboard uses (``ui.views.luxury`` / ``ui.views.process``),
and, while broker-demo routing is ON, the existing guarded read-only OANDA demo view (``luxury.demo_api``) fed ONLY
by what the dashboard caches already hold (``ui.floor_cache`` peek readers: the Floor never triggers a broker / network
call; nothing cached => None) and never PAPER numbers in demo mode (header, trades and the analysts' signal outcomes). Every section is built inside a guard: a read error yields ``None`` for that
section, never an exception. Missing data stays ``None`` (the page shows "—" / "sin datos aún"); nothing is invented.

In sample mode only, sections without real data are filled from ``ui.fixtures.floor_example`` and listed in
``example_fields`` (the page badges them "EJEMPLO"). ``real_fields`` lists every field that came from real data.
No account id, token or other secret is ever read into the payload.
"""
from datetime import datetime, timezone
import json
import math

from ui import i18n_es as es

AGENT_IDS = ("Structure", "Liquidity", "Macro", "Risk", "Reviewer", "Conflict", "RiskEng", "Exec")
# Floor character -> persisted agent names (snapshot display name, agent_decisions.agent_name). The engine has no
# separate "risk analyst" AI: Vega (Risk) shows the Trade Reviewer, the AI that reviews the plan and its risk.
AI_AGENTS = {"Structure": ("Structure AI", "structure_ai"), "Liquidity": ("Liquidity AI", "liquidity_ai"),
             "Macro": ("Macro AI", "macro_ai"), "Risk": ("Trade Reviewer", "trade_reviewer_ai"),
             "Reviewer": ("Setup Reviewer", "setup_reviewer_ai")}
SIDE_OF_BIAS = {"BULLISH": "LONG", "BEARISH": "SHORT", "LONG": "LONG", "SHORT": "SHORT"}
# Floor step (8) <- ui.views.process.STEPS evidence (9); "Entry / Exit" is done when either broker step is.
CYCLE_STEPS = (("Datos", ("Data intake",)), ("Análisis", ("Agent outputs",)),
               ("Reviewer", ("Setup reviewer + deterministic setup",)),
               ("Conflict Engine", ("Conflict engine / opposite-setup policy",)), ("Risk Engine", ("Risk Engine",)),
               ("Plan R:R", ("Planner R:R and plan",)), ("Entry / Exit", ("Paper Broker entry", "Paper Broker exit")),
               ("Resultado", ("Final outcome",)))
TIME_KEYS = ('timestamp', 'timestamp_utc', 'as_of', 'opened_at', 'exited_at', 'completed_at', 'started_at',
             'filled_at', 'fill_timestamp')
SYMBOL = "EURUSD"
SYMBOLS = ("EURUSD", "XAUUSD")
TRADE_LIMIT = 10
SIGNAL_LIMIT = 5
PIP = 0.0001  # EURUSD
CURVE_POINTS = 40
FRESH_SECONDS = 120  # a cached demo NAV observed longer ago than this is shown as "(dato antiguo · HH:MM ET)"


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def num(value):
    return float(value) if number(value) else None


def guarded(read, default=None):
    """``read()`` or ``default`` on ANY error: the Floor page is display only and must never break."""
    try:
        return read()
    except Exception:  # noqa: BLE001 - display only: a failed read is "sin datos", never a broken page
        return default


def as_utc(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo is not None else None
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None


def et_text(value, with_date=True):
    """"19 oct 08:01 ET" (or "08:01") for an aware timestamp / ISO string; None when unknown."""
    at = as_utc(value)
    if at is None:
        return None
    local = at.astimezone(es.ET)
    clock = f"{local:%H:%M}"
    return f"{local.day} {es.MONTHS[local.month - 1]} {clock} ET" if with_date else clock


# -- header ------------------------------------------------------------------------------------------------------
def header_from_stats(stats, *, equity, start=None, daily_loss=None, day=None):
    """KPI values from dashboard stats (``ledger_statistics`` / ``demo_statistics`` shape); unknown -> None."""
    stats = stats or {}
    trades = stats.get('trades') if isinstance(stats.get('trades'), int) else None
    avg_r = num(stats.get('avg_r'))
    drawdown = num(stats.get('max_drawdown'))
    start = num(start)
    loss = daily_loss or {}
    fraction = num(loss.get('fraction'))
    known_loss = loss.get('status') not in (None, 'OFF', 'NO_DATA')
    return {"equity": num(equity), "pnl": num(stats.get('total_pnl')),
            "r_total": avg_r * trades if avg_r is not None and trades else None,
            "win_rate": 100.0 * stats['wins'] / trades if trades and isinstance(stats.get('wins'), int) else None,
            "drawdown": drawdown,
            "drawdown_pct": 100.0 * drawdown / start if drawdown is not None and start and start > 0 else None,
            "daily_loss_used_pct": (num(loss.get('used_pct')) * fraction
                                    if known_loss and number(loss.get('used_pct')) and fraction is not None else None),
            "daily_loss_limit_pct": 100.0 * fraction if fraction is not None and loss.get('status') != 'OFF' else None,
            "daily_loss_status": loss.get('status') if loss.get('status') else None,
            "day_text": es.demo_day_text(day) if isinstance(day, int) and not isinstance(day, bool) else None,
            "trades": trades, "equity_stale": None, "equity_as_of": None}


def paper_header(db, now):
    from ui.views.dashboard import payloads
    from ui.views.luxury import ledger_statistics
    stats, curve = ledger_statistics(db, now)
    accounts = payloads(db, 'paper_accounts') if db is not None else []
    equity = accounts[0].get('equity') if accounts else None  # never the account id: only the equity number
    return header_from_stats(stats if stats.get('trades') else {**stats, 'trades': None}, equity=equity), curve


def curve_values(curve):
    """Last ``CURVE_POINTS`` cumulative realised PnL values (oldest first), or None."""
    values = [num(point.get('Cumulative realized PnL')) for point in curve or ()]
    values = [v for v in values if v is not None][-CURVE_POINTS:]
    return values or None


def demo_header(snapshot, day, now):
    """Demo KPIs. NAV freshness is kept: ``equity_stale`` when the cached read is the last good one (``stale``) or was
    observed more than ``FRESH_SECONDS`` ago; ``equity_as_of`` = its observation time. Stale without a known
    observation time => equity None ("—"), never shown as current."""
    stats = snapshot.get('stats')
    known = stats is not None
    header = header_from_stats(stats if known else {}, equity=snapshot.get('nav'),
                               start=(snapshot.get('capital') or {}).get('start') if known else None,
                               daily_loss=snapshot.get('daily_loss'), day=day)
    at = as_utc(snapshot.get('as_of'))
    stale = snapshot.get('stale') is True or at is None or (now - at).total_seconds() > FRESH_SECONDS
    if header['equity'] is None:
        stale = None
    elif stale and at is None:
        header['equity'], stale = None, None
    header['equity_stale'] = stale
    header['equity_as_of'] = et_text(at, with_date=False) if header['equity'] is not None else None
    return header


# -- trades ------------------------------------------------------------------------------------------------------
def plan_values(plan):
    plan = plan or {}
    entry = next((num(plan.get(k)) for k in ('entry', 'planned_entry') if number(plan.get(k))), None)
    return {"entry": entry, "stop": num(plan.get('stop')), "target": num(plan.get('target')),
            "risk_reward": num(plan.get('risk_reward')), "risk": num(plan.get('capital_at_risk'))}


def risk_amount(db, run_id):
    if db is None or not run_id:
        return None
    row = db.execute("SELECT d.capital_at_risk FROM risk_decisions d JOIN runs r ON r.slot_key=d.slot_key "
                     "WHERE r.run_id=? LIMIT 1", (run_id,)).fetchone()
    return num(row[0]) if row else None


def trade_row(row, plan, *, status, run_id=None, risk=None):
    """Expected (persisted plan) vs actual (fill) of one trade; every unknown value None."""
    side = row.get('side')
    expected = plan_values(plan)
    fill = num(row.get('entry_price'))
    diff = fill - expected['entry'] if fill is not None and expected['entry'] is not None else None
    against = None if diff is None or side not in ('LONG', 'SHORT') else (diff > 0 if side == 'LONG' else diff < 0)
    symbol = row.get('symbol')
    stop = next((num(row.get(k)) for k in ('position_initial_stop', 'initial_stop', 'stop', 'position_stop')
                 if number(row.get(k))), None)
    target = next((num(row.get(k)) for k in ('target', 'position_target') if number(row.get(k))), None)
    units = next((num(row.get(k)) for k in ('units', 'quantity') if number(row.get(k))), None)
    trade_id = row.get('trade_id') or row.get('position_id')
    return {"id": str(trade_id)[:12] if trade_id else None, "symbol": symbol, "side": side, "status": status,
            "opened_at": et_text(row.get('opened_at')), "closed_at": et_text(row.get('exited_at')), "units": units,
            "entry_expected": expected['entry'], "entry_actual": fill,
            "slippage_pips": abs(diff) / PIP if diff is not None and symbol == 'EURUSD' else None,
            "slippage_price": abs(diff) if diff is not None else None, "slippage_against": against,
            "sl_expected": expected['stop'], "sl_actual": stop, "tp_expected": expected['target'], "tp_actual": target,
            "risk_expected": expected['risk'] if expected['risk'] is not None else risk,
            "r_planned": expected['risk_reward'],
            "r_realized": num(row.get('realized_r')),
            "pnl": num(row.get('net_pnl') if status == 'CLOSED' else row.get('unrealized_pnl')),
            "exit_price": num(row.get('exit_price')), "last_price": num(row.get('last_price')),
            "close_reason": es.close_reason_text(row.get('close_reason') or row.get('reason'))
            if status == 'CLOSED' and (row.get('close_reason') or row.get('reason')) else None,
            "_run_id": run_id or row.get('run_id')}


def linked(db, row):
    from ui.views.luxury import linked_plan
    return guarded(lambda: linked_plan(db, row), {}) if db is not None else {}


def paper_trades(db):
    from ui.views.luxury import closed_trade_rows, open_position_rows
    opened = [r for r in open_position_rows(db) if r.get('symbol') == SYMBOL]
    closed = [r for r in closed_trade_rows(db) if r.get('symbol') == SYMBOL]
    rows = [trade_row(r, linked(db, r), status='OPEN', risk=guarded(lambda r=r: risk_amount(db, r.get('run_id'))))
            for r in opened]
    rows += [trade_row(r, linked(db, r), status='CLOSED', risk=guarded(lambda r=r: risk_amount(db, r.get('run_id'))))
             for r in closed]
    return rows[:TRADE_LIMIT]


def demo_run_id(row):
    client = row.get('client_id')
    return client[4:] if isinstance(client, str) and client.startswith('aif-') else None


def demo_trades(db, snapshot):
    if snapshot.get('stats') is None:
        return None
    rows = []
    for status, source in (('OPEN', snapshot.get('open_rows') or ()),
                           ('CLOSED', list(reversed(snapshot.get('closed_rows') or ())))):
        for row in source:
            if row.get('symbol') != SYMBOL:
                continue
            run_id = demo_run_id(row)
            plan = linked(db, {'run_id': run_id}) if run_id else {}
            rows.append(trade_row(row, plan, status=status, run_id=run_id,
                                  risk=guarded(lambda run_id=run_id: risk_amount(db, run_id))))
    return rows[:TRADE_LIMIT]


# -- agents ------------------------------------------------------------------------------------------------------
def latest_snapshot(db):
    from ui.views.dashboard import payloads
    return next((s for s in payloads(db, 'ui_snapshots') if s.get('symbol') == SYMBOL), None)


def agent_decision_rows(db):
    if db is None or not db.execute("SELECT 1 FROM sqlite_master WHERE name='agent_decisions'").fetchone():
        return []
    return [dict(r) for r in db.execute(
        "SELECT a.agent_name, a.as_of, a.status, a.bias, a.confidence, a.reasoning_summary, r.run_id "
        "FROM agent_decisions a JOIN runs r ON r.slot_key=a.slot_key WHERE r.symbol=? "
        "ORDER BY a.as_of DESC, a.rowid DESC LIMIT 400", (SYMBOL,))]


def short_status(status, bias=None):
    parts = [es.status_text(status)] if status else []
    if bias and bias not in ('UNKNOWN',):
        parts.append(es.status_text(bias))
    return ' · '.join(parts) or None


def agent_view(status=None, bias=None, confidence=None, analysis=None, as_of=None):
    return {"status": short_status(status, bias), "bias": SIDE_OF_BIAS.get(bias, bias) if bias else None,
            "confidence": num(confidence), "analysis": analysis or None, "as_of": et_text(as_of),
            "hit_rate": None, "signals": None}


def closed_by_run(db):
    from ui.views.luxury import closed_trade_rows
    return {r['run_id']: r for r in closed_trade_rows(db) if r.get('run_id') and r.get('symbol') == SYMBOL}


def demo_closed_by_run(snapshot):
    """Closed OANDA demo trades by run (client id ``aif-<run_id>``); None when the demo trades are unknown."""
    if not isinstance(snapshot, dict) or snapshot.get('stats') is None:
        return None
    return {run_id: r for r in snapshot.get('closed_rows') or ()
            for run_id in [demo_run_id(r)] if run_id and r.get('symbol') == SYMBOL}


def signals_and_hit_rate(decisions, closed):
    """Last 5 directional signals of one agent with the outcome of the trade of the SAME run (exact run_id link).
    Hit rate = share of linked closed trades where the signal's side matched the trade and it won (or the signal
    opposed it and it lost); a breakeven trade (PnL 0) is neutral and excluded. None when no signal is linked to a
    closed non-breakeven trade. ``closed`` None = outcomes unknown
    (demo trades not readable): every outcome and the hit rate are None."""
    known = closed is not None
    closed = closed or {}
    signals, hits, linked_count = [], 0, 0
    for row in decisions:
        side = SIDE_OF_BIAS.get(row.get('bias'))
        if side is None:
            continue
        trade = closed.get(row.get('run_id'))
        pnl = num(trade.get('net_pnl')) if trade else None
        if trade is not None and pnl is not None and pnl != 0 and trade.get('side') in ('LONG', 'SHORT'):
            linked_count += 1
            hits += (side == trade['side']) == (pnl > 0)
        if len(signals) < SIGNAL_LIMIT:
            signals.append({"at": et_text(row.get('as_of')), "signal": f"{SYMBOL} {side.title()}",
                            "outcome": (es.close_reason_text(trade.get('reason')) if trade.get('reason') else
                                        'Cerrada') if trade else 'sin operación' if known else None,
                            "r": num(trade.get('realized_r')) if trade else None})
    return (signals or None), (100.0 * hits / linked_count if linked_count else None)


def ai_agents(db, snapshot, closed):
    """``closed``: closed trades by run of the SELECTED source (PAPER ledger, or the OANDA demo trades while routing
    is ON: never PAPER outcomes in demo metrics); None = unknown."""
    agents = {}
    decisions = guarded(lambda: agent_decision_rows(db), [])
    persisted = {a.get('name') or a.get('agent_name'): a for a in (snapshot or {}).get('agents') or ()}
    for agent_id, names in AI_AGENTS.items():
        mine = [d for d in decisions if d.get('agent_name') in names]
        latest = next((persisted[n] for n in names if n in persisted), None)
        if latest is not None:
            view = agent_view(latest.get('status'), latest.get('bias'), latest.get('confidence'),
                              latest.get('reasoning_summary'), latest.get('timestamp') or (snapshot or {}).get('as_of'))
        elif mine:
            d = mine[0]
            view = agent_view(d.get('status'), d.get('bias'), d.get('confidence'), d.get('reasoning_summary'),
                              d.get('as_of'))
        else:
            continue
        view["signals"], view["hit_rate"] = signals_and_hit_rate(mine, closed)
        agents[agent_id] = view
    return agents


def latest_run(db):
    from ui.views.dashboard import records
    runs = [r for r in records(db, 'runs') if r.get('symbol') == SYMBOL]
    runs.sort(key=lambda r: (r.get('as_of') or '', r.get('started_at') or ''), reverse=True)
    return runs[0] if runs else None


def run_risk(db, run):
    if run is None or db is None:
        return None
    row = db.execute("SELECT status, reason, risk_fraction, capital_at_risk FROM risk_decisions WHERE slot_key=?",
                     (run['slot_key'],)).fetchone()
    return dict(row) if row else None


def engine_agents(db, snapshot, run, evidence):
    agents = {}
    conflict = (evidence or {}).get("Conflict engine / opposite-setup policy") or []
    if conflict:
        event = conflict[0]
        body = event.get('payload') if isinstance(event.get('payload'), dict) else {}
        agents["Conflict"] = agent_view(event.get('event_type'), analysis=body.get('reason') or body.get('decision'),
                                        as_of=event.get('timestamp'))
    risk = guarded(lambda: run_risk(db, run))
    if risk:
        parts = [risk.get('reason')] if risk.get('reason') else []
        if number(risk.get('risk_fraction')):
            parts.append(f"riesgo {100 * risk['risk_fraction']:.2f} %")
        if number(risk.get('capital_at_risk')):
            parts.append(f"capital en riesgo {es.money(risk['capital_at_risk'])}")
        agents["RiskEng"] = agent_view(risk.get('status'), analysis=' · '.join(parts) or None,
                                       as_of=run.get('completed_at') or run.get('as_of'))
    elif snapshot and snapshot.get('risk_status') and snapshot.get('risk_status') != 'NOT CALLED':
        decision = snapshot.get('risk_decision') or {}
        agents["RiskEng"] = agent_view(snapshot['risk_status'], analysis=decision.get('reason'),
                                       as_of=snapshot.get('as_of'))
    return agents


def exec_agent(trades, source):
    if not trades:
        return None
    trade = trades[0]
    venue = 'OANDA demo' if source == 'OANDA_DEMO' else 'PAPER'
    parts = [f"{es.status_text(trade['side'])} {SYMBOL}"]
    if trade['units'] is not None:
        parts.append(f"{trade['units']:,.0f} unidades")
    if trade['entry_actual'] is not None:
        parts.append(f"@ {trade['entry_actual']:.5f}")
    if trade['entry_expected'] is not None:
        parts.append(f"(esperado {trade['entry_expected']:.5f})")
    view = agent_view(analysis=' '.join(parts) + f" · {venue}", as_of=None)
    view["status"] = 'Posición abierta' if trade['status'] == 'OPEN' else 'Última operación cerrada'
    view["bias"] = trade['side']
    view["as_of"] = trade['opened_at'] if trade['status'] == 'OPEN' else trade['closed_at']
    closed = [t for t in trades if t['status'] == 'CLOSED' and t['pnl'] is not None]
    view["hit_rate"] = 100.0 * sum(t['pnl'] > 0 for t in closed) / len(closed) if closed else None
    view["signals"] = [{"at": t['opened_at'], "signal": f"{SYMBOL} {(t['side'] or '—').title()}",
                        "outcome": 'Abierta' if t['status'] == 'OPEN' else (t['close_reason'] or 'Cerrada'),
                        "r": t['r_realized']} for t in trades[:SIGNAL_LIMIT]]
    return view


# -- cycle -------------------------------------------------------------------------------------------------------
def first_time(rows):
    for row in rows:
        value = {**row, **row['payload']} if isinstance(row.get('payload'), dict) else row
        at = next((value.get(k) for k in TIME_KEYS if value.get(k)), None)
        if at:
            return at
    return None


def cycle_view(run, evidence, trades):
    """The 8 floor steps of the latest EURUSD run: done = the step has persisted evidence (Resultado = the run
    completed); ``at`` = the first persisted timestamp of that step (never inferred)."""
    evidence = evidence or {}
    routed = {t.get('_run_id') for t in trades or ()}
    steps = []
    for label, sources in CYCLE_STEPS:
        rows = [r for s in sources for r in evidence.get(s) or ()]
        if label == 'Resultado':
            done = bool(run.get('completed_at')) or run.get('status') in ('COMPLETED', 'FAILED')
            at = run.get('completed_at')
        else:
            done = bool(rows) or (label == 'Entry / Exit' and run.get('run_id') in routed)
            at = first_time(rows)
        steps.append({"done": done, "at": et_text(at, with_date=False)})
    return {"as_of": et_text(run.get('as_of')), "status": es.status_text(run.get('final_status') or run.get('status')),
            "steps": steps}


def journal_feed(db):
    from ui.views.dashboard import records
    lines = []
    for row in records(db, 'journal'):
        if row.get('symbol') not in SYMBOLS:
            continue
        at = et_text(row.get('timestamp'), with_date=False)
        lines.append(f"{at or '—'}  {row['symbol']}  {es.status_text(row.get('event_type'))}")
        if len(lines) == 5:
            break
    return lines or None


def symbol_states(enabled):
    from runtime.symbol_pause import is_paused
    return {s: 'PAUSED' if is_paused(s) else 'ACTIVE' if s in enabled else 'DISABLED' for s in SYMBOLS}


def market_lines(snapshot):
    if not snapshot:
        return None
    lines = [f"{SYMBOL} · {es.status_text(snapshot.get('state'))}" if snapshot.get('state') else None,
             f"Datos: {es.status_text(snapshot.get('freshness'))}" if snapshot.get('freshness') else None,
             f"Actualizado {et_text(snapshot.get('as_of'))}" if et_text(snapshot.get('as_of')) else None]
    lines = [line for line in lines if line]
    return {"lines": lines} if lines else None


# -- assembly ----------------------------------------------------------------------------------------------------
def real_field_paths(payload):
    paths = []
    for key in ('header', 'symbols'):
        paths += [f"{key}.{k}" for k, v in (payload.get(key) or {}).items() if v is not None]
    paths += [f"agents.{k}" for k, v in (payload.get('agents') or {}).items() if v is not None]
    paths += [key for key in ('trades', 'cycle', 'feed', 'market', 'curve') if payload.get(key)]
    return paths


def apply_examples(payload):
    """Sample mode only: fill sections WITHOUT real data from the prototype example; returns the filled paths."""
    from ui.fixtures import floor_example as ex
    filled = []
    header = payload['header'] or dict.fromkeys(ex.HEADER)
    for key, value in ex.HEADER.items():
        if header.get(key) is None:
            header[key] = value
            filled.append(f"header.{key}")
    payload['header'] = header
    for key, example in (('trades', ex.TRADES), ('cycle', ex.CYCLE), ('feed', ex.FEED), ('market', ex.MARKET),
                         ('curve', ex.CURVE)):
        if not payload.get(key):
            payload[key] = json.loads(json.dumps(example))
            filled.append(key)
    for agent_id in AGENT_IDS:
        if payload['agents'].get(agent_id) is None:
            payload['agents'][agent_id] = json.loads(json.dumps(ex.AGENTS[agent_id]))
            filled.append(f"agents.{agent_id}")
    symbols = payload['symbols'] or {}
    for key, value in ex.SYMBOLS.items():
        if symbols.get(key) is None:
            symbols[key] = value
            filled.append(f"symbols.{key}")
    payload['symbols'] = symbols
    return filled


def build_floor_payload(*, sample_mode=False, now=None, db=None, demo=None, enabled_symbols=None):
    """Floor payload (JSON-serialisable dict). ``db``: a read-only sqlite connection or None (no DB yet); ``demo``:
    ``luxury.demo_api()``-shaped mapping or None (routing OFF => PAPER numbers). Never raises."""
    now = now or datetime.now(timezone.utc)
    payload = {"version": 1, "sample_mode": bool(sample_mode), "generated_at": et_text(now),
               "source": None, "header": None, "trades": None, "agents": {}, "cycle": None, "symbols": None,
               "feed": None, "market": None, "curve": None}
    snapshot = demo_snap = None
    if demo is not None:
        payload["source"] = "OANDA_DEMO"
        from ui.floor_cache import peek_account_view, peek_demo_trades
        # peek-only readers: the stored cache contents, never a refresh (no broker / network call from the Floor)
        demo_snap = guarded(lambda: demo['snapshot'](now, trades_reader=peek_demo_trades,
                                                     account_reader=peek_account_view))
        from ui.views.luxury import demo_day
        day = guarded(lambda: demo_day(demo, now))
        if isinstance(demo_snap, dict):
            payload["header"] = guarded(lambda: demo_header(demo_snap, day, now))
            payload["curve"] = guarded(lambda: curve_values(demo_snap.get('curve')))
            payload["trades"] = guarded(lambda: demo_trades(db, demo_snap))
    elif db is not None:
        payload["source"] = "PAPER"
        payload["header"], curve = guarded(lambda: paper_header(db, now), (None, None))
        payload["curve"] = guarded(lambda: curve_values(curve))
        payload["trades"] = guarded(lambda: paper_trades(db))
    if db is not None:
        snapshot = guarded(lambda: latest_snapshot(db))
        run = guarded(lambda: latest_run(db))
        from ui.views.process import cycle_evidence
        evidence = guarded(lambda: cycle_evidence(db, run)[0]) if run else None
        closed = (guarded(lambda: demo_closed_by_run(demo_snap)) if demo is not None
                  else guarded(lambda: closed_by_run(db)))
        payload["agents"].update(guarded(lambda: ai_agents(db, snapshot, closed), {}))
        payload["agents"].update(guarded(lambda: engine_agents(db, snapshot, run, evidence), {}))
        payload["cycle"] = guarded(lambda: cycle_view(run, evidence, payload["trades"])) if run else None
        payload["feed"] = guarded(lambda: journal_feed(db))
        payload["market"] = guarded(lambda: market_lines(snapshot))
    executed = guarded(lambda: exec_agent(payload["trades"], payload["source"]))
    if executed:
        payload["agents"]["Exec"] = executed
    for agent_id in AGENT_IDS:
        payload["agents"].setdefault(agent_id, None)
    if enabled_symbols is not None:
        payload["symbols"] = guarded(lambda: symbol_states(tuple(enabled_symbols)))
    for trade in payload["trades"] or ():
        trade.pop('_run_id', None)
    payload["real_fields"] = real_field_paths(payload)
    payload["example_fields"] = apply_examples(payload) if sample_mode else []
    return payload


def payload_json(payload):
    """JSON safe to embed inside ``<script type="application/json">``: every ``<`` becomes ``\\u003c`` (only possible
    inside JSON strings), so neither ``</script>`` nor ``<!--`` can appear; ``JSON.parse`` restores the text."""
    return json.dumps(payload, ensure_ascii=False, allow_nan=False, default=str).replace("<", "\\u003c")
