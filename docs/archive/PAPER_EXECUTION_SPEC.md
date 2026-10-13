# Especificaciones internas PAPER — experimento de 14 días

Estas unidades y sus incrementos fueron aprobados expresamente por el usuario el
2026-09-19. No describen contratos de ningún broker real. No se convierten a lots.
`REAL_EXECUTION_ENABLED=False` permanece intacto.

| Símbolo | Unidad de cantidad | Multiplicador | Incremento de precio de referencia | Incremento de cantidad |
| --- | --- | --- | --- | --- |
| XAUUSD | Troy ounces (oz) | 1.0 | 0.01 USD/oz | 0.001 oz |
| EURUSD | Unidades de EUR base | 1.0 | 0.00001 USD/EUR | 1 EUR |

## Semántica numérica aprobada

Para LONG: `gross_pnl = (exit_price - entry_price) * quantity * 1.0`.
Para SHORT se invierte el signo de la diferencia de precios. El resultado está
denominado en USD. Riesgo previsto: `abs(entry - stop) * quantity * 1.0`.

El Risk Engine existente calcula el máximo autorizado. Un adaptador exclusivamente
PAPER aplica `floor(quantity / quantity_increment) * quantity_increment` usando
Decimal y reduce proporcionalmente `capital_at_risk`. No aumenta la cantidad ni el
riesgo; no aprueba rechazos del Risk Engine; no cambia entrada, stop, target ni R:R.
Si queda menos de un incremento, rechaza con `paper_quantity_below_increment`.
La cantidad reducida se entrega a IA, persistencia y broker sin otra conversión.

Los incrementos de precio son referencia explícita de cotización/presentación;
según la aprobación de conservar precios observados, no fuerzan redondeo de OHLC,
fills o niveles derivados, no rellenan precios faltantes y no alteran SL/TP.

Las especificaciones se cargan explícitamente en el runner cloud mediante
`runtime.paper_contracts.paper_instruments()`. El catálogo genérico de instrumentos,
los providers y el Risk Engine no se modifican. Los contratos no se infieren de un
proveedor ni se asumen para símbolos distintos de XAUUSD y EURUSD.

## Modelo económico aprobado y congelado para los 14 días

El usuario confirmó expresamente el 2026-09-19 el modelo ya implementado:
cuenta PAPER en USD, equity inicial **10000 USD**, comisión adicional **0**,
spread adicional **0**, financiación/swap **0**, fill al **Open de la siguiente
barra elegible** procesada y mantenimiento de la política actual de gaps de SL/TP.
Esta aprobación aplica exclusivamente al experimento PAPER de 14 días.

El modelo existente limita también capital utilizado a 100% de equity al hacer
sizing. Mantiene el recheck de riesgo <=1% y R:R >=3 en el fill. El límite de riesgo
es riesgo previsto hasta el stop, no garantía de pérdida máxima: un gap puede
generar una pérdida superior. No incorpora liquidez real ni impacto de mercado.

No quedan especificaciones económicas pendientes para este modelo. Cualquier
cambio posterior de unidades, incrementos, costes, fills, capital o moneda altera
la baseline económica y requiere una nueva aprobación y evaluación del experimento.
