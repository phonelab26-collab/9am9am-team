"""Lot 1 — une erreur interne se voit dans la conversation, et le serveur répond toujours
une erreur lisible au lieu de couper la connexion (« Serveur injoignable » à tort)."""
import json
import shutil
import tempfile
from pathlib import Path

from orch.ceo import CeoAnswer
from orch.gui_controller import GuiController
from orch.procs import CmdResult
from tests.test_engine import EngineCase
from tests.test_gui_controller import drain
from tests.test_webui import WebUiCase

import unittest


class JobErrorInChatTest(unittest.TestCase):
    def test_an_exception_in_a_job_is_said_in_the_conversation(self):
        root = Path(tempfile.mkdtemp(prefix="orch-err-"))
        self.addCleanup(shutil.rmtree, root, True)
        ctl = GuiController(root, dry_run=True, runner=lambda *a, **k: CmdResult(0, "", ""))
        self.addCleanup(ctl.close, 5)

        def boom(cancel):
            raise RuntimeError("tasks.json illisible (id en double) OPENAI_API_KEY=sk-live-77")
        self.assertTrue(ctl.start("Conversation", boom))
        drain(ctl)
        errors = [m for m in ctl.engine.chat.since(0) if m.get("kind") == "error"]
        self.assertEqual(len(errors), 1)
        self.assertIn("id en double", errors[0]["text"])
        self.assertIn("Conversation", errors[0]["text"])
        self.assertNotIn("sk-live", errors[0]["text"])
        self.assertNotIn("Rien d'autre n'a été fait", errors[0]["text"])  # faux après quelques tâches faites


class ReportFailureInChatTest(EngineCase):
    def test_a_final_report_that_cannot_be_written_is_said_in_the_conversation(self):
        self.ceo.report_text = CeoAnswer(None, "CEO indisponible (délai dépassé) : x")
        self.engine().converse("Fais A puis B")
        errors = [m for m in self.msgs() if m.get("kind") == "error"]
        self.assertEqual(len(errors), 1)
        self.assertIn("rapport", errors[0]["text"])
        self.assertIn("délai dépassé", errors[0]["text"])


class ReportFailsOnceTest(EngineCase):
    def test_a_failing_report_is_asked_and_said_only_once(self):
        self.ceo.report_text = CeoAnswer(None, "CEO indisponible (code 1) : x")
        self.ceo.answers = [CeoAnswer(None, "CEO indisponible (code 1) : y"), "VERDICT: ok\nBien."]
        self.engine().converse("Fais A puis B")
        self.assertEqual(self.ceo.reports, 1)
        self.assertEqual(len([m for m in self.msgs() if m.get("kind") == "error"]), 1)


class ServerSafetyNetTest(WebUiCase):
    def test_an_unexpected_server_error_returns_a_json_500_not_a_cut_connection(self):
        chat = self.root / ".mini-orch" / "chat.json"
        chat.parent.mkdir(parents=True, exist_ok=True)
        chat.write_text(json.dumps([{"id": "pas-un-nombre", "role": "user", "text": "a"}]), encoding="utf-8")
        status, raw, headers = self.request("GET", "/api/state?since=0")
        self.assertEqual(status, 500)
        self.assertIn("application/json", headers.get("Content-Type", ""))
        self.assertIn("erreur interne", json.loads(raw)["error"])
        self.assertNotIn("pas-un-nombre", json.loads(raw)["error"])  # le détail va au journal, pas à la page
        chat.unlink()
        self.assertEqual(self.request("GET", "/api/state?since=0")[0], 200)  # le serveur a survécu


class PageTellsServerErrorsFromOfflineTest(unittest.TestCase):
    def test_refresh_shows_the_server_error_and_keeps_offline_for_network_failures(self):
        import re
        js = (Path(__file__).resolve().parents[1] / "orch" / "web" / "app.js").read_text(encoding="utf-8")
        body = re.search(r"async function refresh\(\) \{(.*?)\n\}\n", js, re.S).group(1)
        self.assertIn("res.data.error", body)            # erreur du serveur : son message
        self.assertIn("Serveur injoignable", body)       # réseau coupé : inchangé
        self.assertIn("Erreur d'affichage", body)        # bug d'affichage : dit comme tel
