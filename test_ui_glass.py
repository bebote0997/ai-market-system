"""Glass UI layer: pure HTML builders (escaped, real data only, "Sin datos aún" when missing), the Floor agent panels,
the Inicio sparkline / «Día N de 30» bar, the Process step strip, and the theme.css glass layer (fallbacks, phone blur,
WCAG AA contrast budget, no external assets). The Floor asset itself is never modified."""
from datetime import datetime, timedelta, timezone
from contextlib import nullcontext
import math
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from ui import agent_names
from ui.components import glass
from ui.floor_payload import AGENT_IDS, build_floor_payload
from ui.views import floor_view, luxury

ROOT = Path(__file__).resolve().parent
CSS = (ROOT / "ui" / "static" / "theme.css").read_text(encoding="utf-8")
GLASS_CSS = CSS.split("/* ===== Glass layer ===== */", 1)[-1]
NOW = datetime(2026, 10, 20, 13, 30, tzinfo=timezone.utc)
XSS = '<script>alert("x")</script><img src=x onerror=alert(1)>'


def view(**changes):
    return {"status": "Bien · Alcista", "bias": "LONG", "confidence": 0.7, "analysis": "estructura alcista real",
            "as_of": "20 oct 08:00 ET", "hit_rate": None, "signals": None, **changes}


def media_block(css, query):
    """Body of the first ``@media(<query>){...}`` block (balanced braces)."""
    start = css.index("@media(" + query + "){") + len("@media(" + query + "){")
    depth, i = 1, start
    while depth:
        depth += {"{": 1, "}": -1}.get(css[i], 0)
        i += 1
    return css[start:i - 1]


class SparklineTests(unittest.TestCase):
    def test_needs_two_finite_points(self):
        for values in (None, [], [5.0], [float("nan"), 3.0], [float("inf"), float("-inf"), 1.0], ["7", None, 2.0],
                       [True, 1.0]):
            with self.subTest(values=values):
                self.assertEqual(glass.sparkline_svg(values, "#4cf0a2"), "")

    def test_non_finite_points_are_dropped(self):
        svg = glass.sparkline_svg([1.0, float("nan"), 2.0, float("inf"), 3.0], "#4cf0a2")
        points = re.search(r'<polyline points="([^"]+)"', svg).group(1).split()
        self.assertEqual(len(points), 3)
        self.assertNotIn("nan", svg.lower())
        self.assertNotIn("inf", svg.lower())

    def test_flat_series_is_a_centred_line_and_output_is_deterministic(self):
        svg = glass.sparkline_svg([2, 2, 2], "#4cf0a2")
        ys = {p.split(",")[1] for p in re.search(r'<polyline points="([^"]+)"', svg).group(1).split()}
        self.assertEqual(ys, {"18.0"})
        self.assertEqual(svg, glass.sparkline_svg([2, 2, 2], "#4cf0a2"))
        rising = re.search(r'<polyline points="([^"]+)"', glass.sparkline_svg([0, 10], "#fff")).group(1).split()
        self.assertGreater(float(rising[0].split(",")[1]), float(rising[1].split(",")[1]))  # higher value = higher

    def test_accessible_self_contained_and_escaped(self):
        svg = glass.sparkline_svg([1, 2], '"><script>', label='P&L <b>')
        self.assertTrue(svg.startswith('<svg class="gl-spark" role="img" aria-label="P&amp;L &lt;b&gt;"'))
        self.assertNotIn("<script>", svg)
        for ref in ("http", "url(", "href", "xlink", "<image", "<use"):
            self.assertNotIn(ref, svg)
        self.assertIn('aria-label="Evolución del P&amp;L realizado acumulado"', luxury.pnl_sparkline(
            [{"Cumulative realized PnL": 1.0}, {"Cumulative realized PnL": 2.0}]))


