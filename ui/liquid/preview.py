"""Invented figures for screenshots only. The live panel never imports this module."""
from datetime import datetime, timezone
import json

BANNER = "DATOS DE EJEMPLO · no son la cuenta · no usar estas cifras"

_NOW = datetime(2026, 11, 20, 16, 0, tzinfo=timezone.utc)


def example_view(now=None):
    from ui.liquid.model import build_dashboard
    now = _NOW if now is None else now
    flags = {
        "exness_launch_start": "2026-11-16T21:00:00+00:00",
        "exness_brake_start": "10000",
        "exness_brake_day_equity": "10000",
        "exness_brake_halt": "0",
        "combo_lon_2026-11-20": json.dumps({
            "status": "filled", "symbol": "USDJPY", "side": "SHORT", "position_id": "pos-ejemplo",
            "volume": "0.10", "opened_at": "2026-11-20T12:10:00+00:00", "exit_at": "2026-11-20T16:00:00+00:00",
            "exited": False,
        }),
        "combo_fix_2026-11-20": json.dumps({"status": "pending"}),
        "combo_lon_2026-11-18": json.dumps({
            "status": "done", "symbol": "USDJPY", "side": "LONG", "position_id": "pos-viejo",
            "opened_at": "2026-11-18T12:05:00+00:00", "exit_at": "2026-11-18T16:00:00+00:00", "exited": True,
        }),
    }
    book = {"positions": [{
        "position_id": "pos-ejemplo", "instrument": "USDJPY", "side": "sell", "volume": "0.10",
        "open_price": "150.100", "current_price": "150.020",
        "stop_loss_price": "9.99", "take_profit_price": "8.88", "profit": "-20",
    }], "orders": [{
        "type": "sltp", "position_id": "pos-ejemplo", "stop_loss_price": "150.400", "take_profit_price": "149.500",
    }, {
        "type": "limit", "position_id": "pos-ejemplo", "stop_loss_price": "1", "take_profit_price": "2",
    }]}
    curve = [{"at": "2026-11-18T16:00:00+00:00", "equity": "10000"},
             {"at": "2026-11-19T16:00:00+00:00", "equity": "10030"},
             {"at": "2026-11-20T16:00:00+00:00", "equity": "9980"}]
    view = build_dashboard(
        env={"AI_FLOOR_BROKER": "exness", "AI_FLOOR_EXNESS_STATE_DB_PATH": "/nonexistent/exness.db"},
        now=now, flags=flags,
        account_reader=lambda _env: {"balance": "10020", "equity": "9980", "currency": "USC"},
        book=book, curve=curve,
        health={"runtime_heartbeat": "2026-11-20T15:59:00+00:00", "scheduler": "RUNNING", "status": "ONLINE",
                "last_cycle_status": "COMPLETED", "last_run": "2026-11-20T15:55:00+00:00"},
        errors=[])
    view["banner"] = BANNER
    return view
