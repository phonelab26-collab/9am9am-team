"""Lot 3, point 9 : vérification automatique — empreinte, commande cassée, tests non exécutés."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from orch.verify import broken_reason, fingerprint
from orch.workers import WorkOutcome
from tests.test_engine import EngineCase


class NestedFolderFingerprintTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="orch-fp3-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        (self.dir / "a.py").write_text("x")

    def test_a_file_under_a_nested_build_folder_counts(self):
        (self.dir / "pkg" / "build").mkdir(parents=True)
        before = fingerprint(self.dir)
        (self.dir / "pkg" / "build" / "gen.py").write_text("y")
        self.assertNotEqual(fingerprint(self.dir), before)

    def test_root_level_ignored_folders_are_still_ignored(self):
        before = fingerprint(self.dir)
        (self.dir / "build").mkdir()
        (self.dir / "build" / "out.o").write_text("z")
        self.assertEqual(fingerprint(self.dir), before)


class BrokenCommandTest(unittest.TestCase):
    def test_pytest_missing_is_a_broken_command(self):
        why = broken_reason("python3 -m pytest", 1, "/usr/bin/python3: No module named pytest")
        self.assertIn("pytest", why)

    def test_no_test_collected_is_a_broken_command(self):
        self.assertTrue(broken_reason("python3 -m pytest -q", 5, "no tests ran in 0.01s"))

    def test_unittest_that_ran_nothing_is_a_broken_command(self):
        self.assertTrue(broken_reason("python3 -m unittest", 5, "Ran 0 tests in 0.000s\n\nNO TESTS RAN"))

    def test_real_red_tests_are_not_broken(self):
        self.assertEqual(broken_reason("python3 -m pytest", 1, "1 failed, 3 passed"), "")
        self.assertEqual(broken_reason("make check", 5, "boom"), "")  # code 5 seul ne dit rien d'un make


class VerifyEngineTest(EngineCase):
    def setUp(self):
        super().setUp()
        self.workers.touch = self.dir
        self.cfg.set_verify_cmd(self.paths.root, "python3 -m pytest")
        self.ceo.chat_answers = [json.dumps({"reply": "Je m'en occupe.", "tasks": [
            {"title": "A", "worker": "opencode", "detail": "a"}]})]

    def test_missing_pytest_does_not_rerun_the_agent_three_times(self):
        self.workers.checks = [WorkOutcome(1, "No module named pytest")] * 3
        self.assertEqual(self.engine().converse("Corrige A"), 1)
        self.assertEqual(len(self.workers.runs), 1)
        t = self.store.snapshot()[0]
        self.assertEqual(t["verdict"], "check_error")
        checks = [m for m in self.msgs() if m.get("kind") == "check"]
        self.assertTrue(checks[0]["data"]["broken"])

    def test_the_ceo_is_told_when_the_tests_did_not_run(self):
        self.workers.touch = None  # rien de modifié : vérification sautée
        self.engine().converse("Corrige A")
        sup = [p for p in self.ceo.prompts if "Résultat du worker" in p][0]
        self.assertIn("NON exécutée", sup)


if __name__ == "__main__":
    unittest.main()
