"""Historial (EURUSD reference baseline): strict schema v1 loader, build_summary math, offline CLI and the separate,
money-free Control card. Synthetic data only; no network."""
import copy
import io
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from execution import historial
from scripts import historial_build
from ui.views import control

ROOT = Path(__file__).resolve().parent
VALID = {"version": 1, "source": "paper", "symbol": "EURUSD", "generated_at": "2026-01-02T03:04:05+00:00",
         "period": {"from": "2025-01-01", "to": "2025-12-31"}, "trades": 4, "wins": 2, "losses": 2, "win_rate": 0.5,
         "expectancy_r": 0.5, "profit_factor": 2.0, "max_drawdown_r": 1.0, "avg_r_win": 2.0, "avg_r_loss": -1.0,
         "notes": "sintético"}
# Deterministic set: R = +2, -1, -1, +2 (closed in this order) -> cumulative 2, 1, 0, 2 -> max drawdown 2.
ROWS = [
    {"symbol": "EURUSD", "side": "BUY", "entry": "1.1000", "stop": "1.0990", "exit": "1.1020",
     "opened_at": "2025-03-01T08:00:00Z", "closed_at": "2025-03-01T10:00:00Z", "pnl": "20"},
    {"symbol": "EUR/USD", "side": "SELL", "entry": "1.1000", "stop": "1.1010", "exit": "1.1010",
     "opened_at": "2025-03-02T08:00:00Z", "closed_at": "2025-03-02T10:00:00Z", "pnl": "-10"},
    {"symbol": "EURUSD", "side": "BUY", "entry": "1", "stop": "0.9", "exit": "1", "pnl": "-5", "initial_risk": "5",
     "opened_at": "2025-03-03T08:00:00Z", "closed_at": "2025-03-03T10:00:00Z"},
    {"symbol": "EURUSD", "side": "BUY", "r_multiple": "2", "opened_at": "2025-03-04T08:00:00Z",
     "closed_at": "2025-03-04T10:00:00Z"},
    {"symbol": "XAUUSD", "side": "BUY", "r_multiple": "50", "opened_at": "2025-03-01T08:00:00Z",
     "closed_at": "2025-03-01T09:00:00Z"},
]


def no_network():
    return mock.patch.object(socket, "socket", side_effect=AssertionError("network used"))


class HistorialSchemaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        patcher = no_network()
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, data, name="paper_closed_trades_eurusd.json"):
        path = self.dir / name
        path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
        return path

    def paper(self):
        return historial.load_baselines(self.dir)[1]

    def test_fixed_order_and_missing_is_no_data(self):
        records = historial.load_baselines(self.dir)
        self.assertEqual([r.source for r in records], ["v1_replay", "paper"])
        self.assertEqual([r.filename for r in records], ["v1_replay_eurusd.json", "paper_closed_trades_eurusd.json"])
        self.assertTrue(all(r.status == historial.NO_DATA and r.stats is None for r in records))

    def test_valid_file_accepted(self):
        self.write(VALID)
        record = self.paper()
        self.assertEqual(record.status, historial.OK)
        self.assertEqual(record.stats, VALID)
        self.assertEqual(historial.load_baselines(self.dir)[0].status, historial.NO_DATA)

    def test_env_override(self):
        self.write(VALID)
        with mock.patch.dict(os.environ, {historial.ENV_DIR: str(self.dir)}):
            self.assertEqual(historial.load_baselines()[1].status, historial.OK)

    def test_reject_matrix(self):
        def changed(**kw):
            data = copy.deepcopy(VALID)
            data.update(kw)
            return data
        extra = changed(extra=1)
        missing = copy.deepcopy(VALID)
        del missing["notes"]
        cases = {
            "unknown key": extra, "missing key": missing, "bool trades": changed(trades=True),
            "bool wins": changed(wins=False), "bool version": changed(version=True), "version 2": changed(version=2),
            "float trades": changed(trades=4.0), "negative trades": changed(trades=-1),
            "wins+losses > trades": changed(wins=3), "wrong symbol": changed(symbol="XAUUSD"),
            "lower symbol": changed(symbol="eurusd"), "wrong source": changed(source="v1_replay"),
            "win_rate > 1": changed(win_rate=1.5), "win_rate inconsistent": changed(win_rate=0.9),
            "negative drawdown": changed(max_drawdown_r=-0.1), "string number": changed(expectancy_r="0.5"),
            "bool expectancy": changed(expectancy_r=True), "bad generated_at": changed(generated_at="ayer"),
            "bad period date": changed(period={"from": "2025-13-01", "to": "2025-12-31"}),
            "period reversed": changed(period={"from": "2026-01-01", "to": "2025-12-31"}),
            "period extra key": changed(period={"from": "2025-01-01", "to": "2025-12-31", "x": 1}),
            "datetime as date": changed(period={"from": "2025-01-01T00:00:00", "to": "2025-12-31"}),
            "long notes": changed(notes="x" * 201), "notes not str": changed(notes=None),
            "negative profit factor": changed(profit_factor=-1.0), "not object": [VALID],
        }
        for name, data in cases.items():
            with self.subTest(name):
                self.write(data)
                self.assertEqual(self.paper().status, historial.NO_DATA)

    def test_non_finite_and_duplicates_rejected(self):
        text = json.dumps(VALID)
        for name, bad in {"nan": text.replace('"expectancy_r": 0.5', '"expectancy_r": NaN'),
                          "inf": text.replace('"max_drawdown_r": 1.0', '"max_drawdown_r": Infinity'),
                          "-inf": text.replace('"avg_r_loss": -1.0', '"avg_r_loss": -Infinity'),
                          "huge": text.replace('"expectancy_r": 0.5', '"expectancy_r": 1e999'),
                          "duplicate": text.replace('"notes"', '"wins": 2, "notes"'),
                          "not json": "{"}.items():
            with self.subTest(name):
                self.assertNotEqual(bad, text)
                self.write(bad)
                self.assertEqual(self.paper().status, historial.NO_DATA)
        (self.dir / "paper_closed_trades_eurusd.json").write_bytes(b"\xff\xfe{}")  # not UTF-8
        self.assertEqual(self.paper().status, historial.NO_DATA)

    def test_oversize_rejected(self):
        self.write(json.dumps(VALID) + " " * historial.MAX_BYTES)
        self.assertEqual(self.paper().reason, "oversize")

    @unittest.skipUnless(hasattr(os, "symlink") and os.name == "posix", "POSIX symlinks")
    def test_symlink_and_directory_rejected(self):
        real = self.dir / "real.json"
        real.write_text(json.dumps(VALID), encoding="utf-8")
        os.symlink(real, self.dir / "paper_closed_trades_eurusd.json")
        self.assertEqual(self.paper().status, historial.NO_DATA)
        os.symlink(self.dir / "nowhere.json", self.dir / "v1_replay_eurusd.json")  # dangling
        self.assertEqual(historial.load_baselines(self.dir)[0].status, historial.NO_DATA)
        os.unlink(self.dir / "paper_closed_trades_eurusd.json")
        (self.dir / "paper_closed_trades_eurusd.json").mkdir()
        self.assertEqual(self.paper().status, historial.NO_DATA)

    def test_never_raises(self):
        with mock.patch.object(historial, "load_one", side_effect=RuntimeError("boom")):
            self.assertEqual([r.status for r in historial.load_baselines(self.dir)], [historial.NO_DATA] * 2)
        with mock.patch.object(historial, "_read_regular_file", side_effect=PermissionError("x")):
            self.write(VALID)
            self.assertEqual(self.paper().status, historial.NO_DATA)