class AgentCardTests(unittest.TestCase):
    def test_names_and_roles_come_from_agent_names(self):
        for agent_id in AGENT_IDS:
            with self.subTest(agent=agent_id):
                card = glass.agent_card(agent_id, view())
                self.assertIn(f'<div class="gl-name">{agent_names.display_name(agent_id)}</div>', card)
                self.assertIn(agent_names.short_role(agent_id), card)
        with patch.dict(agent_names.AGENT_NAMES, {"Structure": ("Otro", "Rol largo", "Rol corto")}):
            card = glass.agent_card("Structure", view())
        self.assertIn(">Otro<", card)
        self.assertIn("Rol corto", card)
        source = (ROOT / "ui" / "components" / "glass.py").read_text(encoding="utf-8")
        for name, _role, _short in agent_names.AGENT_NAMES.values():
            self.assertNotIn(name, source)  # never hard-coded twice

    def test_agent_text_and_status_are_escaped(self):
        card = glass.agent_card("Macro", view(analysis=XSS, status=XSS, as_of=XSS))
        self.assertNotIn("<script>", card)
        self.assertNotIn("<img", card)
        self.assertIn("&lt;script&gt;", card)
        with patch.dict(agent_names.AGENT_NAMES, {"Macro": (XSS, XSS, XSS)}):
            card = glass.agent_card("Macro", None)
        self.assertNotIn("<script>", card)
        self.assertNotIn("<img", card)

    def test_missing_agent_says_no_data_never_fake_text(self):
        card = glass.agent_card("Risk", None, waiting_text="Esperando el primer ciclo")
        self.assertIn("Sin datos aún", card)
        self.assertIn("Esperando el primer ciclo", card)
        self.assertIn("gl-empty", card)
        for fake in ("LONG", "SHORT", "gl-conf", "gl-time", "EJEMPLO"):
            self.assertNotIn(fake, card)
        self.assertIn("Sin datos aún", glass.agent_card("Risk", view(analysis=None, status=None)))

    def test_analysis_is_truncated(self):
        text = "palabra " * 80
        card = glass.agent_card("Structure", view(analysis=text))
        bubble = re.search(r'<div class="gl-bubble">([^<]*)</div>', card).group(1)
        self.assertLessEqual(len(bubble), glass.ANALYSIS_LIMIT)
        self.assertTrue(bubble.endswith("…"))
        self.assertEqual(glass.truncate("corto"), "corto")
        self.assertEqual(len(glass.truncate("x" * 500)), glass.ANALYSIS_LIMIT)
        self.assertEqual(glass.truncate(None), "")

    def test_bias_chip_and_confidence_only_when_present(self):
        self.assertIn('<span class="gl-bias pos">LONG</span>', glass.agent_card("Structure", view()))
        self.assertIn('<span class="gl-bias neg">SHORT</span>', glass.agent_card("Structure", view(bias="SHORT")))
        card = glass.agent_card("Macro", view(bias="NEUTRAL", confidence=None, as_of=None))
        for absent in ("gl-bias", "gl-conf", "gl-time", "gl-facts"):
            self.assertNotIn(absent, card)
        self.assertIn("width:70%", glass.agent_card("Structure", view()))
        self.assertIn("width:100%", glass.agent_card("Structure", view(confidence=7.0)))  # clamped
        self.assertNotIn("gl-conf", glass.agent_card("Structure", view(confidence=float("nan"))))
        self.assertNotIn("gl-conf", glass.agent_card("Structure", view(confidence=True)))

    def test_colours_mirror_the_floor_asset(self):
        asset = floor_view.ASSET.read_text(encoding="utf-8")
        table = re.search(r"const COL=\{([^}]*)\}", asset).group(1)
        colours = dict(re.findall(r"(\w+):'(#[0-9a-fA-F]{6})'", table))
        self.assertEqual(colours, glass.AGENT_COLORS)
        self.assertEqual(set(glass.LEFT_AGENTS + glass.RIGHT_AGENTS), set(AGENT_IDS))

    def test_panel_is_closed_details_with_a_tap_summary(self):
        panel = glass.agents_panel(glass.LEFT_AGENTS, {"Structure": view(), "Liquidity": view()}, title="Equipo de análisis",
                                   example_fields=("agents.Liquidity",))
        self.assertTrue(panel.startswith('<details class="gl-agents"><summary>'))
        self.assertNotIn("<details open", panel)
        self.assertIn("Equipo de análisis · 4 agentes", panel)
        self.assertIn("toca para ver", panel)
        self.assertEqual(panel.count("<article"), 4)
        self.assertEqual(panel.count("EJEMPLO"), 1)  # only the sample-filled agent is marked
        self.assertEqual(panel.count("Sin datos aún"), 2)


