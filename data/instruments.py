from core.contracts import InstrumentSpec
from core.timeframes import AUTHORIZED_TIMEFRAMES, DEFAULT_TIMEZONE


INSTRUMENTS = {
    "XAUUSD": InstrumentSpec(
        symbol="XAUUSD", asset_class="metals", provider_symbol=None,
        timezone=DEFAULT_TIMEZONE, price_increment=None,
        quantity_increment=None, contract_multiplier=None,
        allowed_timeframes=AUTHORIZED_TIMEFRAMES,
    ),
    "NAS100": InstrumentSpec(
        symbol="NAS100", asset_class="index", provider_symbol=None,
        timezone=DEFAULT_TIMEZONE, price_increment=None,
        quantity_increment=None, contract_multiplier=None,
        allowed_timeframes=AUTHORIZED_TIMEFRAMES,
    ),
    "EURUSD": InstrumentSpec(
        symbol="EURUSD", asset_class="forex", provider_symbol="EURUSD=X",
        timezone=DEFAULT_TIMEZONE, price_increment=None,
        quantity_increment=None, contract_multiplier=None,
        allowed_timeframes=AUTHORIZED_TIMEFRAMES,
    ),
}


def obtener_instrumento(symbol):
    return INSTRUMENTS.get(symbol.strip().upper()) if isinstance(symbol, str) else None
