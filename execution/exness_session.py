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
from execution.exness_client import safe_detail
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
        self._history_backfill_done = False
        self._brake_logged = None
        self._backfill_history()
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
                LOG.warning("component=exness_m5 event=M5_GAP symbol=%s reason=candles_unavailable error_type=%s "
                            "detail=%s", symbol, type(exc).__name__, safe_detail(exc))
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
            try:
                self._remember_desk(at)
            except Exception as exc:  # noqa: BLE001 - the account cache must turn off even if the copy fails
                LOG.warning("component=exness event=DESK_REMEMBER_FAILED error_type=%s detail=%s",
                            type(exc).__name__, safe_detail(exc))
            try:
                self._publish_state(at)
            except Exception as exc:  # noqa: BLE001 - el estado del panel no puede cortar el ciclo
                LOG.warning("component=exness event=BRAKE_STATE_FAILED error_type=%s", type(exc).__name__)
            if cache is not None:
                cache(False)

    def refresh_chart(self, at):
        """One closed M5 GET, at most every 30 s, on the cycle's client. No order is sent.

        ``AI_FLOOR_CHART_CACHE=0`` skips the read. Unset leaves it on and does not change ``manage``.
        """
        from execution.chart_cache import ChartCache, chart_cache_enabled
        if not chart_cache_enabled(self._env):
            return None
        cache = getattr(self, "_chart_cache", None)
        if cache is None:
            cache = self._chart_cache = ChartCache()
        transport = getattr(self.broker, "_transport", None)
        client = getattr(transport, "_client", None)
        quotes = getattr(transport, "quotes", None)
        try:
            cache.refresh(at, client=client, quotes=quotes, desk=getattr(self, "desk", None))
        except Exception as exc:  # noqa: BLE001 - a chart read must not stop the poll
            LOG.warning("component=exness event=CHART_CACHE_FAILED error_type=%s detail=%s",
                        type(exc).__name__, safe_detail(exc))
            return None
        store = getattr(getattr(self.broker, "_brakes", None), "_store", None)
        if store is not None and hasattr(store, "set_flag"):
            try:
                cache.publish(store)
            except Exception as exc:  # noqa: BLE001
                LOG.warning("component=exness event=CHART_PUBLISH_FAILED error_type=%s detail=%s",
                            type(exc).__name__, safe_detail(exc))
        return cache

    def _remember_desk(self, at):
        """Keep the book ``note_link`` already cached. Reading the cache is not a new broker call."""
        book = getattr(self.broker, "_read_cache", None)
        if book is None:
            return
        from execution.exness_desk import desk_from_book
        desk = desk_from_book(book, at)
        if desk is not None:
            self._stamp_marks(desk, book)
            self.desk = desk

    def _publish_state(self, at):
        """BRAKE_STATE, enlace y marcas, a partir del libro que este ciclo ya leyó. Sin otra llamada al broker."""
        book = getattr(self.broker, "_read_cache", None)
        brakes = getattr(self.broker, "_brakes", None)
        store = getattr(brakes, "_store", None)
        if book is None or store is None:
            return
        from execution.exness_brakes import publish_runtime_state
        from execution.exness_broker import _stale_seconds
        money = getattr(book, "balance", None)
        equity = None if money is None else money.equity
        currency = None if money is None else money.currency
        limit = _stale_seconds(self._env)
        fresh = bool(limit) and book.quotes.fresh(at, limit)
        limits = brakes._limits(currency)
        daily = None if limits is None else limits[0]
        logged = self._brake_logged
        do_log = logged is None or (at - logged).total_seconds() >= 30
        publish_runtime_state(store, equity=equity, currency=currency, fresh=fresh, at=at,
                              marks=self._mark_rows(book), daily_limit=daily, log=do_log)
        if do_log:
            self._brake_logged = at

    def _stamp_marks(self, desk, book):
        by_id = {row["position_id"]: row for row in self._mark_rows(book)}
        for item in desk.get("positions") or []:
            found = by_id.get(item.get("position_id"))
            if not found:
                continue
            if found.get("current") is not None:
                item["current_price"] = found["current"]
            if found.get("profit") is not None:
                item["profit"] = found["profit"]

    def _mark_rows(self, book):
        from execution.exness_sizing import floating_pnl
        rows = []
        for item in getattr(book, "positions", ()) or ():
            slot = book.quotes.quote(getattr(item, "symbol", None))
            bid = None if slot is None else slot.bid
            ask = None if slot is None else slot.ask
            mark = bid if getattr(item, "side", None) == "LONG" else ask
            if mark is None:
                mark = bid if bid is not None else ask
            profit = floating_pnl(getattr(item, "side", None), getattr(item, "price", None), mark,
                                  getattr(item, "volume", None), book.specs.get(getattr(item, "symbol", None)))
            rows.append({"position_id": item.ticket,
                         "current": None if mark is None else format(mark, "f"),
                         "profit": None if profit is None else format(profit, "f")})
        return rows

    def _backfill_history(self):
        """GET-only fill of missing historial fields. A failure does not stop the session."""
        if self._combo is None or self._history_backfill_done:
            return
        try:
            from execution.combo_history import backfill_combo_history
            result = backfill_combo_history(self.broker, now=self._clock())
            if result.get("pending") == 0:
                self._history_backfill_done = True
        except Exception as exc:  # noqa: BLE001
            LOG.warning("component=session_combo event=HISTORY_BACKFILL status=failed error_type=%s",
                        type(exc).__name__)

    def _manage(self, at, bars):
        try:
            self._backfill_history()
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
            LOG.warning("component=exness event=MANAGE_FAILED error_type=%s detail=%s", type(exc).__name__,
                        safe_detail(exc))
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