class DayAndStepsTests(unittest.TestCase):
    def test_day_progress_only_for_a_known_day(self):
        for day in (None, 0, -3, True, 2.5, "3"):
            with self.subTest(day=day):
                self.assertEqual(glass.day_progress(day), "")
        self.assertIn("width:10%", glass.day_progress(3))
        self.assertIn('aria-label="Día 3 de 30"', glass.day_progress(3))
        self.assertIn("width:100%", glass.day_progress(45))  # clamped
        self.assertIn('aria-valuenow="30"', glass.day_progress(45))

    def test_step_strip_marks_done_and_pending(self):
        strip = glass.step_strip([("Llegada de datos", True), ("Motor <b>", False)])
        self.assertEqual(strip.count('class="gl-step done"'), 1)
        self.assertEqual(strip.count(">pendiente<"), 1)
        self.assertIn("1 de 2 con evidencia", strip)
        self.assertIn("Motor &lt;b&gt;", strip)


class FakeSt:
    def __init__(self):
        self.session_state, self.markdowns, self.calls = {}, [], []

    def markdown(self, body, **_):
        self.markdowns.append(body)

    def container(self, key=None, **_):
        self.calls.append(("container", key))
        return nullcontext()

    def columns(self, spec, **_):
        self.calls.append(("columns", spec))
        return [nullcontext() for _ in spec]

    def iframe(self, html, height=None):
        self.calls.append(("iframe", height))
        self.markdowns.append("IFRAME")

    def caption(self, body, **_):
        self.markdowns.append(body)

    def button(self, *_args, **_kwargs):
        return False

    info = warning = caption


class FloorLayoutTests(unittest.TestCase):
    def render(self, payload):
        st = FakeSt()
        with patch.object(floor_view, "current_payload", return_value=payload), \
                patch.object(floor_view, "render_manual_close") as manual:
            floor_view.render(st, ("EURUSD",))
        manual.assert_called_once_with(st)
        return st

    def test_three_columns_agents_around_the_unchanged_iframe(self):
        payload = build_floor_payload(sample_mode=False, now=NOW)
        payload["agents"]["Structure"] = view(analysis="estructura alcista real")
        st = self.render(payload)
        self.assertIn(("container", "floor-stage"), st.calls)
        self.assertIn(("columns", [1, 2.6, 1]), st.calls)
        self.assertIn(("iframe", floor_view.HEIGHT), st.calls)
        left, iframe, right = st.markdowns[:3]
        self.assertEqual(iframe, "IFRAME")
        for agent_id in glass.LEFT_AGENTS:
            self.assertIn(agent_names.display_name(agent_id), left)
        for agent_id in glass.RIGHT_AGENTS:
            self.assertIn(agent_names.display_name(agent_id), right)
        self.assertIn("estructura alcista real", left)
        self.assertEqual((left + right).count("Sin datos aún"), 7)
        self.assertNotIn("EJEMPLO", left + right)

    def test_sample_mode_marks_example_agents(self):
        payload = build_floor_payload(sample_mode=True, now=NOW)
        st = self.render(payload)
        self.assertEqual("".join(st.markdowns[:3]).count('class="gl-ex">EJEMPLO'), 8)

    def test_floor_asset_untouched(self):
        asset = floor_view.ASSET.read_text(encoding="utf-8")
        self.assertEqual(asset.count(floor_view.PLACEHOLDER), 1)
        self.assertNotIn("gl-agent", asset)

    def test_manual_close_is_wrapped_in_the_keyed_card(self):
        st = FakeSt()
        floor_view.render_manual_close(st, api=None, owner=False)
        self.assertEqual(st.calls[0], ("container", "manual-close-card"))
        self.assertIn("Solo el owner", "\n".join(st.markdowns))

    def test_confirm_button_states_keep_white_text_contrast(self):
        import re
        css = (ROOT / "ui" / "static" / "theme.css").read_text(encoding="utf-8")

        def lum(hex6):
            ch = [int(hex6[i:i + 2], 16) / 255 for i in (0, 2, 4)]
            ch = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in ch]
            return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]
        rules = [line for line in css.splitlines() if ".st-key-floor-manual-close-confirm button" in line and "linear-gradient" in line]
        self.assertGreaterEqual(len(rules), 2)  # base + hover
        for line in rules:
            for hex6 in re.findall(r"#([0-9a-f]{6})", line.split("linear-gradient", 1)[1].split(")", 1)[0]):
                self.assertGreaterEqual(1.05 / (lum(hex6) + 0.05), 4.5, (hex6, line))

    def test_sidebar_starts_collapsed_on_phones(self):
        app = (ROOT / "ui" / "app.py").read_text(encoding="utf-8")
        self.assertIn('initial_sidebar_state="auto"', app)


