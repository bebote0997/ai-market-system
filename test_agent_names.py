"""DEC-AGENT-NAMES-1: display names of the Floor characters (ui/agent_names.py), display only."""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from ui import agent_names
from ui.agent_names import AGENT_NAMES, display_name, label, long_role, names_payload, short_role
from ui.floor_payload import AGENT_IDS, build_floor_payload
from ui.views.floor_view import render_html

OLD_NAMES = ("Atlas", "Nova", "Orion", "Vega", "Sage", "Arbiter", "Sentinel", "Echo")
FLOOR_HTML = Path("ui/static/floor/floor.html")


def names_js(html):
    """The NAMES / LABEL block of floor.html (from ``const AIDS`` to the LABEL helper)."""
    start = html.index("const AIDS=")
    end = html.index("\n", html.index("const LABEL="))
    return html[start:end]


class MappingTest(unittest.TestCase):
    def test_covers_exactly_the_eight_internal_ids(self):
        self.assertEqual(set(AGENT_NAMES), set(AGENT_IDS))
        self.assertEqual(len(AGENT_NAMES), 8)

    def test_names_unique_non_empty_and_new(self):
        names = [entry[0] for entry in AGENT_NAMES.values()]
        self.assertEqual(len(set(names)), 8)
        for agent_id, (name, role, short) in AGENT_NAMES.items():
            self.assertTrue(name.strip() and role.strip() and short.strip(), agent_id)
            self.assertNotEqual(name, agent_id)
            self.assertNotIn(name, OLD_NAMES)

    def test_expected_mapping(self):
        self.assertEqual({k: v[0] for k, v in AGENT_NAMES.items()},
                         {"Structure": "Mateo", "Liquidity": "Sofía", "Macro": "Andrés", "Risk": "Valentina",
                          "Reviewer": "Lucía", "Conflict": "Diego", "RiskEng": "Camila", "Exec": "Carlos"})
        self.assertEqual(long_role("Structure"), "Analista de estructura")
        self.assertEqual(short_role("Conflict"), "Conflictos")
        self.assertEqual(label("Exec"), "Carlos · Mesa EURUSD")

    def test_old_names_absent_from_module(self):
        source = Path(agent_names.__file__).read_text(encoding="utf-8")
        for old in OLD_NAMES:
            self.assertIsNone(re.search(rf"\b{old}\b", source), old)

    def test_unknown_id_is_shown_as_itself(self):
        self.assertEqual(display_name("Nope"), "Nope")
        self.assertEqual(label("Nope"), "Nope")
        self.assertEqual(long_role("Nope"), "")

    def test_names_payload_is_json_and_a_fresh_copy(self):
        first = names_payload()
        self.assertEqual(json.loads(json.dumps(first)), first)
        self.assertEqual(first["Exec"], {"name": "Carlos", "role": "Ejecución", "short": "Mesa EURUSD"})
        first["Exec"]["name"] = "x"
        self.assertEqual(names_payload()["Exec"]["name"], "Carlos")


class PayloadTest(unittest.TestCase):
    def test_payload_carries_names_and_internal_ids_unchanged(self):
        for payload in (build_floor_payload(), build_floor_payload(sample_mode=True)):
            self.assertEqual(payload["names"], names_payload())
            self.assertEqual(tuple(payload["agents"]), AGENT_IDS)
        self.assertEqual(AGENT_IDS, ("Structure", "Liquidity", "Macro", "Risk", "Reviewer", "Conflict", "RiskEng",
                                     "Exec"))

    def test_rendered_page_embeds_the_names(self):
        html = render_html(build_floor_payload())
        self.assertIn('"Carlos"', html)
        self.assertIn('"Mesa EURUSD"', html)


class FloorHtmlTest(unittest.TestCase):
    def setUp(self):
        self.html = FLOOR_HTML.read_text(encoding="utf-8")

    def test_no_hardcoded_old_or_new_names(self):
        for old in OLD_NAMES:
            self.assertIsNone(re.search(rf"\b{old}\b", self.html), old)
        for name, _role, _short in AGENT_NAMES.values():
            self.assertNotIn(name, self.html)
        self.assertIn("DATA.names", self.html)

    def test_plate_shows_role_beside_name_with_name_only_fallback(self):
        plate = self.html[self.html.index("function plate(a)"):self.html.index("const IDLE=")]
        self.assertIn("LABEL(a.id)", plate)  # «NOMBRE · ROL» on desktop / wall
        self.assertIn("!MOB()", plate)  # phone: the name only
        self.assertIn("PLACED.some", plate)  # still never overlapping

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_names_block_runs_with_and_without_payload_names(self):
        script = ("const out={};for(const DATA of [{names:%s},{},{names:null},{names:{Exec:{}}}]){%s;"
                  "out[Object.keys(out).length]={exec:NAMES.Exec,label:LABEL('Exec'),risk:LABEL('RiskEng')}}"
                  "console.log(JSON.stringify(out))") % (json.dumps(names_payload()), names_js(self.html))
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30, check=True)
        out = json.loads(result.stdout)
        self.assertEqual(out["0"]["exec"], ["Carlos", "Ejecución", "Mesa EURUSD"])
        self.assertEqual(out["0"]["label"], "Carlos · Mesa EURUSD")
        self.assertEqual(out["0"]["risk"], "Camila · Motor de riesgo")
        for key in ("1", "2", "3"):  # no names => the internal id, no role, no crash
            self.assertEqual(out[key]["exec"], ["Exec", "", ""])
            self.assertEqual(out[key]["label"], "Exec")


class ManualCloseTitleTest(unittest.TestCase):
    def test_title_uses_the_exec_name(self):
        from ui.views.floor_actions import MANUAL_CLOSE_TITLE
        self.assertEqual(MANUAL_CLOSE_TITLE, "🧑‍💼 Carlos · Gestor de la operación · mesa de riesgo")
        self.assertIn(display_name("Exec"), MANUAL_CLOSE_TITLE)
        self.assertNotIn("Echo", MANUAL_CLOSE_TITLE)


if __name__ == "__main__":
    unittest.main()
