"""DEC-14.1 + DEC-14.2 together: production run_cycle reverses on a valid opposite structure change."""
import unittest
from dataclasses import replace
from unittest.mock import patch

from test_dec14_opposite_policy import OppositePolicyTests, reports, snapshot, T
from test_demo_runner import macro_fixture


class CombinedReversalE2E(OppositePolicyTests):
    def setUp(self):
        super().setUp()
        self.rt.close()
        self.config = replace(self.config, v2_planner_policy="V2_F3")
        self.rt = self.runtime()

    def test_live_runtime_preserves_planner_and_rejects_non_fixed_3r_contract(self):
        self.skipTest("V1-planner premise; V2_F3 counterpart is test_live_runtime_profit_reversal_with_both_flags")

    def test_live_runtime_profit_reversal_with_both_flags(self):
        self.assertEqual(self.rt.config.v2_planner_policy, "V2_F3")
        self.assertEqual(self.rt.config.v2_opposite_setup_policy, "STRUCTURE_CHANGE")
        self.rt.store.db.execute("DELETE FROM symbol_locks")
        self.rt.store.db.execute("DELETE FROM runs")
        snap = snapshot()
        self.rt.macro_provider = macro_fixture()
        self.rt.market_provider = type("P", (), {"load_snapshot": lambda _, s, at: snap})()

        def scout(kind):
            def analyze(frame, symbol, tf, run_id, at):
                r = reports(run_id=run_id)
                return (r.structure_reports if kind == "structure" else r.liquidity_reports)[tf]
            return analyze
        with patch("floor.orchestrator.analizar_estructura", scout("structure")), \
                patch("floor.orchestrator.analizar_liquidez", scout("liquidity")):
            self.assertEqual(self.rt.run_cycle("XAUUSD", T), "PLAN_READY")
        account, orders, _ = self.paper()
        self.assertEqual(len(account.closed_trades), 1)          # LONG in profit closed
        self.assertGreater(account.closed_trades[0].net_pnl, 0)
        decision = self.rows("EXECUTION_DECISION")[-1]
        self.assertNotEqual(decision["execution_reason"], "not_a_fixed_3r_plan")
        self.assertNotEqual(decision["execution_reason"], "EXISTING_POSITION")
        new = [o for o in orders.values() if o.side == "SHORT"]
        self.assertEqual(len(new), 1)                            # reversed, never two positions
        self.assertLessEqual(len(account.open_positions), 1)
        print("E2E decision:", decision["execution_reason"], decision.get("execution_status"))


if __name__ == "__main__":
    unittest.main()
