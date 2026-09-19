# Certificación de Fase 7

Desde la raíz del repositorio, con dependencias instaladas y credenciales en el entorno o `.env.local` ignorado:

```powershell
python -m runtime.certify_phase7 --env-file .env.local
```

El comando ejecuta primero `unittest discover -q`, luego preflight local y una sola pasada controlada por XAUUSD y EURUSD con Twelve Data y OpenAI. El runner usa una base SQLite diagnóstica ignorada, `dry_run=True`, scheduler deshabilitado y ninguna orden PAPER. Se admiten diagnósticos fuera de sesión, pero las barras conservan los umbrales existentes de 5m 600 s, 15m 1800 s y 1h 7200 s. Se informa timestamp UTC de última barra, edad, salud por activo, estados de agentes, `run_id`, persistencia de revisión/journal y número de órdenes. OpenAI no cambia de modelo ni reintenta 429 en este diagnóstico. Twelve Data conserva reintento acotado para 429/5xx.

PASS exige preflight READY; ambos símbolos con datos actuales; OpenAI `READY` con usage positivo y respuestas `OK`/`PARTIAL` de Structure, Liquidity y Setup Reviewer. Macro puede ser `NO_DATA` cuando no hay fuente de noticias, y Trade Reviewer se omite cuando no existe un plan. También exige ciclos y revisiones durables, journal poblado y cero órdenes. `NO_SETUP` o `WATCH` son resultados válidos si las revisiones aplicables funcionaron. Datos realmente stale, fallos de proveedor/IA o una base no escribible dan FAIL. La suite offline usa fixtures sintéticas de setup; el mercado live no tiene que producir una señal.

Además del comando, antes del cierre se ejecutan `git diff --check`, escaneo de secretos y comprobación de `.env.local` ignorado/no staged. Solo un PASS completo autoriza el commit de Fase 7 y push. El experimento de 14 días y el scheduler continuo permanecen sin iniciar.
