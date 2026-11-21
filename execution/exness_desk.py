"""JSON the dashboard already knows how to read, taken from a book the cycle already fetched.

Nothing here calls the broker. ``Position.stop`` and ``Position.target`` are the protection
``read_state`` already resolved from the linked ``sltp`` order, so they are stored as that order.
"""
from decimal import Decimal


def _text(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def desk_from_book(book, at):
    """Account, positions and the linked protection. Empty when this cycle has no book."""
    if book is None:
        return None
    positions, orders = [], []
    for item in getattr(book, "positions", ()) or ():
        ticket = _text(getattr(item, "ticket", None))
        if ticket is None:
            continue
        positions.append({
            "position_id": ticket,
            "instrument": getattr(item, "symbol", None),
            "side": getattr(item, "side", None),
            "volume": _text(getattr(item, "volume", None)),
            "open_price": _text(getattr(item, "price", None)),
        })
        stop, target = _text(getattr(item, "stop", None)), _text(getattr(item, "target", None))
        if stop is not None or target is not None:
            orders.append({"type": "sltp", "position_id": ticket,
                           "stop_loss_price": stop, "take_profit_price": target})
    money = getattr(book, "balance", None)
    account, point = {}, None
    if money is not None and getattr(money, "balance", None) is not None and getattr(money, "equity", None) is not None:
        account = {"balance": _text(money.balance), "equity": _text(money.equity),
                   "currency": getattr(money, "currency", None) or getattr(book, "currency", None) or ""}
        point = {"at": at.isoformat() if hasattr(at, "isoformat") else at, "equity": account["equity"]}
    return {"account_state": account, "positions": positions, "orders": orders, "point": point}


def attach_desk(payload, desk, prior):
    """Copy the remembered desk into a UI snapshot and extend the equity curve already stored."""
    if not isinstance(payload, dict) or not isinstance(desk, dict):
        return payload
    out = dict(payload)
    for key in ("account_state", "positions", "orders"):
        if key in desk:
            out[key] = desk[key]
    curve = []
    if isinstance(prior, dict) and isinstance(prior.get("curve"), list):
        curve = [point for point in prior["curve"] if isinstance(point, dict) and point.get("equity") is not None]
    point = desk.get("point")
    if isinstance(point, dict) and point.get("equity") is not None and point.get("at"):
        if not curve or curve[-1].get("at") != point.get("at"):
            curve.append({"at": point.get("at"), "equity": point.get("equity")})
    if curve:
        out["curve"] = curve[-400:]
    return out
