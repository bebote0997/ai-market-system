# Agent Changelog

## 2026-10-21 — Ronda 5, paso 3: rejilla relajada (Jeferson) en EURUSD (branch `feat/backtest-v2-i2`)

- `replay.round5 --sections relaxed`: 12 familias × 4 TP (hold 5 días NY, SL estructura 15–50 pips, TP 1,5R/2R/3R/0,6 ADR20, cierre viernes ON) = 48 bajo C_RAW + 48 bajo C_OANDA + 13 C_RAW_STRESS (R total > 0). **Ninguna pasa.** Mejor R medio RX:JT[R1+R5]:TP3 +0,157 (35 trades, 0/5 años); la más cercana RX:F2:TP1.5 +0,038R, 520 trades, 2/5 años, DD MTM 22,7R (estrés +0,033R, 2/5; C_OANDA +0,012R). RX:E3 +0,008 a +0,016R. Informe en `/workspace/backtest/report_round5.md` (no se commitea).

## 2026-10-21 — Ronda 5, paso 2: familias previas en EURUSD bajo C_RAW / C_RAW_STRESS / C_FUT (branch `feat/backtest-v2-i2`)

- `replay.round5 --sections declare,prior,fut` (sin cambio de código): 81 variantes previas bajo C_RAW, **ninguna pasa** (0–1/5 años). Reproducción C_OANDA idéntica (E3:FIX2 −0,0387, A:SW:TP3 +0,1155). R total > 0 en 8 → C_RAW_STRESS: ninguna pasa. Top 5 por R medio (JT[R1+R5]:SL25 +0,157, A:SW:TP3 +0,121, E3:ADR +0,056 con 766 trades, M3:SL25, F4:SL25) bajo C_FUT: ninguna pasa (E3:ADR +0,056, DD 31R, 0/5). Informe en `/workspace/backtest/report_round5.md` (no se commitea).

## 2026-10-21 — Ronda 5, paso 1: modelos de coste de broker de bajo coste — código y diseño (branch `feat/backtest-v2-i2`)

- `replay/round5.py` (solo replay): modelos de coste constantes declarados antes de resultados — `C_RAW` (FX spread bruto 0,2 pip + comisión USD 7/100k ida y vuelta = 0,7 pip EURUSD/GBPUSD, USDJPY 7 × precio/1000 pips con el cierre 5m anterior (causal), slippage 0,1 pip en entradas, stops y salidas por tiempo, 0 en TP/límites; XAUUSD 0,10 USD + 0,07 USD/oz; US30/NAS100 1,0/0,8 pt sin comisión), `C_RAW_STRESS` (0,4 pip + misma comisión + 0,2 pip), `C_FUT` (6E: 1 tick 0,5 pip + 0,36 pip + 0,5 tick; sin financiación; sin gaps de roll) y `C_OANDA` (costes causales previos, referencia). La comisión se cobra una sola vez en R (también en las marcas MTM) y no ensancha el bid/ask (los toques de límite R5 y los TP solo ven el spread bruto).
- Infra: `intraday_backtest.Market.commissions` / `commission_at`, `round2.simulate_limit` y `round4.Tape.commission` / `slip` restan la comisión (por defecto 0: resultados previos idénticos); `round4.walk_trade` (extraído de `round4.run`) y `walk(no_tp_on_fill=...)` para fills límite. Nuevo `run_rule` (velas 5m, I2 o cierre al 5.º día de trading NY, viernes 17:00 NY) para la rejilla relajada (SL estructura 15–50 pips, TP 1,5R/2R/3R/0,6 ADR20) y las transferencias a otros instrumentos (clamps en pips → múltiplos de ADR20 con la mediana EURUSD 74,3 pips).
- Tests: `test_round5.py` (costes por modelo, comisión USDJPY causal, comisión una sola vez en R en todos los simuladores, TP no acreditado en la vela del fill límite, cierre al 5.º día, `run_rule` reproduce exactamente `round2.run_2r`, invariancia por prefijo). Diseño declarado: 217 tests fijos + estrés por variante con R total > 0 (acumulado ≥ 399). Validación: unittest 2516 OK (41 skipped, 1 expected failure); ruff PASS.

## 2026-10-21 — Ronda 4, cierre: robustez, tests múltiples y lista corta (branch `feat/backtest-v2-i2`)

- `replay.round4 --sections robust,final`: **37 tests en la ronda 4, 182 acumulados, ninguno pasa** (sin aprobados no se dispara el análisis de vecindad ±25 %). Años con R medio > +0,10 y CI80 > 0: 3 variantes con 2/5, 5 con 1/5, 29 con 0/5. Las 3 más cercanas: NAS100USD:A:SW:TP3 (+0,228R, 2/5 años, segunda mitad −0,017R), GBPUSD:JT[R1+R5]:SLADR (+0,047R, 32 trades) y XAUUSD:A:SW:TP3 (+0,247R, 1/5, segunda mitad +0,492R; mayoría de largos en tendencia alcista del oro). Informe y `round4_equity_r.png` en `/workspace/backtest/` (no se commitean). Validación: unittest 2500 OK (41 skipped, 1 expected failure); ruff PASS.

## 2026-10-21 — Ronda 4, paso 3: filtros (d), viernes ON y otros instrumentos (e) (branch `feat/backtest-v2-i2`)

- Mejores familias por R medio: A (A:SW:TP3 +0,115) y C2 (−0,036). (d) A:SW:TP3+d1 +0,215R (27 trades, 2/5 años), +d2 +0,140R (0/5); C2+d1/d2 negativos. Viernes ON en A:SW:TP3: −0,035R. (e) A:SW:TP3 en XAUUSD +0,247R (1/5 años) y NAS100 +0,228R (2/5); C2 negativo salvo GBPUSD +0,029; JT[R1+R5]:SLADR 18–52 trades por instrumento (mejor USDJPY +0,219R, 1/5); E3:SLADR plano o negativo en los seis. **Ninguna variante pasa** (22 + 4 + 1).

## 2026-10-21 — Ronda 4, paso 2: (a)–(c) en EURUSD (branch `feat/backtest-v2-i2`)

- `replay.round4 --sections declare,a,b,c`: diseño de 37 tests declarado antes de resultados (acumulado 182). **Ninguna variante pasa**; 0/5 años en las 10. Mejor R medio: A:SW:TP3 +0,115 (41 trades, ningún año con CI80 > 0; con estrés de carry +0,048), A:SW:TP2 +0,092. Tendencia B1–B4 negativas (B4 −29,6R, DD 48R); C1 RSI(2) −0,068R en 1 602 trades (coste 0,12R), C2 −0,036R. Resultados en `/workspace/backtest/report_round4.md` (no se commitea).

## 2026-10-21 — Ronda 4, paso 1: familias multi-día, filtros y otros instrumentos — código (branch `feat/backtest-v2-i2`)

- `replay/round4.py` (solo replay): D1/H4 remuestreados de velas 1h cerradas (completitud según horario del instrumento: XAU/índices sin la hora 17:00-18:00 NY), indicadores en barras cerradas, decisión al cierre → fill en la apertura del siguiente 5m; recorrido 5m con SL primero en la misma vela, gaps a la apertura, stops que solo se ajustan a favor; coste = mediana causal de 30 días del spread del instrumento + slippage 0,1 × spread (mín. 0,2 pip FX); financiación 0 % (regla) y estrés −3 %/año del nocional en R por día. Familias (a) swing, (b) tendencia B1–B4, (c) reversión C1–C2, (d) filtros d1/d2, viernes ON, (e) otros instrumentos + JT[R1+R5]/E3 con stop relativo a ADR20 (0,25–0,5). Regla de paso y años = `replay.fiveyear`. `fiveyear.plot_equity` acepta `title`.
- Tests: `test_round4.py` (no-lookahead por prefijo de barras, indicadores y setups de cada familia; horario con pausa; walk, costes, carry, filtros, vecindad ±25 %).

## 2026-10-21 — Rerun a 5 años + ronda 3, R3b + F80 + cierre (branch `feat/backtest-v2-i2`)

- `replay.fiveyear --sections families,f80,final` (solo replay): F1 ORB Londres, F1b ORB NY, F2 reversión al rango del día previo, F3 pullback a VWAP de sesión, F4 momentum tras vela 1h ≥ 1,5 ATR × 4 salidas: **todas negativas** (−0,04 a −0,11R, 0/5 años). F80 sobre las 3 mejores familias (JT[R1+R5], M2&M4, JT[R1+R3+R4+R5]; las familias con fills límite R5 solo admiten FIX2/SL25): 8 variantes, ninguna pasa. Shortlist = la variante más cercana por familia.
- Total: **145 tests** (declarados 149; F80 8 en vez de 12 por los fills límite), **ninguno pasa**; 24 cumplen 1/5 años, ninguno 2+. Falsos positivos esperados con independencia ≈ 145 × 0,00046 ≈ 0,07. Las 3 más cercanas (JT[R1+R5]:SL25 +0,23R/31 trades, JT[R1+R3+R4+R5]:SL25 +0,07R/92, JT[R1+R2+R5]:SL25 +0,05R/72) comparten R1+R5, son escasas y fallan 4 de 5 años. Informe `/workspace/backtest/5y_report.md` y `5y_equity_r.png` (no se commitean). REAL OFF.

## 2026-10-21 — Rerun a 5 años + ronda 3, R3a: módulos JT solos, pares y ALL (branch `feat/backtest-v2-i2`)

- `replay.fiveyear --sections modules,pairs` (solo replay): M1–M5b × 4 salidas (24) y pares AND de los 4 mejores módulos por R medio FIX2 a 5 años (M2, M4, M3, M1; M5/M5b nunca juntos) + ALL × 4 salidas (28). **Ninguna pasa**: todos los módulos solos negativos (−0,03 a −0,16R; M5 31 trades/mes −0,14R); mejor par M2&M4:SL25 +0,104R con 80 trades (0/5 años, DD 8,4R); ALL = 1–3 trades. Informe incremental en `/workspace/backtest/5y_report.md` (no se commitea). REAL OFF.

## 2026-10-21 — Rerun a 5 años + ronda 3, paso 1: señales rápidas y walk-forward anual (branch `feat/backtest-v2-i2`)

- `replay/fast_scouts.py` (solo replay): equivalentes numpy exactos de `_swings`, `_break_of_structure` y `_confirmed_pivots` de los scouts de producción, aplicados con `patch` solo dentro de los workers de señales y de la simulación F3 (producción sin cambios). ~50× más rápido (≈ 0,05 s por slot); igualdad comprobada en `test_fast_scouts.py` y en 300 slots del caché de 1 año (153 VALID_SETUP, 0 diferencias). Las señales de 5 años (72 216 slots) se generan en ≈ 8 min.
- `replay/intraday_backtest.py`: `--yearly-checkpoints` / `signals_by_year` (caché por año natural, escritura atómica, reanudable); el CSV 5m carga también `Volume` (tick volume). `entry_search.FOMC_DAYS` ampliado a 2021–2025 (fuera de la ventana 08:15–09:00, no cambia resultados).
- `replay/fiveyear.py`: validación por 5 bloques consecutivos de 12 meses desde el primer momento operable; pasa solo si R medio > +0,10 y CI80 inferior por días > 0 en ≥ 4 de 5 años, R total > 0, DD MTM ≤ 8R y coste medio ≤ 0,10R. Diseño completo (149 tests) declarado en el informe antes de cualquier resultado; secciones añadidas incrementalmente a `/workspace/backtest/5y_report.md` (no se commitea). `replay/round3.py`: generadores M1–M5b, pares/ALL y F1–F4 (ejecutados en los pasos siguientes). Tests: `test_round3.py` (no-lookahead por prefijo para cada módulo, combinación y familia; espejo de cortos; reglas fijas), `test_fiveyear.py`.
- Resultados paso 1 (5 años): **ninguna variante pasa**. V2_F3 −0,017R (1 047 trades, DD 25R), V2_I2 2R −0,124R, E1–E4 negativos (mejor E3:ADR +0,036R, 0/5 años), E5 casi vacío (spread ≤ 1,2 pip en 0,6 % de las velas), JT_SMC 24 trades −0,58R; mejor JT JT[R1+R5]:SL25 +0,23R con 31 trades (1/5 años). REAL OFF.

## 2026-10-20 — Ronda 2: correcciones de las 3 notas Codex (branch `feat/backtest-v2-i2`)

- `replay/round2.py` (solo replay): (1) paso 2 ordena las combinaciones JT únicamente por R medio IS de su variante SL25 (el nº de trades IS se informa, no ordena); (2) walk-forward de 3 folds también para los que pasen la barra estricta en la ronda 1; (3) la barra estricta de la ronda 1 se calcula a precisión completa desde sus trades (`entry_search.round1_results`, mismo código que `entry_search.main`), sin parsear valores redondeados del informe (`strict_pass_row` eliminado). Re-ejecución sobre el año original: veredicto sin cambios (ninguna pasa en ronda 1 ni 2); el paso 2 selecciona ahora R1+R5 y R1+R3+R4+R5. Tests en `test_round2.py`. Producción sin cambios, REAL OFF.

## 2026-10-20 — Búsqueda de entradas, ronda 2: ablación JT_SMC, SL 25–30, pruebas múltiples (branch `feat/backtest-v2-i2`)

- `replay/round2.py` (solo replay; producción sin cambios) reutiliza `entry_search` / `intraday_backtest` (mismos datos, costes, salidas, split con purga, bootstrap por días). Paso 1: rejilla predeclarada de 12 combinaciones R1–R6 (R1 sin FVG 1h, R2 tendencia solo H4, R3 solo H1, R4 fib 50–85 %, R5 orden límite en el borde proximal del OB 15m sin trigger 5m, R6 solo PDL/rango asiático sin regla de nivel intacto) × SL 15–30 / 25–30, TP 2R; sin relajaciones reproduce exactamente JT_SMC de la ronda 1 (test). Ninguna combinación llega a 8 trades/mes (máx. 2,3: R1+R2, R1+R2+R4). Paso 2: E2/E3 con SL 25–30 y las 2 mejores JT por R medio IS (R1+R2+R4, R1+R2): el SL 25 baja el coste de ≈ 0,13R a ≈ 0,08R por trade; E2 SL25 −0,350R IS / +0,021R OOS, E3 SL25 −0,082R / +0,226R; JT negativas en IS y OOS.
- Paso 3: 86 variantes probadas (60 ronda 1 + 26 ronda 2). Barra estricta (R medio IS y OOS > +0,10, CI80 inferior por días > 0 en ambos, ≥ 30 trades OOS): **ninguna pasa** (ronda 1 ni ronda 2), sin walk-forward. Más cercana: E3:2R:SL25 (IS −0,082R, OOS +0,226R, CI80 OOS +0,090, 44 trades). Gates G3/G4/G5 por variante, informe incremental `round2_report.md` y `round2_equity_r.png` en `/workspace/backtest/` (no se commitean). Tests: `test_round2.py` (equivalencia con la ronda 1, invariancia por prefijo con R2/R3/R5/R6, espejo de cortos, fills límite, SL 25, barra estricta, folds, selección solo IS). REAL OFF.

## 2026-10-20 — Búsqueda de entradas: correcciones de la revisión Codex (6 hallazgos de lookahead/sesgo, branch `feat/backtest-v2-i2`)

- `replay/entry_search.py` (solo replay): (1) HIGH IS = trades que entran Y salen antes del split; los que cruzan el split se purgan de IS y OOS (columna `Purged`), así el ranking IS no ve resultados ni marcas MTM OOS; (2) HIGH trigger JT exige `OB low <= close <= OB high` (`_trigger_ob`); (3) un barrido = una idea/entrada: identidad = vela 5m del extremo del barrido (`SweepBook`), las confirmaciones posteriores de otros niveles con el mismo mínimo se unen a la idea aunque ya haya terminado; ID de idea = esa vela; (4) buckets H4/D1 válidos solo con todas las horas esperadas (DST-safe); incompletos fuera de PDH/PDL, ADR20, EMA y tendencia; PDH/PDL no disponibles tras un día incompleto; (5) ventana E5 03:00–11:00 ET sobre la hora de decisión/fill (`candidate.t`), spread de la última vela cerrada; (6) `replay/intraday_backtest.py`: bootstrap por bloques de días (circular, bloques de 5 días de trading, días sin trades incluidos) como CI80 principal y del gate G3; IID como secundario (`ci80_low_iid`). `--compare` añade la comparación con un informe previo.
- Re-ejecución (2025-10-31 → 2026-06-30 IS / → 2026-10-20 OOS): sin cambios en trades ni R medio en las 60 variantes. Purgados en el split: 0 (el split cae el martes 17:00 NY, cuando cierran los trades I2 de fill en Londres). El trigger antiguo daba un candidato JT extra (2025-10-28), anterior al inicio operable. Ideas JT de barrido 556 → 439. JT_SMC 2R/ADR: 3 trades IS / 1 OOS, todos SL (≈ −1,01R); LIQ y variantes 1hOB: ≤ 1 trade. OOS positivas: 10 (E3 ×6 +0,34…+0,43R con CI80 day-block +0,14…+0,21; E2 FIX ≈ +0,05R), ninguna positiva en IS y OOS; E5 sigue vacía (spread ≥ 1,4 pip). Informe/gráfico en `/workspace/backtest/` (no se commitean; el anterior queda en `entries_report_run1.md` / `entries_equity_r_run1.png`).
- Tests: `test_entry_search.py` (purga en el split y ranking, trigger dentro del OB, identidad del barrido, cobertura horaria H4/D1 y PDL, ventana E5, parser/comparación), `test_backtest_v2_i2.py` (bootstrap por días vs IID). REAL OFF.

## 2026-10-20 — Búsqueda de entradas intradía EURUSD: JT_SMC + E1–E5 (branch `feat/backtest-v2-i2`, orden del owner 18:04 ET)

- `replay/entry_search.py` (solo replay; producción sin cambios): reutiliza `replay/intraday_backtest.py` (CSV, spread causal por trade, 0,2 pip de slippage, SL primero, cierre al final de la 2.ª sesión desde el fill, viernes 17:00 NY, halt 2R/día, DD MTM, gates). H4/D1 remuestreados desde velas 1h cerradas (D1 = día NY 17:00→17:00, H4 anclado a 17:00 NY), visibles solo al cierre del bucket; decisiones en cierres 5m/15m, fill en el open de la vela 5m siguiente (sin fill si falta). Los cortos usan la misma regla sobre precios espejados. Parámetros fijados de antemano; IS = 8 meses tras el calentamiento (2025-10-31 → 2026-06-30), OOS = 2026-06-30 → 2026-10-20.
- JT_SMC (plan de Jeferson, mecánico; definiciones en el informe): 556 barridos → 81 a favor de una tendencia H1+H4 alineada → 21 con OB 15m → 12 triggers fib/OB 5m → 4 con FVG 1h sin llenar → 4 candidatos (1 con confluencia OB 1h). 4 trades en el año, todos SL: muestra insuficiente (< 4 trades/mes).
- E1–E4 × {FIX 2R, FIX 1,5R, ADR} × {sin F80, F80}: ninguna variante es positiva en IS y OOS a la vez. Mejor IS: E1:ADR:F80 +0,070R (OOS −0,269R). Positivas en OOS: E3 (+0,34…+0,43R, 43–44 trades, CI80 inferior > 0) y E2 FIX (≈ +0,05R), todas negativas en IS. E5 vacía por construcción: el spread del feed nunca baja de 1,4 pip (> 1,2). Aviso de pruebas múltiples (60 variantes). Informe/gráfico en `/workspace/backtest/entries_report.md` y `entries_equity_r.png` (no se commitean).
- Tests: `test_entry_search.py` (remuestreo y DST, rangos de sesión, ADR solo con días completos, swings confirmados, invariancia por prefijo de todas las features y de todas las señales E1–E4/JT, fill en el open siguiente, geometría de salidas, noticias, split). REAL OFF.

## 2026-10-20 — Backtest V2_I2: correcciones de la revisión Codex de lookahead (branch `feat/backtest-v2-i2`)

- `replay/intraday_backtest.py` (solo replay): (1) SL/TP desde la vela del fill documentado en Method (fill = open de esa vela); (2) `--end` aplicado en la simulación: posiciones abiertas al corte cerradas en la última vela 5m que termina ≤ corte (OPEN_AT_END), nunca se lee una vela posterior; la caché de señales admite un subconjunto; (3) drawdown mark-to-market en R (cierres 5m a bid/ask) junto al realizado, G4 usa el MTM; (4) spread causal por trade: mediana de los 30 días anteriores al fill (redondeado a 0,01 pip, fallback 1,6 pip), la mediana de toda la muestra solo se informa; (5) deadline I2 desde la hora real del fill, fills fuera de sesión se descartan; (6) límites de tiempo/viernes/estructura procesados en su propia hora con la última vela que termina ≤ límite (si no hay, la primera posterior, contada como `stale`).
- Resultado (mismas señales en caché, sin regenerar): V2_F3 202 trades −0,046R (−9,24R, DD realizado 11,74R / MTM 12,98R); V2_I2 253 trades −0,134R (−33,84R, DD 38,10R / MTM 38,68R); V2_I2_15 270 trades −0,091R (−24,50R, DD 32,42R / MTM 32,89R). 0 salidas `stale`, 0 trades con spread de fallback. Ninguna variante pasa G3/G4. REAL OFF.

## 2026-10-20 — Backtest 1 año EURUSD V2_F3 vs V2_I2 / V2_I2_15 (branch `feat/backtest-v2-i2`, orden del owner 15:21 ET)

- `replay/intraday_backtest.py` (solo replay; producción sin cambios: floor/orchestrator, RuntimeConfig, fingerprint, planners, env): floor de producción una vez por slot (ventanas de 500 velas cerradas, IA excluida, `NoMacroDataProvider`), señales en caché (`signals.jsonl`, fuera del repo), planner I2 solo-replay (SL en swing 15m/5m ± 0,2×ATR14 H1, 15–30 pips, TP 2R / 1,5R), ejecución 5m con spread mediano bid/ask + 0,2 pip de slippage, salidas de `execution/trade_duration` para F3, cierre al final de la 2.ª sesión para I2, cierre del viernes, halt diario 2R. Ejecutar: `python -m replay.intraday_backtest --data <dir> --out <dir>`.
- Resultado (2025-11-03 → 2026-10-20, spread 1,6 pip): V2_F3 202 trades −0,046R/trade (−9,2R, DD 11,7R); V2_I2 253 trades −0,128R (−32,5R, DD 36,7R); V2_I2_15 270 trades −0,086R (−23,1R, DD 31,1R). Ninguna variante pasa G3/G4 (DEC-LIVE-1). Informe y CSV en `/workspace/backtest/` (no se commitean datos de mercado).
- Tests: `test_backtest_v2_i2.py` (no-lookahead, clamp/skip del SL I2, horas de cierre por sesión con DST, viernes, SL primero en la misma vela, gap, BE Day-2, gate de fill F3, un trade por setup_id). REAL OFF.

## 2026-10-19 — DEC-SUMMARY-DAY-1: correcciones de revisión Codex (branch `feat/summary-trading-day`)

- HIGH `runtime/demo_runner.py` `daily_summary`: cada pase (sink durable únicamente) entrega también los DAILY_SUMMARY persistidos que un crash dejó sin capturar (journal sin `notification_events`) o sin reclamar (sin fila en `notification_deliveries`). Un envío reclamado sin resultado durante más de `SUMMARY_CLAIM_GRACE = 15 min` (crash a mitad del envío) no se reenvía (Slack no deduplica): estado `UNKNOWN` y una sola línea `component=notifications event=DAILY_SUMMARY_UNKNOWN date=<fecha>`. `storage/database.py` sin cambios (módulo congelado por hash).
- MED `runtime/notifications.py`: NAV, Balance, P&L desde el inicio del demo y operaciones abiertas son la foto a la hora de envío y se rotulan «(a las HH:MM ET)»; «Día N de 30» se calcula a la hora de envío (`SlackNotificationSink(clock=...)`, por defecto ahora). Los agregados del día siguen en la ventana 17:00 → 17:00 NY de su fecha (catch-up de varios días correcto).
- MED `execution/demo_metrics.window_statistics` y `storage/daily_summary.wins_day`: si una operación incluida no tiene P&L, las ganadoras son desconocidas (`None`; Slack muestra «ganadoras: —»), nunca un recuento parcial.
- Tests: `test_daily_summary.py` (crash tras captura ⇒ enviado una vez en el siguiente pase; crash tras reclamo ⇒ no reenviado, `UNKNOWN` registrado una vez; `wins_day` None), `test_demo_gates_oanda.py` (catch-up de varios días con foto a la hora de envío, «ganadoras: —»). REAL OFF.

## 2026-10-19 — Dashboard: terminología profesional de trading (branch `feat/summary-trading-day`, orden del owner 22:39 ET)

- Revierte las frases en español simple de `e672c51` a términos estándar (como antes de esa reescritura): PnL (Realized / Unrealized / Net / Today PnL), Equity, Balance, NAV, Max drawdown, Win rate, Avg R (expectancy), R realized, Open positions, Closed trades, Daily loss limit, Setup, Side, Size, Entry / Fill / Exit, Stop loss (SL), Take profit (TP), R:R, Slippage, Exit reason. Español solo donde es neutral (navegación, estados del sistema, fechas). Se mantienen «AI Trading Floor» y «Día N de 30»; el texto Slack no cambia.
- Archivos: `ui/i18n_es.py` (etiquetas, columnas, estados LONG/SHORT/setup, exit reasons, tooltips, tarjeta Daily loss limit, frases de los KPI; se elimina `times_text`, solo usado por las frases), `ui/views/luxury.py` (KPI, curva, detalle de trade, slippage, demo OANDA; la celda R muestra «+0.40R»), `ui/views/control.py`, `ui/components/panels.py`, `ui/components/setup_chart.py`, `docs/ui_glosario_es.md`. Solo texto: sin cambios de lógica, datos ni cálculos.
- Tests con textos actualizados: `test_ui_redesign.py`, `test_dashboard_luxury.py`, `test_demo_from_zero.py`, `test_demo_ops_dashboard.py`. Lista antes → después: `/workspace/oanda-task/ui_terms_changes.md` (fuera del repo). REAL OFF.

## 2026-10-19 — DEC-SUMMARY-DAY-1: resumen diario por día de trading 17:00 → 17:00 NY (branch `feat/summary-trading-day`)

- `storage/daily_summary.py`: `trading_day(at)` (fecha NY del último 17:00 NY ≤ `at`, vía `daily_loss_limit.day_key`), `summary_day(at)` (salta sábado/domingo ⇒ el viernes), `pending_summary_days(last_sent, at)` (días lun–vie pendientes tras el último enviado, del más antiguo al más reciente, máx. `MAX_CATCH_UP = 5`; sin registro ⇒ solo el último; reloj atrasado ⇒ ninguno), `summary_window(day)` ⇒ `(date_ny, start_utc, end_utc, trading_day_key)` = [día anterior 17:00, `day` 17:00) NY (23 h / 25 h con DST). `build_daily_summary(store, at, day=None)` agrega runs/órdenes/posiciones/cerradas/eventos sobre esa ventana; nuevos campos `trading_day_key`, `window_start_utc`, `wins_day`, `avg_r_day`, `avg_r_day_reason` (R como en el dashboard).
- `runtime/demo_runner.py` `daily_summary`: persiste y notifica cada día pendiente una vez (journal `daily:<fecha>` + estado `daily_summary_date`); devuelve True si persistió alguno.
- `runtime/notifications.py`: el texto OANDA añade «Día de trading: <fecha> (17:00 a 17:00 ET)» y calcula «P&L del día de trading», «Operaciones cerradas en el día (ganadoras)» y «R medio del día» sobre la misma ventana (`execution/demo_metrics.window_statistics`; cierre sin hora ⇒ «sin datos»). Se mantienen NAV, Balance, P&L desde el inicio, abiertas y los logs `DAILY_SUMMARY_SENT/FAILED`; con enrutado ON nunca cifras PAPER.
- Compatibilidad: la clave anterior (fecha NY cuyo 17:00 ya pasó) coincide con la nueva para lun–vie ⇒ sin reenvío al desplegar.
- Tests: `test_daily_summary.py` (ventana a través de 17:00 y medianoche UTC, fin de semana, DST, catch-up tras reinicio), `test_demo_gates_oanda.py` (cifras OANDA de la ventana). Sin env vars ni flags. REAL OFF.

## 2026-10-19 — Ops: holgura del watchdog, logs del router, dashboard demo, latches, resumen diario (branch `feat/ops-diag-fixes`)

