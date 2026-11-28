"""Capturas con los nueve deals reales. El panel en vivo no importa este módulo.

Los precios que no están en un deal leído no se rellenan: salen «sin datos».
La curva no se inyecta: sale del P/L realizado y del balance de inicio del ciclo.
"""
from datetime import datetime, timezone
import json

BANNER = "DATOS DE EJEMPLO · no son la cuenta · no usar estas cifras"

_NOW = datetime(2026, 11, 27, 16, 0, tzinfo=timezone.utc)


def _deal(symbol, side, pnl, ticket, lots, entry, exit_price, reason, opened, closed):
    return json.dumps({
        "status": "done", "exited": True, "symbol": symbol, "side": side,
        "position_id": ticket, "volume": lots, "pnl": pnl,
        "entry": entry, "exit_price": exit_price, "exit_reason": reason,
        "opened_at": opened, "exit_at": closed,
    })


def _pnl(symbol, pnl, ticket):
    """Ticket y P/L reales. Sin precio ni reloj que no hayamos leído."""
    return json.dumps({
        "status": "done", "exited": True, "symbol": symbol,
        "position_id": ticket, "pnl": pnl,
    })


def example_view(now=None):
    from ui.liquid.model import build_dashboard
    now = _NOW if now is None else now
    stamp = now.isoformat()
    # Nine real deals. 18–20 Nov and 440549 keep the prices already read from Exness.
    # 409759, 415945, 424576 and 473902: ticket and P/L only. No made-up entry or exit.
    # The snapshot has no curve: Rendimiento is the cumulative realized P/L.
    # Account equity 10558.60 is the cycle card (equity − 10000 = +558.60), not the deal sum.
    flags = {
        "exness_launch_start": "2026-11-16T21:00:00+00:00",
        "exness_brake_start": "10000",
        "exness_brake_day_equity": "10558.60",
        "exness_brake_halt": "0",
        "exness_link": "1",
        "exness_link_at": stamp,
        "exness_live_marks": json.dumps({"at": stamp, "positions": []}),
        "combo_lon_2026-11-27": _pnl("USDJPY", "181.46", "473902"),
        "combo_lon_2026-11-25": json.dumps({
            "status": "done", "exited": True, "symbol": "USDJPY", "side": "SHORT",
            "position_id": "440549", "volume": "1.19", "client_id": "combo-lon-2026-11-25",
            "opened_at": "2026-11-25T07:30:39+00:00", "exit_at": "2026-11-25T16:07:49+00:00",
            "entry": "159.092", "exit_price": "159.119", "exit_reason": "hq_manual",
            "pnl": "-20.19",
        }),
        "combo_lon_2026-11-24": _pnl("USDJPY", "-139.89", "424576"),
        "combo_fix_2026-11-23": _pnl("GBPUSD", "-13", "415945"),
        "combo_lon_2026-11-23": _pnl("USDJPY", "380.76", "409759"),
        "combo_fix_2026-11-20": _deal(
            "GBPUSD", "LONG", "-9", "382026", "1", "1.31016", "1.31007", "hq_manual",
            "2026-11-20T16:00:13.745398+00:00", "2026-11-20T16:05:17.993306+00:00"),
        "combo_lon_2026-11-20": _deal(
            "USDJPY", "SHORT", "116.42", "375978", "0.6", "159.046", "158.738", "london_1100",
            "2026-11-20T07:32:23.593013+00:00", "2026-11-20T16:00:08.435986+00:00"),
        "combo_lon_2026-11-19": _deal(
            "USDJPY", "LONG", "146.19", "357063", "1.32", "159.628", "159.805", "london_1100",
            "2026-11-19T07:05:08.69103+00:00", "2026-11-19T16:00:07.564338+00:00"),
        "combo_lon_2026-11-18": _deal(
            "USDJPY", "SHORT", "-84.08", "341984", "0.93", "159.15", "159.294", "london_1100",
            "2026-11-18T09:04:25.892074+00:00", "2026-11-18T16:00:13.631297+00:00"),
        "combo_fix_2026-11-27": json.dumps({"status": "skipped", "exit_reason": "window_closed"}),
        "combo_fix_2026-11-26": json.dumps({"status": "done"}),
    }
    snapshot = {"as_of": stamp,
                "account_state": {"balance": "10558.60", "equity": "10558.60", "currency": "USC"}}
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
