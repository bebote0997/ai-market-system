"""Floor session in front of ``ExnessBroker``. Built only when ``AI_FLOOR_BROKER`` is exactly ``exness``.

Reuses the Friday entry ban, optional Friday pre-flatten, the one-position rule, a re-read inside the adapter,
and the dollar brakes. The OANDA pullback manager is not reimplemented here.
"""
from datetime import datetime, timezone
from decimal import Decimal
import logging
from zoneinfo import ZoneInfo

from execution.broker_port import APPROVED
from execution.exness_broker import broker_from_env
from execution.friday_close import OPEN, friday_window, minutes_from_env
from execution.exness_m5 import BAR, FEED_SYMBOLS, M5Feed, floor_m5_utc, m5_feed_enabled
from execution.session_combo import SessionCombo, combo_enabled, range_window
from execution.trade_duration import entries_blocked


LOG = logging.getLogger("ai_floor.exness")
PLAN_READY = "PLAN_READY"
NY = ZoneInfo("America/New_York")


def _dec(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        out = value if isinstance(value, Decimal) else Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None
    return out if out.is_finite() and out > 0 else None


class ExnessSession:
    def __init__(self, broker, *, env, clock=None):
        self.broker = broker
        self._env = env
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.on_alert = None
        self._combo = SessionCombo(broker) if combo_enabled(env) else None
        self._feed = M5Feed(broker._transport.quotes) if m5_feed_enabled(env) else None
        self._quote_feed = None
        self._runner = None
        self.last_manage_at = None
        logging.getLogger("ai_floor.session_combo").info(
            "component=session_combo event=%s", "SESSION_COMBO_ON" if self._combo is not None else "SESSION_COMBO_OFF")
        logging.getLogger("ai_floor.exness_m5").info(
            "component=exness_m5 event=%s", "M5_FEED_ON" if self._feed is not None else "M5_FEED_OFF")
        self._open_live_quotes()

    @property
    def on_alert(self):
        return self._on_alert

    @on_alert.setter
    def on_alert(self, value):
        self._on_alert = value
        self.broker._on_alert = value

    def __repr__(self):
        return f"ExnessSession(broker={self.broker!r})"

    def _alert(self, kind, detail):
        if self.on_alert is None:
            return
        try:
            self.on_alert(kind, dict(detail or {}))
        except Exception as exc:  # noqa: BLE001
            LOG.warning("component=exness event=ALERT_FAILED error_type=%s", type(exc).__name__)

    def _open_live_quotes(self):
        """Open the ticks socket in this process when the live Exness transport is in use.

        A fake transport does not open a socket. The M5 feed flag is not required: quotes have to stay
        fresh for any entry or close. The flag still decides whether ``_feed`` is the session feed.
        """
        from execution.exness_m5 import LiveTickRunner, TickStream, connect_ticks
        from execution.exness_stream import ws_url
        from execution.exness_transport import RestExnessTransport
        transport = self.broker._transport
        if not isinstance(transport, RestExnessTransport):
            return
        feed = self._feed if self._feed is not None else M5Feed(transport.quotes)
        if self._feed is None:
            self._quote_feed = feed
        client = transport._client
        stream = TickStream(feed)

        def handshake():
            point = client.discover()
            path = stream.handshake_path(client.account_id)
            return ws_url(point, path), client.signed_headers("GET", path)

        self._runner = LiveTickRunner(stream, connect_ticks, handshake, clock=self._clock)
        self._runner.start()

    def _history(self, at):
        start = range_window(at.astimezone(NY).date())[0]
        end = floor_m5_utc(at) + BAR
        bars = []
        for symbol in FEED_SYMBOLS:
            try:
                bars.extend(self.broker.candles(symbol, "M5", start, end) or ())
            except Exception as exc:  # noqa: BLE001 - the next manage retries; the message can contain the host
                LOG.warning("component=exness_m5 event=M5_GAP symbol=%s reason=candles_unavailable error_type=%s",
                            symbol, type(exc).__name__)
        return tuple(bars)

    def _bars(self, at):
        feed = self._feed if self._feed is not None else self._quote_feed
        if feed is not None:
            feed.close_elapsed(at)
        if self._combo is not None:
            history = self._history(at)
            if feed is not None:
                feed.insert_bars(history)
            elif history:
                return history
        if feed is not None:
            return feed.bars()
        return None

    def manage(self, at, bars=None):
        self.last_manage_at = at
        cache = getattr(self.broker, "cache_account_reads", None)
        if cache is not None:
            cache(True)
        try:
            return self._manage(at, bars)
        finally:
            if cache is not None:
                cache(False)

    def _manage(self, at, bars):
        try:
            if bars is None:
                bars = self._bars(at)
            fresh = self.broker.note_link()
            brake = self.broker.evaluate_brakes(at)
            if brake.flatten:
                self._alert("EXNESS_BRAKE_HALT", {"reason": brake.reason})
            minutes = minutes_from_env(self._env)
            friday = entries_blocked(at) or (minutes is not None and friday_window(at, minutes) != OPEN)
            if not brake.flatten and not friday:
                self._run_combo(at, bars)
                return {}
            if not fresh:
                self._run_combo(at, bars)
                return {"_": "quote_stale"}
            results = {}
            for position in self.broker.open_trades():
                closed = self.broker.close_market(position.ticket, reason=brake.reason or "friday_close",
                                                   client_id=f"flatten-{position.ticket}")
                results[position.ticket] = closed.status
            self._run_combo(at, bars)
            return results
        except Exception as exc:  # noqa: BLE001
            LOG.warning("component=exness event=MANAGE_FAILED error_type=%s", type(exc).__name__)
            return {"_": type(exc).__name__}

    def _run_combo(self, at, bars):
        if self._combo is None:
            return
        try:
            self._combo.tick(at, bars or ())
        except Exception as exc:  # noqa: BLE001 - the next tick retries; paper is unchanged
            LOG.warning("component=session_combo event=TICK_FAILED error_type=%s", type(exc).__name__)

    def _candidate(self, context, status):
        if status != PLAN_READY or not isinstance(context, dict):
            return ("IGNORED", None)
        symbol, run_id = context.get("symbol"), context.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            return ("NOT_ROUTED", "run_id_missing")
        if symbol != "EURUSD":
            return ("NOT_ROUTED", "symbol_not_supported")
        if context.get("eligible") is not True:
            return ("NOT_ROUTED", "not_eligible")
        deterministic, ai = context.get("deterministic"), context.get("ai")
        decision = getattr(deterministic, "risk_decision", None)
        if (getattr(deterministic, "final_status", None) != PLAN_READY or getattr(ai, "final_status", None) != PLAN_READY
                or getattr(decision, "status", None) != APPROVED
                or getattr(getattr(ai, "risk_decision", None), "status", None) != APPROVED):
            return ("NOT_ROUTED", "risk_not_approved")
        entry, stop, target = (_dec(getattr(decision, "entry", None)), _dec(getattr(decision, "stop", None)),
                               _dec(getattr(decision, "target", None)))
        side = getattr(decision, "side", None)
        if entry is None or stop is None or target is None or side not in ("LONG", "SHORT"):
            return ("NOT_ROUTED", "plan_decision_mismatch")
        if not (stop < entry < target if side == "LONG" else target < entry < stop):
            return ("NOT_ROUTED", "invalid_plan_geometry")
        return decision, entry, stop, target, side, symbol, run_id

    def on_cycle(self, context, status, at, h1_bars=None, m15_bars=None, h1_closes=None):
        del h1_bars, m15_bars, h1_closes
        try:
            self.manage(at)
            candidate = self._candidate(context, status)
            if len(candidate) == 2:
                return candidate
            # Engine plans, including EMA-only practice, never become Exness orders.
            # The session combo places its own orders from manage() via open_market.
            del candidate
            LOG.info("component=exness event=ORDER_REFUSED reason=session_combo_only")
            return ("NOT_ROUTED", "session_combo_only")
        except Exception as exc:  # noqa: BLE001
            LOG.warning("component=exness event=CYCLE_FAILED error_type=%s", type(exc).__name__)
            return ("NOT_ROUTED", type(exc).__name__)

    def process_operator_requests(self, at):
        return self.manage(at)

    def consume_provider_transition(self, store, config, env, at):
        return None

    def sync_transition_fence(self, store):
        return None

    def drawdown_basis(self):
        return {"source": "EXNESS", "error": "exness_basis_not_oanda"}


def session_from_env(env, *, transport=None, clock=None):
    return ExnessSession(broker_from_env(env, transport=transport, clock=clock), env=env, clock=clock)
