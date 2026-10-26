"""Lot 3, point 11 : ce que la page doit montrer — serveur (selftest, curseur) et page (app.js)."""
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from orch.gui_controller import GuiController
from orch.procs import CmdResult

WEB = Path(__file__).resolve().parents[1] / "orch" / "web"
JS = (WEB / "app.js").read_text(encoding="utf-8")


class ServerSideTest(unittest.TestCase):
    def ctl(self, result):
        root = Path(tempfile.mkdtemp(prefix="orch-ui3-"))
        self.addCleanup(shutil.rmtree, root, True)
        ctl = GuiController(root, dry_run=True, runner=lambda *a, **k: result)
        self.addCleanup(ctl.close, 5)
        return ctl

    def test_selftest_result_is_shown_in_the_conversation(self):
        ctl = self.ctl(CmdResult(0, "Selftest : tout est OK (3 tests).", ""))
        self.assertEqual(ctl.selftest_job(__import__("threading").Event()), 0)
        [m] = [m for m in ctl.engine.chat.since(0) if m.get("kind") == "selftest"]
        self.assertIn("réussi", m["text"])
        self.assertIn("tout est OK", m["text"])
        self.assertTrue(m["data"]["ok"])

    def test_failed_selftest_says_failed_with_the_code(self):
        ctl = self.ctl(CmdResult(1, "FAILED (failures=2)", ""))
        self.assertEqual(ctl.selftest_job(__import__("threading").Event()), 1)
        [m] = [m for m in ctl.engine.chat.since(0) if m.get("kind") == "selftest"]
        self.assertIn("échoué", m["text"])
        self.assertIn("1", m["text"])
        self.assertFalse(m["data"]["ok"])

    def test_ids_going_backwards_are_signalled_so_the_page_reloads(self):
        ctl = self.ctl(CmdResult(0, "", ""))
        for _ in range(3):
            ctl.engine.chat.add("event", "x")
        ok = ctl.state_view(2)
        self.assertFalse(ok.get("chat_reset"))
        behind = ctl.state_view(50)  # le curseur de la page est devant : le journal a été remis à zéro
        self.assertTrue(behind["chat_reset"])
        self.assertEqual(behind["chat_last"], 3)


class PageTest(unittest.TestCase):
    def test_app_js_has_no_syntax_error(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js absent")
        subprocess.run([node, "--check", str(WEB / "app.js")], check=True, capture_output=True, timeout=30)

    def test_the_verifying_badge_is_resolved_by_check_and_stop_events(self):
        self.assertRegex(JS, r'case "check":')
        self.assertRegex(JS, r'case "stopped":')

    def test_older_blocked_tasks_are_never_hidden_and_the_cut_is_said(self):
        self.assertNotRegex(JS, r"state\.tasks\.slice\(-12\)\)")
        self.assertIn("plus ancienne", JS)

    def test_file_warnings_are_not_wiped_by_the_next_banner_clear(self):
        self.assertRegex(JS, r'dataset\.kind !== "files"')

    def test_risky_handlers_are_guarded(self):
        for anchor in ('$("auth-logout").addEventListener', '$("ca-add").addEventListener'):
            body = JS[JS.index(anchor):][:900]
            self.assertIn("try {", body, anchor)
            self.assertIn("catch", body, anchor)
        i = JS.index('op: "delete", name: a.name')
        self.assertIn("try {", JS[i - 200:i])

    def test_repos_are_not_reloaded_at_every_poll(self):
        self.assertIn("reposLoading", JS)
        self.assertRegex(JS, r"catch \(e\) \{\s*\n\s*reposLoaded = true")

    def test_the_page_reloads_when_the_conversation_restarts(self):
        self.assertIn("chat_reset", JS)

    def test_the_model_really_used_and_attached_files_are_shown(self):
        self.assertIn("d.model", JS)
        self.assertIn("m.data.files", JS)

    def test_no_innerhtml(self):
        self.assertNotIn("innerHTML", JS)


if __name__ == "__main__":
    unittest.main()