class BuildSummaryTests(unittest.TestCase):
    NOW = historial.datetime(2026, 1, 2, 3, 4, 5, tzinfo=historial.timezone.utc)

    def test_math_on_deterministic_set(self):
        rows = [ROWS[0], ROWS[1], ROWS[2], ROWS[3], ROWS[4]]
        summary = historial.build_summary(rows, "paper", now=self.NOW)
        self.assertEqual(summary["trades"], 4)  # XAUUSD row ignored
        self.assertEqual((summary["wins"], summary["losses"], summary["win_rate"]), (2, 2, 0.5))
        self.assertAlmostEqual(summary["expectancy_r"], 0.5)
        self.assertAlmostEqual(summary["profit_factor"], 2.0)
        self.assertAlmostEqual(summary["max_drawdown_r"], 2.0)
        self.assertAlmostEqual(summary["avg_r_win"], 2.0)
        self.assertAlmostEqual(summary["avg_r_loss"], -1.0)
        self.assertEqual(summary["period"], {"from": "2025-03-01", "to": "2025-03-04"})
        self.assertEqual(historial.validate_baseline(summary, "paper"), summary)

    def test_order_is_by_close_time(self):
        shuffled = [ROWS[3], ROWS[1], ROWS[0], ROWS[2]]
        self.assertAlmostEqual(historial.build_summary(shuffled, "paper", now=self.NOW)["max_drawdown_r"], 2.0)

    def test_r_sources(self):
        self.assertAlmostEqual(historial.row_r(ROWS[0]), 2.0)  # prices, BUY
        self.assertAlmostEqual(historial.row_r(ROWS[1]), -1.0)  # prices, SELL
        self.assertAlmostEqual(historial.row_r(ROWS[2]), -1.0)  # pnl / initial_risk wins over prices
        self.assertAlmostEqual(historial.row_r({"r_multiple": "-0.5", "pnl": "999"}), -0.5)

    def test_no_losses_and_empty(self):
        wins = historial.build_summary([ROWS[0], ROWS[3]], "v1_replay", now=self.NOW)
        self.assertIsNone(wins["profit_factor"])
        self.assertIsNone(wins["avg_r_loss"])
        self.assertEqual(wins["max_drawdown_r"], 0)
        empty = historial.build_summary([], "v1_replay", now=self.NOW)
        self.assertEqual((empty["trades"], empty["win_rate"], empty["expectancy_r"]), (0, 0.0, 0.0))
        self.assertEqual(historial.validate_baseline(empty, "v1_replay"), empty)

    def test_invalid_rows_name_index_only(self):
        bad = {"symbol": "EURUSD", "side": "BUY", "entry": "1.1", "stop": "1.1", "exit": "1.2",
               "closed_at": "2025-01-01T00:00:00Z", "secret_marker": "ZZZ"}
        for row in (bad, {**bad, "stop": "1.0", "closed_at": ""}, {**bad, "r_multiple": "nan"},
                    {**bad, "stop": "1.0", "opened_at": "2025-02-01T00:00:00Z"}):
            with self.subTest(row=row), self.assertRaises(ValueError) as ctx:
                historial.build_summary([ROWS[0], row], "paper", now=self.NOW)
            self.assertTrue(str(ctx.exception).startswith("row 2:"))
            self.assertNotIn("ZZZ", str(ctx.exception))
        with self.assertRaises(ValueError):
            historial.build_summary([], "real")


class BuildCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.input = self.dir / "trades.csv"
        keys = ["symbol", "side", "entry", "stop", "exit", "opened_at", "closed_at", "pnl", "r_multiple",
                "initial_risk"]
        lines = [",".join(keys)] + [",".join(str(row.get(k, "")) for k in keys) for row in ROWS]
        self.input.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.out = self.dir / "out" / "paper_closed_trades_eurusd.json"

    def run_cli(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        with no_network():
            code = historial_build.main(["--input", str(self.input), "--source", "paper", "--out", str(self.out),
                                         *extra], out=out, err=err)
        return code, out.getvalue(), err.getvalue()

    def test_writes_0600_valid_file_and_prints_only_aggregates(self):
        code, out, err = self.run_cli("--notes", "prueba")
        self.assertEqual(code, 0, err)
        data = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual(historial.validate_baseline(data, "paper")["trades"], 4)
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(self.out.stat().st_mode), 0o600)
        for row_value in ("1.1020", "2025-03-01T08:00:00Z", "XAUUSD", "BUY", "SELL", "initial_risk"):
            self.assertNotIn(row_value, out)
        self.assertEqual(historial.load_baselines(self.out.parent)[1].stats, data)
        self.assertEqual([p.name for p in self.out.parent.iterdir()], [self.out.name])  # no temp left behind

    def test_refuses_overwrite_without_force(self):
        self.assertEqual(self.run_cli()[0], 0)
        self.out.write_text("previo", encoding="utf-8")
        code, _, err = self.run_cli()
        self.assertEqual(code, 3)
        self.assertIn("--force", err)
        self.assertEqual(self.out.read_text(encoding="utf-8"), "previo")
        self.assertEqual(self.run_cli("--force")[0], 0)
        self.assertEqual(json.loads(self.out.read_text(encoding="utf-8"))["trades"], 4)
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(self.out.stat().st_mode), 0o600)

    def test_json_input_and_bad_row_never_printed(self):
        self.input = self.dir / "trades.json"
        bad = {**ROWS[0], "stop": ROWS[0]["entry"], "secret_marker": "ZZZ-ROW"}
        self.input.write_text(json.dumps({"trades": [ROWS[0], bad]}), encoding="utf-8")
        code, out, err = self.run_cli()
        self.assertEqual(code, 2)
        self.assertIn("row 2", err)
        self.assertNotIn("ZZZ", out + err)
        self.assertFalse(self.out.exists())

    @unittest.skipUnless(hasattr(os, "symlink") and os.name == "posix", "POSIX symlinks")
    def test_refuses_symlink_out(self):
        target = self.dir / "elsewhere.json"
        target.write_text("x", encoding="utf-8")
        self.out.parent.mkdir()
        os.symlink(target, self.out)
        self.assertEqual(self.run_cli("--force")[0], 2)
        self.assertEqual(target.read_text(encoding="utf-8"), "x")

    def test_runs_as_script(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts" / "historial_build.py"), "--input",
                                 str(self.input), "--source", "paper", "--out", str(self.out)],
                                capture_output=True, text=True, cwd=self.dir, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"expectancy_r": 0.5', result.stdout)