- DEC-WATCHDOG-2 (`runtime/cloud.py`, `runtime/cloud_runner.py`, `runtime/scheduler.py`): `WATCHDOG_STALE_SECONDS = 300` (antes 120; default de `scheduler_health_detail`/`scheduler_healthy`, gracia de arranque y comprobación de `serve()`, cada `WATCHDOG_CHECK_SECONDS = 30`), `STALL_DUMP_SECONDS = 240` (antes 100), `SLOW_TICK_SECONDS = 90` sin cambios. Heartbeat de progreso tras cada ciclo de símbolo completado (best-effort, `PROGRESS_HEARTBEAT_FAILED`); sin hilo de heartbeat en segundo plano: un ciclo colgado sigue disparando el watchdog. Ruta del tick revisada: todas las llamadas de red ya tienen timeout ⇒ sin cambios.
- `execution/broker_demo_router.py`: cada evento CLOSED / CLOSE_UNKNOWN / CLOSE_FAILED / CLOSE_REJECTED / CLOSED_AT_BROKER / BREAKEVEN / BREAKEVEN_UNKNOWN / BREAKEVEN_REJECTED / BREAKEVEN_BLOCKED emite además una línea `component=broker_demo event=<EVENT> trade_id=<nº trade broker> reason=<motivo>` (INFO éxito, WARNING resto; tokens saneados). Todas las rutas de cierre (gestión, pérdida diaria/drawdown, viernes, emergencia) pasan por `_close`. El detalle persistido de los eventos no cambia.
- Dashboard demo (`execution/demo_metrics.py`, `execution/demo_dashboard.py`, `ui/views/luxury.py`, `ui/views/experiment.py`, `ui/i18n_es.py`): tarjeta «Límite de pérdida diaria» en el Resumen (estado OK / ACTIVADO latcheado / aún sin evaluar / no activado / sin datos, pérdida de hoy realizada + abierta = NAV OANDA − NAV de inicio del día − transferencias, % usado del límite, NAV de inicio, freno de drawdown 5 %) leída en `mode=ro` de `broker_demo_flags`; R por operación enrutada con «—» (tooltip con el motivo) si falta el stop inicial; «Día N de 30» en el Resumen, experimento y Slack (día 1 = fecha NY de la primera operación enrutada, fines de semana incluidos; > 30 ⇒ «Día 30 de 30 – completo»; `demo_progress` devuelve el día sin tope). `demo_epoch.py` sin cambios.
- DEC-LATCH-PERSIST-1: los latches de pérdida diaria / drawdown y la E0 ya persistían en `broker_demo_flags` (clave = día de trading NY 17:00); nuevos tests de reinicio de proceso real (`test_latch_persist.py`). Sin cambios de código.
- Resumen diario Slack (`runtime/notifications.py`): verificado el cableado de producción (`cloud_runner` llama `runner.daily_summary` tras cada tick; una vez por fecha UTC ⇒ ~20:00 ET en verano / 19:00 ET en invierno, una por día NY; con enrutado ON el texto es del demo OANDA). Nuevo: línea `component=notifications event=DAILY_SUMMARY_SENT source=OANDA_DEMO|PAPER date=<fecha UTC>` (y `DAILY_SUMMARY_FAILED … error_type=<código>`; nunca la URL del webhook) y línea «Balance» en el texto OANDA.
- Correcciones de revisión (Codex): (1) resumen diario por fecha NY: `storage/daily_summary.summary_window` — clave y dedupe = fecha calendario NY cuyo cierre 17:00 America/New_York ya pasó (primer tick a/tras las 17:00 ET; antes ⇒ la fecha NY anterior), agregación sobre [00:00, 24:00) NY de esa fecha (DST correcto), campos `date_ny` / `close_at_utc` (sustituyen `date_utc`); el texto OANDA se lee a `close_at_utc` (P&L de hoy (ET) = esa fecha NY); dedupe persistido sin cambios (journal `daily:<fecha>`). Las filas PAPER entre 17:00 y 24:00 NY quedan fuera del envío de esa fecha. (2) `_log_action` solo registra códigos de motivo permitidos (cierres de duración, SL_TP, DAILY_LOSS, DRAWDOWN, FRIDAY_PREFLATTEN, EMERGENCY_*, errores Broker*); `EMERGENCY:<texto>` ⇒ `EMERGENCY`, el resto ⇒ `OTHER`. (3) `daily_loss_view`: HALT de hoy ⇒ TRIGGERED; si no, E0 inválida/≤0 o sin transferencias/NAV ⇒ `NO_DATA` (antes OK).
- Tests: nuevos `test_broker_action_logs.py`, `test_latch_persist.py`, `test_demo_ops_dashboard.py`; ampliados `test_watchdog_diagnostics.py`, `test_demo_gates_oanda.py`. Sin cambios de env/flags en Render, fingerprint, preflight ni scope de catch-up. REAL OFF.

## 2026-10-19 — DEC-XAU-OFF-1: XAUUSD pausado en el motor PAPER por código (branch `feat/pause-xauusd`)

- Nuevo `runtime/symbol_pause.py`: `PAUSED_SYMBOLS = frozenset({"XAUUSD"})`, `is_paused`, `EXIT_REASON = "SYMBOL_PAUSED"`, log único por proceso `component=scheduler event=SYMBOL_PAUSED symbol=XAUUSD`. Sin env var: `RuntimeConfig`, fingerprint, preflight (local y cloud), scope de catch-up, env vars y flags sin cambios.
- `runtime/scheduler.py`: los símbolos pausados nunca ejecutan ciclo; en sesión, antes de los ciclos, llama `close_paused_symbols(slot)` (nunca lanza al scheduler; respeta el halt gate). Fuera de sesión lo hace el pase `manage_trade_duration`.
- `runtime/service.py`: `_run_cycle` devuelve `SYMBOL_PAUSED` sin claim ni escritura; `close_paused_symbols(at)` / `_close_paused(symbol, slot)`: cancela las órdenes PENDING (evento `ORDER_CANCELLED` `{"reason": "SYMBOL_PAUSED"}`) y cierra la posición abierta con `PaperBroker.close_market(..., exit_reason="SYMBOL_PAUSED", decision_run_id=<run de apertura>)` vía `_guarded_paper_write` (CAS), al cierre de la última vela 5m cerrada (`td.price_bar` sobre el snapshot normal / evidencia), tras la gestión SL/TP hasta el slot por la misma ruta de catch-up que trade duration (un stop/target anterior gana). Evento `SYMBOL_PAUSED_EXIT` + el `POSITION_CLOSED` normal (notificado). Sin snapshot / sin vela / STALE / error ⇒ warning y reintento en el siguiente tick. Idempotente; respeta `paper_enabled` y el halt gate; nunca toca el router OANDA. `manage_trade_duration` aplica lo mismo a los pausados en vez de la regla de duración.
- `runtime/demo_runner.py`: `close_paused_symbols` captura y entrega las notificaciones (como `manage_trade_duration`); `run_once` trata `SYMBOL_PAUSED` como `HALT_REFUSED` (sin run).
- `ui/i18n_es.py`: texto para el motivo de cierre `SYMBOL_PAUSED`.
- Tests: nuevo `test_pause_xauusd.py`. Los tests existentes que ejecutan ciclos XAUUSD (54 módulos) parchean `runtime.symbol_pause.PAUSED_SYMBOLS` a vacío en `setUpModule` (y en sus procesos hijo); sin otros cambios de aserciones.
- Suite completa: 2328 tests OK (41 skipped, 1 expected failure); `ruff check .` limpio. (Una primera corrida baseline en `fa690f1` dio 40 fallos transitorios en tests de procesos hijo/señales reales; re-ejecutados en `fa690f1` pasan.) REAL OFF.

## 2026-10-19 — DEC-DEMO-GATE-1: gate de drawdown y resumen diario con el demo OANDA (branch `feat/demo-gates-oanda`)

- `execution/demo_epoch.py`: `DEFAULT_DEMO_START_EQUITY = Decimal("99999.977")` (NAV practice en el inicio del demo) y `demo_start_equity(env)` con override `AI_FLOOR_DEMO_START_EQUITY` (decimal positivo; inválido ⇒ default; nunca lanza).
- `execution/risk_engine_v2.evaluate`: keyword opcional `drawdown_basis` (`{"source": "OANDA_DEMO", "current", "starting"}` o `{"source", "error"}`). None ⇒ idéntico. Con base: el gate 4 usa current/starting de la base (misma fracción 5 %, mismo `ACCOUNT_DRAWDOWN_LIMIT`) y registra `drawdown_source`/`drawdown_current_equity`/`drawdown_starting_equity`; base con error o inválida ⇒ `ACCOUNT_DRAWDOWN_UNAVAILABLE` (+ `drawdown_error`). Gates 3/5 (sizing) siguen con la cuenta PAPER.
- Plumbing: `execution/risk_reservation.py` está congelado desde P3 (`test_phase8_p4b`) y los llamadores de `submit_plan` están fijados (`test_safety_invariants`), así que la base llega a `evaluate` mediante `risk_engine_v2.drawdown_basis_scope` (context variable, por hilo/tarea, se restablece al salir; un keyword explícito gana). `execution/opposite_policy.execute(..., drawdown_basis=None)` envuelve la llamada intacta a `prepare_reservation`. En el runtime, Risk V2 solo se alcanza por esta ruta (STRUCTURE_CHANGE); la ruta V1 `run_floor`/`submit_plan` no tiene gate de drawdown y no cambia.
- `execution/broker_demo_router.BrokerDemoRouter.drawdown_basis()`: un `get_balance()` de solo lectura (timeouts del cliente) vs `demo_start_equity(env)`; nunca lanza (dict de error), nunca envía/modifica/cierra órdenes; log INFO `component=broker_demo event=PAPER_GATE_BASIS source=OANDA_DEMO drawdown_pct=<x.xx|NA> status=ok|error` (sin importes ni ids).
- `runtime/service.py`: con `broker_router` presente calcula la base una vez por ciclo antes de la etapa PAPER y la pasa a `opposite_policy.execute`; un router que lanza ⇒ base de error (fail-closed). Sin router: la llamada no cambia.
- `runtime/notifications.py`: con enrutado ON, el texto Slack de `DAILY_SUMMARY` es el resumen del demo OANDA (cabecera `AI Market System OANDA DEMO DAILY_SUMMARY`, «Día N de 30», NAV, P&L desde el inicio, cerradas/ganadoras, R medio, P&L de hoy ET, abiertas) vía `demo_dashboard.demo_snapshot/demo_progress` (import perezoso y protegido); fallo ⇒ «OANDA demo: sin datos». El payload persistido del journal no cambia. OFF ⇒ texto idéntico.
- Tests: nuevo `test_demo_gates_oanda.py`; `test_broker_wiring.py` registra el import perezoso de `execution.demo_dashboard` en `runtime/notifications.py`. Sin cambios en valores de política, fingerprint, experimento/freeze, órdenes del router, env vars de producción ni flags. REAL OFF.

## 2026-10-19 — Fixes revisión Codex de `feat/demo-from-zero`

- P1 `runtime/cloud.py` archivo pre-demo: backup por pasos (`ARCHIVE_STEP_PAGES`) con plazo total de 20 s (`ARCHIVE_DEADLINE_SECONDS`, reloj inyectable) y busy timeout de 1 s; al vencer se aborta, se borra solo el temp y se registra `PRE_DEMO_ARCHIVE_SKIPPED reason=timeout`. La fuente nunca se toca.
- P2 `ui/views/luxury.py` `demo_api()`: lee `AI_FLOOR_BROKER_DEMO_ROUTING` por su cuenta; enrutado ON + módulo demo no importable ⇒ pantallas demo con «sin datos» y filtro de Decisions con el inicio por defecto (constante inline), nunca PAPER. `experiment.py`: «Demo OANDA: sin datos».
- P2 `execution/oanda_readonly.py`: plazo total de 8 s para CLOSED+OPEN (+ 4 s del summary ⇒ ≤ 12 s), timeout de cada GET acotado al tiempo restante ⇒ `{"error": "deadline"}`; tope de páginas agotado antes del inicio ⇒ `{"error": "incomplete"}` (nunca totales parciales). `CachedDemoTrades` ya no retiene el lock durante la red: otras sesiones reciben el valor previo (stale) mientras una refresca. `demo_snapshot` expone `trades_error`; el dashboard muestra «sin datos (totales incompletos…)» / «sin datos (OANDA tardó demasiado…)».
- P2 tests: los tests de `serve()` (`test_security_audit.CloudServeTests`, `test_watchdog_diagnostics`) mockean el archivo; test guardián que ejecuta las suites de `serve()` y verifica que no se crea `data/runtime/archive` ni se archiva una DB del repo.

## 2026-10-19 — Demo OANDA «desde cero» en el dashboard; archivo del PAPER previo (branch `feat/demo-from-zero`)

- Nuevo `execution/demo_epoch.py`: inicio del demo `2026-10-18T22:25:00Z` (deploy que encendió el enrutado; override `AI_FLOOR_DEMO_EPOCH` ISO 8601 con offset, inválido/naive ⇒ default) e `is_demo_trade` (`aif-<run_id>`; excluye la orden manual `aif-test-*`).
- `execution/oanda_readonly.py`: segundo GET de solo lectura `get_trades(state)` (`/v3/accounts/{id}/trades?state=OPEN|CLOSED&count=500`, páginas `beforeID` hasta pasar el inicio o fin, tope 10 páginas), campos seguros `TRADE_FIELDS` (sin id de cuenta ni token), `cached_demo_trades` (solo practice, TTL 10 s, nunca lanza ⇒ None). El transporte por defecto solo admite esa ruta de lista con claves de query permitidas.
- Nuevo `execution/demo_metrics.py` (puro): `demo_statistics` (mismas claves que `EMPTY_STATS`; P&L = realizedPL + financing, hoy en ET, drawdown sobre realizado acumulado, R con `initial_stop`/`stop` y fill/entrada de `broker_demo_trades`), `demo_capital`, `demo_day`, `load_risk_rows` (`mode=ro`). Sin operaciones ⇒ ceros.
- Nuevo `execution/demo_dashboard.py`: única entrada (perezosa y protegida) de Overview/Trades/Decisions/experimento. Con `AI_FLOOR_BROKER_DEMO_ROUTING=1`: KPIs, curva y posiciones solo del demo OANDA («Demo OANDA (práctica) · desde 18 oct 2026 18:25 ET», nota de PAPER archivado); fallo de lectura ⇒ «sin datos», nunca números PAPER; Trades = operaciones OANDA del demo; Decisions = solo `as_of >= inicio`; experimento «Demo OANDA: Día N de 30» desde la primera operación enrutada. Enrutado OFF ⇒ pantallas idénticas (verificado byte a byte contra `main` en el fixture).
- `runtime/cloud.py` `serve()`: copia única, best-effort, de la DB de trading a `<dir>/archive/<stem>_pre_demo_20261018.db` (API backup desde fuente `mode=ro`, temp + `os.replace`, solo si falta el destino, existe la fuente, cabeceras SQLite/WAL válidas y espacio ≥ 2×tamaño + 64 MiB). Log `component=archive event=PRE_DEMO_ARCHIVED bytes=N` / `PRE_DEMO_ARCHIVE_SKIPPED reason=…`. Nunca borra ni lanza; preflight sin cambios.
- Tests: nuevo `test_demo_from_zero.py`; ajustados `test_oanda_readonly.py` (método `get_trades` permitido, sigue prohibido `/trades/`, `/orders`, `/close`, verbos de escritura) y `test_broker_wiring.py` (`execution.demo_dashboard` registrado como módulo vigilado, permitido solo de forma perezosa en `luxury.py`/`experiment.py`). Sin cambios en motor/DB PAPER, experimento/freeze/fingerprint, riesgo, órdenes del router, env vars ni `render.yaml`. REAL OFF.

## 2026-10-18 — Diagnóstico de bloqueos del watchdog/runner cloud (branch `fix/watchdog-diagnostics`)

- `runtime/cloud.py`: nuevo `scheduler_health_detail()` (`healthy`, `scheduler`, `heartbeat_age_seconds`, `error_type`); `scheduler_healthy()` delega con la misma firma y semántica. El watchdog de `serve()` reintenta hasta 3 veces (2 s) solo si la DB no se pudo leer; un heartbeat legible pero viejo o `scheduler != RUNNING` falla de inmediato. Antes de lanzar registra `component=watchdog event=SCHEDULER_STALE` (sin rutas ni secretos) por stdout.
- `runtime/cloud_runner.py`: `faulthandler.dump_traceback_later(100 s)` armado alrededor de cada `tick()` + `daily_summary` y cancelado en `finally`; `component=runner event=SLOW_TICK` (WARNING) si el tick supera 90 s. Los diagnósticos nunca lanzan; cadencia sin cambios.
- Tests: nuevo `test_watchdog_diagnostics.py`. Sin cambios de env vars, flags, execution/, ui/ ni broker.

## 2026-10-17 — BORRADOR: fixes revisión Codex ronda 3 del router demo OANDA (branch `wip/oanda-adapter`)

- `emergency_close_all` respeta dry-run: solo eventos `DRY_RUN` de vista previa, ningún PUT/POST (HIGH).
- Acción UNKNOWN (cierre/breakeven) ya no se libera porque el trade siga abierto: solo con evidencia (trade ausente / SL del broker en la entrada a 5 decimales); mientras, `blocked_reason="unresolved_action"` y sin reintentos hasta `clear_unresolved(actor, motivo)` (MED).
- Gestión de duración: toda acción exige cotización válida y `tradeable is True`; si no, nada ese tick, se reintenta y se registra `MANAGE_SKIPPED` una vez por tick (MED).
- Tests nuevos/ajustados en `test_broker_demo_router.py`; `docs/oanda_adapter.md` actualizado. Sin commit.

## 2026-10-17 — BORRADOR / flag OFF: enrutado demo OANDA practice, script de orden de prueba, vista Demo/Real (branch `wip/oanda-adapter`)

- Nuevo `execution/broker_demo_router.py` (`router_from_env` ⇒ `None` salvo `AI_FLOOR_BROKER_DEMO_ROUTING=1` exacto): deriva su env (demo ON, provider oanda, sin mutar `os.environ`), enciende `broker_demo` solo en la primera construcción (auditado `router_from_env`/`routing_flag`), solo EURUSD con `eligible` + Risk APPROVED, idempotente por `aif-<run_id>`, intent write-ahead, UNKNOWN bloquea hasta conciliar, una posición neta, sizing NAV×risk_fraction (tope 1%, `AI_FLOOR_BROKER_DEMO_MAX_UNITS`), dry-run opt-in (`AI_FLOOR_BROKER_DEMO_DRY_RUN=1`), regla de duración vía `trade_duration.py`, drift, `emergency_close_all`. Estado en `execution/broker_demo_state.py` (tablas propias en `broker_state.db`).
- `runtime/service.py`: hooks mínimos y protegidos (`broker_router=_AUTO`, `on_cycle` tras el registro del ciclo, `manage` al inicio de `manage_trade_duration`); con el flag OFF no se importa nada y paper es idéntico.
- `execution/oanda_broker.py`: lecturas públicas `open_trades()`, `get_quote()`, `trade_id_for_client()` y `openTime` en `OpenTrade`.
- Nuevo `execution/oanda_readonly.py` (solo `GET /v3/accounts/{id}/summary`, practice/live, tokens aislados) y pantalla Control: chip de enrutado, vista Demo | Real (Real solo lectura, operar bloqueado) con auto-refresco, sección «Historial (baseline)» vacía.
- Nuevo `scripts/oanda_first_test_order.py` (dry-run por defecto; envío solo con `--send --confirm I-APPROVE-PRACTICE-TEST-ORDER` + guardas; máx. 100 unidades). **No se ejecutó; ninguna orden colocada.**
- Tests: nuevos `test_broker_demo_router.py`, `test_oanda_readonly.py`, `test_oanda_first_test_order.py`, clases nuevas en `test_ui_redesign.py`; actualizados `test_broker_wiring.py` (import perezoso permitido en service.py/control.py), `test_safety_invariants.py` (`SUBMIT_PLAN_CALLERS` + router y script), bloqueo de red del saldo en `test_phase10_dashboard.py`/`test_dashboard_luxury.py`. Docs: `docs/oanda_adapter.md`, `.env.example` (solo nombres). REAL OFF, NAS100/XAUUSD nunca enrutados, `render.yaml` intacto.

## 2026-10-17 — BORRADOR / NO ACTIVADO: adaptador OANDA v20 PRACTICE, solo EURUSD (branch `wip/oanda-adapter`)

- Nuevos módulos sin cablear: `execution/oanda_client.py` (host `api-fxpractice.oanda.com/v3` fijo, live/http/userinfo/puerto/punto final rechazados, sin redirecciones, token/cuenta redactados, 429 y 5xx de lectura con backoff acotado, trading nunca reintentado) y `execution/oanda_broker.py` (`OandaBroker`, `size_units`, `client_extension_id`, `reconcile`, `unsupported_open_trades`). Ver `docs/oanda_adapter.md`.
- Compuerta previa de solo lectura (idempotencia por `@clientId`, sin hedging/netting, `tradeable`); timeout/5xx en POST ⇒ una lectura de conciliación y `UNKNOWN` si no se prueba, nunca re-post.
- `broker_config`: `AI_FLOOR_BROKER_DEMO_PROVIDER` (`dxtrade` por defecto | `oanda`); `read_control_snapshot()` añade `provider` (etiqueta DXtrade por defecto sin cambios). `.env.example` solo nombres.
- Revisión Codex (mismo día): latch en memoria de client ids con resultado `UNKNOWN` (`unresolved_client_ids()`; reintento ⇒ solo lectura `@clientId`, `previous_submission_unresolved`, nunca segundo POST); re-chequeo de interruptor/emergencia justo antes del POST/PUT; `RLock` por instancia en `submit_plan`/`modify_stop`/`close_market` (propiedad exclusiva de la cuenta, un proceso); evidencia de éxito validada (`malformed_fill_evidence`, `malformed_stop_evidence`); `tradeable` debe ser booleano (`pricing_malformed`).
- Tests: nuevos `test_broker_oanda_client.py`, `test_broker_oanda.py`; `test_broker_registry.py` (clave `provider`) y `test_broker_wiring.py` (módulos OANDA, `OANDA_` fuera de `render.yaml`). Sin red; REAL OFF; nada importado por runtime/floor/ui.

## 2026-10-15 — BORRADOR / NO ACTIVADO: esqueleto inactivo de broker (branch `wip/broker-structure`)

- Nuevos módulos aditivos, sin cablear: `execution/broker_port.py`, `paper_broker_adapter.py`, `dxtrade_client.py`, `dxtrade_broker.py`, `broker_config.py`, `account_registry.py` (DB propia `broker_state.db`, interruptores OFF por defecto, emergency stop, `read_control_snapshot()`). Ver `docs/broker_skeleton.md`.
- Ningún módulo de runtime/floor/ui/entrypoints los importa; `PaperBroker`, `render.yaml` y `docs/STATUS.md` intactos; `.env.example` solo con nombres. REAL OFF (`AI_FLOOR_BROKER_REAL_ENABLED` se ignora), NAS100 bloqueado, sin red en tests.
- Tests: `test_broker_paper_adapter.py`, `test_broker_dxtrade.py`, `test_broker_registry.py`, `test_broker_wiring.py`. `test_safety_invariants.py`: `SUBMIT_PLAN_CALLERS` incluye el adaptador (solo reenvía el floor report a `PaperBroker.submit_plan`).

## 2026-10-15 — Dashboard: tema claro, español simple, R realizada y vista Control (branch `wip/ui-redesign`)

- Solo visualización (`ui/`, `.streamlit/config.toml`): tema claro (`ui/static/theme.css` reemplaza `luxury.css`; fuentes del sistema, sin descargas), tarjetas grandes con frases simples y tooltip «¿Qué significa?», todo el texto de interfaz en español centralizado en `ui/i18n_es.py` (glosario: `docs/ui_glosario_es.md`). Valores guardados sin traducir (mapeo de presentación, original como tooltip).
- Arreglo R realizada (`ui/views/luxury.py`): stop original = `initial_stop` o, si el stop nunca se movió (`duration_stage` vacío), `stop` (igual que `execution/trade_duration.py`); razón en español si faltan datos («sin datos del stop original»). Aplica a la tabla de operaciones y al promedio R.
- Detalle de operación con plan (entrada/stop/meta) y diferencia entrada planeada vs fill (pips solo en EURUSD).
- Nueva vista «Control» (`ui/views/control.py`): solo lectura, interruptores y PARADA DE EMERGENCIA deshabilitados; lee `execution.account_registry.read_control_snapshot()` si existe, si no todo apagado.
- `build_setup_figure` (compartido con el PNG del correo) sin cambios; el dashboard aplica `light_setup_figure`.
- Tests: nuevo `test_ui_redesign.py`; actualizados asserts de textos/estilos viejos en `test_dashboard_luxury.py`, `test_phase10_dashboard.py`, `test_dashboard_readability.py`, `test_dashboard_mobile.py`, `test_security_hardening.py`. Sin cambios de runtime/estrategia/riesgo; REAL OFF, NAS100 bloqueado.

## 2026-10-14 — Evidence Store observe-only con catch-up OFF (branch `v2/evidence-observe-only`)

- `runtime/service.py`: con `AI_FLOOR_V2_POSITION_CATCH_UP=0` y `AI_FLOOR_MARKET_EVIDENCE_PATH` definido, cada ciclo encola (el más reciente por símbolo) su snapshot y `Scheduler.tick` lo escribe en `market_evidence.db` con `flush_evidence_observations` DESPUÉS de todos los ciclos del tick (trading, salidas, alertas). Acotado: store de vida corta con timeouts SQLite de 0,5 s, todas las velas en una sola transacción (`EvidenceStore.batch`), presupuesto de 2 s (no empieza más velas; lo hecho se confirma) e interrupción SQLite 0,5 s después. Ninguna escritura en la DB de trading (sin journal ni estado `evidence_observe`): cualquier Exception (MemoryError incluida) = solo WARNING en log. Nunca se lee en OFF; ON y `_observe_evidence` (P1-B) sin cambios. (Revisión Codex 2026-10-14, MEDIUM 1.)
- `storage/evidence_store.py`: `timeout` / `interrupt` opcionales en el constructor y `batch(expired)`; el store sigue sin leer reloj (predicados del llamador). Valores por defecto sin cambios.
- `runtime/config.py`: sin cambios (`catch_up_scope_checks` corta en la primera vela faltante, como antes; revisión Codex, MEDIUM 2).
- Nuevo CLI READ-ONLY `python -m replay.evidence_readiness` (ambas DB `mode=ro`, mismo criterio que el preflight): vela más reciente, velas faltantes desde el watermark de cada posición OPEN (conteo aritmético, máx. 50 listadas, consultas limitadas a la ventana), anomalías GAP/LATE (máx. 50) y READY/NOT_READY; una ventana > 14 días = NOT_READY sin enumerar.
- Sesiones: fuera de LONDON/NEW_YORK no hay ciclo ni fetch; el snapshot (500 velas 5m) rellena la noche y el fin de semana en el primer ciclo en sesión. Riesgo: el check es estricto 24/7 (fin de semana / pausa diaria de XAUUSD dentro de la ventana de una posición = NOT_READY); no se debilita.
- Tests: `test_evidence_observe_off.py` (24: diferido tras el tick, cola acotada, presupuesto, lock, MemoryError, halt, batch, CLI acotado); `test_runtime_catch_up` / `test_runtime_pending_gate` actualizados (OFF con ruta escribe evidencia solo en el flush, nunca la lee). REAL OFF, NAS100 bloqueado, sin Render ni push.

## 2026-10-14 — Fase 13 iniciada (docs-only)