class OverviewGlassTests(unittest.TestCase):
    def snapshot(self, curve, **extra):
        stats = {"trades": 2, "wins": 1, "total_pnl": 30.0, "today_pnl": 0.0, "max_drawdown": 10.0, "avg_r": 0.5,
                 "avg_r_reason": None}
        return {"epoch": NOW, "stats": stats, "curve": curve, "open_rows": [], "closed_rows": [], "nav": 100030.0,
                "balance": 100030.0, "currency": "USD", "capital": {"start": 100000.0, "change": 30.0},
                "stale": False, "trades_error": None, "daily_loss": None, **extra}

    def curve(self, *values):
        return [{"Closed at": NOW + timedelta(hours=i), "Cumulative realized PnL": v} for i, v in enumerate(values)]

    def test_sparkline_only_on_realized_pnl_with_a_real_series(self):
        tiles = luxury.demo_kpis(self.snapshot(self.curve(10.0, 30.0)))
        with_spark = [i for i, tile in enumerate(tiles) if "gl-spark" in tile]
        self.assertEqual(len(with_spark), 1)
        self.assertIn("P&amp;L realizado", tiles[with_spark[0]])
        for curve in ([], self.curve(30.0), self.curve(float("nan"), 30.0)):
            with self.subTest(points=len(curve)):
                self.assertNotIn("gl-spark", "".join(luxury.demo_kpis(self.snapshot(curve))))
        failed = self.snapshot(self.curve(10.0, 30.0), stats=None, trades_error="deadline")
        self.assertNotIn("gl-spark", "".join(luxury.demo_kpis(failed)))

    def test_paper_overview_sparkline_from_the_ledger_curve(self):
        self.assertIn("gl-spark", luxury.pnl_sparkline(self.curve(1.0, -2.0, 5.0)))
        self.assertIn("#ff8099", luxury.pnl_sparkline(self.curve(1.0, -2.0)))  # negative last value
        self.assertEqual(luxury.pnl_sparkline([]), "")

    def demo_overview(self, day):
        st = MagicMock()
        st.session_state = {}
        st.columns.return_value = [MagicMock(), MagicMock()]
        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, {"AI_FLOOR_DB_PATH": str(Path(tmp) / "none.db")}), \
                patch.object(luxury, "side_panel"), patch.object(luxury, "curve_chart"):
            from ui.state import dashboard_database
            with dashboard_database(Path(tmp) / "none.db") as db:
                luxury.demo_overview(st, db, NOW, self.snapshot([]), day=day)
        return st.markdown.call_args_list[0].args[0]

    def test_day_progress_in_the_demo_header_only_for_a_known_day(self):
        self.assertIn('class="gl-progress"', self.demo_overview(3))
        self.assertIn("width:10%", self.demo_overview(3))
        self.assertNotIn("gl-progress", self.demo_overview(None))

    def test_plotly_background_is_transparent_dark_glass_and_data_unchanged(self):
        st = MagicMock()
        curve = self.curve(10.0, 30.0)
        luxury.curve_chart(st, curve, "t", "c")
        fig = st.plotly_chart.call_args.args[0]
        self.assertEqual(fig.layout.paper_bgcolor, "rgba(0,0,0,0)")
        self.assertTrue(fig.layout.plot_bgcolor.startswith("rgba("))
        self.assertEqual(list(fig.data[0].y), [10.0, 30.0])


