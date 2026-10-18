"""Offline: build the aggregated EURUSD "Historial" JSON (schema v1, execution/historial.py) from an export of closed
trades. No network, no broker, no secrets. Prints ONLY the aggregates, never a row.

    python scripts/historial_build.py --input trades.csv --source paper \\
        --out data/historial/paper_closed_trades_eurusd.json [--force] [--notes "texto corto"]

Input: CSV with a header, or JSON (a list of objects or {"trades": [...]}). Columns/keys: symbol, side, entry, stop,
exit, opened_at, closed_at, pnl, r_multiple (optional), initial_risk (optional, money). Only EURUSD rows are used.
R = r_multiple when present, else pnl / initial_risk, else from prices ((exit - entry) / (entry - stop), mirrored for
SELL). The output is written 0600 and is never overwritten without --force; an --out path with ANY symlink component
is refused. Error messages name only a row number and a field, never a value.
"""
import argparse
import csv
import errno
import io
import json
import os
from pathlib import Path
import sys
import tempfile

if __package__ in (None, ""):  # `python scripts/historial_build.py`: make the repository importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from execution.historial import (  # noqa: E402
    FILENAMES,
    MAX_NOTES,
    SYMLINK_PATH,
    RowError,
    SchemaError,
    build_summary,
    has_symlink_component,
    parse_json,
    validate_baseline,
)

MAX_INPUT_BYTES = 50 * 1024 * 1024
FILE_MODE = 0o600


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Construye el JSON agregado de Historial (EURUSD, solo R y conteos).")
    parser.add_argument("--input", required=True, help="exportación CSV/JSON de trades cerrados")
    parser.add_argument("--source", required=True, choices=sorted(FILENAMES), help="paper | v1_replay")
    parser.add_argument("--out", required=True, help=f"p. ej. data/historial/{FILENAMES['paper']}")
    parser.add_argument("--force", action="store_true", help="sobrescribir --out si ya existe")
    parser.add_argument("--notes", default="", help=f"nota corta (<= {MAX_NOTES} caracteres)")
    return parser.parse_args(argv)


def read_rows(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise SchemaError("input: no es un archivo regular")
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise SchemaError("input: demasiado grande")
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".json":
        data = parse_json(text)
        rows = data.get("trades") if isinstance(data, dict) else data
        if not isinstance(rows, list):
            raise SchemaError("input: JSON sin lista de trades")
        return rows
    return list(csv.DictReader(io.StringIO(text)))


def write_private(out, payload, force):
    """Write ``payload`` to ``out`` with mode 0600. Refuses an existing file unless ``force``; never follows symlinks."""
    out = Path(os.path.abspath(out))
    if has_symlink_component(out):
        raise SchemaError(f"out: {SYMLINK_PATH}")
    if out.exists() and not force:
        raise FileExistsError("out: ya existe (usa --force para sobrescribir)")
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".historial-", dir=out.parent)  # mkstemp creates it 0600
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.chmod(tmp, FILE_MODE)
        if force:
            os.replace(tmp, out)
        else:
            os.link(tmp, out)  # atomic "create only if absent"
            os.unlink(tmp)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return out


def main(argv=None, out=sys.stdout, err=sys.stderr):
    args = parse_args(argv)
    if len(args.notes) > MAX_NOTES:
        print(f"error: --notes supera {MAX_NOTES} caracteres", file=err)
        return 2
    try:
        summary = validate_baseline(build_summary(read_rows(args.input), args.source, notes=args.notes), args.source)
        payload = json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        target = write_private(args.out, payload, args.force)
    except FileExistsError:
        print("error: out: ya existe (usa --force para sobrescribir)", file=err)
        return 3
    except (RowError, SchemaError) as exc:  # fixed messages: row NUMBER and field name only, never row content
        print(f"error: {exc}", file=err)
        return 2
    except OSError as exc:  # never echo the OS message (it may quote paths/content); errno name only
        print(f"error: E/S ({errno.errorcode.get(exc.errno, 'desconocido')})", file=err)
        return 2
    except (ValueError, csv.Error):  # e.g. not UTF-8 / not JSON: the decoder's message may quote input bytes
        print("error: input: formato no válido", file=err)
        return 2
    if Path(args.out).name != FILENAMES[args.source]:
        print(f"aviso: el panel espera el nombre {FILENAMES[args.source]}", file=err)
    print(f"escrito: {target}", file=out)
    print(json.dumps(summary, ensure_ascii=False, indent=2), file=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
