# Auditoría final PAPER — 2026-09-19 UTC

## Alcance y estado

Base auditada: `f9ae6ce79deb9baaa0dab0faa0271e462599d843`.
La suite original pasa 489 tests, pero se reprodujeron defectos no cubiertos por
esa suite. Se aplicaron únicamente correcciones técnicas y las especificaciones
internas PAPER aprobadas por el usuario. Suite final: **507 tests OK**, incluidos
18 tests nuevos de seguridad e incrementos económicos. No se activó el experimento.

**Auditoría completada para freeze de código.** El usuario confirmó el modelo
económico completo de `PAPER_EXECUTION_SPEC.md` el 2026-09-19. La identificación
oficial e inmutable del baseline está en `EXPERIMENT_FREEZE.md`. El freeze no
autoriza despliegue, activación ni inicio del contador de 14 días.

## Hallazgos reproducidos y correcciones

| Hallazgo en la base | Evidencia | Corrección y prueba |
| --- | --- | --- |
| Frescura comprobada solo contra el slot | Con contratos sintéticos válidos, reloj 14 minutos posterior al slot y barra de edad real 840s se creó una orden PAPER | Se conserva el corte de evidencia por slot y se valida también el reloj real, antes de operar y después de IA. Tests 600s permitido/601s bloqueado, latency de IA y pending fill |
| Sesión comprobada solo contra slot | Un slot histórico podía estar abierto aunque el reloj actual ya no lo estuviera | Comprobación adicional de sesión actual al entrar y después de IA; bypass de diagnóstico sigue siendo únicamente sin órdenes |
| Cloud no carga contratos | `instruments={}` provoca rechazo `invalid_input`, incluso con VALID_SETUP | Cloud recibe contratos internos aprobados para XAUUSD/EURUSD; sizing redondeado hacia abajo sin cambiar Risk Engine |
| Reinicio rápido conserva symbol lock | Recover a +30s no recoge el run; el siguiente slot sigue bloqueado a +15m | Solo después de adquirir `flock` exclusivo cloud se recuperan todos los runs interrumpidos, incluidos los recientes. Otros usos conservan umbral 120s |
| FXMacroData omitido de awareness | Review conserva evento HIGH pero journal/Slack/DAILY_SUMMARY tienen 0 | Se añade el modo fxmacrodata a la ruta existente de persistencia/notificación macro; provider intacto |
| Summary incluye skips como ciclos | Runtime retorna SESSION_SKIPPED pero persiste final_status NO_DATA; el filtro anterior falla | Summary excluye también los runs con journal SESSION_SKIPPED; test usa un skip real del runtime |

Las reproducciones previas se ejecutaron con proveedores falsos, SQLite aislada y
scouts de test. Nunca usaron el runner de producción ni generaron órdenes reales.

## Matriz de controles

| Control | Resultado / evidencia |
| --- | --- |
| PAPER only / real execution | `REAL_EXECUTION_ENABLED=False`, únicamente PaperBroker; sin integración de broker real |
| Símbolos | XAUUSD, EURUSD; NAS100 deshabilitado |
| Sesiones | LONDON y NEW_YORK, lunes-viernes 08:00 <= hora local < 17:00 con ZoneInfo/DST; tests de ambas estaciones |
| Cadencia | 15 minutos; idempotencia por símbolo/slot |
| Mercado / macro / IA | Twelve Data / FXMacroData / OpenAI; modelo gpt-5.6-terra; adapters y prompts sin modificar |
| Frescura | 5m=600s, 15m=1800s, 1h=7200s; no se relajan umbrales; se corrige el reloj contra el que se aplican |
| No-lookahead | Se mantiene rechazo de barras futuras/formándose y corte por as_of; tests de vintage/fetch y grounding IA pasan |
| Risk y RR | Configuración existente <=1% previsto y RR mínimo 3; control post-fill intacto. Redondeo aprobado solo reduce cantidad/riesgo |
| Persistencia | SQLite schema 3, transacciones; reinicio de cuenta/órdenes/posiciones, journal y revisiones cubiertos por suite |
| Autoridad única | Una instancia y flock no bloqueante antes de construir runner; test asegura que lock ocupado no crea runtime. No se ejecutó scheduler live |
| Slack | Transporte falso probado: éxito, timeout, conexión, HTTP 429/5xx; persistencia antes de intento; at-most-once durable |
| DAILY_SUMMARY | Día UTC anterior completo, métricas de estado etiquetadas como snapshot actual; null/0 explícitos; concurrencia/reinicio/deduplicación cubiertos |
| Estado del experimento | Sin inicio ni activación; no se cambian variables de Render ni SQLite de producción |

