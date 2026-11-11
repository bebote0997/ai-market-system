"""Replay harness: scout parity, no lookahead, and the A+B / C / D / E rules."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import unittest

import pandas as pd

from agents.setup_validator import evaluar_setup
from tools.eurusd_replay.scan import Signal, _relax_5m, closed_bounds
from tools.eurusd_replay.scouts import equivalent, frame_from_csv_window
from tools.eurusd_replay.simulate import ab_limit, legacy, run, window_et, with_flags
from core.contracts import AgentMessage
from execution.ema_cross import in_entry_window
from test_ema_cross import CONFIRM, h1_up, m15_long

UTC = timezone.utc
PIP = Decimal("0.0001")
FIXTURE = Path(__file__).resolve().parent / "tools" / "eurusd_replay" / "fixtures"


def _at(year=2024, month=1, day=3, hour=12, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def _msg(agent, timeframe, payload, as_of, run_id="r"):
    return AgentMessage("1.0", run_id, as_of, "EURUSD", timeframe, agent, "OK", evidence=(payload,),
                        data_quality={"closed_bars": 10})


def _structure(timeframe, state, as_of, *, bos=True):
    return _msg("structure", timeframe, {
        "bias": state, "structure_state": state,
        "bos": {"type": "BOS", "direction": state, "broken_level": 1.1, "break_timestamp": "t"} if bos else None,
        "retracement": None,
        "levels": {"support": 1.09000, "resistance": 1.12000},
        "evidence": [{"timestamp": as_of - timedelta(minutes=15), "type": "closed_bar"}],
    }, as_of)


def _liquidity(timeframe, as_of):
    return _msg("liquidity", timeframe, {
        "sweeps": [{"type": "low", "price": 1.1}], "liquidity_above": (), "liquidity_below": (),
    }, as_of)


def _reports(m5_state, as_of):
    return {
        "1h": _structure("1h", "bullish", as_of),
        "15m": _structure("15m", "bullish", as_of),
        "5m": _structure("5m", m5_state, as_of, bos=False),
    }, {tf: _liquidity(tf, as_of) for tf in ("1h", "15m", "5m")}


def _bar(start, *, open_, high, low, bid, ask):
    return {"start": start, "end": start + timedelta(minutes=5),
            "open": open_, "high": high, "low": low, "bid": bid, "ask": ask}


def _signal(at, setup_id="s1", entry="1.10000"):
    return Signal(at, "LONG", entry, "1.09750", "1.10500", "1.09000", "LONG", setup_id, False)


class ScoutParityTests(unittest.TestCase):
    def test_transcription_matches_the_real_scouts_on_the_fixture(self):
        frame = pd.read_csv(FIXTURE / "EURUSD_5m.csv")
        frame["start"] = pd.to_datetime(frame["start"], utc=True)
        frame = frame.set_index("start").sort_index()
        window = frame_from_csv_window(frame.iloc[:120])
        as_of = window.index[-1].to_pydatetime() + timedelta(minutes=5)
        self.assertTrue(equivalent(window, "EURUSD", "5m", as_of))

    def test_closed_slice_excludes_the_bar_that_has_not_finished(self):
        index = pd.date_range("2024-01-01", periods=8, freq="5min", tz="UTC")
        slot = (index[3] + pd.Timedelta(minutes=5)).to_pydatetime()
        _lo, hi = closed_bounds(index, timedelta(minutes=5), slot)
        self.assertEqual(hi, 4)
        self.assertLess(index[hi - 1] + pd.Timedelta(minutes=5), slot + timedelta(seconds=1))
        self.assertGreater(index[hi] + pd.Timedelta(minutes=5), slot)


class AlignmentTests(unittest.TestCase):
    def test_neutral_5m_fails_legacy_and_passes_when_rewritten(self):
        as_of = _at()
        structure, liquidity = _reports("neutral", as_of)
        legacy_view = evaluar_setup(structure, liquidity, None, "EURUSD", "r", as_of)
        self.assertEqual(legacy_view.warnings, ("timeframe_incompatibility",))
        relaxed = _relax_5m(structure)
        self.assertIsNotNone(relaxed)
        passed = evaluar_setup(relaxed, liquidity, None, "EURUSD", "r", as_of)
        self.assertEqual(passed.status, "VALID_SETUP")
        self.assertEqual(passed.side, "LONG")

    def test_opposite_5m_is_not_rewritten(self):
        as_of = _at()
        structure, _liquidity = _reports("bearish", as_of)
        self.assertIsNone(_relax_5m(structure))


class BookTests(unittest.TestCase):
    def _run(self, config, signals, bars, m15=(), h1=()):
        fresh = {bar["end"] for bar in bars}
        return run(config, signals, bars, m15, h1, fresh)

    def test_limit_is_not_filled_on_the_bar_that_places_it(self):
        slot = _at()
        # The whole bar trades through the 5-pip limit. The order is placed after this bar.
        bars = [_bar(slot - timedelta(minutes=5), open_=Decimal("1.10020"), high=Decimal("1.10040"),
                     low=Decimal("1.09900"), bid=Decimal("1.09940"), ask=Decimal("1.09960"))]
        book = self._run(ab_limit(), [_signal(slot)], bars)
        self.assertEqual(book.filled, 0)
        self.assertEqual(book.placed, 1)
        self.assertEqual(book.limit_prices, [Decimal("1.09950")])

    def test_legacy_offset_is_12_5_pips(self):
        slot = _at()
        bars = [_bar(slot - timedelta(minutes=5), open_=Decimal("1.10020"), high=Decimal("1.10040"),
                     low=Decimal("1.10000"), bid=Decimal("1.10010"), ask=Decimal("1.10030"))]
        book = self._run(legacy(), [_signal(slot)], bars)
        self.assertEqual(book.limit_prices, [Decimal("1.09875")])

    def test_fifteen_pip_run_cancels_an_unfilled_limit(self):
        slot = _at()
        first = _bar(slot - timedelta(minutes=5), open_=Decimal("1.10020"), high=Decimal("1.10030"),
                     low=Decimal("1.10000"), bid=Decimal("1.10010"), ask=Decimal("1.10030"))
        # High is 15 pips above the 1.10000 signal and the low never reaches the limit.
        second = _bar(slot, open_=Decimal("1.10040"), high=Decimal("1.10150"), low=Decimal("1.10020"),
                      bid=Decimal("1.10130"), ask=Decimal("1.10150"))
        book = self._run(ab_limit(), [_signal(slot)], [first, second])
        self.assertEqual(book.cancelled_run, 1)
        self.assertEqual(book.filled, 0)
        self.assertIsNone(book.limit)

    def test_long_limit_fills_on_the_ask_and_then_the_target(self):
        slot = _at()
        quiet = _bar(slot - timedelta(minutes=5), open_=Decimal("1.10020"), high=Decimal("1.10030"),
                     low=Decimal("1.10000"), bid=Decimal("1.10010"), ask=Decimal("1.10030"))
        # ask_low = low + spread/2. spread = 0.00020, half = 0.00010, low 1.09940 -> ask_low 1.09950.
        dip = _bar(slot, open_=Decimal("1.10000"), high=Decimal("1.10010"), low=Decimal("1.09940"),
                   bid=Decimal("1.09970"), ask=Decimal("1.09990"))
        target = _bar(slot + timedelta(minutes=5), open_=Decimal("1.10300"), high=Decimal("1.10520"),
                      low=Decimal("1.10280"), bid=Decimal("1.10490"), ask=Decimal("1.10510"))
        book = self._run(ab_limit(), [_signal(slot)], [quiet, dip, target])
        self.assertEqual(book.filled, 1)
        self.assertEqual(len(book.trades), 1)
        self.assertGreater(book.trades[0]["r"], 1.5)
        self.assertEqual(book.trades[0]["reason"], "TARGET")

    def test_same_side_replace_needs_a_new_setup_id(self):
        first, second = _at(minute=0), _at(minute=15)
        bars = [
            _bar(first - timedelta(minutes=5), open_=Decimal("1.10020"), high=Decimal("1.10030"),
                 low=Decimal("1.10000"), bid=Decimal("1.10010"), ask=Decimal("1.10030")),
            _bar(second - timedelta(minutes=5), open_=Decimal("1.10040"), high=Decimal("1.10050"),
                 low=Decimal("1.10020"), bid=Decimal("1.10030"), ask=Decimal("1.10050")),
        ]
        same = self._run(ab_limit(), [_signal(first, "s1"), _signal(second, "s1", "1.10020")], bars)
        self.assertEqual(same.replaced, 0)
        self.assertEqual(same.placed, 1)
        changed = self._run(ab_limit(), [_signal(first, "s1"), _signal(second, "s2", "1.10020")], bars)
        self.assertEqual(changed.replaced, 1)
        self.assertEqual(changed.placed, 2)

    def test_ema_priority_cancels_the_resting_limit(self):
        arm, enter = CONFIRM, CONFIRM + timedelta(minutes=15)
        bars = [
            _bar(arm - timedelta(minutes=5), open_=Decimal("1.10020"), high=Decimal("1.10030"),
                 low=Decimal("1.10000"), bid=Decimal("1.10010"), ask=Decimal("1.10030")),
            _bar(enter - timedelta(minutes=5), open_=Decimal("1.10040"), high=Decimal("1.10050"),
                 low=Decimal("1.10020"), bid=Decimal("1.10030"), ask=Decimal("1.10050")),
        ]
        book = self._run(ab_limit(), [_signal(arm)], bars, m15_long(), h1_up())
        self.assertEqual(book.cancelled_ema, 1)
        self.assertEqual(book.trades[0]["strategy"], "ema")
        self.assertEqual(book.trades[0]["fill"], 1.10050)

    def test_breakeven_arms_on_a_later_close(self):
        # Drive an EMA position, then a close 1.5R in favor, then a bar back through the fill.
        arm, enter = CONFIRM, CONFIRM + timedelta(minutes=15)
        bars = [
            _bar(arm - timedelta(minutes=5), open_=Decimal("1.10020"), high=Decimal("1.10030"),
                 low=Decimal("1.10000"), bid=Decimal("1.10010"), ask=Decimal("1.10030")),
            _bar(enter - timedelta(minutes=5), open_=Decimal("1.10040"), high=Decimal("1.10050"),
                 low=Decimal("1.10020"), bid=Decimal("1.10030"), ask=Decimal("1.10050")),
        ]
        # Fill is the ask 1.10050. SL 20 pips = 0.00200. +1.5R = 0.00300. Close bid at 1.10360.
        favor = _bar(enter, open_=Decimal("1.10340"), high=Decimal("1.10380"), low=Decimal("1.10320"),
                     bid=Decimal("1.10360"), ask=Decimal("1.10380"))
        back = _bar(enter + timedelta(minutes=5), open_=Decimal("1.10040"), high=Decimal("1.10060"),
                    low=Decimal("1.10020"), bid=Decimal("1.10030"), ask=Decimal("1.10050"))
        config = with_flags(ab_limit(), name="d", be=Decimal("1.5"))
        book = self._run(config, [], bars + [favor, back], m15_long(), h1_up())
        self.assertEqual(len(book.trades), 1)
        self.assertGreater(book.trades[0]["r"], -0.2)
        self.assertLess(book.trades[0]["r"], 0.2)

    def test_one_strategy_can_be_turned_off(self):
        arm, enter = CONFIRM, CONFIRM + timedelta(minutes=15)
        bars = [
            _bar(arm - timedelta(minutes=5), open_=Decimal("1.10020"), high=Decimal("1.10030"),
                 low=Decimal("1.10000"), bid=Decimal("1.10010"), ask=Decimal("1.10030")),
            _bar(enter - timedelta(minutes=5), open_=Decimal("1.10040"), high=Decimal("1.10050"),
                 low=Decimal("1.10020"), bid=Decimal("1.10030"), ask=Decimal("1.10050")),
        ]
        ema_only = with_flags(ab_limit(), name="ema", pullback=False)
        ema_book = self._run(ema_only, [_signal(arm)], bars, m15_long(), h1_up())
        self.assertEqual(ema_book.placed, 0)
        self.assertEqual(ema_book.trades[0]["strategy"], "ema")
        pullback_only = with_flags(ab_limit(), name="pullback", ema=False)
        pullback_book = self._run(pullback_only, [_signal(arm)], bars, m15_long(), h1_up())
        self.assertEqual(pullback_book.cancelled_ema, 0)
        self.assertEqual(pullback_book.placed, 1)
        self.assertFalse(any(trade["strategy"] == "ema" for trade in pullback_book.trades))

    def test_et_window_accepts_the_hour_the_utc_window_misses_in_summer(self):
        # 14:30 UTC on 2 July 2026 is 10:30 EDT. Live UTC window is [12, 15), so 14:30 is inside it.
        # 15:30 UTC is 11:30 EDT, outside both. 10:30 UTC is 06:30 EDT (outside E) and outside [12, 15).
        summer = datetime(2026, 7, 2, 15, 30, tzinfo=UTC)
        self.assertFalse(in_entry_window(summer))
        winter_like = datetime(2026, 1, 6, 15, 30, tzinfo=UTC)  # 10:30 EST, outside [12, 15)
        self.assertFalse(in_entry_window(winter_like))
        from tools.eurusd_replay.simulate import _window_et
        self.assertTrue(_window_et(datetime(2026, 1, 6, 15, 30, tzinfo=UTC)))
        self.assertFalse(_window_et(summer))
        self.assertTrue(_window_et(datetime(2026, 7, 2, 14, 30, tzinfo=UTC)))
        later = window_et(7, 12)
        self.assertTrue(later(datetime(2026, 1, 6, 16, 30, tzinfo=UTC)))  # 11:30 EST
        self.assertFalse(later(datetime(2026, 1, 6, 17, 0, tzinfo=UTC)))  # 12:00 EST


if __name__ == "__main__":
    unittest.main()
