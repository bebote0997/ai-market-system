"""Prototype example data of the Floor view (approved mockup v4). SAMPLE MODE ONLY.

``ui.floor_payload`` copies a section from here only when the dashboard is in sample mode AND that section has no
real data; every copied section is listed in ``payload["example_fields"]`` and shown with an "EJEMPLO" badge. These
figures are illustrative and never describe the engine.
"""
import math

HEADER = {"equity": 100412.50, "pnl": 412.50, "r_total": 0.83, "win_rate": 57.0, "drawdown": 612.50,
          "drawdown_pct": 0.61, "daily_loss_used_pct": 0.0, "daily_loss_limit_pct": 2.0,
          "daily_loss_status": "OK", "day_text": "Día 3 de 30", "trades": 7}

TRADES = [
    {"id": "14", "symbol": "EURUSD", "side": "LONG", "status": "OPEN", "opened_at": "20 oct 08:01 ET",
     "closed_at": None, "units": 25000.0, "entry_expected": 1.08720, "entry_actual": 1.08724, "slippage_pips": 0.4,
     "slippage_against": True, "sl_expected": 1.08520, "sl_actual": 1.08520, "tp_expected": 1.09120,
     "tp_actual": 1.09120, "risk_expected": 500.0, "r_planned": 2.0, "r_realized": 0.42, "pnl": 210.0,
     "exit_price": None, "last_price": 1.08808, "close_reason": None},
]

AGENTS = {
    "Structure": {"status": "Analizando H1 · M15", "bias": "LONG", "confidence": 0.81, "as_of": "20 oct 07:55 ET",
                  "analysis": "EURUSD H1: BOS alcista sobre 1.0872 con HL confirmado en 1.0851. Sesgo LONG mientras "
                              "no pierda 1.0845.", "hit_rate": 62.0},
    "Liquidity": {"status": "Mapeando pools", "bias": "LONG", "confidence": 0.74, "as_of": "20 oct 07:55 ET",
                  "analysis": "Sweep de lows asiáticos en 1.0851 completado. Siguiente pool de liquidez en 1.0905 "
                              "(equal highs).", "hit_rate": 58.0},
    "Macro": {"status": "Calendario OK", "bias": "NEUTRAL", "confidence": 0.66, "as_of": "20 oct 07:55 ET",
              "analysis": "Sin eventos high-impact próximas 4h. DXY débil (104.2), US10Y estable. Sesgo "
                          "neutral-alcista.", "hit_rate": 55.0},
    "Risk": {"status": "Exposición OK", "bias": None, "confidence": 0.90, "as_of": "20 oct 07:55 ET",
             "analysis": "Riesgo 0.5% ($500) con SL 20 pips. Exposición total 0.5%. Daily loss 0.0% de 2.0%.",
             "hit_rate": None},
    "Reviewer": {"status": "Aprobado 4/4", "bias": "LONG", "confidence": 0.78, "as_of": "20 oct 07:55 ET",
                 "analysis": "Consenso 4/4 alineado LONG. Sin contradicciones entre estructura y liquidez. Aprobado.",
                 "hit_rate": 64.0},
    "Conflict": {"status": "Sin conflicto", "bias": None, "confidence": 0.88, "as_of": "20 oct 07:57 ET",
                 "analysis": "Score de conflicto 0.12 (umbral 0.40). Macro neutral no bloquea. Sin conflicto.",
                 "hit_rate": None},
    "RiskEng": {"status": "GO", "bias": None, "confidence": 0.95, "as_of": "20 oct 07:58 ET",
                "analysis": "G1–G11 en verde. Daily loss 0.0/2.0%. Drawdown 0.61% < 5%. GO con plan R:R 1:2.",
                "hit_rate": None},
    "Exec": {"status": "Posición abierta", "bias": "LONG", "confidence": None, "as_of": "20 oct 08:01 ET",
             "analysis": "Long 25,000 EURUSD @ 1.08724 (esperado 1.08720, slippage 0.4 pip). SL 1.08520 · "
                         "TP 1.09120.", "hit_rate": 57.0},
}
SIGNALS = [{"at": "20 oct 08:01 ET", "signal": "EURUSD Long", "outcome": "Abierta", "r": 0.42},
           {"at": "19 oct 08:01 ET", "signal": "EURUSD Long", "outcome": "TP", "r": 2.0},
           {"at": "19 oct 03:15 ET", "signal": "EURUSD Short", "outcome": "SL", "r": -1.0},
           {"at": "16 oct 09:30 ET", "signal": "EURUSD Long", "outcome": "Time exit", "r": 0.31},
           {"at": "16 oct 03:05 ET", "signal": "EURUSD Short", "outcome": "SL", "r": -1.0}]
for _agent in AGENTS.values():
    _agent["signals"] = [dict(row) for row in SIGNALS] if _agent["hit_rate"] is not None else None

CYCLE = {"as_of": "20 oct 07:55 ET", "status": "Completado",
         "steps": [{"done": True, "at": at} for at in ("07:55", "07:55", "07:55", "07:57", "07:58", "07:58",
                                                       "08:01", None)]}

SYMBOLS = {"EURUSD": "ACTIVE", "XAUUSD": "PAUSED"}

FEED = ["08:01  EURUSD  Long 25k @ 1.08724", "07:58  Risk Engine  GO · 1:2", "07:57  Conflict  sin conflicto",
        "07:55  Reviewer  aprobado 4/4", "02:00  XAUUSD  pausado"]

MARKET = {"lines": ["EURUSD 1.08724  ▲0.18%", "DXY 104.2 · US10Y 4.21%", "Calendario: sin high-impact 4h"]}

# Cumulative realised PnL shape of the mockup's wall chart (illustrative only).
CURVE = [round(412.5 * (i / 40 + .33 * math.sin(i * .7) + .15 * math.sin(i * 2.3)), 2) for i in range(41)]
