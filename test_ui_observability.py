"""Control receipt, health gap text, and narrow history cards. Synthetic facts only."""
from datetime import datetime, timezone
from pathlib import Path
import os
import unittest
from contextlib import closing
from unittest.mock import patch

from ui.observability import (age_phrase, health_gap_note, history_card, mode_label, not_checked_suffix,
                              transition_status_lines)


NOW = datetime(2026, 11, 8, 1, 2, tzinfo=timezone.utc)  # 7 Nov 20:02 ET


class TransitionLines(unittest.TestCase):
    def test_configured_xai_is_not_described_as_unchanged(self):
        lines = transition_status_lines(
            recorded={"from": "openai", "to": "xai", "at": "7 nov 19:32 ET"},
            configured="xai",
            cycle={"mode": "openai", "at": "Ciclo del 6 nov 16:45 ET"},
            fence="UNKNOWN")
        text = "\n".join(lines)
        self.assertIn("OpenAI → xAI", text)
        self.assertIn("7 nov 19:32 ET", text)
        self.assertIn("Proveedor configurado en este proceso: xAI", text)
        self.assertIn("coincide con el destino", text)
        self.assertIn("Proveedor del último ciclo guardado: OpenAI", text)
        self.assertIn("no el configurado ahora", text)
        self.assertIn("Valla de entradas: UNKNOWN", text)
        self.assertNotIn("no cambió", text)
        self.assertNotIn("fingerprint", text.lower())

    def test_fence_comes_only_from_the_explicit_flag(self):
        on = transition_status_lines(recorded=None, configured="xai", cycle=None, fence="on")
        off = transition_status_lines(recorded=None, configured="xai", cycle=None, fence="off")
        self.assertIn("Valla de entradas: activa", "\n".join(on))
        self.assertIn("Valla de entradas: no activa", "\n".join(off))
        self.assertIsNone(mode_label("sk-secret"))
        self.assertIsNone(mode_label("101-001"))

    def test_unknown_mode_is_not_echoed(self):
        lines = transition_status_lines(recorded={"from": "openai", "to": "sk-live"}, configured="??",
                                        cycle=None, fence="maybe")
        text = "\n".join(lines)
        self.assertIn("Paso anotado: UNKNOWN", text)
        self.assertIn("Proveedor configurado en este proceso: UNKNOWN", text)
        self.assertNotIn("sk-live", text)
        self.assertNotIn("??", text)


class HealthGap(unittest.TestCase):
    def test_a_live_heartbeat_is_not_the_first_cycle(self):
        note = health_gap_note(observations=[], heartbeat="7 nov 19:39 ET · hace 23 min",
                               last_cycle="6 nov 16:45 ET · hace 27 h 17 min", now=NOW, sessions_open=False)
        self.assertNotIn("Esperando el primer ciclo", note)
        self.assertIn("no un fallo", note)
        self.assertIn("19:39", note)
        self.assertIn("no tiene una sesión abierta", note)
        self.assertNotIn("próximo", note)
        self.assertNotIn("proximo", note)

    def test_no_facts_still_says_waiting(self):
        self.assertEqual(health_gap_note(observations=[object()], heartbeat=None, last_cycle=None,
                                         now=NOW, sessions_open=False), None)
        self.assertEqual(health_gap_note(observations=[], heartbeat=None, last_cycle=None,
                                         now=NOW, sessions_open=None), "Esperando el primer ciclo")

    def test_age_and_not_checked(self):
        self.assertEqual(age_phrase("2026-11-08T00:39:00+00:00", NOW), "hace 23 min")
        self.assertIsNone(age_phrase("no es una hora", NOW))
        self.assertIn("no es un fallo", not_checked_suffix("NO_DATA"))
        self.assertIn("no es un fallo", not_checked_suffix("NOT_CHECKED"))
        self.assertEqual(not_checked_suffix("ERROR"), "")