## Evidencia efectiva de Render (solo lectura)

Servicio `srv-dama143m8hqs73d2dc00`, instancia observada `lpdw5`.
Commit desplegado: `f3af0c38888dc32c309a5810a44773f1dce9f30f`.
No es el commit f9ae6ce ni contiene las correcciones de esta auditoría.

Se leyeron configuración, procesos y SQLite mediante Web Shell, usando conexión
SQLite `mode=ro` y sin imprimir credenciales:

- XAUUSD/EURUSD; LONDON/NEW_YORK; cadencia 15m.
- market=twelve_data; macro=fxmacrodata; ai=openai; model=gpt-5.6-terra.
- Frescura efectiva: 1h 7200s, 15m 1800s, 5m 600s.
- AI_FLOOR_CLOUD_RUNNER=0; scheduler_enabled=false; instance_count=1.
- REAL_EXECUTION_ENABLED=false; procesos runner/scheduler ausentes.
- Disco montado; SQLite `/opt/render/project/src/data/runtime/trading_floor.db`;
  `PRAGMA quick_check=ok`.
- paper_orders=0; paper_positions=0; closed_trades=0;
  experiment_started ausente/null; scheduler_state ausente/null.

Los PASS live previos de FXMacroData/OpenAI/Slack y el estado Twelve Data READY con
STALE_MARKET_DATA provienen de la certificación anterior suministrada por el usuario
y de la tarea `Configura OPENAI_API_KEY segura`. Esta auditoría no vuelve a enviar
mensajes Slack ni consume otra certificación IA. No convierte stale en PASS.

El Blueprint versionado mantiene macro=none; Render tiene un override efectivo
fxmacrodata. No se alteró ese override ni el Blueprint. Antes de armar, debe
desplegarse el baseline exacto y comprobar otra vez la configuración, conservando
runner/scheduler=0 hasta la autorización de activación.

## Riesgos residuales reales

- Mercado cerrado: falta comprobar barras frescas en la reapertura; no se puede
  armar con evidencia stale ni aumentar 600s.
- Render aún ejecuta el commit anterior. El freeze de código
  no equivaldrá a despliegue ni activación.
- Slack ofrece at-most-once: timeout o crash tras reclamar un intento puede dejar
  mensaje sin entregar; el journal permanece como fuente de verdad.
- El modelo PAPER no garantiza fills reales, liquidez o pérdidas limitadas al 1%
  ante gaps. Costes explícitos 0 aprobados: no representa rentabilidad neta real.
- La supervisión/progreso de posiciones depende de los ciclos disponibles; no hay
  ejecución ni gestión real entre ciclos ni durante interrupciones del servicio.

## Verificación de alcance

Sin cambios en estrategia, agentes, prompts, archivos del Risk Engine, adapters
de providers, reglas de setup/entrada/salida, umbrales de frescura ni ventanas de
sesión. Cambian gates temporales del runtime para hacer cumplir esos mismos
límites; recuperación bajo lock; supervisión y adaptación PAPER de cantidad
expresamente aprobada. No hubo despliegue ni activación.

`git diff --check`: PASS. Secret scan por patrones de claves, tokens, webhook
Slack y claves privadas: sin hallazgos. No se versionan credenciales ni bases SQLite.