- Fase 13 (demo PAPER 30 días) iniciada 2026-10-14 08:29 ET bajo DEC-13.1 (opción B: DB actual, sin DB nueva/sealed genesis; catch-up 0; macro fxmacrodata).
- Actualizados `docs/STATUS.md` (main `1c9118c`, merge PR #41; baseline DEC-8.20 dejado de lado por DEC-13.1), `docs/phases/phase-13.md` y el estado de `docs/phases/phase-13-runbook.md`.
- Solo documentación: sin cambios de env, runtime ni deploy. REAL OFF, NAS100 bloqueado.

## 2026-10-13 — Repo cleanup (organizational only)

- Owner-assigned task on `v2/repo-cleanup`; draft PR only, no merge/Render/env/runtime/economics change. REAL execution OFF, NAS100 blocked (unchanged).
- Deleted unused files: `PROGRESO.md` (empty), `runtime/provider.py`, `runtime/certify_finnhub.py`, `runtime/certify_official_macro.py` (no imports/refs).
- `git mv` of the 40 historical root docs (`AUDIT_*`, `V2_PHASE*`, `*_SPEC`, `V1_POSTMORTEM`, `EXPERIMENT_FREEZE`, etc.) into `docs/archive/`; root keeps `AGENTS.md`, `CLAUDE.md`, `CHANGELOG_AGENT.md`. Updated references in AGENTS.md, docs/decisions.md, docs/phases/phase-06/08, pyproject.toml, runtime/system_health.py docstring, test_phase7 docstring and two relative links. Older entries below keep their original root paths.
- Added `test_repo_cleanup.py` (deleted files absent, root doc set, archive present, relative md links and `docs/archive/` refs resolve).
## 2026-10-13 — Pre-demo fixes: cancelación de órdenes pendientes por tiempo + Setup ID (branch `v2/pre-demo-fixes`, PR #39)

- Órdenes PENDING se cancelan en el primer cierre NY (17:00 America/New_York) que sea viernes o el cierre Day-2 tras
  su envío (`order.as_of`), lo que ocurra antes (`trade_duration.order_cancel_boundary`). Razones claras:
  `TIME_CANCEL_FRIDAY_NY_CLOSE` / `TIME_CANCEL_DAY2_NY_CLOSE` en el evento `ORDER_CANCELLED` (con `boundary_utc`,
  timestamp = la frontera) y en el log. Actúa en el pase fuera de sesión del Scheduler (no necesita datos de mercado)
  y en cada ciclo antes de evaluar el fill (un pase perdido no deja que la orden se llene después). Escritura CAS
  guardada (`order_time_cancel`); sin escritura ni admisión R-HALT si no hay nada que cancelar. `paper_broker.py` intacto.
- Setup ID en alertas (ruta de setup original/V1, orden sin `setup_id`): se toma del run que envió la orden
  (`execution.setup_id` de ese run si corresponde a esa orden; si no, `setup.setup_id` de su explicación), nunca del
  run que emite la alerta (p. ej. un cierre por otro run). Sin registro sigue `NO_DATA`.
- Tests: `test_pending_order_time_cancel.py` (fronteras, DST, viernes Day-1/Day-2, pase fuera de sesión sin
  proveedor, idempotencia, ciclo retrasado, orden Day-1 intacta, resolución de Setup ID); `test_alert_trade_fields.py`
  ahora exige Setup ID real en ORDER SUBMITTED / POSITION OPENED.
- REAL OFF, NAS100 bloqueado, sin Render. pytest: baseline 1631 passed, 1 skipped, 1 xfailed; final 1640 passed, 1 skipped, 1 xfailed.

## 2026-10-13 — Pre-demo fixes: revisión independiente Codex (branch `v2/pre-demo-fixes`, draft PR)

- HIGH: la salida Day-2 por estructura se decide en orden temporal con SL/TP (`trade_duration.first_adverse_structure`
  + `_enforce_trade_duration`): SL/TP se gestiona solo hasta el momento en que el BOS adverso se conoce; un stop
  posterior ya no sustituye la salida, y un stop anterior sigue ganando. El pase fuera de sesión revisa también la
  salida por estructura en cada cierre 15m (posiciones Day 2). Tests: ciclo retrasado (ambos órdenes) y nocturno.
- MED: la escritura de duración revalida la frontera dentro del CAS (`MOVED` si otro runner ya movió el stop) y
  relee la posición; ya no cierra en la frontera Day-1 como "Day-2 close". Test de runner concurrente.
- MED: con catch-up de evidencia ON, el precio de la regla (y el 15m de estructura) sale de la evidencia
  comprometida, no del snapshot crudo; una revisión del proveedor no cambia la decisión +0.5R ni el PnL. Test.
- MED: alertas de posición muestran el stop de la posición (stop movido a entrada); R:R/Risk% siguen sobre el stop
  original de la orden. Test de alerta de cierre tras break-even.
- REAL OFF, NAS100 bloqueado. Baseline pytest 1624 passed, 1 skipped, 1 xfailed; final 1631 passed, 1 skipped,
  1 xfailed; ruff PASS.

## 2026-10-13 — Pre-demo fixes (branch `v2/pre-demo-fixes`, draft PR; no merge/deploy/Render)

- Tarea asignada por el owner. REAL execution OFF, NAS100 bloqueado, sin cambios en Render/env remoto.
- 1) Regla de duración de trade (`execution/trade_duration.py`): cierre NY 17:00 America/New_York (zoneinfo,
  DST correcto, solo días hábiles). Day-1: < +0.5R cierra (`TIME_EXIT_DAY1_BELOW_0_5R`), si no stop a entrada
  (`STOP_MOVED_TO_ENTRY`, `initial_stop` conservado). Day 2: BOS 15m confirmado en contra tras el cierre Day-1
  cierra (`TIME_EXIT_DAY2_STRUCTURE_CHANGE`); cierre Day-2 fuerza salida (`TIME_EXIT_DAY2_NY_CLOSE`); viernes
  17:00 ET cierra todo (`TIME_EXIT_FRIDAY_NY_CLOSE`). SL/TP se gestionan hasta el cierre NY antes de la regla.
  Pase fuera de sesión en el Scheduler para actuar a las 17:00 ET (sin claim de slot). Sin entrada nueva en el
  ciclo que cerró por tiempo. `PaperPosition.initial_stop/duration_stage` opcionales (bytes legacy intactos).
- 2) Macro NO_DATA cuando importa (PLAN_READY o pending): `AI_REVIEW_UNHEALTHY: macro=NO_DATA` en log+journal,
  alerta en la transición (SYSTEM_DEGRADED; RECOVERY al recuperar), un reintento en el mismo ciclo (rerun del
  floor determinista; rerun IA solo si el macro trae evidencia). Sigue NO_DATA => sin trade
  (`execution_reason=AI_REVIEW_UNHEALTHY`).
- 3) Trade Reviewer `NOT_REQUIRED` (no MISSING) sin TradePlan: no más PARTIAL_AI_FAILURE falso en WATCH.
- 4) Alertas ORDER SUBMITTED / POSITION OPENED / CLOSED con Entry/SL/TP/Quantity/R:R/Risk%/tiempos desde el
  estado PAPER durable (`runtime/notification_extras.py`; `storage/database.py` sigue byte-congelado).
- 5) `runtime/candle_cache.py`: cierres 5m por ciclo, SQLite junto a la DB, 288 barras/símbolo; el gráfico de
  alerta lo lee en solo lectura (Evidence Store como respaldo). Nueva env opcional `AI_FLOOR_CANDLE_CACHE_PATH`.
- Limitación conocida: el oráculo REX (solo V1) no modela la regla de duración; los runs donde actúa salen
  INVALID en `replay.rex_chain` (G14/NON_ECONOMIC_EVENT_IN_WRITE). El ciclo funciona con REX/R-HALT ON.
- Baseline pytest: 1590 passed, 1 skipped, 1 xfailed. Final: ver docs/STATUS.md.
## 2026-10-13 — Security audit fixes (branch `v2/security-fixes`, draft PR)

- Owner-assigned fix of the read-only security audit of `main@8b11da0` (M-1, L-1..L-7, I-1..I-5). No Render/env change, no activation, REAL execution stays OFF, NAS100 stays blocked.
- M-1/L-1/L-3 `ui/auth.py`: bytes `hmac.compare_digest` (non-ASCII safe); per-session lockout (5 failures → 300 s) plus process-wide budget (20 failures/15 min → 900 s lockout); 1 s delay and a log line per failure without the input; session expiry (`AI_FLOOR_DASHBOARD_SESSION_TTL_SECONDS`, default 8 h); password ≥ 12 chars (UI gate, cloud preflight `dashboard_auth` and `runtime.cloud.serve`); no-password access only when Streamlit is bound to loopback. Cloud Streamlit starts with `--client.showErrorDetails=none`.
- L-2: `ui/pages/` renamed to `ui/views/` so Streamlit no longer auto-routes unauthenticated page URLs.
- L-4: direct deps pinned exactly (Streamlit 1.65.0); `requirements-ci.txt` (installed by CI and Render) is now a uv-compiled lock with sha256 hashes. I-4: GitHub Actions pinned to commit SHAs.
- L-5: review export redaction list derived from `runtime/env.py:NAMES` (+ `GMAIL_APP_PASSWORD`, `EODHD_API_KEY`).
- L-6: Twelve Data key sent as `Authorization: apikey …` header, never in the URL; undecodable bodies map to `INVALID_RESPONSE`.
- L-7/I-3: Slack text escapes `& < >`; the real transport only opens `https://hooks.slack.com/` URLs (`INVALID_WEBHOOK_URL`, non-retryable).
- I-1: no hardcoded alert email; `AI_FLOOR_ALERT_EMAIL_FROM`/`_TO` required, else `NOT_CONFIGURED` (`runtime.alert_test` prints missing setting names only).
- I-2: `replay/safe_pickle.py` restricted unpickler for `lab_*.pkl` in fill_audit/risk_audit/lab_report.
- I-5: dashboard never lists a blocked symbol (NAS100) as enabled.
- Tests: new `test_security_hardening.py`; fixtures updated for the 12-char password, explicit email addresses and the `ui.views` rename.
- Codex review MED: Streamlit's default `server.address` is unset, so the documented `streamlit run ui/app.py` hit the no-password loopback gate. Launch docs (`UI_SPEC.md`, `OPERATIONAL_RUNTIME_SPEC.md`, `ui/app.py`) and the gate message now use `--server.address=127.0.0.1`; `DocumentedLaunchTests` parses each documented command through Streamlit's own CLI/config and asserts the gate opens (bare launch still refused).

## 2026-10-11 — DEC-14.2: planner policy runtime flag

- Owner Jeferson Tejeda approved 2026-10-11 11:33 ET; local feature branch only, no activation/Render/push/merge.
- Added `V2_PLANNER_POLICY=V1|V2_F3` (default V1; unknown → V1 + warning). V1 fingerprint and floor call/broker behavior preserved.
- F3 selects the existing fixed-3R planner, matching Risk geometry policy (preserving custom risk limits), and broker fill floor 2.50R in legacy and catch-up pending-order paths; SL/TP unchanged. Policy D remains forbidden in runtime.
- REX records effective broker policy; its V1-only oracle explicitly rejects F3 as unsupported.
- Added durable runtime cycle tests: 3R plans; 2.6R fills accepted and 2.4R rejected under F3, V1 rejects below 3R; both fill paths. Config/env/warning/fingerprint tests and minimal Phase 4 static gate updates.
- Baseline: 1517 unittest tests OK (1 expected failure), 196.961 s. Final: ruff PASS; diff-check PASS; full unittest 1520 tests OK (1 expected failure), 165.530 s.
- Requested full pytest: 1514 passed, 1 xfailed, 1100 subtests passed; 5 pre-existing failures in Phase 8 signal/startup AnnotateFailures.run (pytest passes TestCaseFunction, which has no failures/errors). Those two test files are unchanged; scope extension requested for a minimal harness compatibility fix. No local commit while pytest remains failing (AGENTS.md).
## 2026-10-11 — DEC-14.1 review fixes (PR #34, local only)

- Owner-authorized review fixes on `v2/opposite-setup-policy`; commit requested locally only.
  No push, merge, Render/render.yaml, activation, dependency or economic-policy change.
- Removed POLICY_V2_F3 switch and planner_options entirely from runtime/service.py;
  run_floor call byte-identical to origin/main. Restored both Phase 4 test files exactly with
  `git checkout origin/main -- test_phase4_fixed_3r.py test_phase4_target_planner.py`.
- STRUCTURE_CHANGE execute only for an OPEN same-symbol position with no same-symbol pending;
  flat uses the existing certified submit unchanged, including PAPER_ORDER_SUBMITTED. Tests compare
  full journals, durable account/orders/fills and execution records against BLOCK (fixed UUID only),
  plus pending gates both flat and with an open position. No alternate planner authorization inferred:
  legacy runtime V1 plans fail closed as not_a_fixed_3r_plan before closing; candidate/Risk untouched.
- Same setup requires same direction and entry difference <= 0.25 * original entry-stop distance.
  Original stop is read from the originating order, never substituted by current position SL;
  absent origin returns NO_DATA for similarity. Tests cover both inclusive boundaries, just inside/
  outside, moved SL, missing origin and opposite direction with matching setup_id.
- Phase 8 historical compatibility gate now admits only the exact already-authorized close_market
  addition, while comparing every other broker byte to P3 (979e1e6); other historical gates retained.
- Replay tooling/fixtures/builders and historical instructions investigated: historical source DB,
  lab PKL and raw OHLC exports absent. Original git-ignored replay_twelve_data_12m.db was acquired
  from Twelve Data Basic (no payment), 2025-10-06 through 2026-10-06. lab_run can rebuild PKL from
  that DB, but acquisition needs network/API key. Synthetic fixtures cannot restore the corpus.
  Ran `.venv/bin/python -m replay.opposite_policy_impact`; saved NO_DATA and exact provenance/reason
  in docs/replay/dec14_impact.txt. No invented impact counts.
- Baseline WORKFLOW unittest: 1540 tests, 6 failures (5 dashboard subtests + historical broker gate),
  1 expected ISSUE-012 failure. Focused review/Phase 4/storage/Phase 8: 115 passed,
  135 subtests passed. `ruff check .`: PASS. Full `.venv/bin/python -m pytest -q`: 1541 passed,
  5 failed (the same dashboard fixture failures), 1 xfailed (ISSUE-012), 1100 subtests passed
  in 175.10 s. No remaining non-dashboard failure. Local commit follows the owner’s explicit
  instruction allowing the separately fixed dashboard failures; no dashboard changes in this task.

Excepción del fingerprint de `storage/database.py` en `test_system_health.py`: además de
las exclusiones B2.1 existentes, se normaliza únicamente el fragmento
`"POSITION_CLOSED", "STRUCTURE_REVERSE_SUBMITTED", "DAILY_SUMMARY",` a
`"POSITION_CLOSED", "DAILY_SUMMARY",` y se eliminan exactamente estas cuatro líneas:

```python
                if row["event_type"] == "STRUCTURE_REVERSE_SUBMITTED":
                    execution = json.loads(row["payload"])
                elif row["event_type"] == "POSITION_CLOSED" and json.loads(row["payload"]).get("exit_reason"):
                    execution = json.loads(row["payload"])
```

Cada fragmento debe aparecer exactamente una vez. Permite seleccionar la alerta reverse y
entregar geometría/exit_reason al mapper Phase 9, sin cambiar schema ni escrituras económicas.
Todo otro byte LF-normalizado sigue fijado al SHA256 frozen V1
`80f8c9ae037bb27dbcd45403ea33655b07dfed3983f3cb6637d8704e5ef4ebde`.


## 2026-10-11 — DEC-14.1 opposite setup policy (local, owner approved 10:00 ET)

- Branch `v2/opposite-setup-policy`; PAPER only, NAS100 OFF, no Render/render.yaml/push/activation.
- Flag env BLOCK (default, unchanged fingerprint/legacy path) / STRUCTURE_CHANGE; invalid values
  fail closed with log/journal warning and DEGRADED policy-health state.
- Deterministic 15m BOS/closed original bar beyond held SL; existing validator/freshness/1H structure,
  conservative similar-zone ±one price increment, no pyramiding and no pending cancellation.
- Existing fixed-3R planner and Phase 6 candidate validation/immutable claim; both saves use runtime
  slot lock, whole-state CAS and halt/REX guards. PAPER close accounts costs once and MTM equity;
  fresh Risk V2 reservation after durable close, rejected reverse stays flat.
- Close/reverse alerts through Phase 9; terminal decisions and atomic close marker journal every
  outcome. Safe restart choice: REVERSE_ABANDONED_AFTER_RESTART, no re-close or resumed old open.
  Recovery excludes active runs of other writers; same-run/candidate reruns do not duplicate exposure.
- New `test_dec14_opposite_policy.py`: validator/broker/Risk/SQLite, full runtime integration,
  duplicates/same-direction/no-BOS/profit/loss/rejection/stale/AI/lock/pending/crash/rerun/fill/alerts.
- Baseline command from WORKFLOW using `.venv/bin/python` (bare `python` absent): 1517 tests,
  5 preexisting dashboard assertion failures (static 12:00 UTC fixture becomes STALE_DATA),
  1 expected ISSUE-012 failure. Target policy+safety: 30 passed, 16 subtests passed.
- Replay CLI reuses Phase 6 Cohort, chronological calendar, closed-bar reconstruction and production
  broker/Risk V2. Ran it; saved `docs/replay/dec14_impact.txt`: NO_DATA for both policies because
  original replay DB/lab_*.pkl are absent. No fabricated counts; corpus replay remains pending.
- Updated Phase 4 static gates for owner-authorized opt-in fixed-3R (Policy D still forbidden),
  pinned frozen storage bytes after stripping only the exact new alert selection/payload addition,
  and made Phase 8 signal-test annotation adapters work under both unittest and pytest.
- Final full `.venv/bin/python -m pytest -q`: 1537 passed, 5 failed, 1 xfailed (ISSUE-012),
  1091 subtests passed (174.27 s). All five failures reproduce the baseline dashboard clock fixture.
  Policy tests: 23 passed, 4 subtests passed; compatibility/signal/health group: 89 passed,
  109 subtests passed. Final policy/alerts/E2E/safety: 94 passed, 77 subtests passed (14.75 s).
  `git diff --check` clean. No implementation failures remain.
- Local commit withheld by AGENTS.md / WORKFLOW no-failing-tests gate. Requested owner permission
  for a minimal deterministic dashboard-fixture clock fix; no response yet. Original corpus paths
  also requested; no corpus found. No staging, commit, push or Render actions performed.


## 2026-10-11 — Phase 12 End-to-End (issue #30) — IN PROGRESS

- Owner-authorized offline work on existing `v2/phase12-e2e`; baseline before edits:
  1505 tests OK, 1 pre-existing expected failure (180.571 s).
- Added `test_phase12_e2e.py`: five tests, synthetic 5m fixture replay with closed 15m/1h
  aggregates and `replay.engine.snapshot_at`; real DemoRunner over eight cycles on temporary
  SQLite DBs. Reuses F11 structured scout injection, P8 deterministic UUID stream, production
  setup/planner/deterministic AI/risk/PaperBroker/TradeManager and PAPER instrument contracts.
- Covers XAUUSD LONG TP and EURUSD SHORT SL, orders/fills/positions linked, 3R and <=1% risk,
  five validated AI decisions with evidence references, existing-position execution block,
  WATCH after exit, exact full journal event order and duplicate-slot nonmutation.
  Expected equity: 10000 / 10000 / 10095.45 / 10209.10; realized PnL 209.10; quantities 10 / 9090.
  Gross/cost/net and mark-to-market formulas checked; current PAPER cost rate is zero.
- Asserts review reports update opening runs with closed trades, durable notifications retain
  opening-run identity, Gmail adapter delivers two close alerts through fake SMTP with DELIVERED
  ledger, and read-only UI projects persisted account/filled orders/flat positions without writes.
- Two fresh independent DBs yield byte-identical canonical journals and identical SHA-256.
  IDs generated deterministically before hashing; only REX observer wall timestamps normalized;
  every row/ID/payload/digest and all runtime/market timestamps retained. REX verification requires
  eight covered runs, ten economic events, G14 PASS, unchanged source and SQLITE_CHECKS_PASS /
  NOT VERIFIED. REX OFF yields identical economic state; runtime defaults remain OFF.
- Missing market data, macro failure, absent multiplier and AI failure block orders with explicit
  journal/review evidence and unchanged economic state. NAS100 fails configuration and run admission
  before data or writes. Socket connections forbidden in harness; no network, real email or secrets.
- Updated STATUS and phase-12 with F12-T01…T17 mapping and scope/gate limitations. No production
  bugs found, production changes, new dependencies, issues or expected failures. No certification,
  staging, commit, push, runtime activation, merge, deploy or Render changes.
- Focused suite: 5 tests PASS (2.786 s). Ruff full repository PASS (existing malformed-noqa warning
  in runtime/genesis.py:209). Final required unittest discovery: 1510 tests OK in 177.788 s,
  1 pre-existing expected failure; `PYTHONDONTWRITEBYTECODE=1 AI_FLOOR_MARKET_PROVIDER=none
  AI_FLOOR_AI_PROVIDER=deterministic /workspace/repo/.venv/bin/python -m unittest discover -q`.
  `/workspace/repo/.venv/bin/ruff check .` PASS; `git diff --check` PASS.
## 2026-10-11 — Owner-authorized Phase 9/10 small fixes (local, no commit)

- Read existing closed 5m evidence sidecar via mode=ro; last 48 symbol closes whose bar end is at or before the alert event. Worker prepares new and recovered events. Missing evidence yields NO_DATA.
- Replace plotly/kaleido chart PNG with matplotlib Agg; inline related PNG/CID email, SVG fallback on import/render failure. Pin matplotlib==3.10.7 in requirements.txt, included by requirements-ci.txt. Pip installation in /workspace/repo/.venv failed due to package-index DNS/network unavailability; real installation not verified.
- Periodic stale-SENDING recovery: 60 s constant, test-configurable interval.
- Chart reader uses only standard-library SQLite/JSON/datetime; no engine or trading/storage imports. Updated the existing evidence-consumer safety allowlist for this authorized read-only chart, retaining alert import isolation tests.
- Dashboard OPEN/PENDING/failed-run/error-severity SQL filters precede LIMIT; add mock SQL/render coverage and T10 DB-read-error → DASHBOARD_ERROR.
- Baseline: 1488 tests OK (1 expected failure), 167.408 s. Final requested suite: 1495 tests OK (1 expected failure), 176.371 s; Ruff and git diff --check PASS. No commit, push, deploy, activation or risk/economic changes.

## 2026-10-11 — Phase 11 Chaos / Failure Testing (issue #28) — IN PROGRESS

- Owner-authorized offline implementation on `v2/phase11-chaos`; baseline before edits: 1475 tests OK
  (1 pre-existing expected failure).
- Added `test_phase11_chaos.py`: exactly 17 `test_chaos_F11_T01_...` through T17 methods, in the exact F11
  checklist order from `/workspace/libro.txt`. Reuses existing DemoRunner/OperationalRuntime fakes and
  conflict/Risk V2 fixtures; mutable deterministic clock, temporary SQLite, socket connections forbidden.
- Covers AI quota/unavailability/invalid contract, unavailable/stale market and macro, SMTP failure,
  actual SQLite trigger abort, repeated transactional ENOSPC, UI read failure, scheduler/process restarts,
  existing/multiple positions, deterministically interleaved concurrent candidates, opposite setup and drawdown.
- Asserts authoritative execution/journal/ledger outcomes and durable economic state; recovery, replay
  idempotence, rollback, unchanged position geometry, normal TP closure and single fill/cost accounting.
  SMTP failure during a real cycle preserves the sole policy-authorized order. SQLite failure that also
  prevents failure bookkeeping keeps the lock until durable recovery; no fabricated journal is expected.
- IA total failure retains analytical PLAN_READY but execution is POLICY_REVIEW_UNAVAILABLE (no order);
  macro failure is NO_SETUP. Tests characterize these existing policies; no production fixes, new issues,
  new expected failures, trading-semantics changes or dependencies.
- Updated `docs/STATUS.md` and `docs/phases/phase-11.md`: IN PROGRESS, full scenario mapping and limitations.
  Certification still needs predecessor gate, independent review and owner approval.
- Targeted suite: 17 tests PASS. Full required unittest discovery: 1492 tests OK in 155.521 s
  (1 pre-existing expected failure); PYTHONDONTWRITEBYTECODE=1, market=none, AI=deterministic.
  `/workspace/repo/.venv/bin/ruff check .` PASS; `git diff --check` PASS.
- No network, commit, staging, push, merge, deploy, Render changes or runtime activation.

## 2026-10-11 — Phase 9 Codex review fixes (PR #21, M1–M3) + T12 chart + post-cycle wiring — IN PROGRESS
## 2026-10-11 — Phase 10 / issue #22 — Command Center read-only

- Owner-authorized local extension of the existing `ui/` package on `v2/phase10-command-center`.
  Added persisted Overview (equity/realized/unrealized PnL), all-symbol positions/pending orders,
  latest setups, AI/risk decisions, System Health sidecar + provider/API/scheduler observations,
  notification delivery alerts, exact run/setup history search, and errors. Existing snapshot pages remain.
- SQLite URI `mode=ro` only, no migrations or missing-DB creation. Missing delivery tables/rows yield
  `NO_DATA`. IDs come from persisted fields/payloads; account values are not recomputed.
- Reused `ui/auth.py` password gate; configured local passwords now also gate access. Mobile CSS stacks
  columns at ≤640px. No dependencies, trading/risk/broker changes, runtime activation or deploy.
- Added `test_phase10_dashboard.py`: real AppTest navigation on populated/empty fixture DBs, exact values,
  run/setup searches and AND/no-match behavior, missing delivery table/DB, authentication before SQLite,
  write rejection, and unchanged trading/health DB hashes. Responsive rule checked; physical-device visual
  review remains pending. Updated STATUS and phase document to IN PROGRESS with F10-T01…T22 test map.
- Baseline: 1426 tests PASS, 1 expected failure (170.858s). Focused Phase 10: 7 tests PASS (2.373s).
  Final required unittest discovery: 1433 tests PASS, 1 expected failure (169.791s), with
  `PYTHONDONTWRITEBYTECODE=1 AI_FLOOR_MARKET_PROVIDER=none AI_FLOOR_AI_PROVIDER=deterministic`
  and `/workspace/repo/.venv/bin/python`. `/workspace/repo/.venv/bin/ruff check .` PASS;
  `git diff --check` PASS.
- No git add, commit, push, merge, deploy or activation.

## 2026-10-10 — FreezeAmendmentTests / git init environment diagnosis

- Preserved all pre-existing changes on `v2/phase8-p4b`, HEAD `720f4b4`. No test or production-code edits.
- Minimal sandbox probe reproduced Git exit 128: `fatal: cannot change to '.../git-init-probe-llpwl2a6':
  Permission denied`. The identical Git command on that SAME directory outside the sandbox returned 0;
  the probe was then removed. Sandbox tempfile access also raised WinError 5 before test setup reached Git.
- All five FreezeAmendmentTests pass outside the sandbox. After full-suite discovery/imports, the affected module
  runs 26 tests: 25 PASS, 1 skip (symlink privilege). Discovery leaves the Git environment unchanged.
- This is demonstrated executor/temporary-directory permission failure locally, not evidence for a code patch.
  Copilot's precise stderr and eight skipped-test IDs were not supplied; their exact environment cannot be inferred.
  Use a test executor authorized to create/access temporary repositories; do not suppress failures or add skips.
- Single full run (standard unittest discovery, diagnostic result logger): 1376 total, 1370 PASS, 0 errors/failures,
  6 skipped, 188.550s. Git environment delta was empty before each of the five affected tests in full-suite order.
  Skips: `A2ToolTests.test_nc5_symlink_component` (symlink privilege), all 3 `RealSignalTests` in
  `test_phase8_rhalt_signals`, both `RealSignalDuringStartupTests` in `test_phase8_rhalt_startup` (POSIX required).
  Copilot's 1376/5 errors/8 skips implies 1363 PASS, not a different suite size; its 2 additional skips remain
  unattributed without their IDs/reasons. `git diff --check` PASS; no commit, push, merge, deploy or activation.

## 2026-10-10 — HIGH-8.1 local correction (Owner amendment to DEC-8.1)

- Owner authorization received after the preflight below. Exact scope and economic comparability are recorded in
  `HIGH81_OWNER_DECISION.md`; historical DEC-8.1 and `EXPERIMENT_FREEZE.md` remain intact.
- `runtime/service.py` feeds the OFF route's normalized 5m snapshot for EURUSD/XAUUSD into the existing
  `execution/position_catch_up.py`. The core accepts observed bars without adding data/provider dependencies,
  preserving its per-bar durable watermark, CAS, slot ownership, halt checks and stop/target precedence.
- ON Evidence Store semantics and explicit partial scopes remain unchanged. OFF creates no market sidecar.
  Pending fills remain on the current gated bar. No strategy, sizing, Risk Engine, Paper Broker or TradeManager edits.
- `replay/rex_oracle.py` recognizes the explicit snapshot-management identity, with historical record compatibility.
- Six new runtime test methods cover both symbols and LONG/SHORT, intermediate SL/TP, gaps/priority, ordering,
  no lookahead, malformed bars, restart before/after commit, duplicate cycles and pending-fill idempotence.
  Existing expectations for the old omission were updated. Historical missed-touch tests now seed the old durable
  watermark directly. REX/demo/halt fixtures use actually closed bars; halt injection targets per-bar admission.
