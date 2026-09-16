"""Market-data dependency boundary for operational runs."""
from typing import Protocol


class MarketDataProvider(Protocol):
    def load_snapshot(self, symbol: str, as_of):
        """Return closed, sourced 1h/15m/5m pandas frames for one symbol."""
