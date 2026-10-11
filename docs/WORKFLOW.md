# WORKFLOW — cómo trabaja cualquier agente (Codex, Copilot, Claude Code, Grok Bot)

## 1. Antes de empezar
1. Leer `AGENTS.md` y `docs/STATUS.md`. Leer solo el `docs/phases/phase-XX.md` y los archivos citados en el issue.
2. No pedir ni pegar contexto largo en el chat: todo está en el repo.

## 2. Una tarea = un GitHub issue
- Título corto: `[F08] <tarea>`; cuerpo: objetivo, criterios de aceptación, archivos relevantes (rutas), decisión/ID que lo autoriza.
- El issue referencia archivos; no copia su contenido.

## 3. Ramas
- Fases: `v2/phaseN-<tema>`; subpasos: `v2/phaseN-<paso>` (p.ej. `v2/phase8-p4b`).
- Documentación: `docs/<tema>`. Fixes: `fix/<tema>`.
- Nunca `push --force`, `reset --hard`, reescribir historial ni borrar ramas.

## 4. Tests (igual que CI)
```
PYTHONDONTWRITEBYTECODE=1 AI_FLOOR_MARKET_PROVIDER=none AI_FLOOR_AI_PROVIDER=deterministic \
  python -m unittest discover -q
```
Python 3.13. Requiere `ssh-keygen` (openssh-client) para tests de Fase 8. No commitear con tests fallando.

## 5. Pull requests
- Todo cambio entra por PR a `main` (main protegido: PR + check `Tests / unit`).
- El PR **debe** actualizar `docs/STATUS.md` y el `docs/phases/phase-XX.md` afectado (y `decisions.md` / `issues.md` si aplica).
- Cuerpo del PR: alcance, issue(s), resultado de tests, riesgos, decisiones del owner requeridas.
- PRs no certificados se abren como **draft**.

## 6. Regla de certificación
- IMPLEMENTED ≠ ACCEPTED ≠ CERTIFIED ≠ MERGED ≠ ACTIVATED ≠ DEPLOYED.
- Una fase se certifica solo con: tareas aplicables cerradas con evidencia, tests y CI en verde, **revisión independiente** (agente distinto del autor) PASS sin hallazgos CRITICAL/HIGH abiertos, y aprobación explícita del owner.
- Todo N/A requiere razón y decisión registrada.

## 7. Aprobaciones del owner (siempre explícitas)
Merge, deploy, cambios en Render, activación de flags/runtime, cambios de estrategia/riesgo/economía, nuevo baseline, habilitar símbolos, cualquier acción irreversible.

## 8. Ahorro de tokens
- Issues cortos con rutas y IDs, no texto pegado.
- Citar `archivo:línea` o SHA en vez de reproducir código.
- Un cambio de estado se escribe **una vez** (en STATUS + archivo de fase), no en cinco sitios.
- Los SHAs/CI se enlazan desde GitHub, no se transcriben a mano.
