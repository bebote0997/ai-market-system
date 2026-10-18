# Historial (referencia / baseline) · EURUSD

Estadísticas de referencia de sistemas **anteriores**. **No son trades de OANDA** y nunca se mezclan con las
estadísticas de la demo en vivo: el panel de Control las muestra en tarjetas separadas con la etiqueta
"Referencia · no son trades de OANDA".

## Archivos esperados (opcionales)

| Fuente | Archivo | Origen |
|---|---|---|
| `v1_replay` | `v1_replay_eurusd.json` | Replay de la estrategia V1 con velas de Twelve Data |
| `paper` | `paper_closed_trades_eurusd.json` | Exportación de trades cerrados PAPER (solo agregados) |

Si un archivo falta o no es válido, esa fuente aparece como "sin datos todavía (se cargará con la exportación)".

**Los archivos JSON son locales o del disco de Render; no se suben al repositorio público** (`.gitignore` ignora todo
este directorio excepto este README) salvo aprobación explícita del owner. La ruta se puede cambiar con la variable
de entorno `AI_FLOOR_HISTORIAL_DIR`.

## Esquema (versión 1)

Conjunto exacto de claves (sin claves extra), solo conteos y múltiplos de R, sin importes de dinero:

```json
{
  "version": 1,
  "source": "paper",
  "symbol": "EURUSD",
  "generated_at": "2026-10-17T12:00:00+00:00",
  "period": {"from": "2026-01-01", "to": "2026-09-30"},
  "trades": 0,
  "wins": 0,
  "losses": 0,
  "win_rate": 0.0,
  "expectancy_r": 0.0,
  "profit_factor": null,
  "max_drawdown_r": 0.0,
  "avg_r_win": null,
  "avg_r_loss": null,
  "notes": ""
}
```

(Ejemplo con ceros: no son datos reales.)

Reglas: `source` = `v1_replay` | `paper` (debe coincidir con el archivo); `symbol` solo `EURUSD`; fechas ISO;
`period.from <= period.to`; `trades`, `wins`, `losses` enteros >= 0 con `wins + losses <= trades`; `win_rate` entre
0 y 1 y coherente con `wins / trades`; `max_drawdown_r >= 0`; `profit_factor`, `avg_r_win`, `avg_r_loss` número o
`null`; `notes` texto de hasta 200 caracteres. Se rechazan NaN/Infinity, booleanos usados como enteros, claves
duplicadas, archivos de más de 64 KB y enlaces simbólicos.

## Cómo construirlo

```bash
python scripts/historial_build.py --input trades_cerrados.csv --source paper \
    --out data/historial/paper_closed_trades_eurusd.json
```

- Entrada CSV (con cabecera) o JSON (lista de objetos o `{"trades": [...]}`) con: `symbol`, `side`, `entry`, `stop`,
  `exit`, `opened_at`, `closed_at`, `pnl`, `r_multiple` (opcional), `initial_risk` (opcional, en dinero).
- Solo se usan filas `EURUSD`. R = `r_multiple` si existe; si no, `pnl / initial_risk`; si no, desde precios
  (`(exit - entry) / (entry - stop)`, invertido para SELL).
- Métricas: trades, ganadas, perdidas, tasa de acierto, expectativa (R medio), factor de beneficio, máximo drawdown
  (pico a valle del R acumulado, en orden de cierre) y R medio ganador/perdedor.
- No sobrescribe sin `--force`; escribe con permisos `0600`; solo imprime los agregados, nunca filas.
