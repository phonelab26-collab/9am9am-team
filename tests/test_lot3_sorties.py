"""Lot 3, point 10 : une sortie vide n'est pas un succès ; l'enveloppe d'erreur de Claude est lue."""
import json
import unittest

from orch.ceo import Ceo
from orch.claude_cli import read_result
from orch.procs import CmdResult
from tests.test_workers import TEXT, TOOL, FakeRunner, WorkersCase, events


class OpencodeEmptyOutputTest(WorkersCase):
    def run_with(self, out, code=0):
        r = FakeRunner([CmdResult(code, out, "")])
        return self.workers(r).run("opencode", "p", "mon/modele", False)

    def test_empty_output_with_exit_0_is_an_explicit_failure(self):
        out = self.run_with("")
        self.assertNotEqual(out.code, 0)
        self.assertIn("aucune sortie exploitable", out.text)

    def test_non_json_output_with_exit_0_is_an_explicit_failure(self):
        out = self.run_with("Segmentation noise, pas du JSON")
        self.assertNotEqual(out.code, 0)
        self.assertIn("aucune sortie exploitable", out.text)

    def test_a_text_is_a_success(self):
        self.assertEqual(self.run_with(events(TEXT("fait"))).code, 0)

    def test_a_tool_use_without_text_is_a_success(self):
        self.assertEqual(self.run_with(events(TOOL)).code, 0)

    def test_the_empty_run_is_not_a_dead_model(self):
        self.run_with("")
        self.assertEqual(self.cfg.dead_models(), set())


class ReadResultTest(unittest.TestCase):
    def test_error_envelope_without_result_key_is_an_error_with_tokens(self):
        got = read_result(json.dumps({"type": "result", "is_error": True, "subtype": "error_max_turns",
                                      "usage": {"input_tokens": 10, "output_tokens": 5}}))
        self.assertIsNone(got.text)
        self.assertEqual(got.tokens, 15)
        self.assertIn("error_max_turns", got.error)

    def test_error_envelope_keeps_its_tokens(self):
        got = read_result(json.dumps({"type": "result", "is_error": True, "result": "Crédit épuisé",
                                      "usage": {"input_tokens": 7}}))
        self.assertEqual((got.text, got.tokens, got.error), (None, 7, "Crédit épuisé"))

    def test_plain_output_is_still_kept_as_is(self):
        self.assertEqual(read_result("VERDICT: ok").text, "VERDICT: ok")


class CeoErrorMessageTest(unittest.TestCase):
    def test_failure_shows_the_envelope_message_not_the_raw_tail(self):
        env = json.dumps({"type": "result", "is_error": True, "result": "Credit balance is too low",
                          "usage": {"input_tokens": 3}})
        ceo = Ceo("/x/claude", "/tmp", lambda m: None, runner=lambda *a, **k: CmdResult(1, env, ""))
        ans = ceo.ask("p")
        self.assertIsNone(ans.text)
        self.assertIn("Credit balance is too low", ans.error)
        self.assertNotIn('"type"', ans.error)
        self.assertEqual(ans.tokens, 3)


if __name__ == "__main__":
    unittest.main()