- Focused regression: 201 tests PASS; historical preview/simulation after the shared fixture adjustment: 12 PASS
  (6 overlap the focused group). Earlier focused failures exposed obsolete expectations and test fixtures;
  the core isolation test also caught a data-layer import, which was removed rather than weakening the test.
- First full run: 1376 tests, 4 failures / 1 error / 6 skips. All five concerned additional legacy runtime fixtures
  (bars starting at the current slot) or the former ERROR outcome rather than the catch-up's explicit
  STALE_PAPER_STATE event. Corrected fixtures/expectations in `test_phase1_isolation.py`,
  `test_phase6_conflict_characterization.py`, `test_stale_safe_writers.py`; their 33 tests PASS, preserving
  concurrency, bounded retry, zero lost updates and zero stale management writes. These concrete failures justify
  a second full run; no further executable-code changes were needed.
- Final full regression: `python -m unittest discover -q` — 1376 tests, OK (6 skipped), 192.520s:
  1370 passed, 6 skipped. `git diff --check` PASS. Existing CI command verified locally; remote CI not triggered.
  HIGH-8.1 fixed locally for the authorized default OFF EURUSD/XAUUSD route; READY FOR COPILOT REVIEW.
- Economic effect: first observed intermediate touch may change exits/PnL/equity and downstream decisions.
  No historical repair; no mixing with the frozen baseline. Missing provider history is not reconstructed.
- A2/G-8.INT remain separate and inactive. PAPER ONLY / REAL OFF / NAS100 OFF. No operational DB, deployed flag,
  Libro Maestro or freeze changes. No git add, commit, push, merge or deploy. Not Phase 8 certification.

## 2026-10-10 — HIGH-8.1 focused preflight (Owner decision required)

- Baseline inspected: `v2/phase8-p4b`, `720f4b4d410e19c723dcaaefaad34aab337f3fe5`.
  Pre-existing untracked `V2_PHASE8_OWNER_READINESS_PACKAGE.md` preserved.
- Reproduced the default/OFF intermediate SL/TP omission using the existing runtime tests. The ON path already
  calls `catch_up_position` over committed chronological evidence, with durable per-bar saves and recovery.
  No additional catch-up implementation is missing from `runtime/service.py` for an in-scope symbol.
- DEC-8.1 in `V2_PHASE8_EXECUTION.md` explicitly preserves the OFF runtime; changing that path changes realized
  exits/PnL versus the frozen baseline. Requested the exact Owner decision to replace the default path locally
  and record supersession of DEC-8.1. No approval inferred for operational activation or a new baseline.
- Focused baseline: 68 tests PASS (`test_runtime_catch_up`, `test_position_catch_up`,
  `test_position_catch_up_concurrency`, `test_phase8_catch_up_certification`, `test_phase8_catch_up_scope`).
  Initial sandbox run hit Windows temporary-directory access errors; rerun outside the sandbox passed.
- Full baseline executed once: `python -m unittest discover -q`, 1370 tests, OK (6 skipped), 188.423s.
  `git diff --check` PASS. CI workflow exists but remote CI was not run. These are baseline results, not fix evidence.
- A2 and G-8.INT remain separate operational follow-ups, neither activated nor prerequisites for running local
  tests. This entry does not close HIGH-8.1 or certify Phase 8. No executable code, flags, operational databases,
  freeze or Libro Maestro changed; PAPER only, REAL OFF, NAS100 OFF. No commit, merge or deploy.

## 2026-10-10 — V2 Phase 8 P4b audit fix (Copilot REQUEST CHANGES on 7c82cf3)

- MEDIUM-1 (reproduced on 7c82cf3: `--out` equal to the source destroyed the P1 DB; equal to `--archive` overwrote
  the archive with JSON; an existing file was overwritten): `replay/archive_anchor.py` validates `--out` BEFORE any
  work (canonical realpath vs the source, its `-wal`/`-shm`, the archive and its sidecars; any existing path refused,
  hardlinks and symlinks included) and writes it with `O_CREAT | O_EXCL` (+ `O_NOFOLLOW` where available).
- LOW-1: `replay/a2_readiness.py` genesis-only = EDG equal to the expected genesis (no order, fill, position, closed
  trade or account change), one GENESIS_PREPARED, no economic event, run or E0; only the non-economic rows of a sealed
  start stopped before E0 are allowed. Findings listed.
- LOW-2: `replay/freeze_amendment.py` requires full 40-hex SHAs and checks each resolves to itself.
- LOW-3: `replay/g8int.py` G2 from a section 6.7 rehearsal record (PASS only bound to the deployed commit, no STOP);
  G3 / G4 classifiers tested at their exact thresholds. LOW-4: CI annotations list every skipped test; NC5 emits a
  nominal notice when it executes.
- No A2, E0, Owner signature, deploy, PR or merge. Phase 8 BLOCKED.

## 2026-10-09 — V2 Phase 8 P4b preparation (tools, validators, G-8.INT; no A2 created)

- Agent: Claude. Branch `v2/phase8-p4b` from the certified P2b `fd29dc7`.
- P2b LOW-1: `runtime/genesis.py` `SealedStore` opens the sealed DB with URI `mode=rw` (never creates it); used by the
  sealed constructor and the sealed preflights. A file removed after steps 1-4 → refusal, no residual file or account.
- P2b LOW-2: I-G18 kept as specified (design 5.1.3.2): a start without a PASS seal check → NOT VERIFIED; a FAIL check →
  INVALID; regression test added.
- Finding (fixed): the P2b `SEALED_BASELINE_SHA` constant in `runtime/genesis.py` would have forced `Y_P2` to touch a
  third file (violating I-C6). X_P2 is now read from `runtime.cloud_runner.EXPERIMENT_BASELINE_SHA` (the only pin);
  while it is still the P1 baseline no sealed start can pass.
- New read-only tools: `replay/archive_anchor.py` (P1 archive, flat proof, unsigned OAR-A), `replay/a2_readiness.py`
  (3.11.1 items 2/5, I-C5/I-C7, genesis-only first start), `replay/freeze_amendment.py` (I-C6, NC10),
  `replay/g8int.py` (G1-G15 + 10 integration points; PASS / FAIL / NOT VERIFIED / BLOCKED; global BLOCKED).
- Docs: `V2_PHASE8_CERTIFICATION_CHECKLIST.md`. Tests: `test_phase8_p4b.py` (synthetic, ephemeral keys).
- No A2, E0, Owner signature, deploy, PR or merge. HIGH-8.1 OPEN; M-5 OPEN; B-STRICT PROVISIONAL; Phase 8 BLOCKED.

## 2026-10-09 — V2 Phase 8 P2b Sealed Genesis (DEC-8.22-i; Owner D-1..D-4; implementation only)

- Agent: Claude. Branch `v2/phase8-p2b` from the P4a closure `ce0ebe1`.
- New: `runtime/genesis.py` (SEALED_RUNTIME: `verify_startup_context` = steps 1-4 before any SQLite open, signature via
  `ssh-keygen -Y`, canonical OAR-G, X_P2 pinned at the P4b cut (`SEALED_BASELINE_SHA = None` until then), Y_P2 =
  `AI_FLOOR_GIT_COMMIT`, file identity; `seal_check` = steps 2-5 with the REX chain head after E0; preflight checks),
  `runtime/genesis_prepare.py` (the only creator: signed authorization, create-exclusive, account 10000 and
  `GENESIS_PREPARED` in one transaction), `replay/genesis_anchor.py` (G2 checks and the unsigned OAR-G body; never
  signs), `replay/genesis_verifier.py` (GEN-1/2, I-G18, NG40/41; integrated in G15 via `replay/rex_chain.py`).
- Changed: `runtime/config.py` (`AI_FLOOR_V2_SEALED_GENESIS`, OFF, requires REX ON), `runtime/service.py` (sealed
  context required; identity re-check after open; seal check as a P4a STARTUP site; no creation path when sealed),
  `runtime/demo_runner.py` / `runtime/cloud.py` (preflights never create the DB when sealed; rolled-back probe kept,
  D-3), `runtime/cloud_runner.py` (sealed order: lock, steps 1-4, then any DB open).
- Test compatibility: `test_phase8_economic_digest.py` inventory superseded again (genesis modules use the P2a
  library). New tests: `test_phase8_genesis.py` (ephemeral keys; no Owner signature).
- Flags OFF; SEALED_RUNTIME is not the only mode yet (P4b cut). No A2, E0, deploy, PR or merge. Phase 8 BLOCKED;
  HIGH-8.1 OPEN; M-5 OPEN; B-STRICT PROVISIONAL.

## 2026-10-09 — V2 Phase 8 P4a closure (documentation only)

- Agent: Claude. Branch `v2/phase8-p4a`; certified SHA `d21ce66` (CI `37998764069` SUCCESS; Copilot CERTIFY subject
  to LOW-4).
- Recorded: Owner approval of the DEC-8.17b amendment (STARTUP deferral of `T_h`; restricted category R); LOW-4
  resolved documentarily; LOW-1…LOW-3 accepted as residual risks; P4a CERTIFIED — COMPONENT LEVEL.
- Files: `V2_PHASE8_P86_HANDOFF.md` (DEC-8.17b row, new section 2b), `V2_PHASE8_P86_DESIGN.md` (3.8 note),
  `V2_PHASE8_EXECUTION.md` (P4a closure).
- No functional code changed. Libro Maestro not updated. M-5 OPEN; HIGH-8.1 OPEN; B-STRICT PROVISIONAL; Phase 8
  BLOCKED.

## 2026-10-09 — V2 Phase 8 P4a R-HALT, correction of Copilot MEDIUM-1 and restricted R

- Agent: Claude. Branch `v2/phase8-p4a`, audited SHA `3b311e3` (REQUEST CHANGES).
- MEDIUM-1 (reproduced: 23 post-T_h writes on a new DB, 28 on an existing one): `runtime/halt.py` `startup_write`
  guards every startup write in `OperationalRuntime` (`Store` open, `recover`, startup events, account creation,
  startup state, heartbeat), `DemoRunner` (preflight probe, runner state, `EXPERIMENT_STARTED`, notifications) and
  `cloud_runner` (cloud preflight); after T_h the constructor stops without writing (now 0 post-T_h writes). A halt
  requested during a startup check + write is deferred by the handler to the end of that section (CI showed that
  OS-level signal masking did not defer the handler; replaced by this OS-independent deferral).
- R restricted (Owner ratification, conditioned): `HaltGate.allow_evidence` validates run, process, event type,
  admitted write and duplicates at REX write time; REX_FAILURE is never written after T_h; REX_RUN carries
  `process_id`; `replay/halt_verifier.py` checks the post-halt REX_RUN (one, halted run, same process).
- Found and fixed while testing: `runtime/rex.py` `rule_identity` cache ignored its arguments (a bare recorder could
  drop the gate constants from later runtime identities).
- Design §3.8: P4a implementation note (D-1, restricted R, STARTUP). New tests: `test_phase8_rhalt_startup.py`.
- Flags OFF; no activation. M-5 OPEN. Phase 8 BLOCKED; HIGH-8.1 OPEN.

## 2026-10-09 — V2 Phase 8 P4a R-HALT (DEC-8.17b Alternative 1, D-1; implementation only)

- Agent: Claude. Branch `v2/phase8-p4a` from the certified P3 `979e1e6`.
- Pre-correction (separate commit `8c4d249`, CI success): `replay/rex_chain.py` classifies malformed input
  (economic JSON, missing fields, unexpected types, malformed REX records, non-UTF-8 text, an unprocessable copy,
  snapshot cleanup) as CERTIFICATION_INVALID with a code and a structured `context`; criteria unchanged.
- New `runtime/halt.py`: `HaltGate` (`request` = the signal handler's single assignment; `admit` = L(W);
  `pre_save` = read 2; `allow`), `HaltRefused`, `HaltPending`, `PERSISTENCE_SITES` (I-R14),
  `halt_bookkeeping` (one H transaction: the halted run's status fields, the release of THAT run's symbol lock —
  D-1, a documented exception to the original I-R1c allowlist — and one HALT_OBSERVED row; a failure rolls back and
  the halt is not confirmed), `startup_halt_check` (read-only, before any write; the resume token must equal the
  latest HALT_OBSERVED journal id; no automatic resume; no resume write).
- New `replay/halt_verifier.py` (I-R1b, I-R9, I-R13, post-halt allowlist, missing evidence, residual measurement),
  integrated in G15; never VERIFIED.
- Changed: `runtime/config.py` (`AI_FLOOR_V2_RHALT`, OFF by default, requires REX ON; legacy fingerprint unchanged),
  `runtime/service.py` (admission per attempt incl. every STALE retry, read 2 before `save_paper`, every persistence
  site guarded, HALTED / HALT_REFUSED / HALT_UNCONFIRMED), `execution/position_catch_up.py` and
  `execution/pending_order_gate.py` (`halt_gate=None`), `runtime/scheduler.py` (no claim after T_h),
  `runtime/demo_runner.py` (S suppressed; process-level HALT_OBSERVED at close; P keys only),
  `runtime/cloud_runner.py` (handler sets T_h; startup barrier), `runtime/rex.py` (REX_WRITE `admission`:
  process id, seq, L(W), read2, T_stop), `replay/rex_oracle.py` (HALTED runs).
- Post-T_h allowlist: H, P and R (REX evidence of admitted writes and of the halted run, required by DEC-8.17b (b)).
- Not changed: `storage/database.py`, `storage/economic_digest.py`, risk, adapter, broker, AI models and prompts.
- Tests: `test_phase8_rex_chain_malformed.py`, `test_phase8_rhalt_gate.py`, `test_phase8_rhalt_runtime.py`,
  `test_phase8_rhalt_signals.py` (real POSIX SIGTERM in child processes: CI only; crash/restart everywhere).
- Flags OFF; no activation, deploy, PR or merge. M-5 OPEN (independent review required). Phase 8 BLOCKED;
  HIGH-8.1 OPEN.

## 2026-10-09 — V2 Phase 8 P3 REX, G14 oracle and G15 chain verifier (DEC-8.22-d; implementation only)

- Agent: Claude. Branch `v2/phase8-p3` from the certified P2a-lib `f707b32` (`v2/phase8-execution` fast-forwarded
  `a43d10e..f707b32`, no merge commit; `main` unchanged). Owner decisions O-1…O-7 (2026-10-09).
- New:
  - `runtime/rex.py`: `rex_encode` (typed, unambiguous), `RexRecorder` (stages ST0–ST10, write evidence,
    `rex_digest = H("V2REX/1", cj(...))`, payload limit, failure isolation), `RexStore` (`save_paper` forwarded
    unchanged), `RexProviderProbe` (AI request identity). Journal rows `source='rex'` (REX_WRITE / REX_RUN /
    REX_FAILURE); no schema change. EDG / psh only in a read transaction opened on a connection without one;
    `data_version` + `total_changes` + CAS read-back as integrity evidence, with their limits documented.
  - `replay/rex_oracle.py`: independent G14 oracle (standard library only): F1, ST4 V1 sizing (`float.hex`), F1b,
    V-P1…V-P3, A1–A4, H1, P1, legacy and gated pending eligibility, the V1 fill gate clause by clause, ST8, ST9,
    ST2 management; every write reproduced from its pre-state; reconstructed values labelled.
  - `replay/rex_chain.py`: read-only G15 verifier on a backup-API copy; best result `SQLITE_CHECKS_PASS / NOT VERIFIED`.
  - Tests: `test_phase8_rex_writer.py`, `test_phase8_rex_inertness.py`, `test_phase8_rex_oracle.py`,
    `test_phase8_rex_chain.py`.
- Changed: `runtime/config.py` (`AI_FLOOR_V2_REX`, OFF by default, legacy fingerprint unchanged when OFF);
  `runtime/service.py` (REX stages, RexStore / observer / probe only when ON; flag OFF path call-identical);
  `execution/position_catch_up.py`, `execution/pending_order_gate.py` (`observer=None` hooks, failures ignored);
  `test_phase8_economic_digest.py` (explicit supersession, Owner authorization 2026-10-09: `runtime/rex.py` is now
  the only runtime importer of the P2a library).
- Not changed: `storage/database.py` (hash-pinned), `storage/economic_digest.py`, risk, adapter, broker, AI, floor.
- Inertness: twin ON/OFF runs byte-identical in the five PAPER tables, runs and non-REX journal, also under injected
  failures at every REX point.
- AI observability correction (Owner decision 2026-10-09): `ai/runtime.py` gains a passive request observer
  (`observe_requests`, a ContextVar, OFF by default; observer failures swallowed; no extra provider call, no change to
  requests, responses, audit or order). Every request `call_agent` handles is observed from the real `AIRequest`
  object, emitted or skipped, with the exact `evidence_fingerprint` material. The G14 oracle distinguishes
  EMITTED_OBSERVED, LEGITIMATE_OMISSION (verified independently: fingerprint recomputed, skip rule re-evaluated) and
  insufficient / contradictory evidence (FAIL); no request is ever synthesized. New test module
  `test_phase8_rex_ai_observability.py`; EURUSD runs whose macro agent has no news now pass G14 legitimately.
- Flag OFF; no activation, deploy, PR or merge. B-STRICT not certified; DEC-8.18 PENDING. Phase 8 BLOCKED;
  HIGH-8.1 OPEN; M-5 OPEN.

## 2026-10-08 — V2 Phase 8 P8.6 Owner decisions and implementation handoff (documentation only)

- Agent: Claude. Branch `v2/phase8-execution`; baseline `f2f87be`.
- Recorded:
  - DEC-8.13, 8.14, 8.15, 8.16, 8.17 (G-8.INT measurement policy; an earlier "emergency stop" label corrected),
    8.17b (halt stop policy, Alternative 1), 8.20 (A2) and 8.21 APPROVED POLICY;
  - DEC-8.21b PROVISIONAL;
  - DEC-8.18 / 8.19 PENDING;
  - DEC-8.22-a…j and OP NOT AUTHORIZED;
  - the package plan approved for planning only; P1 split into P1-A (LOW-1) and P1-B (catch-up); DEC-8.15 added to
    the P1 gate; the Libro Maestro deferred by the Owner.
- New: `V2_PHASE8_P86_HANDOFF.md` (register, packages P1–P4, sequence, P1 prompt not executed, blockers).
- Appended: a P8.6 section in `V2_PHASE8_EXECUTION.md`; §8.4 in the design.
- Libro Maestro: latest accessible v1.7 (Phase 5 checkpoint) predates Phases 6–8, so the current version is not
  verifiable and it was not edited.
- No code, tests, config or DB changed; not committed. Phase 8 BLOCKED; HIGH-8.1 OPEN; M-5 OPEN.

## 2026-10-08 — V2 Phase 8 P8.6F10 (closing the P8.6R10 HIGH; documentation only)

- Agent: Claude. Branch `v2/phase8-execution`; baseline `f2f87be`. Complete P8.6R10 read; findings checked against the
  code (also: `Store(path)` creates a missing DB).
- `V2_PHASE8_P86_DESIGN.md`:
  - start-mode contract: GENESIS_PREPARATION only through a separate tool with an Owner-signed authorization and
    create-exclusive; SEALED_RUNTIME hard-wired with no creation branch;
  - the default is refusal;
  - configuration and DB identity verified before any DB open;
  - start-mode failure policy;
  - R-GEN-1a…e requirements;
  - NG54–NG61.
- Not committed; no code, DB, config or deploy change. Phase 8 BLOCKED; HIGH-8.1 OPEN; M-5 OPEN.

## 2026-10-08 — V2 Phase 8 P8.6F9 (final targeted correction of P8.6R9; documentation only)

- Agent: Claude. Branch `v2/phase8-execution`; baseline `f2f87be`. Complete P8.6R9 read; findings checked against the
  code.
- `V2_PHASE8_P86_DESIGN.md`:
  - sealed-genesis mode (OAR-G by external configuration): no account creation after sealing;
  - a gate inside the constructor before any economic write;
  - failure policy before and after `E0`;
  - `GENESIS_SEAL_CHECK` evidence per process start;
  - the restoration-detection overclaim withdrawn;
  - R-HALT terminology unified (Alternative 1 / 2, "second check before invoking `save_paper`").
- Not committed; no code, DB, config or deploy change. Phase 8 BLOCKED; HIGH-8.1 OPEN; M-5 OPEN.

## 2026-10-08 — V2 Phase 8 P8.6F8 (closing P8.6R8 findings; documentation only)

- Agent: Claude. Branch `v2/phase8-execution`; baseline `f2f87be`. Complete P8.6R8 read; findings checked against the
  code.
- `V2_PHASE8_P86_DESIGN.md`:
  - account genesis before the period (G0–G5, OAR-G, `edg_start = edg_genesis`); no account reconstruction after `E0`
    is certifiable; R-GEN-1 as a future requirement;
  - the stop policy corrected (Alternative 1 admission contract recommended; the residual may open a transaction after
    `T_h`; Alternative 2 needs a pinned-module authorization);
  - chain-head anchors per UTC day, gaps ≤ 26 h, independent of counting;
  - G14 scoped to deterministic rule replay.
- DEC-8.17b and DEC-8.21b PENDING; M-5 OPEN. Not committed; no code, DB, config or deploy change.

## 2026-10-08 — V2 Phase 8 P8.6F7 (closing P8.6R7 findings; documentation only)

- Agent: Claude. Branch `v2/phase8-execution`; baseline `f2f87be`. Complete P8.6R7 read; every finding checked
  against the code.
- `V2_PHASE8_P86_DESIGN.md`:
  - EDG complete economic digest (all rows of the five PAPER tables, including closed positions; startup
    reconciliation covered);
  - trust root and the INVALID / NOT VERIFIED / VERIFIED classification, with the limitation stated (reverted
    alterations are not detectable);
  - halt Policies A / B (recommend B, pre-begin abort at the caller, no change to `storage/database.py`), with no
    "zero effect" claim;
  - adapter stage F1b and AI provenance V-P1…V-P3;
  - post-halt persistence policy by category (allowlist: the HALTED bookkeeping plus `heartbeat`, `scheduler`,
    `runner`).
- DEC-8.17b and DEC-8.21b PENDING; M-5 OPEN. Not committed; no code, DB, config or deploy change.

## 2026-10-08 — V2 Phase 8 P8.6F6 (closing P8.6R6 findings; documentation only)

- Agent: Claude. Branch `v2/phase8-execution`; baseline `f2f87be`. Complete P8.6R6 read; every finding checked
  against the code.
- `V2_PHASE8_P86_DESIGN.md`:
  - REX AI / floor reproduction (F1, A1–A4 from recommendations, base status and warnings; P1 / H1; rule versions;
    missing-value rules) and full pending-order inputs;
  - B-STRICT evidence-gap rule (any unevidenced economic write invalidates the whole period; day exclusion never
    applies to writes; no backfill), with B-QUARANTINE assessed;
  - R-HALT linearization (T_sig / T_h / T_ack; single-read admit point as the write start; per-operation coverage;
    race tests);
  - I-R1 replaced by I-R1a–d.
- M-5 OPEN. Not committed; no code, DB, config or deploy change. Phase 8 BLOCKED; HIGH-8.1 OPEN.

## 2026-10-08 — V2 Phase 8 P8.6F5 (closing P8.6R5 findings; documentation only)

- Agent: Claude. Branch `v2/phase8-execution`; baseline `f2f87be`. Complete P8.6R5 read; every finding checked
  against the code.
- `V2_PHASE8_P86_DESIGN.md`:
  - V1 fill gate (`current_equity` and `equity_at_submission` at 1 %) added to the coupling; the "sizing only" claim
    withdrawn;
  - REX completed (inputs → decisions → effects → evidence; AI statuses, `ai_healthy`, `paper_policy`, fill-gate and
    `submit_plan` inputs; not-evaluated paths);
  - atomicity alternatives A vs B (recommend B, PENDING);
  - `psh` defined over the `paper_state` tuple, stages ST0–ST10, G15 stage replay;
  - R-HALT CP1–CP9, bookkeeping, verifier-level recovery, I-R1 restated (M-5 OPEN);
  - A2 contract (X_P2/Y_P2 baseline, pinned DB path, backup-API archive, OAR-A, inode/nlink/symlink checks, flat
    proof);
  - decisions split into policy / implementation / operational levels (acyclic); R3-5 and R-21 corrected.
- No code, DB, config or deploy change; not committed. Phase 8 BLOCKED; HIGH-8.1 OPEN.

## 2026-10-08 — V2 Phase 8 P8.6F4 (closing P8.6R4 findings; documentation only)

- Agent: Claude. Branch `v2/phase8-execution`; baseline `f2f87be`. Complete P8.6R4 read.
- Grounding correction: the runtime sizes with V1 `riesgo.evaluar_trade_plan` (V2 `reserve_and_submit` is not
  wired); §1.9 corrected.
- `V2_PHASE8_P86_DESIGN.md`:
  - G14/G15 evidence contract (REX, a future observability-only runtime addition), covering not-evaluated runs;
    oracle and sequence replay;
  - T14 as a full 8-column row, an id partition with no gaps, explicit transition ids with `seq = MAX` checks;
  - multi-symbol sequential semantics documented unchanged;
  - continuity A1 / A2 / B comparison (recommend A2: new DB, fresh 10000 account);
  - exact post-halt activity, R-HALT-1…5 requirements; M-5 kept OPEN (partial);
  - M1–M10 table restored (dropped by P8.6F3); DEC-8.13…8.22 all PENDING.
- Not committed. Phase 8 BLOCKED; HIGH-8.1 OPEN; not certified.

## 2026-10-08 — V2 Phase 8 P8.6F3 (design correction after the complete P8.6R3; documentation only)

- Agent: Claude. Branch `v2/phase8-execution`; baseline `f2f87be`. All four audit reports (P8.5R2, P8.6R, P8.6R2,
  P8.6R3) received and reconciled. `V2_PHASE8_P86_DESIGN.md`:
  - H-1: closing-event attribution (E1–E7), origin run identity kept, `run_id` uniqueness; review_reports removed from
    the digest, with a frozen snapshot.
  - H-2: self-hash exclusion rule and a non-circular DAG; Owner-retained SSH-signed anchors; a digest-stability
    argument and the encoding invariant I-H6.
  - Shared equity (new HIGH): A vs B comparison; mixed-path segments TECHNICAL_ONLY; G1/G14.
  - Halt: control-plane procedure H1–H7.
  - Decisions DEC-8.13…8.22 all PENDING; traceability, invariants and re-audit readiness added.
- No code, DB, flag or deployment change; not committed. Phase 8 BLOCKED; HIGH-8.1 OPEN; not certified.

## 2026-10-08 — V2 Phase 8 P8.6F2 (targeted design remediation after P8.6R2; documentation only)

- Agent: Claude. Branch `v2/phase8-execution`; baseline `f2f87be`. `V2_PHASE8_P86_DESIGN.md`: H-1 history mapping
  by journal id (M1–M10, PRE_START quarantine, fail-closed), H-2 deterministic integrity (canonical JSON, SHA-256,
  membership T1–T13, PREPARE→SIGN→COMMIT→ANCHOR, crash recovery, verification V1–V6, stated limits), M-2 evidence
  completeness (STRICT/CALENDAR), M-5 operator table, experiment status axes FS/EX/PD/RS, G-8.INT counting rules;
  invariants and negative tests; items for independent validation. P8.6R2 report not attached. Not committed.

## 2026-10-08 — V2 Phase 8 P8.6F (design correction after P8.6R; documentation only)

- Agent: Claude. Branch `v2/phase8-execution`; baseline `f2f87be`. `V2_PHASE8_P86_DESIGN.md` revised: H-1/H-2
  (immutable hash-chained segments, per-segment full historical validation, separate opening/closing equity, external
  anchor, freeze amendments, per-segment 14-day counter), M-1…M-7, read-only experiment-state classification
  (NOT_STARTED/STARTED/ACTIVE/ENDED/INCONSISTENT), risk matrix, DEC-8.13…8.22 as proposals. The P8.6R report was not
  attached. No code, DB, flag or deployment change; not committed. Phase 8 BLOCKED; not certified.

## 2026-10-08 — V2 Phase 8 P8.6 (design only: per-symbol catch-up, experiment continuity, LOW-1)

- Agent: Claude. Branch `v2/phase8-execution`; baseline `f2f87be`. Design document `V2_PHASE8_P86_DESIGN.md` only;
  no code, config, flag, DB or deployment change; not committed (no commit authorization in P8.6).
