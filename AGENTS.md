# AI Market System — Agent Protocol

## Fuente de verdad (desde 2026-10-10)

El estado oficial del proyecto vive en el repositorio, no en documentos externos:

1. `AGENTS.md` (este archivo): reglas e invariantes.
2. `docs/STATUS.md`: estado actual, fase activa, decisiones pendientes.
3. `docs/WORKFLOW.md`: cómo trabaja cualquier agente (issues, ramas, PRs, certificación).
4. `docs/phases/phase-XX.md`, `docs/decisions.md`, `docs/issues.md`: detalle por fase, decisiones y hallazgos.

El Libro Maestro en Word (v1.9, 2026-10-07) es **histórico/archivado**; no se actualiza más.
Pasos obligatorios para todo agente, en orden:

5. Ejecutar la suite baseline antes de cambiar nada (comando en `docs/WORKFLOW.md`).
6. Inspeccionar el código existente antes de crear duplicados.
7. Implementar únicamente la fase o tarea autorizada (issue asignado por el owner o por Grok Bot).
8. Ejecutar la suite completa al terminar; no commitear con tests fallando.
9. Actualizar `CHANGELOG_AGENT.md` (además de `docs/STATUS.md` y el archivo de fase si cambia el estado).
10. No hacer `git add`, commit ni push sin autorización del owner. Solo cuando el owner o Grok Bot asignó la tarea, el agente trabaja en una rama de feature y entrega mediante PR; **nunca push directo a `main`**.

Los documentos técnicos de cada fase (`V2_PHASE*.md`, `AUDIT_*.md`, `CHANGELOG_AGENT.md`) siguen siendo evidencia.
El freeze PAPER del 2026-09-19 (`EXPERIMENT_FREEZE.md`) es histórico; el baseline económico vigente lo decide el owner (ver `docs/STATUS.md`).

## Invariantes de seguridad (no negociables)

- PAPER / DEMO únicamente. `REAL_EXECUTION_ENABLED = False` (`runtime/demo_runner.py`). Ningún broker real ni dinero real.
- NAS100 OFF (símbolos habilitados por defecto: XAUUSD, EURUSD). No habilitarlo sin decisión registrada del owner.
- Flags V2 de runtime OFF salvo autorización explícita. Sin deploy ni cambios en Render sin autorización.
- Ningún agente hace merge, deploy, activación runtime ni cambia estrategia/riesgo/economía sin aprobación explícita del owner.
- Nunca commitear secretos; usar `.env` local (ignorado) y `.env.example` solo con nombres.

## Reglas de trabajo

- No modificar archivos fuera del alcance autorizado.
- No introducir dependencias sin autorización.
- Preservar compatibilidad, no-lookahead, train/test, equity mark-to-market, posiciones abiertas y costes cobrados una sola vez.
- Preferir cambios pequeños, deterministas y testeables.
- No ocultar errores de contrato con accesos defensivos que borren la causa raíz.
- No inventar datos ausentes; usar estados explícitos como `NO_DATA`.

## Producto congelado

AI Market System evolucionará hacia un AI Trading Floor personal especializado inicialmente en:

- `XAUUSD`
- `NAS100` / `US100` (OFF; fuera de alcance hasta decisión del owner)
- `EURUSD`

Timeframes:

- `1H`: contexto principal
- `15M`: estructura y setup
- `5M`: ejecución y refinamiento

Sesiones prioritarias: Londres y Nueva York.

Características operativas:

- Intradía y scalping selectivo.
- LONG y SHORT.
- Sin obligación de operar.
- Máximo 1% de riesgo por operación.
- R:R mínimo base 1:3.
- Paper trading únicamente.
- Ningún broker real ni dinero real.

Estados de setup:

- `NO_SETUP`
- `WATCH`
- `VALID_SETUP`

## Separación de responsabilidades

La IA puede interpretar, clasificar, sintetizar y explicar evidencia.

Python determinista controla datos numéricos, risk sizing, SL/TP, R:R, límites, estado de órdenes, PnL, equity y autorización final de riesgo.

Ningún agente puede saltarse el Risk Engine. Los agentes se comunican mediante contratos estructurados y versionados, nunca mediante prosa libre como API interna.

Nunca inventar precios, noticias, timestamps, especificaciones contractuales, valores por punto/tick ni datos ausentes. `NO_DATA` es un estado válido.

## Fases

La fase activa y lo autorizado están en `docs/STATUS.md`. No adelantar fases sin autorización explícita del owner.

## Git en entorno local Windows

Git executable:

`C:\Program Files\Git\cmd\git.exe`

Si `git` no está disponible en PATH, los agentes deben usar el ejecutable anterior mediante ruta absoluta.

La ausencia de Git en PATH no significa que Git no esté instalado. Antes de declarar `MANUAL_PUSH_REQUIRED`, se debe probar la ruta absoluta.

Reglas de seguridad:

- Nunca usar `push --force`.
- Nunca usar `reset --hard`.
- Nunca usar `clean -fd`.
- Nunca reescribir historial.
- Nunca borrar branches.
- Nunca hacer commit con tests fallando.
- Revisar `git diff --check` antes del commit.
- Añadir solamente archivos pertenecientes al trabajo autorizado.
