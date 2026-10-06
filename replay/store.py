"""Separate Replay Store (DEC-4.3): Phase 2 Market Evidence format in its own SQLite file.

The file name must contain ``replay``; the trading DB (and its hard links) is refused by ``EvidenceStore``.
Replay history is never mixed with the trading DB or the runtime evidence sidecar.
"""
from pathlib import Path

from data.market_evidence import MarketEvidenceEngine
from storage.evidence_store import EvidenceStore

REPLAY_SYMBOLS = ("XAUUSD", "EURUSD")


class ReplayStoreError(ValueError):
    pass


def open_replay_store(path, *, readonly=False):
    path = Path(path)
    if "replay" not in path.name.lower():
        raise ReplayStoreError("replay store file name must contain 'replay'")
    return EvidenceStore(path, readonly=readonly)


def replay_engine(store):
    return MarketEvidenceEngine(store, enabled_symbols=REPLAY_SYMBOLS)