- LOW-1 reproduced on temporary copies (unknown review decision -> gate CLEAR); fix proposed, not implemented.
- Owner decisions DEC-8.13 … DEC-8.22 requested. HIGH-8.1 OPEN for the runtime; Phase 8 BLOCKED; not certified.

## 2026-10-08 — V2 Phase 8 P8.4G (fixes for the two P8.5R HIGH findings)

- Agent: Claude. Branch `v2/phase8-execution`; baseline `b5a8c3c`; final SHA = the commit that adds this entry.
- FIX A `1dc82c8` (DEC-8.11): `persistent_write_probe` (BEGIN IMMEDIATE + main table + ROLLBACK, schema/version
  unchanged, OS write access) replaces the TEMP probes of `cloud_preflight` and `demo_runner.preflight`; read-only,
  permission or SQLite errors -> NOT_READY. `test_phase8_trading_db_preflight.py` (5).
- FIX B `87dfd62` (DEC-8.12): only ACCEPTED_FIRST_COMMITTED is final; effectively ESCALATED anomalies block the gate;
  append-only auditable review rows. `EscalationTests` (6); three earlier expectations explicitly superseded.
- Full suite 1080. No activation; HIGH-8.1 OPEN for the runtime; Phase 8 BLOCKED; not certified.

## 2026-10-08 — V2 Phase 8 P8.4F (fixes for the P8.5 independent review blockers)

- Agent: Claude. Branch `v2/phase8-execution`; baseline `0f67527`; final SHA = the commit that adds this entry.
- FIX1 `70ff008` (R2 HIGH): Evidence Store preflight uses a persistent write probe (BEGIN IMMEDIATE + main table +
  ROLLBACK, no residue) instead of a TEMP table; a read-only store fails closed.
- FIX2 `3d59ecc` (R3 MEDIUM): activation preview reports a pending gate bar only if it starts strictly after the
  order's as_of (`expected_status`), as the runtime does.
- FIX3 `e2ab0c5` (R4 HIGH): revision gate requires the Evidence DB and full coverage by `anomaly_id` (records carry
  the evidence anomaly id; dedup/review by id; BLOCKED on missing DB, unclassified, orphan or material unreviewed).
  P8.4 flag-OFF CLEAR assertion explicitly superseded.
- Tests +7 (FIX1 1, FIX2 1, FIX3 5; one assertion superseded). Full suite 1069. Same-class TEMP probe on the trading DB preflight reported,
  not changed. Per-symbol catch-up proposal documented, not implemented. Nothing activated; HIGH-8.1 OPEN for the
  runtime; Phase 8 BLOCKED; not certified.

## 2026-10-08 — V2 Phase 8 P8.4 Batch 3 (R4 REVISION visibility + pre-DEMO gate)

- Agent: Claude. Branch `v2/phase8-execution`; previous SHA `fc5f8b6`; final SHA = the commit that adds this entry.
- `runtime/revision_review.py` (classification MATERIAL/non-material, deduplicated EVIDENCE_REVISION journal rows,
  system_state summary, Owner review, read-only pre-DEMO gate + CLI); `runtime/service.py` (flag-ON-only hook via
  _audit_safely). P8.1B scenario 17 assertion explicitly superseded (LOW-8.6 resolved by R4).
- `test_phase8_revision_review.py` (6). Full suite 1062. No activation; HIGH-8.1 OPEN for the default runtime;
  Phase 8 BLOCKED. Open risks and the copied-DB simulation procedure documented.

## 2026-10-08 — V2 Phase 8 P8.4 Batch 2 (R3 read-only activation preview)

- Agent: Claude. Branch `v2/phase8-execution`; previous SHA `4fe10bd`; final SHA = the commit that adds this entry.
- `replay/activation_preview.py` (offline, read-only: backup-API copies, source hashes, independent Decimal oracle;
  watermarks, historic missed SL/TP report-only, catch-up bars, expected closes/PnL, pending gate bars, REVISIONs,
  risks) and `test_phase8_activation_preview.py` (5: prediction equals a real flag-ON cycle on a separate copy).
  Full suite 1056. No runtime change; no activation.

## 2026-10-08 — V2 Phase 8 P8.4 Batch 1 (R1 activation route + R2 evidence preflight; flag OFF)

- Agent: Claude. Branch `v2/phase8-execution`; previous SHA `8d8f98e`; final SHA = the commit that adds this entry.
- `runtime/config.py`: `from_env` enables catch-up only for `AI_FLOOR_V2_POSITION_CATCH_UP="1"` + a separate evidence path;
  ambiguous values fail closed; `catch_up_storage_checks` (fail-closed Evidence Store preflight, ON only).
  `runtime/cloud.py` / `runtime/demo_runner.py`: preflights include those checks (OFF reports unchanged).
- `test_phase8_activation_route.py` (10). Full suite 1051. P8.2 independent verdict recorded; author skip-count
  statement corrected (CI: 1 environmental skip). No activation; HIGH-8.1 OPEN for the default runtime.

## 2026-10-08 — V2 Phase 8 P8.3 (HIGH-8.1 closure preparation; no activation)

- Agent: Claude. Branch `v2/phase8-execution`; previous SHA `0744868`; final SHA = the commit that adds this entry.
  Tests and docs only; no production code changed; no flag activated; no operational DB touched.
- `test_phase8_activation_simulation.py` (6): first-activation simulation on temporary DBs. Missed touches before
  activation are NOT retroactively corrected (watermark); touches since the last flag-OFF cycle close at the true
  bar; pending orders, REVISION detection, P&L reconciliation and rollback.
- Doc P8.3: diagnosis (A), minimal plan R1-R5 (B), activation risks (C), isolated simulation procedure (D), REVISION
  handling (E), certification conditions (F), DEC-8.6..8.10 (G). Recommendation: activation NO-GO now; Phase 8
  BLOCKED; P8.2 report not available to the agent.

## 2026-10-07 — V2 Phase 8 P8.1B (isolated certification of the chronological SL/TP catch-up)

- Agent: Claude. Branch `v2/phase8-execution`; previous SHA `0bb6927` (P8.1A); final SHA = the commit that adds
  this entry. Tests only; `v2_position_catch_up` ON only inside temporary test databases; runtime defaults unchanged.
- `test_phase8_catch_up_certification.py` (14 methods: 11 deterministic intermediate-touch/gap/same-bar cases vs an
  independent Decimal oracle, flag-OFF contrast, cadence jump, repeated cycle, pending interplay, evidence failure,
  AI failure, 40 randomized oracle sequences, full traceability chain, provider revision, and real-process crash
  before/after the close commit and a two-process race).
- HIGH-8.1: evidence supports closure for the flag-ON path (pending P8.2 independent confirmation); it remains OPEN
  for the default flag-OFF runtime. New LOW-8.6 (revisions visible only in evidence anomalies). Activation gate
  documented, nothing activated.

## 2026-10-07 — V2 Phase 8 P8.1A (adversarial execution tests, tests only)

- Agent: Claude. Branch `v2/phase8-execution`; previous SHA `c5ba9d5` (P8.0); final SHA = the commit that adds this
  entry. No production code changed; all flags OFF.
- `test_phase8_adversarial.py` (12: SQLite failure matrix, fill transitions, scheduler/locks, conflicts, PAPER
  safety, rollback fail-closed + restore idempotency) and `test_phase8_process_races.py` (4 real-process: crash
  after/before submit commit, same-slot race, live-run recovered by another process).
- Results: no phantom or duplicate order in any scenario. DEC-8.3 investigation documented (fail-stop accepted;
  optional truthfulness tweak needs authorization); LOW-8.5 Store not closed on fail-closed startup. Rollback
  procedure (DEC-8.4) and certification separation (DEC-8.5) documented. HIGH-8.1 remains OPEN for the default runtime.

## 2026-10-07 — V2 Phase 8 P8.0 (Execution / Fill / Trade Manager: audit and certification plan)

- Agent: Claude. Branch `v2/phase8-execution` from `main` `2753130d22abc33e12caf766e8ed5dfba17a8b70`; final SHA = the
  commit that adds this entry. Audit only; no production code changed; no runtime activation.
- `V2_PHASE8_EXECUTION.md` (architecture map, F08-T01..T17 matrix, findings, P8.1 batches, P8.2 gate, rollback,
  DEC-8.1..8.5); `test_phase8_execution_characterization.py` (4 tests: finish() DB-failure fail-stop + recovery,
  startup orphan / quantity-mismatch refusal, pending without time-in-force).
- HIGH-8.1: the default runtime evaluates SL/TP only on the newest 5m bar per cycle (the certified catch-up is OFF).
  Status: **READY FOR OWNER DECISIONS**.

## 2026-10-07 — V2 Phase 7 P7.1F (P7.2 FAIL correction: late AI response after the cycle budget)

- Agent: Claude (author; not the reviewer). Branch `v2/phase7-ai-agent-floor`; failed P7.2 candidate
  `564a4276698d2229fd1afda6cb91d784cba358a8`; final SHA = the commit that adds this entry.
- P7.2 independent certification: **FAIL** (HIGH: a valid response completing after the 120 s AI cycle budget was
  accepted and left `paper_policy` True).
- `ai/resilience.py`: post-call deadline re-check; late responses fail closed as `AI_TIME_BUDGET_EXHAUSTED`
  (`late_response_rejected`), boundary `remaining <= 0` = exhausted. `ai/runtime.py` / `ai/call_audit.py`: late-response
  fields and truthfully consumed usage in the audit record.
- New `test_phase7_ai_budget_boundary.py` (9; 5 fail on 564a427). Full suite 1001/1001. Fail-closed removal of
  eligibility only; no authority, economic, schema or runtime-activation change. Status: **READY FOR P7.2
  RE-CERTIFICATION**.

## 2026-10-07 — V2 Phase 7 P7.1 Batch C (resilience, health/recovery, alerts OFF)

- Agent: Claude. Branch `v2/phase7-ai-agent-floor`; previous SHA `41d7f92` (Batch B); final SHA = the commit that
  adds this entry.
- `ai/provider_health.py` (READY/DEGRADED/FAILED/UNKNOWN, recovery events), `ai/resilience.py` (per-cycle
  non-transient short-circuit + AI time budget; fail-closed only), `ai/alerts.py` (typed, deduplicated, OFF by
  default), `ai/openai_provider.py` (optional deadline caps attempts/retries), `runtime/config.py` +
  `runtime/service.py` (`v2_ai_resilience` flag, OFF, health events via `_audit_safely`).
- Tests: `test_phase7_ai_resilience.py` (12). Full suite 992/992. Economic behavior, AI authority, schema and runtime
  activation unchanged. Status: **P7.1 READY FOR P7.2 INDEPENDENT REVIEW**.

## 2026-10-07 — V2 Phase 7 P7.1 Batch B (durable AI_CALL observability)

- Agent: Claude. Branch `v2/phase7-ai-agent-floor`; previous SHA `2ab5e79` (Batch A).
- `ai/call_audit.py` (AI_CALL records, evidence fingerprint, estimated cost, observational soft budgets),
  `ai/openai_provider.py` (sanitized per-call attempts/response id), `ai/runtime.py` (call timing + fingerprint),
  `runtime/review.py` (provider-reported usage per agent), `runtime/config.py` + `runtime/service.py`
  (`v2_ai_call_audit` flag, OFF, observability-only hook).
- Tests: `test_phase7_ai_call_audit.py` (11). Full suite 980/980. No economic or authority change.

## 2026-10-07 — V2 Phase 7 P7.1 Batch A (typed AI outcomes + ai_availability)

- Agent: Claude. Branch `v2/phase7-ai-agent-floor`; starting SHA `6f4f68c929df28fa5863d0a689bb5e38d6e6967b`.
- `ai/outcomes.py` (typed taxonomy, non-authoritative availability); `ai/runtime.py` (typed `ai_outcome:` warning
  appended, AuditLog outcome/error_kind/http_status); `ai/contracts.py` + `ai/orchestrator.py` (`ai_availability`);
  `runtime/review.py` (agent outcome + availability in the review). No gate reads any of it; final_status unchanged.
- Tests: `test_phase7_ai_outcomes.py` (9); two P7.0 characterization assertions explicitly superseded (DEC-7.1).
  Full suite 969/969.

## 2026-10-07 — V2 Phase 7 P7.0 (AI Agent Floor: audit, characterization, design gate)

- Agent: Claude. Branch `v2/phase7-ai-agent-floor` from `main` `405df6a8b18172cd94da23b9e48380a4405ba8cd`;
  final SHA = the commit that adds this entry. Audit only; no production code changed; no runtime activation.
- `V2_PHASE7_AI_AGENT_FLOOR.md` (architecture, agent contracts, provider taxonomy, authority matrix, fail-closed,
  retries, token/cost observability, health state machine, incident mapping, DEC-7.1..7.11, P7.1 plan, P7.2 gates);
  `test_phase7_ai_characterization.py` (15 tests, no live provider calls). Legacy V1 Phase 7 artifacts untouched.
- Key finding (HIGH, operational): typed provider failure kinds (quota vs rate limit vs auth vs timeout) are not
  persisted. No AI authority violation found. Status: **READY FOR OWNER DECISIONS**.

## 2026-10-07 — V2 Phase 6 P6.1E (same-run candidate identity: immutable run claim)

- Agent: Claude (author; not the reviewer). Branch `v2/phase6-multi-setup-conflict`; starting SHA
  `6bcf7cc0b732c39c5096d1bddeea5ae14a0a252e` (failed review); final SHA = the commit that adds this entry.
- New `execution/candidate_identity.py`: canonical fingerprint `V2_P6_CANDIDATE_1` and the immutable, non-economic
  `RUN_CANDIDATE_CLAIM` (one BEGIN IMMEDIATE, schema 3, no DDL). `execution/conflict_path.py`: claim before any
  decision; mismatch → `RUN_ID_CANDIDATE_MISMATCH`; pre-claim history → `RUN_ID_IDENTITY_UNKNOWN`; claim re-verified
  in decision transactions; reconciliation refuses non-matching orders/decisions; P6.1D pre-save re-read removed.
- New `test_phase6_candidate_identity.py`, `test_phase6_candidate_race.py` (real processes; fails on 6bcf7cc,
  passes now). Runtime, storage, Phase 5 unchanged; REAL DISABLED; NAS100 OFF. Rollback MEDIUM still pending.
  Status: **READY FOR INDEPENDENT DELTA REVIEW**.

## 2026-10-06 — V2 Phase 6 P6.1D (P6-STALE-01 correction: journal-safe stale outcomes)

- Agent: Claude (author; not the reviewer). Branch `v2/phase6-multi-setup-conflict`; starting SHA
  `7648eb9c52ab123a38f256a845d3661772df317b` (failed P6.2); final SHA = the commit that adds this entry.
- `execution/conflict_path.py`: after a double stale the durable same-run outcome (order / decisive decision) wins
  (`DUPLICATE_RUN`, nothing appended); otherwise ≤ 1 sanitized non-decisive `CONFLICT_ATTEMPT_STALE` row per run
  (no provisional order_id). `commit_decision_rows` also refuses when the run already has an order; `DUPLICATE`
  reconciles to the durable outcome.
- New `test_phase6_stale_regression.py` (5 real-process tests; fails 5/5 on 7648eb9, passes after the fix).
- Runtime, storage, Phase 5 unchanged; schema 3; REAL DISABLED; NAS100 OFF. Status: **READY FOR INDEPENDENT DELTA
  REVIEW**.

## 2026-10-06 — V2 Phase 6 P6.1 (conflict engine, durable setup_id, atomic conflict + Risk V2 path)

- Agent: Claude (author; not the reviewer). Branch `v2/phase6-multi-setup-conflict`; starting SHA
  `f83b63c70bfc6ba35e7ecf4afc1c977272b5d97c`; final SHA = the commit that adds this entry.
- New: `execution/conflict_engine.py` (pure classification, `V2_P6_CONFLICT_1`), `execution/conflict_path.py`
  (conflict + Risk V2 + reservation in one guarded save; `CONFLICT_DECISION` rows), `test_phase6_conflict_engine.py`,
  `test_phase6_conflict_concurrency.py` (real multi-process A–L).
- Changed: optional `setup_id` on `PaperOrder`/`PaperPosition` (omitted when None; schema 3), broker fill inherits it,
  `storage/codec.py` optional-identity omission, `execution/risk_reservation.py` extracts `prepare_reservation`
  (Phase 5 behavior identical), `replay/conflict_audit.py` uses the engine, one P6.0 characterization assertion
  explicitly superseded by DEC-6.1, `V2_PHASE6_MULTI_SETUP_CONFLICT.md` P6.1 section.
- Replay reproduces P6.0 exactly (21 / 1,020 / 882 / 17 / pending 0). Runtime unchanged; no flag; REAL DISABLED;
  NAS100 OFF. Status: **READY FOR INDEPENDENT REVIEW**.

## 2026-10-06 — V2 Phase 6 P6.0 (multi-setup / position conflict: audit, characterization, owner decisions)

- Agent: Claude. Branch `v2/phase6-multi-setup-conflict` from `main` `b3e93dfcff06233a7f0d22748d626e9a4b8d57bf`;
  final SHA = the commit that adds this entry. Audit only; no production code changed; no runtime behavior changed.
- `V2_PHASE6_MULTI_SETUP_CONFLICT.md` (behavior map, data model, setup_id, taxonomy, Risk V2 integration,
  concurrency, observability, replay, DEC-6.1..6.11, P6.1 plan); `test_phase6_conflict_characterization.py`
  (12 tests); `replay/conflict_audit.py` (offline, observational).
- Findings: same-symbol multi-position structurally unsafe (latent HIGH); pending progression coupled to the
  current-cycle analysis (HIGH design constraint); setup_id is opportunity identity, not thesis identity.
- REAL DISABLED; NAS100 OFF; schema 3; runtime OFF. Status: **READY FOR OWNER DECISIONS**.

## 2026-10-06 — V2 Phase 5 P5.1C (certification-blocker correction: durable pending-risk policy identity)

- Agent: Claude (author; not the reviewer). Branch `v2/phase5-risk-engine`; starting SHA
  `a71688ee4279f2257a72d921a8231290a8782b21`; final SHA = the commit that adds this entry.
- HIGH fixed: pending reservations no longer assume 8/7. `PaperOrder.risk_policy_version` (additive, omitted when
  None; schema 3), registry in `core/risk_policy.py`, stamping in `execution/risk_reservation.py`, identity-bound
  reservation and `UNKNOWN_PENDING_RISK_POLICY` in `execution/risk_engine_v2.py`, stamped-order fill binding in
  `execution/paper_broker.py`, codec omission in `storage/codec.py`, audit stamping in `replay/risk_audit.py`.
- Docs: open risk renamed entry-basis loss to stop (no "never understates" claim); P5.1C section C1–C8.
- Tests: reviewer HIGH regression on the real 2.30% cap, restart/reload identity, unknown legacy, fixed-3R happy
  path (1.00/0.75/0.63/0.25%), fill binding, 4 new multi-process cases.
- REAL DISABLED; NAS100 OFF; schema 3; runtime not wired/activated. Status: **READY FOR INDEPENDENT DELTA REVIEW**.

## 2026-10-06 — V2 Phase 5 P5.1 (Risk Engine V2 implementation, DEC-5.1 → DEC-5.8)

- Agent: Claude (author; not the reviewer). Branch `v2/phase5-risk-engine`; starting SHA
  `9c9e9817deaeb1c17f7cf946f5c20b535c8a7113`; final SHA = the commit that adds this entry.
- New: `core/risk_policy.py` (versioned policy `V2_P5_RISK_1`, derived 8/7), `execution/risk_engine_v2.py` (pure
  portfolio-aware Risk: conservative equity, 1% / 100% notional sizing, open + pending + proposed ≤ 2.30%, one per
  symbol, 5% drawdown gate, traceability record), `execution/risk_reservation.py` (atomic reservation = PENDING
  order via B2.3A CAS, run_id idempotency), `replay/risk_audit.py`, `test_phase5_risk_engine.py`,
  `test_phase5_risk_concurrency.py` (real multi-process cases A–E).
- Changed: `execution/paper_broker.py` DEC-5.7 fill money rule for fixed 3R (V1 and policy D unchanged);
  `test_phase5_risk_characterization.py` H1 assertion explicitly superseded; `V2_PHASE5_RISK_ENGINE.md` P5.1 section.
- Replay: the old 1% cap rejected 523/1,940 fills; DEC-5.7 rejects 0 money fills; R:R ≥ 2.50 vs 8/7 disagreements 0.
- REAL DISABLED; NAS100 OFF; schema 3; runtime not wired/activated. Status: **READY FOR INDEPENDENT REVIEW**.

## 2026-10-06 — V2 Phase 5 P5.0 (Risk Engine V2 audit & owner-decision package)

- Agent: Claude. Branch `v2/phase5-risk-engine` from `main` `3edb64804cfc4b1432dbc79aa7ed3d810be4e558`; final SHA
  = the commit that adds this entry. Audit/design only; no production code changed.
- `V2_PHASE5_RISK_ENGINE.md` (authority map, F05 gap matrix, sizing, portfolio, drawdown, correlation, invalid
  geometry, abnormal market, concurrency, architecture); `test_phase5_risk_characterization.py` (8 tests).
- Finding: the broker's hard-coded 1% money-at-risk cap rejects 27.4% of DEC-4.7-accepted fills in replay.
- REAL DISABLED; NAS100 OFF; schema 3; runtime OFF. Status: **READY FOR OWNER DECISIONS**.

## 2026-10-06 — V2 Phase 4 DEC-4.7 (fixed-3R fill execution floor 2.50R)

- Agent: Claude. Branch `v2/phase4-trade-planner`; starting SHA `de24ccd771dd28a8506fc7be3808ca9ca6bef45a`;
  final SHA = the commit that adds this entry.
- `core/rr_contract.py`: `FILL_LIMITS[POLICY_V2_F3] = 2.50` (execution tolerance; planning stays exactly 3R).
  `execution/paper_broker.py`: V2 fill check with explicit reasons (`fill_rr_below_minimum`,
  `fill_invalid_geometry`) and full fill telemetry; V1 fill gate byte-identical. `replay/fill_audit.py`: strict vs
  DEC-4.7 comparison (vectorized). `test_phase4_fixed_3r.py`: DEC-4.7 boundary matrix (LONG/SHORT, exact 2.50).
- Replay: fill rejection 47.37% -> 1.55% (runtime NEXT_CYCLE model). REAL DISABLED; NAS100 OFF; Phase 4 runtime
  OFF; schema 3; Phase 3 unchanged. Status: **READY FOR INDEPENDENT PHASE 4 REVIEW** (not certified).

## 2026-10-06 — V2 Phase 4 DEC-4.6 (fixed 3R policy) and fill-geometry audit

- Agent: Claude. Branch `v2/phase4-trade-planner`; starting SHA `944f29f40329788a554b8cbe4f781bd4e20d5d56`;
  final SHA = the commit that adds this entry. P4.1B superseded (stopped, not committed).
- `core/rr_contract.py` (`POLICY_V2_F3`, provisional `FILL_LIMITS`), `agents/target_planner.py`
  (`plan_fixed_3r`), `floor/orchestrator.py` (opt-in policy), `riesgo.py` (`crear_configuracion_riesgo_fixed_3r`),
  `ai/provider.py` (contract gate), `execution/paper_broker.py` (fill-geometry trace; V1 unchanged),
  `replay/engine.py` (fill limits), new `replay/fill_audit.py`, `test_phase4_fixed_3r.py`.
- Fill audit: ~47% fill rejection is sign-driven (every adverse tick) under both fill models; DEC-4.7 required.
- REAL DISABLED; NAS100 OFF; Phase 4 runtime OFF; schema 3. Phase 3 not reopened. Status: **DEC-4.7 REQUIRED**.

## 2026-10-06 — V2 Phase 4 P4.1A (Target Policy Lab, offline, DEC-4.5 option a)

- Agent: Claude. Branch `v2/phase4-trade-planner`; starting SHA `36d7fc5da1d15149b9be1de2570c2bb4a643f32d`;
  final SHA = the commit that adds this entry. Offline only; no production code changed.
- New `replay/lab.py` (pre-registered variants D0–D4, split, selection rule; sha256 `81e30c9c…c114`),
  `replay/lab_run.py` (15-minute resumable runner), `replay/lab_report.py` (protocol-ordered report),
  `test_phase4_lab.py` (lookahead and invariant tests).
- Result: D0 0, D1 7, D2 53, D3 5 tradeable of 1,941 valid setups (12 months, 15-minute cadence); D2 selected on
  discovery (32) and confirmed frequency on holdout (21); observational outcomes stop-dominated. Status:
  **READY FOR OWNER DECISION**. REAL DISABLED; NAS100 OFF; Phase 4 runtime OFF; schema 3.

## 2026-10-06 — V2 Phase 4 P4.1 (Policy D, single R:R contract, replay foundation)

- Agent: Claude. Branch `v2/phase4-trade-planner`; starting SHA `f862176d6660b81a8574d8ba8007fb1504b7e402`
  (P4.0); final SHA = the commit that adds this entry. Owner decisions DEC-4.1..4.4 recorded.
- New `core/rr_contract.py` (single R:R authority), `agents/target_planner.py` (Policy D, explicit opt-in),
  `replay/` (separate Replay Store, acquisition, lookahead-free engine, comparison). Changed: `riesgo.py`
  (recompute R:R for every plan; Phase 4 config), `floor/orchestrator.py` (`planner_policy`, default V1),
  `ai/provider.py` + `ai/agents/trade_reviewer_ai.py` (policy-aware deterministic gate; V1 unchanged),
  `execution/paper_broker.py` (`rr_policy`, default V1 expression), `core/contracts.py` and `runtime/review.py`
  (additive fields), `.gitignore` (`data/replay/`). Runtime cannot select Policy D.
- Fixed: Risk declared-R:R trust (DEC-4.2). Tests: new Phase 4 suites 28/28; oracle updated for DEC-4.2;
  mutations 12/12 killed. 12-month real-data replay: Policy D 0 plans (HIGH finding, owner decision).
- REAL DISABLED; NAS100 OFF; schema 3; System Health inactive. Status: **READY FOR INDEPENDENT REVIEW**;
  Phase 4 NOT certified.

## 2026-10-06 — V2 Phase 4 P4.0 (Trade Planner + Target/R:R design & owner decision package)

- Agent: Claude. Branch `v2/phase4-trade-planner` from `main` `b8b7493d91b37156e653d8ce7a8850a9abd82fb0`;
  final SHA = the commit that adds this entry. Design/audit only: no production code changed.
- `V2_PHASE4_TRADE_PLANNER.md`: V1 audit, evidence classification, invalidation-first design, policies
  0/A/B/C/D, bands, scenarios, fail-closed matrix, replay/metrics/certification plans, 4 owner decisions.
- `test_phase4_v1_planner_oracle.py`: frozen V1 planner/Risk/PAPER/AI/fill-gate outputs (6 tests).
- Full suite once 783 pass / 0 fail / 0 skip. REAL DISABLED; NAS100 OFF; V2 runtime not authorized;
  System Health inactive; schema 3. Status: **READY FOR OWNER DECISION**; P4.1 NOT STARTED.

## 2026-10-05 — V2 Phase 3 P3.2 (final regression, E2E, certification candidate)

- Agent: Claude. Branch `v2/phase3-setup-validator`; starting SHA
  `55e1585e9259d3dd456777d08ec31b64e482302a` (P3.1 accepted); final SHA = the commit that adds this entry.
- LOW fix (metadata only): evidence references are VALID only for lineage-valid reports whose bar is
  aligned to its timeframe and closed by as_of; otherwise UNAVAILABLE/INVALID with a reason, no bar.
- F03-T09: `test_setup_validator_regression.py` (verbatim V1 oracle, 17 cases, real Planner/Risk/AI).
  New `test_phase3_e2e.py` (real runtime). P3.1 `test_13` fixed (it compared None == None).
- Skip explained: the git-dependent V1 rollback test (environmental). Carry-forward: same-setup re-entry
  (owner policy), invalidation-window aging (pre-existing).
- Focused 29/29; Phase 2 targeted 143/143; mutations 10/10 killed. REAL DISABLED; NAS100 OFF; flag OFF;
  System Health inactive; schema 3. Status: **READY FOR INDEPENDENT CERTIFICATION** (Phase 3).

## 2026-10-05 — V2 Phase 3 P3.1 (Setup Validator V2 core)

- Agent: Claude. Branch `v2/phase3-setup-validator` from certified `main`
  `c7aaafb4ff0fe35c6985d7089d8ea85a144b2121`; final SHA = the commit that adds this entry.