class FakeSt:
    def __init__(self):
        self.out = []

    def markdown(self, text, unsafe_allow_html=False):
        self.out.append(text)

    def text(self):
        return "".join(self.out)


class HistoryCardTests(unittest.TestCase):
    def render(self, records=None, error=None):
        st = FakeSt()
        kwargs = {"side_effect": error} if error else {"return_value": records}
        with no_network(), mock.patch.object(historial, "load_baselines", **kwargs):
            control.render_history(st)
        return st.text()

    def test_sources_mirror_module(self):
        self.assertEqual(control.HISTORY_SOURCES, historial.SOURCES)

    def test_separate_cards_and_sin_datos(self):
        records = [historial.Baseline("v1_replay", "v1_replay_eurusd.json", "Replay estrategia V1 (velas Twelve Data)",
                                      historial.NO_DATA, reason="missing"),
                   historial.Baseline("paper", "paper_closed_trades_eurusd.json", "Trades cerrados PAPER (agregado)",
                                      historial.OK, stats=VALID)]
        text = self.render(records)
        self.assertIn("📚 Historial (referencia) · no son trades de OANDA", text)
        self.assertEqual(text.count("Referencia · no son trades de OANDA"), 2)
        self.assertIn("separadas de las estadísticas de la demo en vivo de OANDA", text)
        self.assertIn("sin datos todavía (se cargará con la exportación)", text)
        self.assertIn("data/historial/v1_replay_eurusd.json", text)
        self.assertNotIn("data/historial/paper_closed_trades_eurusd.json", text)
        for expected in ("+0.50 R", "1.00 R", "2.00", "50.0 %", "+2.00 R / -1.00 R", "2025-01-01 → 2025-12-31",
                         "sintético"):
            self.assertIn(expected, text)
        self.assertLess(text.index("Replay estrategia V1"), text.index("Trades cerrados PAPER"))
        self.assertEqual(text.count('<div class="card">'), 3)

    def test_money_free_and_no_oanda_trade_rows(self):
        text = self.render([historial.Baseline(s, f, label, historial.OK, stats={**VALID, "source": s})
                            for s, f, label in historial.SOURCES])
        for forbidden in ("Balance", "NAV", "USD ", "$", "trade_id", "unidades", "Operación abierta",
                          "Cuenta practice", "pnl"):
            self.assertNotIn(forbidden, text)

    def test_import_or_read_failure_means_sin_datos(self):
        for text in (self.render(error=RuntimeError("boom")), self.render(records="garbage")):
            self.assertEqual(text.count("sin datos todavía (se cargará con la exportación)"), 2)
            self.assertIn("data/historial/paper_closed_trades_eurusd.json", text)
        with mock.patch.dict(sys.modules, {"execution.historial": None}):
            st = FakeSt()
            control.render_history(st)
            self.assertEqual(st.text().count("sin datos todavía"), 2)

    def test_html_escaped(self):
        stats = {**VALID, "notes": "<script>x</script>"}
        text = self.render([historial.Baseline("paper", "paper_closed_trades_eurusd.json", "<b>", historial.OK,
                                               stats=stats)])
        self.assertNotIn("<script>", text)
        self.assertNotIn("<h4><b>", text)


class HistorialHygieneTests(unittest.TestCase):
    def test_data_dir_ignored_except_readme(self):
        lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("data/historial/*", lines)
        self.assertIn("!data/historial/README.md", lines)
        self.assertTrue((ROOT / "data" / "historial" / "README.md").is_file())

    def test_stdlib_only_imports(self):
        import ast
        allowed = {"argparse", "csv", "dataclasses", "datetime", "io", "json", "math", "os", "pathlib", "stat", "sys",
                   "tempfile", "execution", "execution.historial"}
        for rel in ("execution/historial.py", "scripts/historial_build.py"):
            tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
            names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
            names |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
            with self.subTest(rel):
                self.assertLessEqual(names, allowed)

    def test_ruff_clean(self):
        ruff = Path(sys.executable).with_name("ruff")
        if not ruff.exists():
            self.skipTest("ruff not installed in this venv")
        files = ["execution/historial.py", "scripts/historial_build.py", "ui/views/control.py", "test_historial.py",
                 "test_broker_wiring.py"]
        result = subprocess.run([str(ruff), "check", *files], cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
