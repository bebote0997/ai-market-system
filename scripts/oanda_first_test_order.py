"""ONE explicit OANDA PRACTICE test order (EUR_USD). NOT run by the agent that wrote it: run only after the owner's
explicit approval (docs/oanda_adapter.md, "Primera orden de prueba").

Default = DRY-RUN: reads pricing and prints the order JSON preview (EUR_USD, 1 unit, SL 20 pips / TP 40 pips from the
current ask (BUY) or bid (SELL), clientExtensions id ``aif-test-<utc timestamp>``); nothing is sent.

    python scripts/oanda_first_test_order.py                     # dry-run preview (read-only GET pricing)
    python scripts/oanda_first_test_order.py --status            # read-only summary / openTrades / pricing
    python scripts/oanda_first_test_order.py --send --confirm I-APPROVE-PRACTICE-TEST-ORDER [--units N] [--side SELL]
    python scripts/oanda_first_test_order.py --close TRADE_ID --confirm I-APPROVE-PRACTICE-TEST-ORDER

``--send`` needs the exact confirm string AND the env guards (AI_FLOOR_BROKER_DEMO_ENABLED=1,
AI_FLOOR_BROKER_DEMO_PROVIDER=oanda, registry ``broker_demo`` switch ON, no emergency; all enforced by OandaBroker)
and a tradeable market. Units: default 1, hard cap 100. Practice host only (hard-coded in the client). Output never
contains the token or the account id (masked only). Nothing runs at import.
"""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import sys

if __package__ in (None, ""):  # `python scripts/oanda_first_test_order.py`: make the repository importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from execution.broker_port import ACCEPTED, BrokerError, OrderPlan, RiskApproval  # noqa: E402

CONFIRM = "I-APPROVE-PRACTICE-TEST-ORDER"
MAX_UNITS = 100
PIP = Decimal("0.0001")
SL_PIPS, TP_PIPS = 20, 40
SYMBOL = "EURUSD"


def parse_args(argv):
    parser = argparse.ArgumentParser(description="ONE OANDA practice test order (dry-run by default).")
    parser.add_argument("--send", action="store_true", help="really send (needs --confirm and the env guards)")
    parser.add_argument("--confirm", default=None, help=f"exact string {CONFIRM}")
    parser.add_argument("--units", type=int, default=1, help=f"1..{MAX_UNITS} (default 1)")
    parser.add_argument("--side", choices=("BUY", "SELL"), default="BUY")
    parser.add_argument("--close", metavar="TRADE_ID", default=None, help="close this test trade (needs --confirm)")
    parser.add_argument("--status", action="store_true", help="read-only summary / openTrades / pricing")
    return parser.parse_args(argv)


class PracticeReader:
    """Read-only view for --status and the dry-run preview: token + practice account only (no switch needed). Uses the
    practice-only OandaClient (live host refused) and only its GET endpoints."""

    def __init__(self, env, transport=None):
        from execution.oanda_broker import ACCOUNT_ENV, TOKEN_ENV
        from execution.oanda_client import OandaClient
        token = env.get(TOKEN_ENV)
        account = env.get(ACCOUNT_ENV)
        account = account.strip() if isinstance(account, str) else ""  # missing -> OandaClient BrokerGuardError
        self._client = OandaClient(token.strip() if isinstance(token, str) else "", account, transport=transport)
        self.account_masked = self._client.account_masked

    def get_quote(self, symbol=SYMBOL):
        from execution.oanda_broker import _quote
        return _quote(self._client.get_pricing("EUR_USD"), "EUR_USD")

    def open_trades(self):
        from execution.oanda_broker import _trades
        return tuple(_trades(self._client.get_open_trades()))

    def get_balance(self):
        from execution.broker_port import BrokerMalformedResponse
        account = self._client.get_summary().get("account")
        if not isinstance(account, dict):
            raise BrokerMalformedResponse("summary answer without 'account'")
        return {key: account.get(key) for key in ("balance", "NAV", "marginUsed", "currency")}


