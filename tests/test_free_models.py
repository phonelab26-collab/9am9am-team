"""Scan des models gratuits d'opencode à l'ouverture de l'interface."""
import threading
import unittest

from orch.free_models import FreeModelScanner, free_models, is_free_model
from orch.procs import CmdResult

CATALOG = "\n".join([
    "9router/code-free",
    "9router/code-pro",
    "apineex/free/glm-5.3-flash",
    "browserfree/gpt-6-astra",
    "google/gemini-2.5-pro",
    "opencode/big-pickle",
    "opencode/space-bunny-free",
    "opencode/gpt-6-pro",
    "openrouter/google/gemma-4-31b-it:free",
    "openrouter/freedom/model",
    "  opencode/step-5-preview-free  ",
    "ligne de bruit sans slash",
    "-opencode/option-free",
])


class IsFreeModelTest(unittest.TestCase):
    def test_a_free_word_in_the_name_or_the_provider_marks_it_free(self):
        for name in ("9router/code-free", "apineex/free/glm-5.3-flash", "browserfree/gpt-6-astra",
                     "openrouter/google/gemma-4-31b-it:free", "opencode/space-bunny-free"):
            with self.subTest(name=name):
                self.assertTrue(is_free_model(name))

    def test_known_free_models_without_the_word_are_kept(self):
        self.assertTrue(is_free_model("opencode/big-pickle"))

    def test_paid_or_lookalike_names_are_not_free(self):
        for name in ("9router/code-pro", "google/gemini-2.5-pro", "openrouter/freedom/model",
                     "opencode/gpt-6-pro", "-opencode/option-free", "", "free"):
            with self.subTest(name=name):
                self.assertFalse(is_free_model(name))


class FreeModelsTest(unittest.TestCase):
    def test_only_free_models_of_the_catalog_opencode_zen_first(self):
        self.assertEqual(free_models(CATALOG), [
            "opencode/big-pickle",
            "opencode/space-bunny-free",
            "opencode/step-5-preview-free",
            "9router/code-free",
            "apineex/free/glm-5.3-flash",
            "browserfree/gpt-6-astra",
            "openrouter/google/gemma-4-31b-it:free",
        ])

    def test_duplicates_are_listed_once(self):
        self.assertEqual(free_models("opencode/a-free\nopencode/a-free\n"), ["opencode/a-free"])


class ScannerTest(unittest.TestCase):
    def make(self, result, binary="/bin/opencode"):
        self.calls, self.logs = [], []

        def runner(cmd, timeout, cwd=None, **kw):
            self.calls.append(cmd)
            if isinstance(result, Exception):
                raise result
            return result
        return FreeModelScanner(lambda: binary, "/tmp", self.logs.append, runner=runner,
                                clock=lambda: 0.0)

    def test_scan_reads_the_catalog_and_keeps_free_models(self):
        scanner = self.make(CmdResult(0, CATALOG, ""))
        view = scanner.scan()
        self.assertEqual(self.calls, [["/bin/opencode", "models"]])
        self.assertEqual(view["status"], "ok")
        self.assertEqual(view["models"][0], "opencode/big-pickle")
        self.assertEqual(len(view["models"]), 7)
        self.assertEqual(view["error"], "")
        self.assertTrue(any("7 models gratuits" in line for line in self.logs))

    def test_failure_is_explained_never_hidden(self):
        scanner = self.make(CmdResult(1, "", "Cannot connect to API"))
        view = scanner.scan()
        self.assertEqual(view["status"], "error")
        self.assertEqual(view["models"], [])
        self.assertIn("Cannot connect to API", view["error"])
        self.assertTrue(any("Cannot connect to API" in line for line in self.logs))

    def test_timeout_is_reported_as_such(self):
        view = self.make(CmdResult(-9, "", "", timed_out=True)).scan()
        self.assertEqual(view["status"], "error")
        self.assertIn("délai dépassé", view["error"])

    def test_missing_opencode_is_explained_without_running_anything(self):
        scanner = self.make(CmdResult(0, CATALOG, ""), binary="")
        view = scanner.scan()
        self.assertEqual(self.calls, [])
        self.assertEqual(view["status"], "error")
        self.assertIn("opencode introuvable", view["error"])

    def test_unexpected_crash_becomes_an_error_state(self):
        view = self.make(RuntimeError("boum")).scan()
        self.assertEqual(view["status"], "error")
        self.assertIn("boum", view["error"])

    def test_a_failed_rescan_keeps_the_last_good_list_and_says_so(self):
        scanner = self.make(CmdResult(0, CATALOG, ""))
        scanner.scan()
        scanner._run = lambda cmd, timeout, cwd=None, **kw: CmdResult(1, "", "panne")
        view = scanner.scan()
        self.assertEqual(view["status"], "error")
        self.assertEqual(len(view["models"]), 7)

    def test_empty_free_list_is_ok_but_empty(self):
        view = self.make(CmdResult(0, "google/gemini-2.5-pro\n", "")).scan()
        self.assertEqual((view["status"], view["models"]), ("ok", []))

    def test_unreadable_catalog_is_an_error_not_an_empty_success(self):
        view = self.make(CmdResult(0, "", "")).scan()
        self.assertEqual(view["status"], "error")
        self.assertIn("vide ou illisible", view["error"])

    def test_stop_cancels_the_running_command(self):
        seen = []

        def runner(cmd, timeout, cwd=None, cancel=None, **kw):
            seen.append(cancel)
            return CmdResult(-15, "", "", cancelled=cancel.is_set())
        scanner = FreeModelScanner(lambda: "/bin/opencode", "/tmp", lambda m: None, runner=runner)
        scanner.stop()
        view = scanner.scan()
        self.assertTrue(seen[0].is_set())
        self.assertIn("annulé", view["error"])

    def test_view_is_a_copy(self):
        scanner = self.make(CmdResult(0, CATALOG, ""))
        scanner.scan()
        scanner.view()["models"].clear()
        self.assertEqual(len(scanner.view()["models"]), 7)

    def test_idle_before_any_scan_and_skip_says_why(self):
        scanner = self.make(CmdResult(0, CATALOG, ""))
        self.assertEqual(scanner.view()["status"], "idle")
        scanner.skip("mode démo : scan non lancé.")
        self.assertEqual(scanner.view(), {"status": "off", "models": [], "error": "mode démo : scan non lancé.",
                                          "at": ""})
        self.assertEqual(self.calls, [])

    def test_start_runs_in_background_and_refuses_a_second_scan_meanwhile(self):
        gate, entered = threading.Event(), threading.Event()

        def runner(cmd, timeout, cwd=None, **kw):
            entered.set()
            gate.wait(5)
            return CmdResult(0, CATALOG, "")
        scanner = FreeModelScanner(lambda: "/bin/opencode", "/tmp", lambda m: None, runner=runner)
        self.assertTrue(scanner.start())
        self.assertTrue(entered.wait(5))
        self.assertEqual(scanner.view()["status"], "scanning")
        self.assertFalse(scanner.start())
        gate.set()
        self.assertTrue(scanner.join(5))
        self.assertEqual(scanner.view()["status"], "ok")


if __name__ == "__main__":
    unittest.main()
