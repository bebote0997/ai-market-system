"""Replay-only speed-up of the production scouts' pure helper loops (REPLAY ONLY; production code is untouched).

``agents.structure_agent._swings`` / ``_break_of_structure`` and ``agents.liquidity_agent._confirmed_pivots`` walk
a 500-bar window with pandas scalar access (``.iloc[i]`` / ``.loc[ts, col]``), ~2.5 s per floor run, which makes a
5-year signal generation (~70k slots) take many hours. The functions below compute the SAME outputs (same dicts /
tuples, same order, same Python types; strict comparisons, ties excluded exactly as in production) with numpy on the
same closed frame. ``patched()`` swaps them in for the duration of a ``with`` block (the replay signal workers); the
equality is asserted on real and synthetic windows by ``test_fast_scouts.py``. Behaviour, inputs and the window
(bars closed by the slot) are unchanged, so there is no new lookahead surface.
"""
from contextlib import ExitStack
from unittest.mock import patch

import numpy as np


def _pivot_positions(values):
    """Positions i in [1, n-2] with values[i] strictly above both neighbours (production loop order)."""
    v = np.asarray(values)
    if len(v) < 3:
        return np.array([], dtype=int)
    mid = v[1:-1]
    return np.flatnonzero((mid > v[:-2]) & (mid > v[2:])) + 1


def swings(frame):
    """== ``agents.structure_agent._swings``."""
    index = frame.index
    high = frame["High"].to_numpy()
    low = frame["Low"].to_numpy()
    highs = [{"timestamp": index[i + 1], "pivot_timestamp": index[i], "price": float(high[i])}
             for i in _pivot_positions(high)]
    lows = [{"timestamp": index[i + 1], "pivot_timestamp": index[i], "price": float(low[i])}
            for i in _pivot_positions(-low)]
    return highs, lows


def break_of_structure(frame, highs, lows):
    """== ``agents.structure_agent._break_of_structure`` (first close beyond each swing after its confirmation;
    the latest such break wins, first-listed on a tie)."""
    index = frame.index
    close = frame["Close"].to_numpy()
    candidates = []
    for swings_, direction in ((highs, "bullish"), (lows, "bearish")):
        for swing in reversed(swings_):
            start = int(index.searchsorted(swing["timestamp"], side="right"))
            after = close[start:]
            hits = np.flatnonzero(after > swing["price"] if direction == "bullish" else after < swing["price"])
            if not len(hits):
                continue
            j = start + int(hits[0])
            candidates.append({
                "type": "BOS",
                "direction": direction,
                "broken_level": swing["price"],
                "swing_timestamp": swing["pivot_timestamp"],
                "confirmation_timestamp": swing["timestamp"],
                "break_timestamp": index[j],
                "break_close": float(close[j]),
            })
    if not candidates:
        return None
    return max(candidates, key=lambda item: item["break_timestamp"])


def confirmed_pivots(frame):
    """== ``agents.liquidity_agent._confirmed_pivots``."""
    index = frame.index
    high = frame["High"].to_numpy()
    low = frame["Low"].to_numpy()
    return ([(float(high[i]), index[i]) for i in _pivot_positions(high)],
            [(float(low[i]), index[i]) for i in _pivot_positions(-low)])


def patched():
    """Context manager: the production module attributes point at the numpy equivalents inside the block."""
    import agents.liquidity_agent as liquidity_agent
    import agents.structure_agent as structure_agent
    stack = ExitStack()
    stack.enter_context(patch.object(structure_agent, "_swings", swings))
    stack.enter_context(patch.object(structure_agent, "_break_of_structure", break_of_structure))
    stack.enter_context(patch.object(liquidity_agent, "_confirmed_pivots", confirmed_pivots))
    return stack