class ProcessStripTests(unittest.TestCase):
    def test_strip_reflects_the_selected_run_evidence(self):
        from test_floor_view import seed
        from ui.state import dashboard_database
        from ui.views import process
        from ui.i18n_es import STEPS as LABELS
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"AI_FLOOR_BROKER_DEMO_ROUTING": "0"}):
            os.environ.pop("AI_FLOOR_RESULTS_SOURCE", None)
            path = Path(tmp) / "p.db"
            seed(path)
            st = MagicMock()
            st.session_state = {}
            st.selectbox.side_effect = lambda _label, options, index=0, **_: list(options)[index]
            with dashboard_database(path) as db:
                process.render(st, db)
                run = next(r for r in luxury.records(db, "runs"))
                evidence, _gaps = process.cycle_evidence(db, run)
        bodies = [c.args[0] for c in st.markdown.call_args_list]
        strip = next(b for b in bodies if "gl-steps" in b)
        self.assertEqual(strip.count('<li class="gl-step'), 9)
        self.assertEqual(strip.count('class="gl-step done"'), sum(1 for rows in evidence.values() if rows))
        for step in process.STEPS:
            self.assertIn(LABELS[step], strip)
        self.assertLess(bodies.index(strip), next(i for i, b in enumerate(bodies) if "1. " in b))


