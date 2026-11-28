"""Capturas con los nueve deals reales. El panel en vivo no importa este módulo.

La curva no se inyecta: sale del P/L realizado, anclada al inicio del Historial (brake_start más el P/L anterior al 18 nov).
"""
from datetime import datetime, timezone
import json

BANNER = "DATOS DE EJEMPLO · no son la cuenta · no usar estas cifras"

_NOW = datetime(2026, 11, 27, 21, 0, tzinfo=timezone.utc)


def _deal(symbol, side, pnl, ticket, lots, entry, exit_price, reason, opened, closed):
    return json.dumps({
        "status": "done", "exited": True, "symbol": symbol, "side": side,
        "position_id": ticket, "volume": lots, "pnl": pnl,
        "entry": entry, "exit_price": exit_price, "exit_reason": reason,
        "opened_at": opened, "exit_at": closed,
    })


def example_view(now=None):
    from ui.liquid.model import build_dashboard
    now = _NOW if now is None else now
    stamp = now.isoformat()
    # Nine real Exness deals. Times below are ET converted to UTC (EST, UTC−5).
    # Cycle card: equity 30644.6 − brake_start 30086 = +558.60. The curve starts at
    # 30085.93 (30086 plus the −0.07 close of 17 nov) so the last point is 30644.60.
    flags = {
        "exness_launch_start": "2026-11-16T21:00:00+00:00",
        "exness_brake_start": "30086",
        "exness_closed_deals": json.dumps([
            {"type": "open", "position_id": "332845", "instrument": "EURUSD", "direction": "buy",
             "volume": "0.01", "price": "1.10152", "profit": {"amount": "0", "currency": "USC"},
             "create_time": "2026-11-17T16:50:47+00:00"},
            {"type": "close", "position_id": "332845", "instrument": "EURUSD", "direction": "buy",
             "volume": "0.01", "price": "1.10145", "profit": {"amount": "-0.07", "currency": "USC"},
             "create_time": "2026-11-17T16:50:50+00:00"},
        ]),
        "exness_brake_day_equity": "30644.6",
        "exness_brake_halt": "0",
        "exness_link": "1",
        "exness_link_at": stamp,
        "exness_live_marks": json.dumps({"at": stamp, "positions": []}),
        "combo_lon_2026-11-27": _deal(
            "USDJPY", "LONG", "181.46", "473902", "1.88", "159.39", "159.544", "london_1100",
            "2026-11-27T07:38:54+00:00", "2026-11-27T16:00:08+00:00"),
        "combo_lon_2026-11-25": _deal(
            "USDJPY", "SHORT", "-20.19", "440549", "1.19", "159.092", "159.119", "hq_manual",
            "2026-11-25T07:30:39+00:00", "2026-11-25T16:07:49+00:00"),
        "combo_lon_2026-11-24": _deal(
            "USDJPY", "LONG", "-139.89", "424576", "0.77", "159.36", "159.071", "london_1100",
            "2026-11-24T07:00:08+00:00", "2026-11-24T16:00:03+00:00"),
        "combo_fix_2026-11-23": _deal(
            "GBPUSD", "LONG", "-13", "415945", "1", "1.30193", "1.3018", "fix_30m",
            "2026-11-23T16:00:09+00:00", "2026-11-23T16:31:11+00:00"),
        "combo_lon_2026-11-23": _deal(
            "USDJPY", "LONG", "380.76", "409759", "1.71", "159.067", "159.422", "london_1100",
            "2026-11-23T08:02:04+00:00", "2026-11-23T16:00:03+00:00"),
        "combo_fix_2026-11-20": _deal(
            "GBPUSD", "LONG", "-9", "382026", "1", "1.31016", "1.31007", "hq_manual",
            "2026-11-20T16:00:13+00:00", "2026-11-20T16:05:17+00:00"),
        "combo_lon_2026-11-20": _deal(
            "USDJPY", "SHORT", "116.42", "375978", "0.6", "159.046", "158.738", "london_1100",
            "2026-11-20T07:32:23+00:00", "2026-11-20T16:00:08+00:00"),
        "combo_lon_2026-11-19": _deal(
            "USDJPY", "LONG", "146.19", "357063", "1.32", "159.628", "159.805", "london_1100",
            "2026-11-19T07:05:08+00:00", "2026-11-19T16:00:07+00:00"),
        "combo_lon_2026-11-18": _deal(
            "USDJPY", "SHORT", "-84.08", "341984", "0.93", "159.15", "159.294", "london_1100",
            "2026-11-18T09:04:25+00:00", "2026-11-18T16:00:13+00:00"),
        "combo_fix_2026-11-27": json.dumps({"status": "skipped", "exit_reason": "window_closed"}),
        "combo_fix_2026-11-26": json.dumps({"status": "done"}),
    }
    snapshot = {"as_of": stamp,
                "account_state": {"balance": "30644.6", "equity": "30644.6", "currency": "USC"}}
    view = build_dashboard(
        env={"AI_FLOOR_BROKER": "exness", "AI_FLOOR_EXNESS_STATE_DB_PATH": "/nonexistent/exness.db",
             "AI_FLOOR_EXNESS_RISK_CAP": "300", "AI_FLOOR_EXNESS_DAILY_LOSS_USD": "600",
             "AI_FLOOR_EXNESS_MAX_DRAWDOWN_USD": "3000",
             "RENDER_INSTANCE_ID": "fase3demo01",
             "AI_FLOOR_EXNESS_REAL_EXECUTION": "1", "AI_FLOOR_EXNESS_REAL_CONFIRM": "EXNESS_REAL_AUTHORIZED"},
        now=now, flags=flags, snapshot=snapshot,
        health={"runtime_heartbeat": stamp, "scheduler": "RUNNING", "status": "ONLINE",
                "last_cycle_status": "COMPLETED", "last_run": stamp},
        errors=[])
    view["banner"] = BANNER
    return view