class HistoryCards(unittest.TestCase):
    def test_openai_on_an_old_cycle_is_labeled_historical(self):
        card = history_card({"symbol": "EURUSD", "when": "Ciclo del 6 nov 16:45 ET",
                             "status": "Completado", "provider": "openai"})
        self.assertEqual(card["title"], "EURUSD · Ciclo del 6 nov 16:45 ET")
        self.assertIn("Proveedor de ese ciclo: OpenAI", card["lines"])
        self.assertIn("No es el proveedor configurado ahora.", card["lines"][-1])

    def test_missing_provider_stays_unknown(self):
        card = history_card({"symbol": "", "when": "", "status": "", "provider": "gpt-secret"})
        self.assertIn("UNKNOWN", card["title"])
        self.assertIn("Proveedor de ese ciclo: UNKNOWN", card["lines"])
        self.assertNotIn("gpt-secret", " ".join(card["lines"]))


class ScreenContracts(unittest.TestCase):
    def test_control_copy_no_longer_claims_the_provider_is_unchanged(self):
        source = Path("ui/views/control.py").read_text(encoding="utf-8")
        self.assertNotIn("proveedor activo no cambió", source)
        self.assertIn("provider_observation", source)

    def test_narrow_history_is_hidden_on_desktop_and_the_rail_does_not_pad_390(self):
        css = Path("ui/static/theme.css").read_text(encoding="utf-8")
        self.assertIn("@media(max-width:480px)", css)
        self.assertIn(".st-key-history-wide{display:none!important}", css)
        self.assertIn("@media(min-width:481px){.aif-history-narrow{display:none!important}}", css)
        self.assertIn("padding-left:14px!important", css)

    def test_floor_legend_is_readable_without_the_canvas(self):
        html = Path("ui/static/floor/floor.html").read_text(encoding="utf-8")
        self.assertIn("#legend{display:flex", html)
        self.assertIn("font-size:13px", html)


class ObservationRead(unittest.TestCase):
    def test_reads_the_recorded_step_and_does_not_invent_the_fence(self):
        from storage.database import Store
        from runtime.provider_transition import record_provider_transition
        from runtime.config import RuntimeConfig
        from dataclasses import replace
        from ui.views.control import provider_observation
        path = Path("data/runtime") / "obs-view-test.db"
        path.unlink(missing_ok=True)
        self.addCleanup(path.unlink, missing_ok=True)
        origin = replace(RuntimeConfig(db_path=path, ai_provider_mode="openai"), scheduler_enabled=True)
        dest = replace(origin, ai_provider_mode="xai")
        with closing(Store(path)) as store:
            record_provider_transition(
                store.db, from_payload=origin.fingerprint_content(), to_payload=dest.fingerprint_content(),
                flat_counts={"paper_open": 0, "paper_pending": 0, "broker_live": 0},
                operator_note="reviewed", git_commit="a" * 40, starting_equity=10000.0, actor="owner",
                recorded_at=datetime(2026, 11, 8, 0, 32, tzinfo=timezone.utc))
            store.db.execute(
                "INSERT INTO runs(slot_key,symbol,as_of,started_at,status,provider_metadata) "
                "VALUES(?,?,?,?,?,?)",
                ("slot", "EURUSD", "2026-11-06T21:45:00+00:00", "2026-11-06T21:45:00+00:00",
                 "COMPLETED", '{"provider":"openai","model":"hidden-model"}'))
            store.db.commit()
        env = {"AI_FLOOR_DB_PATH": str(path), "AI_FLOOR_AI_PROVIDER": "xai"}
        with patch.dict(os.environ, env, clear=False):
            lines = provider_observation(env)
        text = "\n".join(lines)
        self.assertIn("OpenAI → xAI", text)
        self.assertIn("19:32 ET", text)
        self.assertIn("Proveedor configurado en este proceso: xAI", text)
        self.assertIn("OpenAI", text)
        self.assertIn("Valla de entradas:", text)
        self.assertNotIn("hidden-model", text)
        self.assertNotIn("no cambió", text)