class GlassCssTests(unittest.TestCase):
    def test_glass_layer_and_fallbacks_exist(self):
        self.assertIn("/* ===== Glass layer ===== */", CSS)
        for needle in ("@supports not ((backdrop-filter:blur(1px)) or (-webkit-backdrop-filter:blur(1px)))",
                       "@media(prefers-reduced-transparency:reduce)", "@media(prefers-reduced-motion:reduce)",
                       "backdrop-filter:var(--gl-filter)", "--gl-blur:20px", "saturate(150%)"):
            self.assertIn(needle, GLASS_CSS)
        self.assertIn("--gl-blur:14px", media_block(GLASS_CSS, "max-width:1024px"))
        motion = media_block(GLASS_CSS, "prefers-reduced-motion:reduce")
        self.assertIn("animation:none!important", motion)
        self.assertIn("transition:none!important", motion)
        transparency = media_block(GLASS_CSS, "prefers-reduced-transparency:reduce")
        self.assertIn("backdrop-filter:none!important", transparency)
        self.assertIn("--gl-base:#0d1416", transparency)

    def test_phone_blur_is_at_most_12px_and_kpis_are_one_column(self):
        phone = media_block(GLASS_CSS, "max-width:640px")
        blur = int(re.search(r"--gl-blur:(\d+)px", phone).group(1))
        self.assertLessEqual(blur, 12)
        self.assertGreaterEqual(blur, 8)
        self.assertIn(".tf .kpis{grid-template-columns:minmax(0,1fr)}", phone)

    def test_no_external_assets_media_or_imports(self):
        lowered = CSS.lower()
        for forbidden in ("video", "@import", "url(http", "http", "url("):
            self.assertNotIn(forbidden, lowered)

    def test_existing_rules_still_present(self):
        for rule in ('[data-testid="stAlertContainer"]{background:#0d1416!important', "/* Detail KPIs",
                     '[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked)'):
            self.assertIn(rule, CSS)

    def test_desktop_agent_panels_open_only_where_details_content_exists(self):
        gate = GLASS_CSS.split("@supports selector(::details-content){", 1)[1]
        desktop = media_block(gate, "min-width:900px")
        self.assertIn(".tf details.gl-agents > summary{display:none}", desktop)
        self.assertIn("::details-content{content-visibility:visible;display:block}", desktop)
        outside = GLASS_CSS.replace(gate, "")
        self.assertNotIn("summary{display:none}", outside)  # without support the summary stays tappable

    def test_tap_targets_and_sidebar_toggle(self):
        self.assertIn('header[data-testid="stHeader"] [data-testid="stToolbar"]{display:flex!important', GLASS_CSS)
        toggle = GLASS_CSS.split('[data-testid="stExpandSidebarButton"],[data-testid="stSidebarCollapseButton"] button{', 1)[1]
        self.assertIn("min-width:44px;min-height:44px", toggle.split("}", 1)[0])
        self.assertIn("min-height:44px", GLASS_CSS.split('[role="radiogroup"] label{', 1)[1].split("}", 1)[0])
        self.assertIn('content:"Menú"', GLASS_CSS)

    def test_manual_close_card_styles_both_steps(self):
        for key in (".st-key-manual-close-card", ".st-key-floor-manual-close button",
                    ".st-key-floor-manual-close-confirm button", ".st-key-floor-manual-close-cancel button"):
            self.assertIn(key, GLASS_CSS)

    def test_text_tokens_pass_wcag_aa_over_the_brightest_blob(self):
        def rgb(hex_):
            return [int(hex_[i:i + 2], 16) for i in (1, 3, 5)]

        def over(top, alpha, bottom):
            return [alpha * t + (1 - alpha) * b for t, b in zip(top, bottom)]

        def lum(c):
            c = [v / 255 for v in c]
            c = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
            return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]

        def ratio(a, b):
            hi, lo = sorted((lum(a), lum(b)), reverse=True)
            return (hi + 0.05) / (lo + 0.05)
        page = rgb(re.search(r"--gl-page:(#[0-9a-f]{6})", GLASS_CSS).group(1))
        base = [float(v) for v in re.search(r"--gl-base:rgba\(([^)]*)\)", GLASS_CSS).group(1).split(",")]
        fill = float(re.search(r"--gl-fill:rgba\(255,255,255,([.\d]+)\)", GLASS_CSS).group(1))
        blobs = GLASS_CSS.split(".stApp::before{", 1)[1].split("}", 1)[0]
        peaks = [([int(r), int(g), int(b)], float(a))
                 for r, g, b, a in re.findall(r"rgba\((\d+),(\d+),(\d+),(\.\d+)\)", blobs) if float(a) > 0]
        self.assertEqual(len(peaks), 4)  # gold, purple, blue, green
        # page text: over each blob's peak (the blobs sit in different corners and fade out at 70 % of their radius)
        backdrops = [over(colour, alpha, page) for colour, alpha in peaks]
        # panels: stricter, every peak stacked under the dark under-layer and the white glass fill
        stacked = page
        for colour, alpha in peaks:
            stacked = over(colour, alpha, stacked)
        panel = over([255, 255, 255], fill, over(base[:3], base[3], stacked))
        tokens = re.search(r"--gl-t1:(#\w+);--gl-t2:(#\w+);--gl-t3:(#\w+);--gl-gold:(#\w+);--gl-green:(#\w+);"
                           r"--gl-red:(#\w+);--gl-amber:(#\w+)", GLASS_CSS).groups()
        for token in tokens + ("#a9b8b6", "#ff6b85", "#19e3d3", "#9ff5ee"):
            for background in [panel] + backdrops:
                with self.subTest(token=token, background=[round(v) for v in background]):
                    self.assertGreaterEqual(ratio(rgb(token), background), 4.5)
        self.assertTrue(all(math.isfinite(v) for v in panel))


if __name__ == "__main__":
    unittest.main()
