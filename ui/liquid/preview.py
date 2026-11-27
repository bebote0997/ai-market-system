"""Cifras inventadas solo para capturas. El panel en vivo no importa este módulo."""
from datetime import datetime, timezone
import json

BANNER = "DATOS DE EJEMPLO · no son la cuenta · no usar estas cifras"

_NOW = datetime(2026, 11, 27, 16, 0, tzinfo=timezone.utc)


def _done(day, symbol, side, pnl, ticket):
    return json.dumps({
        "status": "done", "exited": True, "symbol": symbol, "side": side,
        "position_id": ticket, "volume": "0.40", "pnl": pnl,
        "opened_at": f"2026-11-{day}T12:00:00+00:00",
        "exit_at": f"2026-11-{day}T16:00:00+00:00",
    })


def example_view(now=None):
    from ui.liquid.model import build_dashboard
    now = _NOW if now is None else now
    stamp = now.isoformat()
    # The 25 Nov leg is the live shape: still filled, no ticket, P/L as a deal amount.
    # The snapshot has no positions key. The cycle already published an empty book on the marks flag.
    flags = {
        "exness_launch_start": "2026-11-16T21:00:00+00:00",
        "exness_brake_start": "10000",
        "exness_brake_day_equity": "10558.67",
        "exness_brake_halt": "0",
        "exness_link": "1",
        "exness_link_at": stamp,
        "exness_live_marks": json.dumps({"at": stamp, "positions": []}),
        "combo_lon_2026-11-27": _done("27", "USDJPY", "LONG", "181.46", "t27"),
        "combo_lon_2026-11-25": json.dumps({
            "status": "filled", "exited": False, "symbol": "USDJPY", "side": "LONG",
            "position_id": None, "volume": "0.40", "client_id": "combo-lon-2026-11-25",
            "opened_at": "2026-11-25T07:10:00+00:00", "exit_at": "2026-11-25T16:00:00+00:00",
            "entry": "156.10", "exit_price": "156.02",
            "pnl": {"amount": "-20.19", "currency": "USC"},
        }),
        "combo_lon_2026-11-24": _done("24", "USDJPY", "LONG", "-139.89", "t24"),
        "combo_fix_2026-11-23": _done("23", "GBPUSD", "LONG", "-13.00", "f23"),
        "combo_lon_2026-11-23": _done("23", "USDJPY", "LONG", "380.76", "t23"),
        "combo_fix_2026-11-20": _done("20", "GBPUSD", "LONG", "-9", "f20"),
        "combo_lon_2026-11-20": _done("20", "USDJPY", "SHORT", "116.42", "t20"),
        "combo_lon_2026-11-19": _done("19", "USDJPY", "LONG", "146.19", "t19"),
        "combo_lon_2026-11-18": _done("18", "USDJPY", "SHORT", "-84.08", "t18"),
        "combo_fix_2026-11-27": json.dumps({"status": "skipped", "exit_reason": "window_closed"}),
        "combo_fix_2026-11-26": json.dumps({"status": "done"}),
    }
    curve = [{"at": "2026-11-18T16:00:00+00:00", "equity": "9915.92"},
             {"at": "2026-11-25T16:00:00+00:00", "equity": "10377.21"},
             {"at": "2026-11-27T16:00:00+00:00", "equity": "10558.67"}]
    snapshot = {"as_of": stamp,
                "account_state": {"balance": "10558.67", "equity": "10558.67", "currency": "USC"},
                "curve": curve}
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
