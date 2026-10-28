"""DEC-FLOOR-LABELS-1: the Exec label shows the open trade's real P&L (and real entry / SL / TP, never a lone "—"),
and the wall screens are lit on a narrow stage. UI / payload only: no execution path is involved."""
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from test_floor_view import NOW, fake_demo, seed
from ui.floor_payload import build_floor_payload
from ui.state import dashboard_database
from ui.views import dashboard, floor_view

ASSET = floor_view.ASSET.read_text(encoding="utf-8")


def payload_with(pnl):
    demo = fake_demo()
    snapshot = demo["snapshot"](None)
    snapshot["open_rows"][0]["unrealized_pnl"] = pnl
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "floor.db"
        seed(path)
        with dashboard_database(path) as db, patch.object(dashboard, "demo_since",
                                                          return_value=NOW - timedelta(days=2)):
            return build_floor_payload(now=NOW, db=db, demo=demo, enabled_symbols=("EURUSD",))


class ExecLabelPayloadTests(unittest.TestCase):
    def test_open_trade_carries_real_pnl_and_levels(self):
        trade = next(t for t in payload_with(33.0)["trades"] if t["status"] == "OPEN")
        self.assertEqual(trade["pnl"], 33.0)
        self.assertEqual(trade["side"], "LONG")
        self.assertEqual(trade["entry_actual"], 1.10013)
        self.assertEqual(trade["sl_actual"], 1.098)
        self.assertEqual(trade["tp_actual"], 1.106)

    def test_unknown_pnl_is_none_not_zero(self):
        trade = next(t for t in payload_with(None)["trades"] if t["status"] == "OPEN")
        self.assertIsNone(trade["pnl"])


class ExecLabelAssetTests(unittest.TestCase):
    def test_label_is_side_and_pnl_and_r_only_when_known(self):
        self.assertIn("const openLine=o=>`${SIDE[o.side]||NA} · ${money(o.pnl,1)}"
                      "${ok(o.r_realized)?' · '+fR(o.r_realized):''}`;", ASSET)
        # bubble above Carlos, the desk status box and the legend strip all use it (no lone "Long · —")
        self.assertIn("say.Exec=[openLine(OPEN)+(MOB()&&TSTALE?' '+TSTALE:''),openSub(OPEN)]", ASSET)
        self.assertIn("if(OPEN)return openLine(OPEN);", ASSET)
        self.assertNotIn("`${SIDE[OPEN.side]||NA} · ${fR(OPEN.r_realized)}", ASSET)

    def test_second_line_lists_only_real_entry_sl_tp(self):
        self.assertIn("const openSub=o=>", ASSET)
        self.assertIn(".filter(q=>ok(q[1]))", ASSET)
        self.assertIn("['E',", ASSET)
        self.assertIn("['SL',", ASSET)
        self.assertIn("['TP',", ASSET)

    def test_rendered_page_embeds_the_open_trade(self):
        html = floor_view.render_html(payload_with(33.0))
        data = html.split('<script id="floor-data" type="application/json">', 1)[1].split("</script>", 1)[0]
        trade = next(t for t in json.loads(data)["trades"] if t["status"] == "OPEN")
        self.assertEqual(trade["pnl"], 33.0)
        self.assertIn("openLine(OPEN)", html)


class MobileWallTests(unittest.TestCase):
    def test_wall_screens_are_lit_on_a_narrow_stage(self):
        self.assertIn("const wallTxt=!MOB();", ASSET)  # long wall text stays desktop-only
        self.assertIn("poly(pts,MOB()?'#12393b':'#0c0b09',", ASSET)
        self.assertIn("cx.shadowColor='#19e3d3';cx.shadowBlur=hot?10:6", ASSET)

    def test_one_legible_line_per_wall_from_the_payload(self):
        self.assertIn("if(MOB()){const cap=(x,y,cands,col,maxU)=>", ASSET)
        for needle in ("`NAV ${money(HD.equity)}`", "FEED[0]", "ML[0]", "HD.day_text"):
            self.assertIn(needle, ASSET)
        # a candidate is used only when it fits: never clipped, never invented
        self.assertIn("cands.find(c=>c&&fits(c,9,null,maxU))", ASSET)


if __name__ == "__main__":
    unittest.main()