- `agents/setup_validator.py`: frozen V1 decision (byte-identical) + structured explanation (checks,
  details, evidence refs, setup identity); `core/contracts.py`: optional `SetupAssessment.explanation`;
  `runtime/observability.py`: setup_id delegates to the validator (same values); `runtime/review.py`:
  review report carries the explanation. No schema change; `storage/database.py` untouched.
- Confidence not implemented (no defensible methodology). F03-T09/T11 not started.
- Tests: new `test_setup_validator_v2.py` 15/15; mutations 5/5 killed; full suite 767 pass / 0 fail /
  0 skip. REAL DISABLED; NAS100 OFF; V2 runtime activation not authorized; System Health inactive.
- Status: **READY FOR INDEPENDENT REVIEW**.

## 2026-10-05 — V2 Phase 2 B2.3D regression preservation (test-only)

- Agent: Claude. Branch `v2/phase2-market-evidence`; starting SHA
  `05afa4e0c64d5731d4f3a8fcfd31b7b237d9526c`; final SHA = the commit that adds this entry.
- New permanent `test_phase2_e2e.py`: the B2.3D deterministic end-to-end scenario, assertions
  unchanged from the certification run. No production code change.
- B2.3D PASS. H02 CLOSED (owner-accepted) for the V2 implementation under P1. Runtime activation NOT
  AUTHORIZED; flag OFF by default. Schema 3; REAL DISABLED; NAS100 OFF; System Health inactive.
- Status: **READY FOR FINAL INDEPENDENT PHASE 2 CERTIFICATION**.

## 2026-10-05 — V2 Phase 2 B2.3C (runtime pending orders via CurrentCycleGate, flag OFF)

- Agent: Claude. Branch `v2/phase2-market-evidence`; starting SHA
  `a5b1ce51a1fc109c1a43df4189f207c66f69012a`; final SHA = the commit that adds this entry.
- `runtime/service.py`: with the flag ON, pending orders progress only through B2.2
  `gate_pending_orders` with a `CurrentCycleGate` built from this cycle's real V1 values and the
  committed current Evidence bar; missing bar / evidence error / persistent STALE fail closed. The
  V1 session check is computed once into `session_open` (same expression). Flag OFF unchanged.
- Tests: new `test_runtime_pending_gate.py` 14/14 (real-process crash and contention); isolation
  tests updated. Mutations 6/6 killed; flag-OFF old/new byte-identical; full suite once 751 pass /
  0 fail / 0 skip.
- Schema 3; REAL DISABLED; NAS100 OFF; System Health inactive; H02 PARTIAL; B2.3D NOT STARTED.
  Status: **READY FOR INDEPENDENT REVIEW**.

## 2026-10-05 — V2 Phase 2 B2.3B (Evidence + position catch-up runtime wiring, flag OFF)

- Agent: Claude. Branch `v2/phase2-market-evidence`; starting SHA
  `5258e125f03eed7dda711ad9af090858cc5c0a83`; final SHA = the commit that adds this entry.
- `runtime/config.py`: `v2_position_catch_up` (default False, never from env) + `market_evidence_path`.
- `runtime/service.py`: with the flag ON, snapshot ingestion into the Evidence Store, then B2.1
  catch-up replaces the newest-bar TradeManager step; evidence failure / persistent STALE fails
  closed for PAPER economics (no fallback, no pending progress, no submission).
- Tests: new `test_runtime_catch_up.py` 15/15 (real-process crash and contention); isolation tests in
  `test_market_evidence.py`, `test_position_catch_up.py`, `test_stale_safe_writers.py` updated to the
  authorized flag-gated wiring. Mutations 6/6 killed; flag-OFF old/new byte-identical; full suite once
  737 pass / 0 fail / 0 skip.
- Schema 3; REAL DISABLED; NAS100 OFF; System Health inactive; H02 PARTIAL; B2.3C NOT STARTED.
  Status: **READY FOR INDEPENDENT REVIEW**.

## 2026-10-05 — V2 Phase 2 B2.3A (stale-safe runtime PAPER writers)

- Agent: Claude. Branch `v2/phase2-market-evidence`; starting SHA
  `a261f62aea8b52e4edb1c905c920693ecd35d30a`; final SHA = the commit that adds this entry.
- `runtime/service.py`: all runtime PAPER writers (bootstrap, legacy TradeManager, legacy pending,
  submit) use the B2.1 `expected_state` guard via `_guarded_paper_write`; one fresh recompute on
  STALE; submit fails closed as `STALE_PAPER_STATE` on changed eligibility/equity; later decisions
  never use a stale broker. Fixes a V1 cross-process lost update (XAU/EUR sharing one account).
- New `test_stale_safe_writers.py` 12/12 (pre-fix fails 9/12); mutations 4/4 killed; single-writer
  old/new equivalence byte-identical; full suite once 722 pass / 0 fail / 0 skip.
- No Evidence/catch-up/gate wiring; no flag; schema 3; System Health inactive; REAL DISABLED;
  NAS100 OFF; H02 PARTIAL. Status: **READY FOR INDEPENDENT REVIEW**.

## 2026-10-05 — V2 Phase 2 B2.2 F1 fix (fail closed on missing AI final status)

- Agent: Claude. Branch `v2/phase2-market-evidence`; starting SHA
  `4c7c956478acd24b5c26ccfa2acf8636c811d5e4`; final SHA = the commit that adds this entry.
- F1: `CurrentCycleGate.passed()` now requires a non-empty `str` `ai_final_status` before the
  unchanged V1 deny-list; None/empty/non-str never authorize evaluation. Tests `test_7b`/`test_7c`.
- Focused 26/26; B2.1 28/28; mutations 2/2 killed; full suite once 710 pass / 0 fail / 0 skip.
- No other changes. REAL DISABLED; NAS100 OFF; schema 3; H02 PARTIAL; B2.3 NOT STARTED.
- Status: **READY FOR F1-ONLY INDEPENDENT RE-REVIEW**. Phase 2 IN PROGRESS / NOT CERTIFIED.

## 2026-10-05 — V2 Phase 2 B2.2 (cycle-gated pending orders, isolated)

- Agent: Claude. Branch `v2/phase2-market-evidence`; starting SHA
  `8e880bfa09bb724c75eb5cd82de0534fc46fab63`; final SHA = the commit that adds this entry.
- Owner decision P1 APPROVED: no current cycle gate = no pending-order evaluation.
- Scope: new `execution/pending_order_gate.py` (`CurrentCycleGate`, `gate_pending_orders`), new
  `test_pending_order_gate.py`, Phase 2 doc section. Not wired into the runtime. No schema change.
- Intermediate bars journaled idempotently as `PENDING_NOT_EVALUATED` / `NO_CURRENT_CYCLE_GATE` in
  the existing journal; only the current gate's own bar reaches unchanged `process_next_bar`, only
  after `order.as_of`; stale gates are not current; evaluation persisted via B2.1 guarded save.
- Tests: focused 24/24; B2.1 28/28; mutations 4/4 killed; full suite once 708 pass / 0 fail / 0 skip.
- Trading semantics unchanged (fill/stop/target/quantity/economics/gates). REAL DISABLED; NAS100 OFF;
  H02 PARTIAL; B2.3 NOT STARTED (opt-in stale guard recorded as B2.3 activation gate).
- Status: **READY FOR INDEPENDENT REVIEW**, not accepted. Phase 2 IN PROGRESS / NOT CERTIFIED.

## 2026-10-05 — V2 Phase 2 B2.1 P1 concurrency correction

- Agent: Codex. Branch `v2/phase2-market-evidence`; clean/fetched starting SHA
  `8c99d7dfd1b726443c85b4ebc5ccb0004dec7579`; final SHA = this corrective commit.
- Reproduced A-computes / B-commits / A-resumes before production edits: two closed trades and
  POSITION_CLOSED events; account realized -10 versus persisted trade sum -20.
- Fix: immutable pre-computation PAPER snapshot compared with durable state inside the existing
  `save_paper` BEGIN IMMEDIATE. Mismatch returns False before writes; catch-up discards the computed
  transition and returns STALE. Whole-account/object comparison also excludes cross-symbol lost
  updates. SQLite serializes processes; no Python-only lock or Evidence Store transaction coupling.
- Files: `execution/position_catch_up.py`, narrow schema-neutral `storage/database.py` opt-in guard,
  new `test_position_catch_up_concurrency.py`, narrow `test_system_health.py` compatibility assertion,
  this changelog and `V2_PHASE2_MARKET_EVIDENCE.md`. Original B2.1 tests unchanged.
- Focused 28/28; includes two independent processes, progress/close, crash holding write transaction,
  repeated restarts, PnL reconciliation, other-symbol progress and bidirectional transaction isolation.
  Exactly 3 scratch mutants detected: no revalidation, stale watermark accepted, duplicate close path.
- Full local suite once: 683 pass / 1 failure / 0 skipped, 684 total. Only failure was the historical
  whole-file frozen hash gate. Updated it to permit only the authorized opt-in addition while keeping
  the original hash for every remaining byte; targeted compatibility rerun 2/2 PASS. No production
  edits after full run and no full rerun. Actual frozen V1 code also opens a guarded-close DB read-only
  and read-write, schema 3, one close/event, PnL -10 and equity 9990. Final-SHA Linux push CI pending.
- Windows sandbox temp-permission adjustment confined to external launcher; no skipped tests.
- Comparability: unchanged single-writer SL/TP/economics; removes invalid duplicate concurrent effects.
  No historical data repair. TradeManager, Paper Broker/pending orders, Risk, AI, Planner, providers,
  freshness, strategy, config and runtime unchanged; schema 3 preserved. REAL DISABLED; NAS100 OFF;
  H02 PARTIAL; B2.2/B2.3 NOT STARTED. No PR/merge/deploy. Phase 2 IN PROGRESS / NOT CERTIFIED.
- Status: **READY FOR INDEPENDENT RE-REVIEW**, not accepted/certified.

## 2026-10-05 — V2 Phase 2 B2.1 (position catch-up core, isolated)

- Agent: Claude. Branch `v2/phase2-market-evidence`; starting SHA
  `e12797701eb4184c47837bbe0c83388a86db60c2`; final SHA = the commit that adds this entry.
- Scope: `execution/position_catch_up.py` (`catch_up_position`), `test_position_catch_up.py`
  (19 tests), Phase 2 doc section. Not wired into the runtime.
- Invariant: per (position, closed 5m bar) either no effect commits and the bar stays eligible, or
  all effects plus `last_processed_at` commit in one existing `Store.save_paper` transaction.
  Watermark from the trading DB only; evidence read-only; fail closed on evidence failure.
- Tests: focused 19/19 (A–R incl. real-process crash before/inside/after a bar transaction and
  repeated crashes). Scratch mutations 5/5 detected (newest-bar-only, reversed ordering, watermark
  check removed, duplicate bar allowed, latest-bar fallback on evidence failure).
- Unchanged: `storage/database.py`, `execution/trade_manager.py`, `execution/paper_broker.py`,
  runtime, Risk, AI, Setup, Planner, providers, pending-order eligibility; trading DB schema 3.
- Safety: REAL EXECUTION = DISABLED; NAS100 = OFF. H02 PARTIAL. B2.2 and B2.3 not started.
  No PR, no merge, no deploy. Phase 2 IN PROGRESS / NOT CERTIFIED.

## 2026-10-05 — V2 Phase 2 Batch 1 final store hardening (M1 URI alias, M2 effective schema)

- Agent: Claude. Branch `v2/phase2-market-evidence`; starting SHA
  `dd9459f137b06589e869e847387afed1e12ada35`; final SHA = the commit that adds this entry.
  Independent re-review of `dd9459f`: M3 closed; M1 and M2 reopened (MEDIUM).
- M1: reproduced `trading_floor.db#evidence` initializing an empty protected `trading_floor.db`
  (raw path interpolated into a SQLite URI). `EvidenceStore` now resolves the path once, validates
  that target and opens exactly it: plain filename for read-write, `Path.as_uri()` (percent-encoded)
  for read-only; SQLite's `PRAGMA database_list` must report the same target before any write.
- M2: the schema contract now also refuses partial/extra UNIQUE indexes, partial or expression
  indexes, hidden/generated columns, foreign keys, affinity changes and (by a literal/comment-free
  keyword scan of the table definitions) CHECK, ON CONFLICT, non-BINARY COLLATE, STRICT,
  WITHOUT ROWID, AUTOINCREMENT. Harmless syntactic variants still open.
- Tests: focused 47/47 (was 43). Full suite 656/656 pass, 0 failed, 0 errors, 0 skipped. Scratch
  mutation checks 12/12 detected (raw URI, fragment bypass, partial indexes, CHECK, extra UNIQUE,
  triggers, collation, ON CONFLICT, generated columns, unsafe non-unique indexes, foreign keys,
  affinity).
- Unchanged: M3 crash tests, owner decisions A–E, Batch 2 trading-side idempotency gate (HIGH,
  OPEN; pending-order gate reason reconfirmed), H02 **PARTIAL**. `storage/database.py` unchanged,
  trading DB schema 3, no runtime wiring, no trading/risk/Paper Broker/provider/freshness/session/
  cadence change. REAL EXECUTION = DISABLED; NAS100 = OFF. No deploy, no merge, no PR, Batch 2 not
  started. Status: **PHASE 2 — IN PROGRESS / NOT CERTIFIED**.

## 2026-10-05 — V2 Phase 2 Batch 1 review fixes (M1–M3, decisions A–E)

- Agent: Claude. Branch `v2/phase2-market-evidence`; starting SHA
  `817a35947b305de6524b593c008d1c5fecc03c4a`; final SHA = the commit that adds this entry.
- M1: `EvidenceStore` refuses the trading DB by name (case-insensitive, after link/`..`
  resolution, Windows trailing dot/space and stream suffix ignored) and by identity (same file as a
  sibling trading DB; initialized trading DB under any name), before any connection or write,
  including an existing empty `trading_floor.db` / `TRADING_FLOOR.DB`.
- M2: an existing sidecar must match the schema contract read from SQLite metadata (columns, types,
  NOT NULL, primary-key membership, exact UNIQUE/PRIMARY KEY column sets with BINARY collation, no
  triggers); otherwise it is refused on open and left unchanged. Equivalent schemas are accepted.
- M3: the old pre-commit crash test actually crashed in the duplicate-T0 transaction; renamed to
  what it proves. New real-process test crashes after a genuine INSERT and before COMMIT (first
  and middle new bar); restart commits the bar exactly once, nothing lost or duplicated.
- `V2_PHASE2_MARKET_EVIDENCE.md`: owner-approved decisions A–E replace the open questions; Batch 2
  trading-side idempotency recorded as an OPEN HIGH gate (pending orders have no durable per-bar
  progress); V1 defect and `contiguous` (ingestion continuity != market-history completeness)
  stated precisely. H02 remains **PARTIAL**.
- Tests: focused 43/43 (was 39). Full suite 652/652 pass, 0 failed, 0 errors, 0 skipped. Scratch
  mutation checks: 8/8 detected (case-insensitive refusal, empty trading DB initialization, column
  validation, uniqueness validation, defective crash test, trigger check, hard-link identity,
  collation).
- Safety: only `storage/evidence_store.py`, its test and the Phase 2 doc changed (plus this entry).
  `storage/database.py` unchanged, trading DB schema 3, no runtime wiring, no trading, risk, Paper
  Broker, provider, freshness, session or cadence change. REAL EXECUTION = DISABLED; NAS100 = OFF.
  No deploy, no merge, no PR, Batch 2 not started. Status: **PHASE 2 — IN PROGRESS / NOT CERTIFIED**.

## 2026-10-05 — V2 Phase 2 Batch 1 (Market Evidence Engine foundation + H02 evidence processing)

- Agent: Claude. Branch `v2/phase2-market-evidence` (worktree `ai-market-system-phase2-evidence`);
  starting `main` SHA `1f3362f47cf2439f7d1c6df844a2a9f7656dee1b`; final SHA = the commit that adds
  this entry.
- Scope: isolated V2 market-evidence foundation. `data/market_evidence.py` (`MarketBar` contract,
  V1-frame adapter, `MarketEvidenceEngine`), `storage/evidence_store.py` (isolated MARKET EVIDENCE
  SQLite sidecar, application_id "V2ME", schema 1), `test_market_evidence.py` (39 tests),
  `V2_PHASE2_MARKET_EVIDENCE.md` (V1 path audit, design, limitations, owner decisions).
- H02 status: **PARTIAL**. Evidence processing complete and proven: every new closed bar, per
  stream, chronological, idempotent across overlap/retry/duplicate slot/restart, one bar per
  transaction, real-process crash/restart tests, gaps/late/revised bars recorded and never
  fabricated or overwritten. Not done: applying intermediate bars to 5m position management,
  SL/TP, pending orders and fills (runtime integration; owner decisions in the Phase 2 doc).
- Persistence decision: isolated sidecar; `trading_floor.db` not migrated (schema 3, byte-identical
  in tests).
- Tests: baseline 609/609 pass, 0 skipped. Focused 39/39. Full suite 648/648 pass, 0 failed,
  0 errors, 0 skipped. Scratch mutation testing (no file modified): newest-bar-only, reversed
  chronology, duplicate processing, in-memory-only recovery, skipped middle bar, duplicate commit
  after restart and forming-bar commit are all caught.
- Safety: no runtime wiring; no change to runtime, providers, config, freshness, sessions, cadence,
  setup/AI/prompts, risk, Paper Broker, execution, PnL or existing tests. SYSTEM HEALTH untouched
  and inactive. REAL EXECUTION = DISABLED; NAS100 = OFF. No deploy, no merge, no PR.
  Status: **PHASE 2 — IN PROGRESS / NOT CERTIFIED**.

## 2026-10-05 — V2 Phase 1 final PR polish (documentation + test only)

- Agent: Claude. Branch `v2/phase1-system-health`; starting SHA
  `0b6b6936965d704c9a6a9579664726345ee4d588`; final SHA = the commit that adds this entry.
- From the final independent re-review; no production code changed.
  1. `V2_PHASE1_SYSTEM_HEALTH.md`: H04 corrected to **PARTIALLY SATISFIED BY PHASE 1**
     (liveness/progress separation satisfied; bounded supervisor/recovery/shutdown behavior still
     open, mandatory, carried forward). AI-provider timeout observation consistency recorded as a
     second blocker before SYSTEM HEALTH runtime activation, next to synchronous SQLite latency.
  2. `test_phase1_hardening.py`: one regression test pins the existing timeout retry behavior
     (3 attempts at `retries=2`, configured timeout, unchanged backoff, final `CONNECTION_ERROR`)
     for every health-sink mode, closing the reviewer's surviving `timed_out` retry mutation.
  3. This changelog: Phase 1 Batch 1–3 history added from git evidence.
- Safety: REAL EXECUTION = DISABLED; NAS100 = OFF; trading DB schema 3. No deploy, no merge,
  Phase 2 not started, Phase 1 not certified.

## 2026-10-05 — V2 Phase 1 final hardening (SYSTEM HEALTH semantics)

- Agent: Claude. Branch `v2/phase1-system-health`; starting SHA
  `0a9687b9a78b8466903f29a667dcefc130d3116b`; final SHA = the commit that adds this entry.
- Purpose: three authorized observability fixes before final independent re-review.
  1. An explicitly `NOT_ALIVE` heartbeat never projects `HEALTHY` (H04: liveness != progress).
  2. A real OpenAI transport timeout is classified `TIMEOUT`, not `CONNECTION_ERROR`
     (`OpenAIProviderError.timed_out` evidence; provider kind, retries and exceptions unchanged).
  3. `NOT_CONFIGURED` is recorded as an `UNKNOWN` configuration state, never `HEALTHY` and never
     `UNKNOWN_PROVIDER_FAILURE`.
- Tests: `test_phase1_hardening.py` (22 focused tests: liveness A–E, timeout/connection/rate/
  billing/model/unknown classification, `NOT_CONFIGURED`, observer-failure isolation). Full suite
  608/608 pass, 0 skipped.
- Owner decisions recorded in `V2_PHASE1_SYSTEM_HEALTH.md`: H04 → Phase 1 (code/test evidence);
  H02 → mandatory Phase 2; H15 and deploy/build/config/run traceability → mandatory transversal V2;
  F01-T13 → Phase 1 representation only, Email Notification Engine → Phase 9; synchronous health
  SQLite persistence → blocker before SYSTEM HEALTH runtime activation.
- Safety: REAL EXECUTION = DISABLED; NAS100 = OFF; `storage/database.py` and trading DB schema 3
  unchanged; no trading, risk, execution or runtime behavior changed. No deploy, no merge,
  Phase 2 not started. Status: IMPLEMENTATION COMPLETE — PENDING FINAL CERTIFICATION.

## 2026-10-04 — V2 Phase 1 Batches 1–3 (history recorded 2026-10-05 from git)

Branch `v2/phase1-system-health` from `main` @ `2075e7f`. Test counts are Phase 1 test methods in
`test_system_health.py`, `test_health_probes.py` and `test_phase1_isolation.py` at each commit.

- `894def6` — Batch 1, SYSTEM HEALTH core (F01-T11 foundation, F01-T15..T19): per-component
  status, last success/error/check, consecutive errors, latency, heartbeat kept separate from
  progress, typed provider errors with legacy-name normalization, sanitized reasons. Initially an
  additive trading DB migration v3 → v4 (superseded by `43fdfc6`). 25 tests.
- `fbb883a` — Batch 1 review fix: `MODEL_UNAVAILABLE` and `CONNECTION_ERROR` kept as canonical
  error classes instead of `UNKNOWN_PROVIDER_FAILURE`. 27 tests.
- `43fdfc6` — Batch 1 owner decision: health persistence moved to an isolated sidecar
  (`storage/health_store.py`, application_id `V2HS`, schema 1) that refuses the trading DB;
  v3 → v4 migration reverted, `storage/database.py` byte-identical to the frozen V1 baseline
  (schema 3); frozen V1 code proven to open the trading DB after sidecar use. 30 tests.
- `a07ee5a` — Batch 2, component probes (F01-T01..T13, F01-T14 projection prep): read-only probes
  of trading DB evidence recorded only to the sidecar; email reported as `NOT_IMPLEMENTED`;
  exception-isolated observers not wired into the runtime; `record_state` for non-error
  conditions. 53 tests.
- `0a9687b` — Batch 3: passive no-op-by-default hooks (`runtime/health_hooks.py`) after the
  existing market-data verdict (F01-T02) and around `OpenAIProvider.generate` (F01-T04, result
  and exception unchanged); `ComponentErrorType`; read-only dashboard projection; F01-T20 PAPER
  scenario identical across six health modes. 62 tests.
- Safety throughout: REAL EXECUTION = DISABLED; NAS100 = OFF; no trading, risk, provider or
  execution behavior change; health sink never installed in the runtime; no deploy, no merge.

## 2026-10-02 — ISSUE-001/005 test contract and governance

- Reproduction: the full 523-test baseline suite had two failures because the
  cloud-runner test fixtures omitted `AI_FLOOR_GIT_COMMIT`; the runner rejected
  startup before the intended assertions.
- Change: cloud-runner fixtures now supply the full SHA of the commit that
  introduced the official freeze document. Added regression coverage that
  missing or malformed freeze identity remains fail-closed. No runtime code or
  CI workflow behavior changed.
- Governance: documented required `main` branch rules, independent review,
  successful `unit` CI, and PR/SHA/CI/deploy evidence. Added a PR template.
  GitHub branch protection remains an administrator setting and must be enabled
  before ISSUE-005 can be considered closed; no Render or production settings
  were changed.
- Comparability: test fixtures and governance documentation only; no trading,
  risk, execution, provider, prompt, or runtime behavior changed.

## 2026-09-25 — Authorized PAPER restart repair during Slack routing

- Reproduction: after a configuration-only redeploy of frozen commit 4428fc20,
  cloud startup rejected the existing experiment because experiment_not_started
  was required on every boot. User explicitly authorized this startup-only fix.
- Change: runtime/cloud.py accepts an existing experiment only when its freeze
  identity, account and durable run metadata match the active configuration.
  All other infrastructure/provider checks and the exclusive runner lock remain.
- Preservation: no trading logic, strategy, prompts, sizing, providers, sessions,
  cadence, freshness, economics or SQLite records are manually modified. The
  original freeze identity and start timestamp remain unchanged. The new commit
  is an operational repair, not a new trading baseline; record its deploy SHA
  separately from AI_FLOOR_GIT_COMMIT (the original experiment identity).
- Validation: baseline 509 tests pass with the required freeze environment;
  regression reproduces the rejected resume, then 513 tests pass. Existing
  Windows temporary-directory cleanup warnings occur after the successful suite.
  New tests cover unchanged durable state, identity/configuration drift and
  missing account/credentials. Production table hashes are compared after deploy.
- Comparability: no decision/execution rule changes. Restart resets process caches;
  repair is performed outside trading sessions. Any downtime crossing a scheduled
  slot must be recorded rather than backfilled. Do not clear experiment_started.

## 2026-09-19 — Experiment lifecycle

- `agent`: GitHub Copilot
- `task`: Añadir lifecycle durable e idempotente para el primer arranque autorizado del cloud runner.
- `lifecycle`: preflight es read-only; la marca se realiza solo tras adquirir el slot exclusivo y conserva `experiment_started_at_utc`, baseline SHA y freeze SHA.
- `safety`: no se modificaron estrategia, riesgo, prompts, proveedores, sesiones, cadence, freshness ni economía; no se añadió configuración que active Render, runner o scheduler.

## 2026-09-19 — Freeze oficial del experimento PAPER

- `status`: EXPERIMENT_FROZEN; baseline f5032baeb87766ad74905093c1b4195117995092.
- `economics`: USD, equity 10000, comisión/spread adicional/swap 0; fill al Open
  siguiente barra elegible y gaps actuales, aprobados expresamente por el usuario.
- `policy`: Solo fixes técnicos que invaliden experimento/integridad; cambios con
  impacto económico o decisional requieren evaluar nueva baseline y reinicio.
- `validation`: 507 tests OK; diff check y secret scan PASS. Este commit es
  exclusivamente documental y no cambia el ejecutable probado.
- `activation`: Runner/scheduler apagados; experimento no iniciado; Render sin
  desplegar la nueva baseline. Ver EXPERIMENT_FREEZE.md para pasos pendientes.

## 2026-09-19 — Auditoría final y preparación de freeze PAPER

- `agent`: Codex.
- `base`: f9ae6ce79deb9baaa0dab0faa0271e462599d843.
- `bugs`: Frescura/sesión solo por slot, ausencia de contratos en cloud,
  recuperación incompleta tras reinicio rápido, awareness FXMacroData omitido,
  y SESSION_SKIPPED contado por DAILY_SUMMARY. Reproducidos con fixtures offline.
- `fixes`: Doble gate temporal con reloj real; recuperación reciente solo bajo
  lock cloud exclusivo; awareness y conteo corregidos. Contratos PAPER aprobados
  con multiplier=1 y quantity grid inferior, sin modificar Risk Engine ni precios.
- `validation`: 489 tests baseline; 507 tests finales OK (18 nuevos);
  ver AUDIT_FREEZE.md y PAPER_EXECUTION_SPEC.md.
- `economics`: Usuario confirmó USD, equity 10000, comisión/spread adicional/swap
  0, fill al Open de siguiente barra elegible y gaps SL/TP existentes.
- `state`: Preparado para freeze oficial de código; sin activar runner/scheduler/
  experimento. Render sigue desplegado en f3af0c3; no se despliega en esta misión.

## 2026-09-19 — DAILY_SUMMARY y validación de notificaciones

- `agent`: Codex.
- `task`: Resumen del día UTC anterior completo desde SQLite: ciclos/símbolo,
  setups/riesgo, órdenes/posiciones PAPER, PnL/equity, problemas de proveedor,
  macro HIGH y estado operativo persistido. Contrato y ejemplo en DAILY_SUMMARY.md.
- `delivery`: Snapshot confirmado antes de Slack; deduplicación por día durable,
  incluso concurrente y tras reinicio; transporte fallido aislado del trading.
- `scope`: Solo supervisión y tests; sin cambios en estrategia, agentes/prompts,
  riesgo, gates, providers, sesiones ni ejecución PAPER. Sin activar experimento.
- `validation`: Baseline aislada 480 tests; final 489 tests OK, con 9 nuevos
  tests offline; Slack falso; git diff --check y secret scan por patrones.
- `workspace`: Cambios previos no committed de proveedores excluidos del commit.

## 2026-09-19 — Cloud preflight para FXMacroData certificado

