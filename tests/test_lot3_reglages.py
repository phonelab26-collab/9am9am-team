"""Lot 3, points 3-5 : compte Claude, config abîmée, mode cloud effectif — la vérité, dite."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from orch.ceo import Ceo
from orch.chat import ChatStore
from orch.config import ConfigStore, Paths
from orch.engine import Engine
from orch.gui_controller import GuiController
from orch.procs import CmdResult
from orch.store import Store
from tests.test_engine import FakeCeo, FakeWorkers


class AuthStatusTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="orch-auth-"))
        self.ctl = GuiController(self.root, dry_run=True, runner=lambda *a, **k: CmdResult(0, "", ""))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addCleanup(self.ctl.close, 5)

    def status(self, result):
        with mock.patch("orch.gui_controller.run_cmd", return_value=result):
            return self.ctl.auth_status()

    def test_missing_binary_is_not_reported_as_logged_out(self):
        got = self.status(CmdResult(127, "", "binaire introuvable : claude"))
        self.assertFalse(got["ok"])
        self.assertNotIn("loggedIn", got)
        self.assertIn("introuvable", got["error"])
        self.assertNotIn("non connecté", got["error"])

    def test_timeout_is_said_as_a_timeout(self):
        got = self.status(CmdResult(124, "", "", timed_out=True))
        self.assertNotIn("loggedIn", got)
        self.assertIn("délai", got["error"])

    def test_a_crash_shows_its_message_and_is_not_logged_out(self):
        got = self.status(CmdResult(2, "", "segfault"))
        self.assertNotIn("loggedIn", got)
        self.assertIn("segfault", got["error"])
        self.assertNotIn("non connecté", got["error"])

    def test_silent_failure_still_says_the_code(self):
        got = self.status(CmdResult(3, "", ""))
        self.assertIn("3", got["error"])
        self.assertNotIn("loggedIn", got)

    def test_really_logged_out_is_still_logged_out(self):
        for code in (0, 1):
            got = self.status(CmdResult(code, json.dumps({"loggedIn": False}), ""))
            self.assertEqual((got["ok"], got.get("loggedIn")), (False, False))
            self.assertIn("non connecté", got["error"])

    def test_logged_in(self):
        got = self.status(CmdResult(0, json.dumps({"loggedIn": True, "email": "a@b.c"}), ""))
        self.assertEqual((got["ok"], got["email"]), (True, "a@b.c"))


class DamagedConfigTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="orch-cfg-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.paths = Paths.for_root(self.dir)
        self.paths.state.mkdir(parents=True)
        self.logs = []

    def cfg(self):
        return ConfigStore(self.paths, self.logs.append)

    def test_unreadable_config_turns_the_paid_escalation_off(self):
        self.paths.config.write_text("{pas du json", encoding="utf-8")
        cfg = self.cfg()
        self.assertFalse(cfg.escalation_enabled())
        self.assertFalse(self.cfg().escalation_enabled())  # et ça tient au lancement suivant
        self.assertEqual(self.cfg().load().get("escalation"), "off")

    def test_non_object_config_is_handled_the_same_way(self):
        self.paths.config.write_text("[1, 2]", encoding="utf-8")
        self.assertFalse(self.cfg().escalation_enabled())

    def test_a_healthy_or_missing_config_keeps_the_default_on(self):
        self.assertTrue(self.cfg().escalation_enabled())
        self.paths.config.write_text(json.dumps({"ceo_model": "opus"}), encoding="utf-8")
        self.assertTrue(self.cfg().escalation_enabled())

    def test_the_notice_names_the_kept_file_without_a_system_path(self):
        self.paths.config.write_text("{pas du json", encoding="utf-8")
        cfg = self.cfg()
        cfg.load()
        notices = cfg.pop_notices()
        self.assertEqual(len(notices), 1)
        self.assertIn("config.json.corrupt-", notices[0])
        self.assertIn("Claude Code", notices[0])
        self.assertNotIn(str(self.dir), notices[0])
        self.assertEqual(cfg.pop_notices(), [])  # dit une fois

    def test_user_can_turn_escalation_back_on(self):
        self.paths.config.write_text("{pas du json", encoding="utf-8")
        cfg = self.cfg()
        cfg.load()
        cfg.update(escalation="on")
        self.assertTrue(cfg.escalation_enabled())

    def test_invalid_effort_is_reported_not_swallowed(self):
        self.paths.config.write_text(json.dumps({"ceo_effort": "turbo"}), encoding="utf-8")
        cfg = self.cfg()
        self.assertEqual(cfg.ceo_effort(), "")
        self.assertEqual(cfg.ceo_effort_raw(), "turbo")
        problems = cfg.setting_problems()
        self.assertEqual(len(problems), 1)
        self.assertIn("turbo", problems[0])
        self.assertIn("effort", problems[0])

    def test_valid_settings_raise_no_problem(self):
        self.paths.config.write_text(json.dumps({"ceo_effort": "élevé"}), encoding="utf-8")
        self.assertEqual(self.cfg().setting_problems(), [])


class EngineSaysItTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="orch-eng3-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.paths = Paths.for_root(self.dir)
        self.paths.state.mkdir(parents=True)
        self.logs = []
        self.chat = ChatStore(self.paths, self.logs.append)

    def engine(self, ceo=None):
        return Engine(self.paths, Store(self.paths, self.logs.append), ConfigStore(self.paths, self.logs.append),
                      FakeWorkers(claude=True), ceo or FakeCeo(), self.logs.append, chat=self.chat)

    def errors(self):
        return [m["text"] for m in self.chat.since(0) if m.get("kind") == "error"]

    def test_damaged_config_is_announced_in_the_conversation(self):
        self.paths.config.write_text("{pas du json", encoding="utf-8")
        ceo = FakeCeo()
        ceo.chat_default = json.dumps({"reply": "Bonjour.", "tasks": []})
        self.engine(ceo).converse("Salut")
        said = self.errors()
        self.assertEqual(len(said), 1)
        self.assertIn("illisible", said[0])
        self.assertIn("désactivée", said[0])

    def test_invalid_effort_is_announced_once_per_engine(self):
        self.paths.config.write_text(json.dumps({"ceo_effort": "turbo"}), encoding="utf-8")
        ceo = FakeCeo()
        ceo.chat_default = json.dumps({"reply": "Bonjour.", "tasks": []})
        eng = self.engine(ceo)
        eng.converse("Salut")
        eng.converse("Re-salut")
        said = self.errors()
        self.assertEqual(len(said), 1)
        self.assertIn("turbo", said[0])

    def test_build_hands_the_raw_effort_to_the_ceo_so_its_warning_can_fire(self):
        from orch.cli import build
        self.paths.config.write_text(json.dumps({"ceo_effort": "turbo"}), encoding="utf-8")
        engine, _ = build(self.dir, None)
        self.assertEqual(engine.ceo._effort(), "turbo")
        logs = []
        engine.ceo._log = logs.append
        self.assertEqual(engine.ceo._effort_args(), [])
        self.assertTrue(any("effort" in line and "invalide" in line for line in logs))


class CloudModeTest(unittest.TestCase):
    def ceo(self, mode, session=""):
        self.logs = []
        return Ceo("/x/claude", "/tmp", self.logs.append, runner=lambda *a, **k: CmdResult(0, "", ""),
                   mode=lambda: mode, cloud_session=lambda: session)

    def test_cloud_without_session_runs_local_and_says_why(self):
        ceo = self.ceo("cloud", "")
        self.assertEqual(ceo.effective_mode(), "local")
        self.assertIn("session", ceo.fallback_note())
        self.assertIn("local", ceo.fallback_note())

    def test_cloud_with_a_session_is_cloud_with_no_note(self):
        ceo = self.ceo("cloud", "session_abc123")
        self.assertEqual((ceo.effective_mode(), ceo.fallback_note()), ("cloud", ""))

    def test_local_has_no_note(self):
        ceo = self.ceo("local")
        self.assertEqual((ceo.effective_mode(), ceo.fallback_note()), ("local", ""))


class CloudViewTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="orch-cloud-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.ctl = GuiController(self.root, dry_run=True, runner=lambda *a, **k: CmdResult(0, "", ""))
        self.addCleanup(self.ctl.close, 5)

    def test_the_page_shows_the_effective_mode_not_the_setting(self):
        self.ctl.save_setting("ceo_mode", "cloud")
        ceo = self.ctl.team_view([])["ceo"]
        self.assertEqual(ceo["mode"], "local")
        self.assertEqual(ceo["mode_setting"], "cloud")
        self.assertIn("local", ceo["where"])
        self.assertNotIn("cloud", ceo["where"])
        self.assertTrue(ceo["fallback"])

    def test_with_a_valid_session_it_is_cloud(self):
        self.ctl.save_setting("ceo_cloud_session", "session_abc123")
        self.ctl.save_setting("ceo_mode", "cloud")
        ceo = self.ctl.team_view([])["ceo"]
        self.assertEqual((ceo["mode"], ceo["fallback"]), ("cloud", ""))
        self.assertIn("cloud", ceo["where"])

    def test_the_engine_says_the_fallback_in_the_conversation(self):
        self.ctl.save_setting("ceo_mode", "cloud")
        eng = self.ctl.engine
        eng.converse("Salut", dry_run=True)
        texts = [m["text"] for m in eng.chat.since(0) if m.get("kind") == "error"]
        self.assertTrue(any("cloud" in t and "local" in t for t in texts), texts)

    def test_settings_warn_that_cloud_mode_drops_the_read_only_restriction(self):
        html = (Path(__file__).resolve().parents[1] / "orch" / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn("lecture seule", html)
        self.assertIn('id="cloud-readonly-warning"', html)


if __name__ == "__main__":
    unittest.main()
