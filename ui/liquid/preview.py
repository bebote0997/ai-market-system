"""Cifras inventadas solo para capturas. El panel en vivo no importa este módulo."""
from datetime import datetime, timezone
import json

BANNER = "DATOS DE EJEMPLO · no son la cuenta · no usar estas cifras"

_NOW = datetime(2026, 11, 27, 16, 0, tzinfo=timezone.utc)


def example_view(now=None):
    from ui.liquid.model import build_dashboard
    now = _NOW if now is None else now
    stamp = now.isoformat()
    flags = {
        "exness_launch_start": "2026-11-16T21:00:00+00:00",
        "exness_brake_start": "10000",
        "exness_brake_day_equity": "10020.19",
        "exness_brake_halt": "0",
        "exness_link": "1",
        "exness_link_at": stamp,
        "combo_lon_2026-11-25": json.dumps({
            "status": "done", "symbol": "USDJPY", "side": "SHORT", "position_id": "pos-ejemplo",
            "volume": "0.10", "opened_at": "2026-11-25T12:10:00+00:00",
            "exit_at": "2026-11-25T16:00:00+00:00", "exited": True,
            "entry": "150.250", "exit_price": "150.452", "exit_reason": "london_1100",
            "pnl": "-20.19",
        }),
        "combo_fix_2026-11-27": json.dumps({
            "status": "skipped", "exit_reason": "window_closed",
        }),
    }
    curve = [{"at": "2026-11-25T16:00:00+00:00", "equity": "10020.19"},
             {"at": "2026-11-26T16:00:00+00:00", "equity": "10000"},
             {"at": "2026-11-27T16:00:00+00:00", "equity": "10000"}]
    snapshot = {"as_of": stamp,
                "account_state": {"balance": "10000", "equity": "10000", "currency": "USC"},
                "positions": [], "orders": [], "curve": curve, "floating_pnl": None}
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