- `agent`: Codex.
- `task`: Reconocer `fxmacrodata` como proveedor macro certificado cuando su clave existe, manteniendo `none` solo para infraestructura sin readiness de experimento.
- `safety`: PAPER only; runner/scheduler y experimento siguen apagados; ningún otro gate se relajó.
- `tests`: añadidos casos de FXMacroData con y sin credencial y preservado el caso `none`.

## 2026-09-19 — FXMacroData macro provider

- `agent`: Codex.
- `task`: Integrar y certificar calendario USD/EUR, announcements, predictions, changes y research/panel con caché, errores explícitos y no-lookahead por fetch/publicación/vintage.
- `decision`: `FXMACRODATA_CERTIFIED` live; adapter configurable y todavía no activo. El Blueprint conserva macro `none`.
- `safety`: runner/scheduler `0`, PAPER only; sin experimento, commit ni push.
- `tests_after`: 485 OK; certificación live PASS; `git diff --check` y secret scan OK.

## 2026-09-18 — Infraestructura cloud con macro diferido

- `agent`: Codex.
- `task`: Separar readiness de infraestructura y experimento, fijar macro efectivo `none`/`NO_DATA`, reforzar el gate del supervisor/runner, conservar una sola autoridad PAPER, redacción y límite de exportaciones, y retirar Finnhub de las variables del Blueprint activo.
- `decision`: INFRA_READY para despliegue controlado con montaje/entorno simulados; EXPERIMENT_READY=false por MACRO_PROVIDER_NOT_CERTIFIED. EODHD Free HTTP 403, sin adapter nuevo ni más consultas macro.
- `safety`: scheduler y runner apagados, real execution deshabilitada, sin despliegue, experimento, commit ni push.
- `tests_after`: 470 OK; `git diff --check` y secret scan OK; preflight simulado INFRA_READY/experiment_ready=false. Ver `AUDIT_POST_PHASE7.md`.

## 2026-09-18 — Evaluación del híbrido macro oficial

- `agent`: Codex.
- `task`: Implementar `OfficialMacroProvider` sobre calendarios ICS BLS/BEA/Eurostat y RSS Fed/BCE, con procedencia, precisión temporal, caché, aislamiento de fallos y evidencia en `ReviewReport`.
- `decision`: OFFICIAL_MACRO_INSUFFICIENT. BEA, Eurostat, Fed y BCE accesibles; BLS Access Denied. Los RSS monetarios no ofrecen agenda futura FOMC/BCE; Eurostat observado publica solo fecha. USD y EUR siguen PARTIAL.
- `safety`: Finnhub y FRED inactivos; macro `none` en Blueprint, scheduler apagado; sin despliegue, experimento, commit ni push.
- `tests_after`: 467 OK; `git diff --check` y secret scan OK; preflight NOT_READY por gate macro y entorno local. Ver `AUDIT_POST_PHASE7.md`.

## 2026-09-17 — Preparación cloud posterior a Fase 7

- `agent`: Codex.
- `task`: Preparar Render, persistencia durable, dashboard privado, Slack, evidencias compartidas y Finnhub Economic Calendar sin activar el experimento.
- `tests_before`: 447 OK.
- `tests_after`: 455 OK offline.
- `status`: READY_FOR_CLOUD_SETUP; Finnhub live sin certificar por HTTP 403 con la clave presente.
- `safety`: PAPER, scheduler y runner apagados; sin despliegue, mensajes Slack reales, commit ni push.

## 2026-09-17 — Evaluación FRED/ALFRED

- `agent`: Codex.
- `task`: Evaluar oficialmente FRED/ALFRED como fuente macro única para XAUUSD/EURUSD y endurecer el gate temporal.
- `decision`: FRED_INSUFFICIENT; calendario futuro solo por fecha, sin hora/zona, consenso ni importancia; FOMC/ECB no acreditados como cobertura completa. No se creó adapter activo ni se solicitó clave.
- `status`: Finnhub SUPPORTED/NOT_ACTIVE, macro provider `none`, sin despliegue, scheduler, experimento, commit ni push.
- `tests_after`: 459 OK; `git diff --check` y secret scan OK.

## 2026-09-17 — Fase 6D: Twelve Data activo

- `agent`: Codex
- `task`: Añadir Twelve Data como MarketDataProvider activo para la demo PAPER, con XAU/USD y EUR/USD en 5m/15m/1h; conservar Massive configurable/NOT_ACTIVE y NAS100 soportado/NOT_ENABLED.
- `tests_before`: 427 OK.
- `tests_after`: 435 OK en la suite final de cierre.
- `safety`: barras cerradas UTC, ticker exacto, validación OHLC, caché únicamente de datos actuales, freshness gate intacto, 429/5xx con reintentos acotados y sin exposición de credenciales. Scheduler y experimento de 14 días siguen apagados.
- `certification`: PASS de OpenAI `gpt-5.6-terra` y Twelve Data XAUUSD/EURUSD en 5m/15m/1h, 17 de septiembre de 2026. Ver `PROVIDER_CERTIFICATION.md` y `AUDIT_PHASE6D.md`.

## 2026-09-16 — Fase 6C

- `agent`: Codex
- `task`: Implementar runtime PAPER durable con SQLite, scheduler, recovery, health y compuerta final de ejecución.
- `tests_before`: 389 OK.
- `tests_after`: 405 OK.
- `files_created`: `storage/`, `runtime/`, `test_runtime.py`, `OPERATIONAL_RUNTIME_SPEC.md`, `AUDIT_PHASE6C.md`.
- `files_modified`: `.gitignore`, `ui/adapters.py`, `ui/state.py`, `ui/app.py`, `ui/pages/journal.py`, `ui/pages/system.py`, `ARCHITECTURE.md`, `ROADMAP.md`, `UI_SPEC.md`, `CHANGELOG_AGENT.md`.
- `safety`: PAPER only; `AI_CAUTION` bloquea progresión incluso con riesgo APPROVED; datos stale, barras abiertas y fallos bloquean nuevos paper orders.
- `next_phase`: preparación Fase 7, no implementada.

## 2026-09-16 — Fase 6B

- `agent`: Codex
- `task`: Implementar Hybrid Trading Floor UI en Streamlit y Plotly, preservando `app.py` legacy.
- `tests_before`: 381 OK.
- `tests_after`: 389 OK.
- `files_created`: `ui/`, `test_ui.py`, `requirements-ui.txt`, `UI_SPEC.md`, `AUDIT_PHASE6B.md`.
- `files_modified`: `ROADMAP.md`, `CHANGELOG_AGENT.md`.
- `integration`: `FloorRunReport → AIFloorReport → FloorViewModel`; renderers de solo lectura.
- `safety`: PAPER MODE, REAL EXECUTION DISABLED, RiskDecision como única autoridad, SAMPLE / DEMO aislado.
- `next_phase`: Fase 6C, no implementada.

## 2026-09-16

- `timestamp`: 2026-09-16
- `agent`: GitHub Copilot
- `phase`: Fase 6A — AI Agent Runtime
- `task`: Construir el motor de agentes IA advisory sobre el Trading Floor determinista existente, sin reemplazarlo.
- `tests_before`: 315 tests OK.
- `tests_after`: 381 tests OK.
- `files_created`: `ai/__init__.py`, `ai/contracts.py`, `ai/provider.py`, `ai/runtime.py`, `ai/prompts.py`, `ai/orchestrator.py`, `ai/agents/__init__.py`, `ai/agents/structure_ai.py`, `ai/agents/liquidity_ai.py`, `ai/agents/macro_ai.py`, `ai/agents/setup_reviewer_ai.py`, `ai/agents/trade_reviewer_ai.py`, `test_ai_contracts.py`, `test_ai_provider.py`, `test_ai_runtime.py`, `test_ai_agents.py`, `test_ai_reviewers.py`, `test_ai_orchestrator.py`, `AI_RUNTIME_SPEC.md`, `AUDIT_PHASE6A.md`.
- `files_modified`: `ARCHITECTURE.md`, `TRADING_FLOOR_SPEC.md`, `ROADMAP.md`, `CHANGELOG_AGENT.md`.
- `provider_abstraction`: `AIProvider.generate(request) -> AIResponse`; `DeterministicAIProvider` es el proveedor por defecto (sin red, sin API keys) y `FakeAIProvider` es el doble de prueba; un proveedor LLM real puede añadirse después sin tocar Risk Engine, Paper Broker ni scouts.
- `grounding_policy`: `validate_ai_response` rechaza cualquier respuesta con `run_id`, `symbol`, `agent_name`, `as_of`, `bias`, `recommendation` o `confidence` fuera de lo esperado, o que cite un `evidence_id` no presente en el `AIRequest`.
- `failure_isolation`: una excepción del proveedor se captura y se convierte en `AIResponse(status="ERROR")` local; el resto del run determinista continúa.
- `cost_control`: si no hay evidencia determinista utilizable, no se invoca al proveedor y se retorna `NO_DATA` directamente.
- `authority_policy`: `ai/orchestrator.py` compone sobre un `FloorRunReport` ya calculado por `floor/orchestrator.py` (no lo modifica); `NO_SETUP`, `WATCH` y `RISK_REJECTED` deterministas nunca se convierten en un estado ejecutable; un `PLAN_READY` con revisión IA adversa (`DISAGREE`/`REJECT_RECOMMENDATION`) se reporta como `AI_CAUTION` sin alterar el `RiskDecision` original.
- `immutability`: `MarketBar`, `TradePlan`, `RiskDecision` e `InstrumentSpec` son dataclasses frozen (cualquier intento de escritura lanza `FrozenInstanceError`); `PaperAccount` solo se expone a la capa IA mediante `ai.runtime.snapshot_account`, una copia profunda de solo lectura.
- `audit_log`: estructura en memoria con `run_id`, agente, versión de prompt, IDs de evidencia, respuesta, resultado de validación y metadata del proveedor; sin API keys ni credenciales. Persistencia durable diferida a Fase 6C.
- `real_execution_check`: REAL_EXECUTION = DISABLED; `ai/` no importa `execution.paper_broker` ni `execution.trade_manager`.
- `warnings`: warnings legacy de Streamlit/pandas preexistentes; sin red, broker ni dinero real.
- `known_debt`: sin proveedor LLM real conectado (deliberado), sin scheduler, sin persistencia durable del audit log.
- `next_phase`: Fase 6B — Floor Assistant y Trading Floor UI.

## 2026-09-16

- `timestamp`: 2026-09-16
- `agent`: GitHub Copilot
- `task`: Crear protocolo persistente y documentación base para AI Trading Floor V2.
- `files_changed`: `AGENTS.md`, `TRADING_FLOOR_SPEC.md`, `ARCHITECTURE.md`, `ROADMAP.md`, `CHANGELOG_AGENT.md`.
- `tests_before`: 230 tests OK.
- `tests_after`: No se ejecutaron cambios de código; baseline confirmado con 230 tests OK.
- `decisions`: Fase 0 marcada como completada; Fase 1 marcada como siguiente. Se conserva el prototipo legacy y se separan contratos, datos, agentes y motores deterministas en el diseño futuro.
- `warnings`: `git` no estaba disponible en el PATH de la terminal; no se modificó código Python.
- `next_step`: Diseñar e implementar Fase 1 solo con autorización explícita.

## Estado inicial conocido

- Baseline actual: `230 tests OK`.
- Checkpoint previo: Risk Engine V1 existente.
- No se ha autorizado todavía la implementación de Fase 1.

## 2026-09-16

- `timestamp`: 2026-09-16
- `agent`: GitHub Copilot
- `task`: Implementar el vertical slice determinista de Fase 1: contratos, InstrumentSpec, timeframes y Risk Engine LONG/SHORT con simulación explícita.
- `files_changed`: `core/contracts.py`, `core/timeframes.py`, `core/__init__.py`, `data/instruments.py`, `data/__init__.py`, `riesgo.py`, `simulador.py`, `estrategia.py`, `metricas_estrategia.py`, `evaluacion_estrategia.py`, `test_core_contracts.py`, `test_risk_v2.py`, `test_simulador.py`, `test_metricas_estrategia.py`, `test_evaluacion_estrategia.py`, `ROADMAP.md`.
- `tests_before`: 230 tests OK.
- `tests_after`: 237 tests OK.
- `decisions`: Mantener las APIs legacy; añadir una ruta V2 explícita. Rechazar instrumentos sin multiplicador contractual conocido. Mantener UTC y timeframes 1h/15m/5m. Usar APPROVED/REJECTED determinista y soportar LONG/SHORT.
- `warnings`: Warnings existentes de Streamlit y pandas durante la suite; no se conectó red ni broker.
- `next_step`: Fase 2: Structure Agent y Liquidity Agent deterministas.

## 2026-09-16

- `timestamp`: 2026-09-16
- `agent`: GitHub Copilot
- `phase`: Fase 2
- `task`: Implementar Structure Agent y Liquidity Agent deterministas con contrato AgentMessage versionado.
- `tests_before`: 237 tests OK.
- `tests_after`: 248 tests OK.
- `files_created`: `agents/__init__.py`, `agents/structure_agent.py`, `agents/liquidity_agent.py`, `test_structure_agent.py`, `test_liquidity_agent.py`.
- `files_modified`: `core/contracts.py`, `ROADMAP.md`, `CHANGELOG_AGENT.md`.
- `definitions_used`: swings confirmados con una barra cerrada posterior; HH/HL/LH/LL por comparación de swings; soporte/resistencia como extremos de swings; displacement como cuerpo/rango >= 0.6; equal levels con tolerancia relativa 0.1%; sweep high/low por ruptura del extremo previo y cierre de vuelta dentro.
- `heuristics`: displacement y equal-level detection están marcados `HEURISTIC`; order blocks no se implementaron.
- `warnings`: warnings preexistentes de Streamlit y pandas durante tests; sin red ni broker.
- `next_phase`: Fase 3 — Macro/News Agent.

## 2026-09-16

- `timestamp`: 2026-09-16
- `agent`: GitHub Copilot
- `phase`: Fase 4
- `task`: Implementar Setup Validator, Trade Planner y Orchestrator deterministas.
- `tests_before`: 266 tests OK.
- `tests_after`: 278 tests OK.
- `files_created`: `agents/setup_validator.py`, `agents/trade_planner.py`, `floor/__init__.py`, `floor/orchestrator.py`, `test_setup_validator.py`, `test_trade_planner.py`, `test_orchestrator.py`.
- `files_modified`: `core/contracts.py`, `agents/structure_agent.py`, `agents/liquidity_agent.py`, `agents/trade_planner.py`, `ROADMAP.md`, `CHANGELOG_AGENT.md`, `AUDIT_PRE_PHASE4.md`.
- `setup_policy`: requiere los tres timeframes explícitos; estructura compatible; evidencia 15m y liquidez 5m; macro HIGH ACTIVE_WINDOW bloquea; estados NO_SETUP/WATCH/VALID_SETUP.
- `multi_timeframe_policy`: 1h contexto, 15m setup, 5m confirmación; faltantes fallan cerrado.
- `liquidity_policy`: utiliza únicamente sweeps y pools/equal levels existentes; order blocks no implementados.
- `macro_filter_policy`: macro actúa como filtro de contexto; no genera dirección ni orden.
- `entry_policy`: último Close cerrado del timeframe 5m hasta `as_of`.
- `stop_policy`: invalidación estructural del SetupAssessment; no se inventa fallback.
- `target_policy`: target matemático mínimo R:R 3.0.
- `risk_handoff`: todo TradePlan se entrega al Risk Engine; Orchestrator nunca convierte VALID_SETUP directamente en APPROVED.
- `orchestrator_flow`: scouts -> SetupAssessment -> TradePlan -> RiskDecision -> FloorRunReport; no ejecución.
- `run_id_policy`: un UUID por run se propaga a scouts y reporte.
- `as_of_policy`: un instante lógico compartido por todos los scouts y planner.
- `fail_closed_policy`: datos faltantes, errores o evidencia insuficiente producen NO_SETUP/WATCH/RISK_REJECTED.
- `warnings`: warnings legacy de Streamlit/pandas; sin broker ni dinero real.
- `next_phase`: Fase 5 — Paper Execution y Trade Manager.

## 2026-09-16

- `timestamp`: 2026-09-16
- `agent`: GitHub Copilot
- `phase`: Fase 5
- `task`: Implementar Paper Broker y Trade Manager locales, deterministas y sin ejecución real.
- `tests_before`: 282 tests OK.
- `tests_after`: 284 tests OK.
- `files_created`: `execution/__init__.py`, `execution/contracts.py`, `execution/paper_broker.py`, `execution/trade_manager.py`, `test_execution.py`.
- `files_modified`: `ROADMAP.md`, `CHANGELOG_AGENT.md`.
- `paper_account_model`: equity = starting_equity + realized_pnl + unrealized_pnl; una posición abierta por símbolo.
- `order_state_machine`: PENDING -> FILLED/CANCELLED; validación fail-closed.
- `position_state_machine`: OPEN -> CLOSED; no reapertura.
- `fill_policy`: próxima barra elegible, Open real, sin sustitución por planned entry.
- `stale_plan_policy`: orden local idempotente por run_id; no se persigue una orden duplicada.
- `gap_policy`: manager evalúa gaps antes de niveles intrabar y conserva stop-first.
- `post_fill_risk_policy`: Paper Broker valida orden aprobada; no hay broker ni dinero real.
- `RR_policy`: RiskDecision debe llegar APPROVED desde el Risk Engine.
- `PnL_policy`: PnL LONG/SHORT determinista, costes paper y journal de transiciones.
- `equity_policy`: mark-to-market sobre posiciones abiertas y PnL realizado al cerrar.
- `journal_policy`: eventos estructurados con timestamp, run_id, símbolo y entity_id.
- `idempotency_policy`: segundo submit del mismo run devuelve la orden existente.
- `no_lookahead_check`: tests de fill en próxima barra y gestión secuencial.
- `real_execution_check`: REAL_EXECUTION = DISABLED.
- `known_debt`: expiración avanzada, partial fills, persistencia y reconciliación quedan para evolución posterior.
- `next_phase`: Fase 6 — Floor Assistant y Trading Floor UI.

## 2026-09-16

- `timestamp`: 2026-09-16
- `agent`: GitHub Copilot
- `phase`: Fase 5.1 hardening
- `task`: Auditar y endurecer Paper Broker/Trade Manager antes de Fase 6.
- `tests_before`: 284 tests OK.
- `tests_after`: 286 tests OK.
- `files_created`: `AUDIT_PHASE5.md`.
- `files_modified`: `execution/contracts.py`, `execution/paper_broker.py`, `execution/trade_manager.py`, `test_execution.py`, `ROADMAP.md`, `CHANGELOG_AGENT.md`.
- `decisions`: InstrumentSpec/multiplier requerido para ejecución; primera barra cerrada posterior a as_of; post-fill risk/RR/geometry fail-closed; símbolo y timestamp aislados; costes explícitos y journal estructurado.
- `warnings`: sin broker, red, credenciales ni dinero real.
- `next_phase`: Fase 6 — Floor Assistant y Trading Floor UI.

## 2026-09-16

- `timestamp`: 2026-09-16
- `agent`: GitHub Copilot
- `phase`: Fase 5.2 final execution safety gate
- `task`: Cerrar invariantes de Paper Execution con pruebas aisladas y flujo end-to-end real.
- `tests_before`: 286 tests OK.
- `tests_after`: 315 tests OK.
- `files_modified`: `execution/contracts.py`, `execution/paper_broker.py`, `execution/trade_manager.py`, `core/contracts.py`, `riesgo.py`, `test_execution.py`, `AUDIT_PHASE5.md`, `ROADMAP.md`, `CHANGELOG_AGENT.md`.
- `decisions`: eliminar fallback económico del multiplier; exigir timestamps aware, símbolo exacto, OHLC completo y vela cerrada; proteger opened_at/current equity; alinear LONG/SHORT en Risk Engine; propagar metadata y costes.
- `warnings`: el fixture sintético actual del Orchestrator sigue produciendo NO_SETUP; no se declara PLAN_READY sin evidencia.
- `next_phase`: Fase 6 — Floor Assistant y Trading Floor UI.

## 2026-09-16

- `timestamp`: 2026-09-16
- `agent`: GitHub Copilot
- `phase`: Fase 4.1 hardening
- `task`: Eliminar equity hardcodeada, exigir barras cerradas en Trade Planner, validar lineage/run_id, symbol, timeframe y as_of, y conservar PLAN_UNAVAILABLE.
- `tests_before`: 278 tests OK.
- `tests_after`: 282 tests OK.
- `files_created`: ninguno.
- `files_modified`: `core/timeframes.py`, `agents/structure_agent.py`, `agents/liquidity_agent.py`, `agents/trade_planner.py`, `agents/setup_validator.py`, `floor/orchestrator.py`, `core/contracts.py`, `test_trade_planner.py`, `test_setup_validator.py`, `test_orchestrator.py`, `CHANGELOG_AGENT.md`.
- `decisions`: equity ahora es argumento explícito del Orchestrator; equity inválida falla cerrado. Barras forming no pueden ser planned entry. Lineage y timestamps inconsistentes producen NO_SETUP. VALID_SETUP sin plan produce PLAN_UNAVAILABLE.
- `warnings`: warnings legacy de Streamlit/pandas; sin broker, scheduler ni ejecución real.
- `next_phase`: Fase 5 — Paper Execution y Trade Manager.

## 2026-09-16

- `timestamp`: 2026-09-16
- `agent`: GitHub Copilot
- `phase`: Pre-Phase-4 Audit
- `task`: Auditoría integral y hardening de la base Fase 0-3.
- `tests_before`: 253 tests OK.
- `tests_after`: 268 tests OK.
- `files_created`: `AUDIT_PRE_PHASE4.md`.
- `files_modified`: `agents/structure_agent.py`, `agents/liquidity_agent.py`, `agents/macro_news_agent.py`, `core/contracts.py`, `validacion_historica.py`, `test_structure_agent.py`, `test_liquidity_agent.py`, `test_macro_news.py`, `ROADMAP.md`, `CHANGELOG_AGENT.md`.
- `defects_fixed`: selección temporal incorrecta de BOS; equal highs/lows basados en barras no estructurales; Macro/News sin known_at/result_timestamp; deduplicación sin namespace de source; warning pandas de parsing temporal ambiguo.
- `known_non_blocking_debt`: dataclasses frozen con contenedores internos mutables; especificaciones contractuales desconocidas; limitaciones intradía de yfinance; warnings ScriptRunContext de Streamlit.
- `warnings`: no se encontraron secrets; no se conectó red, broker ni dinero real.
- `next_phase`: Fase 4 — Setup Validator, Trade Planner y Orchestrator.

## 2026-09-16

- `timestamp`: 2026-09-16
- `agent`: GitHub Copilot
- `phase`: Fase 3
- `task`: Implementar Macro/News Agent determinista con proveedor normalizado, freshness, relevancia y deduplicación.
- `tests_before`: 253 tests OK.
- `tests_after`: 266 tests OK.
- `files_created`: `data/macro_news.py`, `agents/macro_news_agent.py`, `test_macro_news.py`.
- `files_modified`: `core/contracts.py`, `ROADMAP.md`, `CHANGELOG_AGENT.md`.
- `provider_contract`: `MacroNewsProvider` con `macro_events()` y `news_items()`; `InMemoryMacroNewsProvider` para tests sin red.
- `timestamp_policy`: fuente/evento/publicación/recepción separados; timestamps timezone-aware normalizados a UTC; datos futuros rechazados.
- `no_lookahead_policy`: solo se aceptan datos cuyo `received_at` sea <= `as_of`; actuals sobre eventos futuros se rechazan.
- `freshness_policy`: ventanas explícitas UPCOMING 24h, ACTIVE_WINDOW 1h, RECENT 24h y STALE posterior.
- `dedup_policy`: IDs de proveedor cuando existen; fallback estable con campos disponibles.
- `relevance_policy`: categorías macro explícitas y relevancia USD/EUR/símbolo; sin causalidad ni dirección de precio.
- `warnings`: warnings legacy de Streamlit y mensajes esperados de tests; sin red ni claves API.
- `next_phase`: Fase 4 — Setup Validator, Trade Planner y Orchestrator.

## 2026-09-16

- `timestamp`: 2026-09-16
- `agent`: GitHub Copilot
- `phase`: Fase 2.1 closure gate
- `task`: Formalizar Break of Structure, retracement y parsing temporal sin warning ambiguo.
- `tests_before`: 248 tests OK.
- `tests_after`: 253 tests OK.
- `files_created`: ninguno.
- `files_modified`: `agents/structure_agent.py`, `agents/liquidity_agent.py`, `validacion_historica.py`, `test_structure_agent.py`, `test_liquidity_agent.py`, `ROADMAP.md` sin cambio de estado, `CHANGELOG_AGENT.md`.
- `BOS_definition`: Ruptura bullish/bearish por cierre de vela cerrada sobre el último swing confirmado; el swing requiere una vela cerrada posterior de confirmación y se registra evidencia completa.
- `retracement_definition`: Tras impulso confirmado y pivote protegido, el cierre actual queda entre ambos niveles sin invalidar el pivote; resultado marcado `HEURISTIC`.
- `no_lookahead_tests`: BOS y retracement usan `as_of`; barras futuras no cambian los reportes históricos.
- `pandas_warning_fix`: `pd.to_datetime(..., format="mixed")` elimina la inferencia ambigua sin silenciar warnings.
- `warnings_remaining`: warnings legítimos de ScriptRunContext de Streamlit y mensajes legacy esperados; warning temporal ambiguo de pandas eliminado.
- `next_phase`: Fase 3 — Macro/News Agent.
# Fase 6D — integración de proveedores

- Añadidos adapters Massive y OpenAI tras contratos existentes, con timeouts, reintentos acotados, salidas estructuradas, validación de barras cerradas y estados de error explícitos.
- Configuración local segura, modos de proveedor, certificador sin trading y CI de Python 3.13.
- Baseline 405 tests OK; suite ampliada 415 tests OK. Certificación en vivo pendiente de MASSIVE_API_KEY y confirmación de proyecto OpenAI. El conector Massive muestra NOT_ENTITLED para I:NDX reciente y RATE_LIMIT tras consultas adicionales; no se inicia la demo.
- Decisión posterior: enabled_symbols predeterminado XAUUSD,EURUSD; NAS100 soportado mediante I:NDX pero NOT_ENABLED/NOT_CERTIFIED. Runtime, scheduler, certificador y UI distinguen selección habilitada del catálogo. La clave OpenAI de Default project fue aceptada; 419 tests offline OK; la certificación live espera MASSIVE_API_KEY.
- Certificación live solicitada: MASSIVE_API_KEY no aparece en el .env.local indicado ni en el entorno; Massive no se consultó. OpenAI devolvió HTTP 429 RATE_LIMITED para gpt-5.6-terra. Se añadió clasificación segura de cuota agotada y prueba; 420 tests offline OK. Fase 6D sigue PARTIAL y sin commit/push.
- Reintento posterior con MASSIVE_API_KEY presente: XAUUSD referencia y tres intervalos PASS, pero STALE_DATA; EURUSD referencia PASS, pero RATE_LIMITED antes de certificar barras. NAS100 no se consultó. Massive permanece PARTIAL; OpenAI no se reintentó en esta pasada; no se hace commit/push.

# Fase 7 — Demo Runner PAPER

- Demo Runner durable por slot, preflight local READY/NOT_READY, modo diagnóstico sin órdenes y certificador offline/live.
- SQLite schema 2 agrega revisiones y notificaciones versionadas; migración desde schema 1, recuperación de ciclos interrumpidos y lectura de UI sin escritura.
- Tests E2E sintéticos cubren setup LONG/SHORT, riesgo, IA adversa, datos inválidos, fallos de proveedores, duplicados y reinicio/cierre de posición.
- Proveedores activos de la demo: Twelve Data y OpenAI; Massive soportado/no activo; NAS100 soportado/no habilitado. El experimento no se inicia.
- Certificación final local: 447 tests OK; preflight READY; diagnóstico live XAUUSD/EURUSD PASS con barras actuales, IA contractual y cero órdenes.

# 2026-09-29 — PAPER execution observability (PR only)

- Reproduced a `PLAN_READY`/risk `APPROVED` cycle with no order because an
  existing position or pending order owns the symbol. The audit result is now
  persisted in the review and journal and sent as a separate final Slack
  `EXECUTION_DECISION`. No new field participates in the PAPER policy gate.
- Added an advisory `setup_id` from existing 15m structural evidence. It is
  never used for deduplication, sizing, risk, or order submission.