def make_broker(env, transport=None, registry=None):
    from execution.account_registry import AccountRegistry
    from execution.broker_config import load_broker_config
    from execution.oanda_broker import OandaBroker
    if registry is None:
        registry = AccountRegistry(load_broker_config(env).state_db_path, env=env)
    return OandaBroker(env=env, mode="broker_demo", registry=registry, transport=transport)


def test_plan(side, units, bid, ask, now):
    """OrderPlan + matching RiskApproval for the test order: SL/TP 20/40 pips from the executable side."""
    long = side == "BUY"
    entry = ask if long else bid
    stop = entry - SL_PIPS * PIP if long else entry + SL_PIPS * PIP
    target = entry + TP_PIPS * PIP if long else entry - TP_PIPS * PIP
    client_id = "aif-test-" + now.strftime("%Y%m%dT%H%M%SZ")
    plan = OrderPlan(SYMBOL, "LONG" if long else "SHORT", Decimal(units), entry, stop, target, client_id=client_id,
                     source_run_id=client_id)
    approval = RiskApproval("APPROVED", plan.source_run_id, plan.symbol, plan.side, plan.quantity, plan.planned_entry,
                            plan.stop, plan.target)
    return plan, approval


def _print(out, payload):
    out.write(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")


def run_status(reader, out):
    balance = reader.get_balance()
    tradeable, bid, ask = reader.get_quote(SYMBOL)
    trades = [{"trade_id": t.trade_id, "instrument": t.instrument, "units": t.units, "price": t.price,
               "stop": t.stop, "target": t.target} for t in reader.open_trades()]
    _print(out, {"account": reader.account_masked, "balance": balance["balance"], "nav": balance["NAV"],
                 "margin_used": balance["marginUsed"], "currency": balance["currency"], "open_trades": trades,
                 "pricing": {"instrument": "EUR_USD", "tradeable": tradeable, "bid": bid, "ask": ask}})
    return 0


def main(argv=None, env=None, transport=None, *, out=None, now=None, registry=None):
    """Exit codes: 0 ok, 2 refused (usage/confirm/units/guards/market), 1 broker error or non-accepted result."""
    from execution.broker_demo_router import build_order_preview
    out = out or sys.stdout
    env = os.environ if env is None else env
    try:
        args = parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    if not 1 <= args.units <= MAX_UNITS:
        out.write(f"REFUSED: --units must be between 1 and {MAX_UNITS}\n")
        return 2
    if (args.send or args.close) and args.confirm != CONFIRM:
        out.write(f"REFUSED: --send/--close require --confirm {CONFIRM}\n")
        return 2
    if args.send and args.close:
        out.write("REFUSED: use --send or --close, not both\n")
        return 2
    try:  # Writes need every OandaBroker guard; read-only modes only need a token and a practice account.
        broker = make_broker(env, transport, registry) if (args.send or args.close) else PracticeReader(env, transport)
    except BrokerError as exc:
        out.write(f"REFUSED: guards not satisfied ({type(exc).__name__}: {exc})\n")
        return 2
    try:
        if args.status:
            return run_status(broker, out)
        if args.close:
            result = broker.close_market(args.close, reason="TEST_ORDER_CLOSE", client_id=f"aif-test-close-{args.close}")
            _print(out, {"close": result.status, "reason": result.reason, "broker_ref": result.broker_ref})
            return 0 if result.status == ACCEPTED else 1
        tradeable, bid, ask = broker.get_quote(SYMBOL)
        plan, approval = test_plan(args.side, args.units, bid, ask, now or datetime.now(timezone.utc))
        preview = build_order_preview(plan, args.units)
        if not args.send:
            _print(out, {"mode": "DRY_RUN (nothing sent)", "tradeable": tradeable, "order": preview})
            return 0
        if not tradeable:
            out.write("REFUSED: EUR_USD is not tradeable now (market closed?)\n")
            return 2
        result = broker.submit_plan(plan, approval)
        _print(out, {"submit": result.status, "reason": result.reason, "trade_id": result.broker_ref,
                     "client_id": plan.client_id, "order": preview})
        return 0 if result.status == ACCEPTED else 1
    except BrokerError as exc:  # Messages are already scrubbed by the client (no token / account id).
        out.write(f"ERROR: {type(exc).__name__}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
