"""SAMPLE / DEMO data only; fixed historical-looking values are fictional."""
from datetime import datetime, timedelta, timezone

from ai.contracts import AIFloorReport, AIResponse
from core.contracts import MarketBar, RiskDecision, SetupAssessment, TradePlan
from ui.adapters import MARKETS, from_ai_report

SAMPLE_LABEL = "SAMPLE / DEMO — NOT CURRENT PRICES"
AS_OF = datetime(2026, 1, 1, 15, 0, tzinfo=timezone.utc)


def demo_market(symbol="XAUUSD"):
    if symbol not in MARKETS:
        raise ValueError("unsupported sample market")
    base = {"XAUUSD": 2640.0, "NAS100": 21500.0, "EURUSD": 1.0420}[symbol]
    step = {"XAUUSD": 1.4, "NAS100": 18.0, "EURUSD": 0.0003}[symbol]
    bars = []
    for i in range(48):
        close = base + (i // 6) * step + ((i % 6) - 3) * step * 0.35
        opened = close - (step * 0.45 if i % 3 else -step * 0.35)
        bars.append(MarketBar("1.0", symbol, "15m", AS_OF - timedelta(minutes=15 * (47 - i)),
                              opened, max(opened, close) + step * 0.3,
                              min(opened, close) - step * 0.3, close, None, "SAMPLE / DEMO"))
    run_id = f"sample-{symbol.lower()}"
    setup = SetupAssessment("1.0", run_id, AS_OF, symbol, "WATCH", "LONG", ("1h", "15m", "5m"),
                            evidence=("sample-structure",), warnings=("SAMPLE / DEMO",))
    response = AIResponse("1.0", run_id, AS_OF, symbol, "Structure AI", "OK", bias="BULLISH",
                          confidence=0.62, observations=("Fictional sample structure.",),
                          supporting_evidence=("sample-structure",), reasoning_summary="Sample bullish context; no trade authorized.")
    report = AIFloorReport("1.0", run_id, AS_OF, symbol,
        {"structure_reports": {}, "liquidity_reports": {}, "macro_news_report": None, "setup_assessment": setup},
        response, None, None, None, None, None, None, "WATCH", ("SAMPLE / DEMO",))
    return from_ai_report(report, now=AS_OF, sample=True, bars=bars)