- SYSTEM can look up a persisted review and journal by run ID. Its bulk export
  shrinks to the newest records when the 1 MB bound is reached, avoiding a
  page-wide exception; a single oversized review yields an explicit warning.
- Comparability: trading signals, risk decisions, order/fill economics, and
  exit logic are unchanged. Additional journal/notification records and review
  metadata affect observability only. The deployed commit remains untouched.
- Tests: 512 passed on the main-based PR with a valid `AI_FLOOR_GIT_COMMIT`
  supplied to the existing cloud-runner test harness. Without that variable, two pre-existing baseline
  tests fail before this change because cloud_runner requires a commit SHA.
- No deploy, merge, production database mutation, or REAL execution activation.

## 2026-09-29 — Slack signal policy and isolated PAPER audit (PR #2 follow-up)

- Reproduced redundant cycle alerts and injected setup ID, audit transaction,
  analysis journal, post-persist order journal, dashboard snapshot, notification capture and Slack
  failures. PAPER decisions and saved orders survive these audit failures.
- Capture immediate SUBMITTED, first VALID_SETUP blocked outcome or changed
  blocker/reason, RISK_REJECTED, AI_CAUTION, position transitions and operational
  failures. WATCH/NO_SETUP and repeated identical blockers remain in journal and
  review; SETUP_VALID_SETUP and duplicate order alert no longer add Slack noise.
- The run export enforces its byte bound even when compact evidence is oversized.
  Capture and delivery errors log only the exception type, never credentials.
- The operational restart repair is separately proposed in PR #3. Revalidate
  the combined tree before either merge; keep the original freeze identity.

## 2026-10-10 — M-5 R-HALT race certification (design 3.8 "Race tests", checklist I5; no commit)

- New `test_phase8_rhalt_races.py`: one injector fires the halt at every 3.8 point, both in process (the handler
  body) and with a REAL `os.kill(os.getpid(), SIGTERM)` (plus one `SIGINT`) in isolated child processes using the
  production handler `HaltGate.request`. The points: (a) just before L(W), (b) after L(W) before the load,
  (c) between the load and BEGIN (the computation; c2 is the read2 -> BEGIN residual), (d) inside the save
  transaction at its first economic statement, (e) after the commit before the next admission, (f) between
  catch-up bars (f2: a computed bar refused at read2), (g) between symbols.
- Randomized property test (in process, plus a real-SIGTERM sample): one draw per injection-event type plus uniform
  draws over the 4-cycle period. For every draw it checks: `L(W) < T_h` and `read2 < T_h`; at most one residual;
  every `save_paper` call admitted; the post-T_h allowlist (H / P / R plus the single in-flight save); the halted
  economic state equals the reference state after some committed write (prefix property); a clean verifier and G14.
- Verifier boundary tests: `L(W) == T_h` -> `ADMITTED_AFTER_HALT`, `read2 == T_h` -> `READ2_AFTER_HALT`,
  `T_ACK_BEFORE_T_STOP`, `HALT_RECORD_CONTRADICTION`, and missing read2 / T_stop.
- Defect found and fixed (coarse clock): halt instants came from raw `time.monotonic_ns()`. With ~15.6 ms
  resolution (Windows), `read2 == T_h` refused the residual write's REX_WRITE, which left orphan economic events and
  a `FINAL_EDG_MISMATCH`. `T_stop == T_h` also hid the residual. Fix: `HaltGate.now_ns()` is a strictly increasing
  gate clock (assignments only, so the handler may call it). It is used for L(W), read2, T_h and T_ack
  (`runtime/halt.py`) and for T_stop (`runtime/rex.py` `RexStore._t_stop_ns`, raw clock when there is no gate).
  On Linux, ns resolution makes the values effectively identical.
- Defect found and fixed (oracle false positive): a catch-up bar refused at read2 is observed (ST2C) before its
  computation and never saved, so G14 reported `CATCH_UP_BAR_COVERAGE`. `replay/rex_oracle.py` now accepts exactly
  one unwritten observed bar, and only when the run's `EXIT_HALT` is `catch_up_bar` refused at `read2`.
- `test_phase8_rhalt_gate.py`: the handler-minimality test now allows `now_ns` and asserts it calls only
  `monotonic_ns`.
- Not changed: strategy, risk, sizing, SL/TP, broker, flag defaults (`AI_FLOOR_V2_RHALT` stays OFF),
  `storage/database.py`, Render / deploy. Flag OFF path unchanged.
- Tests: baseline 1376 OK; after 1389 OK (13 new). M-5 closure still needs the independent certification (I5).

### 2026-10-10 — M-5 follow-up: Codex review fixes (HIGH clock reentrancy, MEDIUM G14 ordering; no commit)

- HIGH, fixed (`runtime/halt.py`): `HaltGate.now_ns()` was not reentrant with respect to the signal handler. The
  handler called `now_ns()` between an interrupted call's compute and its store of `_last_ns`. That store then
  rewound the clock below T_h. On a frozen clock this gave T_stop < T_h (residual hidden) or a later instant equal to
  T_h (old code, 5 bytecode positions: `T_ack == T_h == 1002`). Fix:
  - The handler no longer writes the clock. It reads it once: `T_h = max(monotonic_ns(), _last_ns + 1)`, still a
    single assignment.
  - `now_ns()` stores its value BEFORE reading `requested_at` and moves the value above T_h if T_h is set.
  - Result: T_h is above every instant already returned, every instant is != T_h, and every later instant is > T_h.
  - An instant that overlaps T_h is ordered after it. A T_stop overlapping T_h is reported as a residual
    (over-reported, never hidden). An admission or read2 overlapping T_h is refused, because it checks
    `requested_at` after its clock read.
- MEDIUM, fixed (`replay/rex_oracle.py`): the G14 catch-up read2 exception only checked cardinality. It now does
  ordered observation -> write matching: write i's context must equal observed bar i (new code
  `CATCH_UP_BAR_ORDER`). This proves the single unmatched observation is the final, refused bar.
- New tests in `test_phase8_rhalt_races.py`:
  - `ClockReentrancyTests` fires the handler at EVERY bytecode of an interrupted `now_ns` (`sys.settrace` opcode
    events, frozen clock) for T_stop, admission and read2. It also covers the handler running during the clock
    read, checks that `request` stores only `requested_at` / `deferred_signal`, and sends a REAL SIGTERM from inside
    `now_ns` in an isolated child (POSIX).
  - `CatchUpOracleOrderTests`: a real halted catch-up has 3 observed bars and 2 writes, and the third bar is refused
    at read2; it passes. A doctored record with an earlier write missing (cardinality still balances) fails with
    `CATCH_UP_BAR_ORDER` and no `CATCH_UP_BAR_COVERAGE`. Reordered writes also fail.
  - Mutation check: against the previous `halt.py` / `rex_oracle.py`, the new clock test fails at the store-window
    bytecodes and the negative oracle tests fail.
- `test_phase8_rhalt_gate.py`: the handler-minimality allowlist is now `{max, monotonic_ns, _utc_now}`.
- No trading semantics changed. The flag OFF path is unchanged.
- Tests: full suite 1397 OK (8 new).
## 2026-10-10 — DEC-8.18a read-only status verifier and offline G-8.INT

- Added `replay/status_verifier.py`: Owner-made SQLite copies opened with
  `mode=ro`; SHA-256 before/after for trading and optional evidence inputs;
  JSON Q1–Q8, independent FS/EX/PD/RS, integrity overlay and STOP findings.
  Reports formal-start contradictions, schema/integrity failures, outstanding
  RUNNING rows for recovery, period boundary exclusions, M1–M10 history mapping
  and E1–E7 economic links, quarantine counts and the existing REVISION gate.
- DEC-8.18a removes H5 SSH execution signatures. EX is a DB observation, always
  marked `EX_UNATTESTED (DEC-8.18a: live verification in Phase 13)`; STOPPED
  requires the stopped runner key and stale recorded activity. Missing, future
  or contradictory activity stays UNKNOWN. No recovery is performed.
- Segment histories requiring external anchored section 3.3.6 verification
  fail closed with `SEGMENT_CHAIN_NOT_VERIFIED`; this command accepts no trust
  root and does not infer trusted anchors from DB-local records. Results remain
  UNCERTIFIED. Missing copy_time cannot establish STARTED or fresh execution.
- Added 14 deterministic status tests and one offline G-8.INT CLI test using
  schema-3 Store fixtures and the existing real runtime/REX scenario helpers.
  Covers fresh/consistent/duplicate starts, UTC/SHA contracts, schema/corruption,
  execution observations, recovery listing, unchanged hashes, output-path
  protection, orphan/missing history, duplicate closes and the 14-day boundary.
- Offline G1–G15: G1 PASS; G2 BLOCKED; G3 BLOCKED; G4 BLOCKED; G5 BLOCKED;
  G6 PASS; G7 PASS; G8 NOT VERIFIED; G9 PASS; G10 PASS; G11 BLOCKED;
  G12 PASS; G13 BLOCKED; G14 PASS; G15 NOT VERIFIED. No criterion or integration
  check reports FAIL. G8 records the sparse fixture's GAPs, without fabricating
  explanations. No formal genesis/live session period, rehearsal, external
  anchors or independent review is supplied, so the overall verdict is BLOCKED.
- Comparability: observability and temporary test fixtures only; no trading,
  risk, strategy, provider, prompt, schema or economic implementation changed.
  No production DB access, runner/scheduler activation, commit, staging or push.
- Validation: baseline suite passed; 15 focused tests passed. Full-suite final
  result recorded below.
- Final requested command: **1391 tests OK** (15 new; focused suite 15 OK),
  with `PYTHONDONTWRITEBYTECODE=1 AI_FLOOR_MARKET_PROVIDER=none
  AI_FLOOR_AI_PROVIDER=deterministic /workspace/repo/.venv/bin/python -m
  unittest discover -q`. `git diff --check` and whitespace checks on all three
  new files: PASS. Offline input SHA-256 values equal before/after.
## 2026-10-10 — Process safety: ruff (F) lint and safety invariants (v2/process-safety, not committed)

- Added `pyproject.toml` with `[tool.ruff.lint] select = ["F"]` only, pinned `ruff==0.17.0` in the new
  `requirements-dev.txt`, and a `lint` job in `.github/workflows/ci.yml` running `ruff check .`. No reformatting.
- Fixed the pyflakes findings (86): 74 unused imports removed; unused variables removed in `replay/rex_oracle.py`,
  `replay/risk_audit.py` and three test files. F821: lambdas inside `except ... as exc` in `runtime/service.py` and
  `runtime/rex.py` now bind `exc` as a default argument (same value, called synchronously; no behaviour change).
  `ejecutar_estrategia.py` (legacy V1) had a real bug: `detalle` was always `None` because the exception name is
  unbound after the `except` block; it now carries `str(error)`. `execution/risk_engine_v2.py` (frozen Risk Engine)
  is left byte-identical via a per-file `F841` ignore for its dead `stop` binding.
- NAS100 hard block: `runtime.config.BLOCKED_SYMBOLS = ("NAS100",)`; `RuntimeConfig` rejects it in
  `enabled_symbols`, including via `AI_FLOOR_ENABLED_SYMBOLS`; `python -m runtime --once` no longer offers NAS100.
  NAS100 data/instrument/UI definitions are untouched; no existing test needed adjustment.
- New `test_safety_invariants.py` (13 tests): REAL_EXECUTION_ENABLED is a single literal `False` with no env read;
  no source imports a real broker SDK (AST); NAS100 block in config/env/CLI; `PaperOrder` is built only in
  `PaperBroker.submit_plan`, whose callers are an explicit allowlist, and `submit_plan` is never reached without an
  APPROVED Risk decision (unit, Risk V2 reservation and full runtime cycle with a spy). Mutation-checked.
- Comparability: strategy, signals, risk, R:R, sizing, fills, prompts, providers and economics unchanged. The NAS100
  block matches the frozen config (NAS100 not enabled). Tests: 1389 OK; `ruff check .` passes.

## 2026-10-10 — Phase 8 owner decisions DEC-8.18 / DEC-8.19 (documentation only)

- DEC-8.18 APPROVED as-is (status-verification procedure, axes, H5). DEC-8.19 APPROVED, option (a): flat precondition under A2.
- Updated: `V2_PHASE8_CERTIFICATION_CHECKLIST.md`, `V2_PHASE8_P86_DESIGN.md` (§8.5), `V2_PHASE8_P86_HANDOFF.md`. Historical entries unchanged.
- No code, flags, deploy or merge. Phase 8 NOT CERTIFIED. PAPER only; REAL OFF; NAS100 OFF.

## 2026-10-10 — DEC-8.23 (documentation only)

- Owner decision: live-session G-8.INT gates re-scoped to offline/replay evidence for Phase 8; live validation moves to the 30-day demo after Phase 13. Checklist §0, design §8.5, handoff updated. No code/flags/deploy.

## 2026-10-10 — M-5 CLOSED, DEC-8.18a, offline G-8.INT summary (docs only)

- Checklist §0 updated: DEC-8.18a amendment, M-5 CLOSED (PR #16), offline G-8.INT results (PR #18), ISSUE-012/013.

## 2026-10-11 — PR #23 MEDIUM/LOW review fixes (Phase 10, uncommitted)

- Preserved main's Phase 9 STATUS; updated only Phase 10 row/next steps after Grok Bot REQUEST_CHANGES.
- ALERTS reads the optional Phase 9 alert_deliveries ledger directly with SQLite mode=ro, plus V1 deliveries; missing/empty sources show NO_DATA without creating files or importing dispatcher at runtime.
- Bounded dashboard queries (200, deterministic ORDER BY); SQL exact identity filters before LIMIT and per-table history pagination. No invented IDs; parent runs remain resolvable outside the recent page.
- Missing system_state falls back to DEFAULT; removed unused legacy positions page. Six regression tests cover these fixes, immutable ledger, missing data, nested IDs, old matches and pagination.
- Baseline requested suite exited 0; focused Phase 10: 13 tests PASS. Full requested suite: 1481 tests OK, 1 expected failure (183.177s). `/workspace/repo/.venv/bin/ruff check .` and `git diff --check`: PASS.
- PAPER only; flags, execution, risk, economy and Render unchanged. No staging/commit/push.
## 2026-10-11 — Phase 9 issues #24 / #25 (local, no commit)

- `AlertHook` only admits events via a bounded queue (default 1000); full queues drop and log. A single daemon worker owns mapping, persistence, sends and retry/backoff. Added bounded `flush(timeout)` / `close(timeout)`; runner shutdown signals the hook without waiting for delivery. Alerts remain OFF unless `AI_FLOOR_ALERTS_ENABLED=1`.
- Durable ledger now stores complete immutable alert snapshots before channel delivery. Enabled hook startup replays unclaimed events and stale SENDING claims using existing atomic CAS / `AMBIGUOUS_RETRY`; terminal outcomes do not resend. Idle/active worker revisits fresh claims once per second so they recover after expiry. Legacy claims without snapshots explicitly log `RECOVERY_NO_DATA`.
- Gmail SMTP default timeout reduced from 15 s to 10 s; SMTP/Slack reject timeouts outside `(0, 10]`. Added mock-only tests covering 5-second sends with hook latency <50 ms, overflow, bounded shutdown, worker-only retries, timeout bounds, durable restart and delayed stale recovery. Queue admission is not durable until the worker persists it; overflow / pre-persistence crashes can lose alerts.
- Updated phase 9 and status documentation; no strategy, risk, economics, Render, activation, dependency, staging, commit or push changes. Baseline: 1468 tests OK (one expected failure). Final validation recorded below.
- Final validation: requested offline full-suite command **1475 tests OK (one expected failure)**; focused alert suite **49 tests OK**. `/workspace/repo/.venv/bin/ruff check .` PASS; `git diff --check` PASS. Seven new regression tests, mocks only.

## 2026-10-11: certificación ligera de las fases 9-12 y runbook de la Fase 13 (Grok Bot)
- Checklists en docs/phases/phase-09..12.md con enlaces a PRs, CI y revisiones; STATUS a 13/14.
- Nuevo docs/phases/phase-13-runbook.md (no se ejecuta nada).

## 2026-10-13 — PR #37 dashboard readability, bounded memory and candle-cache charts

Owner-authorized work on `v2/dashboard-luxury`: fetched origin and ran `git merge origin/main`;
latest main `8b11da0` already an ancestor, result **Already up to date**, no conflicts.
Inspected `origin/v2/pre-demo-fixes:runtime/candle_cache.py` without merging that branch.

- JSON: identities/outcomes first, escaped indented collapsible details; legacy dataframe JSON
  detail limited to 20 displayed records, native JSON collapsed. Scalar ET/number/NO_DATA formatting retained.
- Mobile: single-column Streamlit columns and luxury grids through 768px. Computed Chromium CSS
  checks at 375/640/768/1024px, including long identities/no page overflow.
- Memory: tables capped at 200; Process/validated Setups queries bounded (validation filters before
  LIMIT), one selected setup chart; old exact-run Process links preserved. Overview metrics stream
  scalar full-ledger fields with only 200 curve points retained. Legacy view loads account totals,
  OPEN positions and latest 200 orders (pending prioritized), without reconstructing full fills/closed-trade ledgers.
- Read-only stdlib cache adapter for exact pre-demo `candles(symbol,bar_start,close) WITHOUT ROWID`
  schema. `AI_FLOOR_CANDLE_CACHE_PATH`, default `candle_cache.db` beside `AI_FLOOR_DB_PATH`.
  Last 48 closed 5m prices at setup `as_of`, chronological, symbol scoped; no future/open bars,
  file creation, provider calls or synthetic OHLC. Missing/invalid cache falls back to exact-cycle
  persisted 15m OHLC or explicit unavailable state. UI `st.cache_data(ttl=30,max_entries=32)`.
- Setup email stays OFF by default; cache prices saved with event for retries/restart. Existing
  worker/dedup path retained, reconciliation paged. Real PNG generated via existing matplotlib Agg,
  plan levels and multiplier-aware labels shared with dashboard; no Chrome/Kaleido requirement.
  Removed unused Kaleido requirement. Render errors preserve text delivery; SMTP tests mocked.
- Validation: repo venv baseline 1614 tests OK (1 skipped, 1 expected failure); system Python lacked
  optional dependencies. Focused dashboard/readability/legacy/replay tests: 58 PASS. Chromium CSS: PASS.
  Ruff and `git diff --check`: PASS. Closing full suite: 1629 tests OK (2 skipped, 1 expected failure), 190.416s.
  The optional Chromium test passes separately in the existing Playwright environment; the other skip is preexisting.
  Running authenticated app: Overview columns PASS at 375/640/768/1024px; Setups cache chart PASS at 375px.
  First closing run detected legacy FILLED-order compatibility; fixed with bounded pending-first order history,
  and the replay regression plus final full suite pass.

PAPER/DEMO only; no risk/strategy/runtime activation, NAS100 enablement, Render changes, main merge or deploy.

## 2026-10-13 — PR #37 History evidence pagination review fix (P2)

- History renders each already-paginated record type in its own bounded table, so
  200 runs no longer discard setups, reviews, orders, fills, positions, trades or journal.
- In-memory regression covers 201 records per type, two complete/non-overlapping
  pages, exact combined ID filters and empty pages. It fails against the original
  rendering (one table instead of eight); focused Phase 10 suite: 17 tests PASS.
- Baseline: 1629 tests, two preexisting errors caused by missing matplotlib
  (PNG rendering and SMTP attachment), 2 skipped, 1 expected failure.
- Local changes only on `v2/dashboard-luxury`; no commit, push, runtime or deploy.

Validación final del fix History: 1630 tests en 190.121 s; mismos dos errores
preexistentes por matplotlib ausente, 2 skipped, 1 expected failure; sin fallos nuevos.
Suite enfocada: 17 PASS; git diff --check PASS. Ruff no disponible en el entorno.

## 2026-10-13 — PR #39: cancellation block and originating signal time (local)

- Snapshot position catch-up still runs, but cannot overwrite an earlier PAPER block. Two failed cancellation
  CAS writes retain `STALE_PAPER_STATE`, blocking pending fills and new submissions for the cycle.
- Trade alerts use the originating order's `as_of`, falling back only to its submitting review. A later closing
  run cannot supply the signal timestamp; absent original evidence remains NO_DATA.
- Two regression tests failed on the original implementation (expired order FILLED; later signal timestamp)
  and pass after the fixes. Coverage includes successful catch-up following both stale writes, order timestamp
  priority, submitting-review fallback and missing timestamps.
- Baseline: unittest 1642 tests OK (1 skipped, 1 expected failure). Focused suite: 39 tests OK.
- Final full offline unittest suite: 1644 tests OK (1 skipped, 1 expected failure), 205.346 s.
  `ruff check .` and `git diff --check`: PASS. PAPER only; no flags, dependencies, activation, merge or deploy.
  No staging, commit or push.

## 2026-10-13 — PR #39: owner-approved gold options 1 and 4 ONLY (local)

- FXMacroData keeps exactly the previous and current snapshots, selecting the newest event-ID vintage
  fetched by the slot. Refreshed received_at/known_at/fetched_at use fetch completion, never slot backdating.
  Cold starts still return NO_DATA for slots preceding the first fetch. Two snapshots are retained,
  not an unbounded historical archive; provider-error behavior remains unchanged.
- Lightweight cycle summaries in `<trading-db-stem>.cycles.db` beside the trading database: symbol/UTC slot,
  run ID, existing setup/operational gate results, each TF's structure state, setup/rejection,
  AI statuses (including NOT_REQUIRED/NOT_EVALUATED), execution reason and risk reason.
  One short connection per claimed cycle, 100 ms SQLite lock timeout, 30-day global retention measured
  from newest slot and hard cap 10 000 rows. No candles/raw prompts or review prose copied.
  Duplicate slots keep their original record; observability errors only log, and R-HALT suppresses writes.
  In-memory test databases have no persistent sidecar. This audit is best effort, not a certified ledger.
- cloud_runner configures INFO logging to stdout, including existing-handler startup, for RUN_COMPLETED.
- Baseline with system python3: missing existing streamlit/yfinance/plotly dependencies (8 errors).
  Existing repo venv baseline: 1644 tests OK (1 skipped, 1 expected failure), 200.247 s; no installs.
- Final offline unittest suite: 1657 tests OK (1 skipped, 1 expected failure), 205.739 s.
  Focused suite: 21 tests OK. `ruff check .` and `git diff --check`: PASS. All three new macro regressions
  fail against the original HEAD provider, then pass with the fix. Deterministic refresh, fetch-completion,
  alignment, success/stale/error/session cycles, duplicate slots, pruning/restart, disk/lock failures,
  halt and stdout logging tests added.
- No timeframe-alignment or strategy/risk/economy changes, dependencies, trading-DB schema changes,
  flags, Render, activation, merge or deploy. No staging, commit or push.

## Integración de main en dashboard luxury (2026-10-13)

Merge autorizado por el owner en `v2/dashboard-luxury`: dashboard luxury, Process y Setups
conservados bajo `ui/views`; imports de aplicación, email y tests migrados. Se conserva
el hardening de auth (rate limit, lockout y TTL), Twelve Data por cabecera, email con
remitente/destinatario explícitos, candle cache y cycle records de main. Plotly==7.1.0
y Streamlit==1.65.0 coinciden con requirements-ci.txt y sus hashes; PNG usa
matplotlib==3.10.7/Agg, sin requisito Kaleido. Fixture de email actualizado para el
contrato env-required. Ambas historias de CHANGELOG/STATUS/fase 09 preservadas.
REAL OFF, NAS100 bloqueado; sin activación, deploy ni merge del PR.

Validación del merge: `TMPDIR=/tmp /workspace/venv/bin/python -m pytest -q` →
1721 passed, 2 skipped, 1 xfailed, 1181 subtests passed (222.50 s).
`git diff --check` PASS; conflictos resueltos. Ruff no instalado en el entorno.

## 2026-10-13 — owner-authorized halt/shutdown and dashboard fixes

Branch: v2/halt-shutdown-dashboard-fixes. Scope: normal SIGTERM -> CLEAN_SHUTDOWN; all other causes
retain exact-token safety barrier. Atomic bookkeeping, economic write gates, residual REX verification
and startup fail-closed behavior preserved. Existing safety fixtures now inject SIGINT; new real SIGTERM
restart tests require no token and show that CLEAN_SHUTDOWN cannot clear a prior safety halt.

Luxury: close reason and realized R from exact persisted position/original stop; full-ledger Avg R via
streamed indexed join, bounded trade rows, open-position MTM from stored marks; exact-run snapshot
fallback for missing agent fields and batched parent lookup. Overview axis margins fixed. Setups cache
path already implemented: added read-only bounded render regression. Legacy views retained following
inspection. EURUSD historical trade absent; tests characterize adverse bar-open gap after moving the
stop to entry, intrabar stop at entry and one cost charge; no economic execution change.

Baseline: prescribed unittest suite ran 1729 tests (2 skipped, 1 expected failure), one preexisting failure
from ignored ui/pages/__pycache__ only. Moved stale directory to /tmp/halt-dashboard-stale-pages-cache;
no source page removed. Interpreter: /workspace/venv/bin/python (python absent from initial PATH).
Final validation: python -m pytest -q via PATH=/workspace/venv/bin:$PATH, offline deterministic
providers and PYTHONDONTWRITEBYTECODE=1: 1736 passed, 2 skipped, 1 xfailed, 1197 subtests passed
(211.83 s). Prior full run also green (215.31 s). Focused UI/startup: 46 passed, 16 subtests;
final luxury labels: 26 passed, 7 subtests. git diff --check PASS; Ruff unavailable.
No dependencies/env/Render/AI_FLOOR_GIT_COMMIT/freeze checks changed.

Commit attempt after green validation blocked by environment: .git is read-only; git add and git commit
cannot create .git/index.lock. No files staged, no commit/push/merge/deploy performed.

## 2026-10-13 — review fixes: signal priority and original trade risk

Owner-authorized corrections on the current branch: deferred startup requests retain safety priority;
non-SIGTERM requests upgrade pending SIGTERM before bookkeeping is recorded, preserving the original
T_h. Later SIGTERM cannot downgrade a safety request. Regression tests cover both signal orders inside
startup_write and before bookkeeping, plus immutable committed shutdown classification.
Luxury realized R requires persisted initial_stop and uses the trade's stored quantity, including the
streamed full-ledger calculation; missing original risk remains NO_DATA. Tests cover moved/missing
stops, trade/position quantity differences and invalid stored quantities.
Baseline: prescribed unittest suite PASS, 1739 tests (2 skipped, 1 expected failure).
Final validation: `/workspace/venv/bin/python -m pytest -q` PASS: 1739 passed, 2 skipped,
1 xfailed, 1201 subtests passed in 215.78 s. Focused gate/dashboard suite: 45 passed,
18 subtests passed. `git diff --check` PASS.
No staging/commit/push/deploy or AI_FLOOR_GIT_COMMIT/freeze changes.

## 2026-10-17 · Security headers via ASGI middleware (OANDA draft branch)
`ui/security_headers.py` (HSTS, CSP, X-Frame-Options DENY, nosniff, Referrer-Policy, Permissions-Policy, COOP) wrapped by
`ui/asgi_app.py` (`st.App`); `runtime/cloud.py` starts `ui/asgi_app.py` (rollback: `AI_FLOOR_SECURITY_HEADERS=0` -> `ui/app.py`).
Tests: `test_security_headers.py`; login, navigation and the Control view verified in headless Chrome with zero CSP violations.
No Render/env change; no deploy.

## 2026-10-18 · Hardening para el arranque del demo OANDA (rama release/oct18-hardening; todo OFF salvo el botón de emergencia)
- Límite de pérdida diaria 2 % (equity NAV, día 17:00 NY, flatten ON, gate de drawdown 5 %, transferencias vía consulta de solo lectura `TRANSFER_FUNDS`, latches persistentes): `execution/daily_loss_limit.py`, flag `AI_FLOOR_DAILY_LOSS_LIMIT=1` (OFF por defecto). DEC-5.5a.
- Botón de parada de emergencia cableado al kill switch (confirmación escrita `PARAR TODO`, auditoría, solicitud de flatten consumida por el router, router close-only tras reinicio); drift-clear verificado por episodio (`AI_FLOOR_DRIFT_CLEAR_BUTTON=1`, OFF); cierre previo del viernes (`AI_FLOOR_FRIDAY_PREFLATTEN=1`, OFF); alertas Slack del broker (usa `AI_FLOOR_ALERTS_ENABLED` + webhook existentes).
- Historial (referencia V1/PAPER, separado de OANDA) y textos 30 días en la UI. Sin cambios de entorno de Render; enrutado OFF.
