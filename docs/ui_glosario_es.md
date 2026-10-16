# Glosario del dashboard (español simple)

El dashboard muestra los términos técnicos en lenguaje simple; el término original queda pequeño al lado o como
tooltip («¿Qué significa?»). Los valores guardados en la base de datos **no se traducen**: se muestran mediante
`ui/i18n_es.py` y el original sigue visible al pasar el mouse.

| Término técnico | En el dashboard | Qué significa |
|---|---|---|
| Equity | Capital de tu cuenta (equity) | Cuánto vale la cuenta de práctica ahora, contando las operaciones abiertas. |
| Realized PnL | Ganaste / Perdiste $X desde el inicio del historial | Suma de lo ganado o perdido en todas las operaciones cerradas guardadas (incluye las anteriores al demo). |
| Unrealized PnL | Va ganando / perdiendo | Resultado de operaciones abiertas; cambia con el precio. Sin precio: «Aún sin precio actual». |
| Win rate | De N operaciones, M ganaron | Cuántas operaciones cerradas terminaron con ganancia. |
| Max drawdown | La mayor caída del resultado de operaciones cerradas: $X | La caída más grande de la ganancia acumulada de operaciones cerradas desde su punto más alto. No incluye pérdidas de operaciones abiertas. |
| R (realized R) | ganaste / perdiste X veces lo que arriesgabas | Resultado dividido por el riesgo inicial (distancia al stop original × cantidad × multiplicador). |
| Stop loss (SL) | Stop loss (límite de pérdida) | Precio donde la operación se cierra para limitar la pérdida. |
| Take profit (TP) / target | Meta de ganancia (TP) | Precio donde la operación se cierra con ganancia. |
| R:R | Ganancia posible vs riesgo (R:R) | Cuánto se puede ganar por cada unidad arriesgada. |
| Fill | Entrada real (fill) | Precio al que realmente se entró. |
| Slippage | Diferencia entre lo planeado y lo ejecutado | Entrada planeada vs fill; en EURUSD también en pips (1 pip = 0.0001). |
| LONG / SHORT | Compra (LONG) / Venta (SHORT) | Dirección de la operación. |
| Setup | Oportunidad | Situación de mercado que cumple las reglas para operar. |
| Risk Engine | Motor de riesgo | Reglas fijas en Python que aprueban o rechazan cada operación. |
| PAPER | Cuenta de práctica (PAPER) | Sin dinero real; la ejecución real está desactivada. |
| NO_DATA | Sin datos | No hay datos guardados. «not available yet» → «no disponible aún». |
| ET | Hora de Nueva York (ET) | Todas las fechas y horas se muestran en hora de Nueva York. |

## Razones de cierre

| Valor guardado | Frase |
|---|---|
| `stop` | Tocó el stop loss (límite de pérdida) |
| `target` | Llegó a la meta de ganancia |
| `TIME_EXIT_DAY1_BELOW_0_5R` | Se cerró al final del primer día porque avanzó menos de media vez el riesgo |
| `TIME_EXIT_DAY2_STRUCTURE_CHANGE` | Se cerró el segundo día porque el mercado cambió de dirección |
| `TIME_EXIT_DAY2_NY_CLOSE` | Se cerró al final del segundo día (cierre de Nueva York) |
| `TIME_EXIT_FRIDAY_NY_CLOSE` | Se cerró el viernes al cierre de Nueva York para no pasar el fin de semana |
| otra razón | Se muestra el texto guardado tal cual |

## Vista Control

Solo lectura: interruptores «Broker demo» y «Cuenta real» y el botón «PARADA DE EMERGENCIA» están desactivados.
Si el registro de cuentas no se puede leer, todo se muestra apagado. «Real: bloqueado por seguridad».
