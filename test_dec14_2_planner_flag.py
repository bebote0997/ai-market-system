"""DEC-14.2: opt-in fixed-3R planning and PAPER fill tolerance through durable runtime cycles."""
import os
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from core.rr_contract import POLICY_V2_F3, geometry
from riesgo import crear_configuracion_riesgo_v2
from runtime import service
from runtime.config import RuntimeConfig
from test_demo_runner import T, macro_fixture, patched_scouts
from test_runtime_catch_up import Bars
from test_phase4_fixed_3r import INS


class PlannerFlagTests(unittest.TestCase):
    def test_default_and_explicit_v1_keep_original_fingerprint(self):
        config = RuntimeConfig()
        self.assertEqual(config.v2_planner_policy, "V1")
        self.assertEqual(config.fingerprint(), "16b257235a26f293cedf46c74fff8f3231f41467f08c504e8c714aff29d0b430")
        self.assertEqual(replace(config, v2_planner_policy="V1").fingerprint(), config.fingerprint())
        self.assertNotEqual(replace(config, v2_planner_policy="V2_F3").fingerprint(), config.fingerprint())
        with patch.dict(os.environ, {}, clear=True), patch("runtime.env.load_local_env"):
            self.assertEqual(RuntimeConfig.from_env(), config)

    def test_env_selects_only_known_policies_and_warns_on_unknown(self):
        for value in ("V1", "V2_F3", "POLICY_V2_D", "V2_D", "v2_f3", "", "unknown"):
            with self.subTest(value=value), patch.dict(os.environ, {"V2_PLANNER_POLICY": value}, clear=True), \
                    patch("runtime.env.load_local_env"):
                if value in ("V1", "V2_F3"):
                    self.assertEqual(RuntimeConfig.from_env().v2_planner_policy, value)
                else:
                    with self.assertLogs("ai_floor.runtime", level="WARNING") as logs:
                        config = RuntimeConfig.from_env()
                    self.assertEqual(config.v2_planner_policy, "V1")
                    self.assertEqual(config.fingerprint(), RuntimeConfig().fingerprint())
                    self.assertIn("fail closed to V1", logs.output[0])

    def cycle_fill(self, policy, ratio, catch_up):
        # Planned LONG 100 / 90 / 130. Actual fill solves (130-fill)/(fill-90) = ratio.
        price = float((Decimal(130) + Decimal(str(ratio)) * 90) / (1 + Decimal(str(ratio))))
        next_slot = T + timedelta(minutes=15)
        with tempfile.TemporaryDirectory() as directory:
            config = RuntimeConfig(db_path=Path(directory) / "paper.db", enabled_symbols=("XAUUSD",),
                                   market_provider_mode="none", v2_planner_policy=policy,
                                   v2_position_catch_up=catch_up,
                                   v2_position_catch_up_symbols=("XAUUSD",) if catch_up else (),
                                   market_evidence_path=Path(directory) / "evidence.db" if catch_up else None)
            provider = Bars(overrides={("XAUUSD", next_slot - timedelta(minutes=5)): (price, price, price, price)})
            now = [T]
            runtime = service.OperationalRuntime(config, market_provider=provider, macro_provider=macro_fixture(),
                                                 instruments=INS, clock=lambda: now[0])
            with closing(runtime):
                with patched_scouts("LONG"), patch.object(service, "run_floor", wraps=service.run_floor) as floor:
                    runtime.run_cycle("XAUUSD", T)
                    _, orders, _ = runtime.store.load_paper(config.account_id)
                    self.assertEqual(len(orders), 1)
                    order = next(iter(orders.values()))
                    self.assertEqual((order.planned_entry, order.stop, order.target, order.status), (100, 90, 130, "PENDING"))
                    kwargs = floor.call_args.kwargs
                    if policy == "V2_F3":
                        self.assertEqual(kwargs["planner_policy"], POLICY_V2_F3)
                        self.assertEqual(geometry("LONG", order.planned_entry, order.stop, order.target)[0].rr, Decimal(3))
                        self.assertEqual(runtime._broker("XAUUSD").rr_policy, POLICY_V2_F3)
                    else:
                        self.assertNotIn("planner_policy", kwargs)
                        self.assertIsNone(runtime._broker("XAUUSD").rr_policy)
                        self.assertEqual(runtime.risk_config, crear_configuracion_riesgo_v2())
                    now[0] = next_slot
                    runtime.run_cycle("XAUUSD", next_slot)
                _, orders, _ = runtime.store.load_paper(config.account_id)
                result = orders[order.order_id]
                self.assertEqual((result.stop, result.target), (90, 130))
            return result.status

    def test_fill_floor_end_to_end_both_runtime_paths(self):
        for catch_up in (False, True):
            for policy, ratio, expected in (("V2_F3", 2.6, "FILLED"), ("V2_F3", 2.4, "REJECTED"),
                                             ("V1", 2.6, "REJECTED"), ("V1", 3.0, "FILLED")):
                with self.subTest(policy=policy, ratio=ratio, catch_up=catch_up):
                    self.assertEqual(self.cycle_fill(policy, ratio, catch_up), expected)


def setUpModule():  # DEC-XAU-OFF-1: these tests drive XAUUSD cycles; the pause itself is in test_pause_xauusd
    import unittest.mock
    unittest.enterModuleContext(unittest.mock.patch("runtime.symbol_pause.PAUSED_SYMBOLS", frozenset()))


if __name__ == "__main__":
    unittest.main()
