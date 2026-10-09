"""V2 Phase 8 / P1-B (DEC-8.13, DEC-8.16): per-symbol catch-up scope configuration (P8.6 design 1.1-1.2, matrix C/F).

Configuration only; no runtime is activated. Scope values here are test scenarios, never operational flags.
"""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from runtime.config import CATCH_UP_SYMBOLS_ENV, RuntimeConfig, parse_catch_up_symbols

FLAG = "AI_FLOOR_V2_POSITION_CATCH_UP"
LEGACY_FINGERPRINT = "16b257235a26f293cedf46c74fff8f3231f41467f08c504e8c714aff29d0b430"  # f0a60c1, both flags absent


def env(**values):
    """Environment with every catch-up key removed, then ``values`` set (an absent key stays absent)."""
    base = {k: v for k, v in __import__("os").environ.items()
            if k not in (FLAG, CATCH_UP_SYMBOLS_ENV, "AI_FLOOR_MARKET_EVIDENCE_PATH", "AI_FLOOR_ENABLED_SYMBOLS")}
    base.update(values)
    return patch.dict("os.environ", base, clear=True)


class ScopeConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="v2-p1b-cfg-")
        self.addCleanup(self.tmp.cleanup)
        self.evidence = str(Path(self.tmp.name) / "market_evidence.db")

    def on(self, scope, **extra):
        return env(**{FLAG: "1", "AI_FLOOR_MARKET_EVIDENCE_PATH": self.evidence, CATCH_UP_SYMBOLS_ENV: scope}, **extra)

    # C1: both absent -> OFF, legacy fingerprint byte-identical
    def test_c1_both_absent_is_legacy_and_fingerprint_identical(self):
        with env():
            config = RuntimeConfig.from_env()
        self.assertEqual((config.v2_position_catch_up, config.v2_position_catch_up_symbols), (False, ()))
        self.assertEqual(config.fingerprint(), LEGACY_FINGERPRINT)
        self.assertEqual(RuntimeConfig().fingerprint(), LEGACY_FINGERPRINT)
        self.assertEqual(config.certification_eligibility, "LEGACY_NOT_CERTIFIABLE")
        self.assertFalse(config.catch_up_applies("XAUUSD") or config.catch_up_applies("EURUSD"))

    # C2: flag unset / "0" with the scope variable present, even empty -> ValueError (DEC-8.13)
    def test_c2_scope_variable_while_off_fails_closed(self):
        for flag in (None, "", "0"):
            for scope in ("", "XAUUSD", "XAUUSD,EURUSD", " "):
                with self.subTest(flag=flag, scope=scope):
                    values = {CATCH_UP_SYMBOLS_ENV: scope}
                    if flag is not None:
                        values[FLAG] = flag
                    with env(**values), self.assertRaises(ValueError):
                        RuntimeConfig.from_env()

    # C3: flag "1" with the scope absent or empty -> ValueError
    def test_c3_flag_on_without_explicit_scope_fails_closed(self):
        with env(**{FLAG: "1", "AI_FLOOR_MARKET_EVIDENCE_PATH": self.evidence}), self.assertRaises(ValueError):
            RuntimeConfig.from_env()
        with self.on(""), self.assertRaises(ValueError):
            RuntimeConfig.from_env()

    # C4: XAUUSD only
    def test_c4_xauusd_only(self):
        with self.on("XAUUSD"):
            config = RuntimeConfig.from_env()
        self.assertEqual(config.v2_position_catch_up_symbols, ("XAUUSD",))
        self.assertTrue(config.catch_up_applies("XAUUSD"))
        self.assertFalse(config.catch_up_applies("EURUSD"))
        self.assertFalse(config.catch_up_applies("NAS100"))
        self.assertEqual(config.certification_eligibility, "TECHNICAL_ONLY")  # DEC-8.16, I-S1

    # C5: order-insensitive canonical scope and fingerprint (F1 part)
    def test_c5_canonical_order(self):
        with self.on("EURUSD,XAUUSD"):
            a = RuntimeConfig.from_env()
        with self.on("XAUUSD,EURUSD"):
            b = RuntimeConfig.from_env()
        self.assertEqual(a.v2_position_catch_up_symbols, ("XAUUSD", "EURUSD"))
        self.assertEqual(a.fingerprint(), b.fingerprint())
        self.assertEqual(a.certification_eligibility, "REQUIRES_G8_INT")

    # C6: malformed tokens
    def test_c6_malformed_lists_fail_closed(self):
        for scope in ("XAUUSD,XAUUSD", "XAUUSD,", ",XAUUSD", " XAUUSD", "XAUUSD ", "xauusd", "XAU USD", "XAUUSD;EURUSD",
                      "XAUUSD,,EURUSD", "XAU-USD", "XAUUSD\n"):
            with self.subTest(scope=scope), self.on(scope), self.assertRaises(ValueError):
                RuntimeConfig.from_env()

    # C7: NAS100 never in scope
    def test_c7_nas100_rejected(self):
        for scope in ("NAS100", "XAUUSD,NAS100"):
            with self.subTest(scope=scope), self.on(scope), self.assertRaises(ValueError):
                RuntimeConfig.from_env()
        with self.on("NAS100", AI_FLOOR_ENABLED_SYMBOLS="XAUUSD,EURUSD,NAS100"), self.assertRaises(ValueError):
            RuntimeConfig.from_env()  # even if a future config enables NAS100

    # C8: supported but not enabled, and unknown symbols
    def test_c8_not_enabled_or_unknown_rejected(self):
        with self.on("EURUSD", AI_FLOOR_ENABLED_SYMBOLS="XAUUSD"), self.assertRaises(ValueError):
            RuntimeConfig.from_env()
        with self.on("GBPUSD"), self.assertRaises(ValueError):
            RuntimeConfig.from_env()

    # C9: direct construction invariants
    def test_c9_direct_construction_invariants(self):
        base = dict(db_path=Path(self.tmp.name) / "trading_floor.db", market_evidence_path=Path(self.evidence))
        for kwargs in ({"v2_position_catch_up": True},  # ON without scope
                       {"v2_position_catch_up": False, "v2_position_catch_up_symbols": ("XAUUSD",)},  # OFF with scope
                       {"v2_position_catch_up": True, "v2_position_catch_up_symbols": ["XAUUSD"]},  # not a tuple
                       {"v2_position_catch_up": True, "v2_position_catch_up_symbols": ("EURUSD", "XAUUSD")},  # order
                       {"v2_position_catch_up": True, "v2_position_catch_up_symbols": ("XAUUSD", "XAUUSD")},
                       {"v2_position_catch_up": True, "v2_position_catch_up_symbols": ("NAS100",)}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                RuntimeConfig(**base, **kwargs)
        ok = RuntimeConfig(**base, v2_position_catch_up=True, v2_position_catch_up_symbols=("XAUUSD",))
        self.assertTrue(ok.catch_up_applies("XAUUSD"))
        with self.assertRaises(ValueError):
            replace(ok, v2_position_catch_up=False)  # OFF must also clear the scope

    # F1: each scope has its own fingerprint; OFF has none of the new keys
    def test_f1_fingerprint_reflects_exact_scope(self):
        with self.on("XAUUSD"):
            xau = RuntimeConfig.from_env().fingerprint()
        with self.on("XAUUSD,EURUSD"):
            both = RuntimeConfig.from_env().fingerprint()
        self.assertEqual(len({xau, both, LEGACY_FINGERPRINT}), 3)

    def test_parser_returns_canonical_tuple(self):
        self.assertEqual(parse_catch_up_symbols("EURUSD,XAUUSD", ("XAUUSD", "EURUSD")), ("XAUUSD", "EURUSD"))
        for bad in (None, "", 5):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_catch_up_symbols(bad, ("XAUUSD", "EURUSD"))


if __name__ == "__main__":
    unittest.main()
