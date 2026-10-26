"""Lot 1 — une commande d'agent qui n'est pas du shell valide (ex. une ligne de tâche collée)
n'est ni enregistrée, ni proposée au CEO : sinon chaque essai plante."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from orch.config import Bins, ConfigStore, Paths
from orch.shellcheck import shell_syntax_error
from orch.workers import Workers
from tests.test_webui import WebUiCase

BAD = "[5] done        (opencode) Définir le contenu du plan de test"  # vraie valeur trouvée à l'audit
GOOD = 'cline task new --yolo "$ORCH_PROMPT"'


class SyntaxCheckTest(unittest.TestCase):
    def test_a_pasted_task_line_is_rejected_with_the_shell_message(self):
        self.assertIn("syntax", shell_syntax_error(BAD).lower())

    def test_a_nul_byte_is_refused_not_a_crash(self):
        self.assertIn("nul", shell_syntax_error("abc\x00def"))

    def test_valid_commands_pass_and_are_never_executed(self):
        marker = Path(tempfile.mkdtemp(prefix="orch-sh-")) / "ran"
        self.addCleanup(shutil.rmtree, marker.parent, True)
        for cmd in (GOOD, f"touch {marker}", "FOO=1 mon-cli run && echo fini"):
            with self.subTest(cmd=cmd):
                self.assertEqual(shell_syntax_error(cmd), "")
        self.assertFalse(marker.exists(), "sh -n ne doit rien exécuter")


class AvailableWorkersTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="orch-shw-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.logs = []
        self.cfg = ConfigStore(Paths.for_root(self.dir), self.logs.append)
        self.workers = Workers(Paths.for_root(self.dir), Bins(claude="/x/claude", opencode="/x/opencode"),
                               self.cfg, self.logs.append)

    def test_a_shell_worker_with_an_invalid_command_is_not_offered_and_the_reason_is_logged_once(self):
        self.cfg.update(cline_cmd=BAD)
        self.assertNotIn("cline", self.workers.available())
        self.assertNotIn("cline", self.workers.available())
        said = [m for m in self.logs if "cline" in m and "syntaxe" in m]
        self.assertEqual(len(said), 1, self.logs)

    def test_a_valid_command_is_offered(self):
        self.cfg.update(cline_cmd=GOOD)
        self.assertIn("cline", self.workers.available())

    def test_a_custom_agent_with_an_invalid_command_is_not_offered(self):
        self.cfg.update(custom_agents=[{"name": "mon-api", "cmd": "run (", "model": "", "effort": ""}])
        self.assertNotIn("mon-api", self.workers.available())


class SettingRejectsInvalidShellTest(WebUiCase):
    def test_saving_an_invalid_worker_command_is_refused_with_the_reason(self):
        status, raw, _ = self.request("POST", "/api/setting", {"key": "cline_cmd", "value": BAD})
        self.assertEqual(status, 400)
        self.assertIn("syntaxe", json.loads(raw)["error"])
        self.assertEqual(self.request("POST", "/api/setting", {"key": "cline_cmd", "value": GOOD})[0], 200)

    def test_a_nul_byte_in_a_command_is_a_400_not_a_500(self):
        self.assertEqual(self.request("POST", "/api/setting", {"key": "cline_cmd", "value": "a\x00b"})[0], 400)
        self.assertEqual(self.request("POST", "/api/custom_agent", {"name": "x-a", "cmd": "a\x00b", "model": ""})[0], 400)

    def test_adding_a_custom_agent_with_an_invalid_command_is_refused(self):
        status, raw, _ = self.request("POST", "/api/custom_agent", {"name": "mon-api", "cmd": "run (", "model": ""})
        self.assertEqual(status, 400)
        self.assertIn("syntaxe", json.loads(raw)["error"])


if __name__ == "__main__":
    unittest.main()
