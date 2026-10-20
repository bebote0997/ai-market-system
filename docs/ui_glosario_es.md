# Glosario del dashboard (terminología de trading)

Desde 2026-10-19 (orden del owner, branch `feat/summary-trading-day`) el dashboard usa la terminología estándar de
trading (PnL, Equity, Balance, Drawdown, Win rate, Expectancy, R, Open positions, Closed trades, Daily loss limit) en
lugar de las frases en español simple; el español se mantiene solo donde es neutral (navegación, estados del sistema,
fechas). Se conservan la marca «AI Trading Floor» y el contador «Día N de 30». Los valores guardados en la base de
datos **no se traducen**: se muestran mediante `ui/i18n_es.py` y el original sigue visible al pasar el mouse.

| Término | En el dashboard | Qué significa |
|---|---|---|
| Equity | Equity · PAPER / Equity (NAV) · OANDA demo | Balance + unrealized PnL de las open positions. |
| Realized PnL | Realized PnL · desde el inicio del historial | Suma del net PnL de los closed trades guardados (incluye los anteriores al demo). |
| Unrealized PnL | Unrealized PnL | PnL de las open positions (mark-to-market). Sin precio: «Aún sin precio actual». |
| Win rate | Win rate · M/N closed trades | Closed trades con net PnL > 0 / total. |
| Max drawdown | Max drawdown (realized PnL): $X | Mayor caída pico-valle del realized PnL acumulado. No incluye open positions. |
| R / Avg R | +0.40R · Avg R (expectancy) | Net PnL / riesgo inicial (distancia al stop original × size × multiplicador); Avg R = expectancy por trade. |
| Stop loss (SL) | Stop loss (SL) | Precio de salida que limita la pérdida. |
| Take profit (TP) | Take profit (TP) | Precio de salida con ganancia. |
| R:R | R:R | Reward / risk del plan. |
| Fill | Fill / Entry (fill) | Precio real de entrada. |
| Slippage | Slippage · plan vs fill | Planned entry vs fill; en EURUSD también en pips (1 pip = 0.0001); adverse / favorable. |
| LONG / SHORT | LONG / SHORT | Side de la operación. |
| Setup | Setup / Valid setup / Watch / No setup | Situación de mercado que cumple las reglas para operar. |
| Daily loss limit | Daily loss limit | Límite de pérdida diaria 2 % (DEC-5.5a): OK / TRIGGERED / OFF / NO DATA. |
| Risk Engine | Motor de riesgo | Reglas fijas en Python que aprueban o rechazan cada operación. |
| PAPER | Cuenta de práctica (PAPER) | Sin dinero real; la ejecución real está desactivada. |
| NO_DATA | Sin datos | No hay datos guardados. «not available yet» → «no disponible aún». |
| ET | Hora de Nueva York (ET) | Todas las fechas y horas se muestran en hora de Nueva York. |

## Exit reasons

| Valor guardado | Texto |
|---|---|
| `stop` | Stop loss hit (SL) |
| `target` | Take profit hit (TP) |
| `TIME_EXIT_DAY1_BELOW_0_5R` | Time exit · día 1 < 0.5R |
| `TIME_EXIT_DAY2_STRUCTURE_CHANGE` | Time exit · día 2 · cambio de estructura |
| `TIME_EXIT_DAY2_NY_CLOSE` | Time exit · día 2 · cierre NY |
| `TIME_EXIT_FRIDAY_NY_CLOSE` | Time exit · viernes · cierre NY (sin exposición de fin de semana) |
| otra razón | Se muestra el texto guardado tal cual |

## Vista Control

Solo lectura: interruptores «Broker demo» y «Cuenta real» y el botón «PARADA DE EMERGENCIA» están desactivados.
Si el registro de cuentas no se puede leer, todo se muestra apagado. «Real: bloqueado por seguridad».
