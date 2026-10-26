import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from orch.cli import main
from orch.cli_parser import build_parser as _parser
from orch.store import StoreError


class CliTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="orch-cli-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def run_cli(self, *argv, inputs=None):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            if inputs is None:
                rc = main(list(argv), self.root)
            else:
                with mock.patch("builtins.input", side_effect=inputs):
                    rc = main(list(argv), self.root)
        return rc, buf.getvalue()

    def tasks(self):
        data = json.loads((self.root / ".mini-orch" / "tasks.json").read_text(encoding="utf-8"))
        return data["tasks"] if isinstance(data, dict) else data

    def test_dry_run_commands(self):
        self.assertEqual(self.run_cli("--dry-run", "plan", "G")[0], 0)
        self.assertEqual(self.run_cli("--dry-run", "run")[0], 0)
        self.assertEqual(self.run_cli("--dry-run", "tick", "H")[0], 0)
        self.assertEqual([t["status"] for t in self.tasks()], ["done", "done"])
        rc, out = self.run_cli("status")
        self.assertEqual(rc, 0)
        self.assertIn("[2]", out)

    def test_gui_options_for_the_desktop_app(self):
        a = _parser().parse_args(["gui", "--no-browser", "--parent", "123"])
        self.assertEqual((a.no_browser, a.parent), (True, 123))
        b = _parser().parse_args(["gui"])
        self.assertEqual((b.no_browser, b.parent), (False, None))

    def test_chat_command(self):
        rc, out = self.run_cli("--dry-run", "chat", "Fais une démo")
        self.assertEqual(rc, 0)
        chat = json.loads((self.root / ".mini-orch" / "chat.json").read_text(encoding="utf-8"))
        self.assertEqual(chat[0]["role"], "user")
        self.assertEqual(chat[0]["text"], "Fais une démo")
        self.assertIn("ceo", [m["role"] for m in chat])

    def test_retry_command(self):
        (self.root / ".mini-orch").mkdir()
        (self.root / ".mini-orch" / "tasks.json").write_text(
            json.dumps([{"id": 1, "status": "failed", "attempts": 3}]), encoding="utf-8")
        self.assertEqual(self.run_cli("retry")[0], 0)
        self.assertEqual(self.tasks()[0]["status"], "todo")

    def test_bad_state_file_is_reported_not_crashing(self):
        (self.root / ".mini-orch").mkdir()
        (self.root / ".mini-orch" / "tasks.json").write_text(
            json.dumps([{"id": 1, "status": "todo"}, {"id": 1, "status": "todo"}]), encoding="utf-8")
        rc, out = self.run_cli("--dry-run", "run")
        self.assertEqual(rc, 1)
        self.assertIn("en double", out)

    def test_menu_status_then_quit(self):
        rc, out = self.run_cli(inputs=["4", "5"])
        self.assertEqual(rc, 0)
        self.assertGreaterEqual(out.count("[1] plan"), 2)

    def test_menu_eof_quits_cleanly(self):
        self.assertEqual(self.run_cli("menu", inputs=EOFError())[0], 0)

    def test_menu_unknown_choice_then_quit(self):
        rc, out = self.run_cli("--dry-run", inputs=["9", "5"])
        self.assertEqual(rc, 0)
        self.assertIn("Choix inconnu", out)

    def test_menu_plan_then_tick_choices(self):
        # [1] plan (demande l'objectif) puis [3] tick (plan + run en dry-run)
        rc, out = self.run_cli("--dry-run", inputs=["1", "G", "3", "H", "5"])
        self.assertEqual(rc, 0)
        self.assertGreaterEqual(out.count("[1] plan"), 3)

    def test_menu_run_and_retry_choices(self):
        rc, out = self.run_cli("--dry-run", inputs=["2", "7", "5"])
        self.assertEqual(rc, 0)
        self.assertGreaterEqual(out.count("[1] plan"), 3)

    def test_menu_selftest_choice_without_tests_folder(self):
        # Le dossier tests/ est introuvable à côté de l'exécutable : message, retour au menu.
        rc, out = self.run_cli("--dry-run", inputs=["6", "5"])
        self.assertEqual(rc, 0)
        self.assertIn("dossier tests/ introuvable", out)

    def test_menu_reports_store_errors_and_keeps_going(self):
        engine = mock.Mock()
        engine.status_lines.return_value = ["etat : 0"]
        engine.plan.side_effect = StoreError("taches inutilisables")
        with mock.patch("orch.cli.build", return_value=(engine, None)):
            rc, out = self.run_cli("--dry-run", inputs=["1", "G", "5"])
        self.assertEqual(rc, 0)
        self.assertIn("ERREUR : taches inutilisables", out)
        self.assertGreaterEqual(out.count("[1] plan"), 2)  # le menu a survécu

    def test_selftest_without_tests_folder_returns_1(self):
        rc, out = self.run_cli("selftest")
        self.assertEqual(rc, 1)
        self.assertIn("dossier tests/ introuvable", out)

    def test_store_error_returns_1(self):
        with mock.patch("orch.cli.build", side_effect=StoreError("panne")):
            rc, out = self.run_cli("status")
        self.assertEqual(rc, 1)
        self.assertIn("ERREUR : panne", out)

    def test_keyboard_interrupt_returns_130(self):
        with mock.patch("orch.cli.build", side_effect=KeyboardInterrupt):
            rc, out = self.run_cli("status")
        self.assertEqual(rc, 130)
        self.assertIn("Interrompu", out)

    def test_journal_file_written(self):
        self.run_cli("--dry-run", "plan", "G")
        log = (self.root / ".mini-orch" / "journal.log").read_text(encoding="utf-8")
        self.assertIn("Plan CEO", log)


if __name__ == "__main__":
    unittest.main()
