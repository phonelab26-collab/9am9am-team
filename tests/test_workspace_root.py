"""Choix du dossier de travail : validation stricte et sélecteur natif du Mac."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from orch.procs import CmdResult
from orch.workspace_root import pick_folder, validate_workspace


class ValidateWorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="orch-wsroot-"))
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_an_existing_writable_folder_is_accepted_and_resolved(self):
        path, err = validate_workspace(str(self.dir) + "/")
        self.assertEqual((path, err), (self.dir.resolve(), ""))

    def test_refusals_are_explicit(self):
        afile = self.dir / "f.txt"
        afile.write_text("x")
        cases = {
            "": "vide", "relatif/dossier": "absolu", str(self.dir / "absent"): "introuvable",
            str(afile): "pas un dossier", "/": "trop large", str(Path.home()): "trop large",
            "/System": "système", "/Applications": "système", "/usr/local": "système",
        }
        for raw, word in cases.items():
            with self.subTest(raw=raw):
                path, err = validate_workspace(raw)
                self.assertIsNone(path)
                self.assertIn(word, err)

    def test_case_variants_of_forbidden_folders_are_refused_too(self):
        # APFS ignore la casse : « /USERS/<moi> » est le dossier personnel.
        for raw in ("/USERS", str(Path.home()).upper(), "/system/Library", "/APPLICATIONS"):
            with self.subTest(raw=raw):
                if not Path(raw).exists():
                    self.skipTest("système de fichiers sensible à la casse")
                self.assertIsNone(validate_workspace(raw)[0])

    def test_read_only_folder_is_refused(self):
        ro = self.dir / "ro"
        ro.mkdir()
        ro.chmod(0o500)
        self.addCleanup(ro.chmod, 0o700)
        if os.access(str(ro), os.W_OK):  # root ou ACL : test sans objet
            self.skipTest("dossier toujours inscriptible ici")
        path, err = validate_workspace(str(ro))
        self.assertIsNone(path)
        self.assertIn("écriture", err)


class PickFolderTest(unittest.TestCase):
    def runner(self, result):
        self.calls = []

        def run(cmd, timeout, cwd=None, cancel=None, on_spawn=None, env=None):
            self.calls.append((cmd, timeout))
            return result
        return run

    def test_chosen_folder_is_returned_without_trailing_slash(self):
        path, err = pick_folder(self.runner(CmdResult(0, "/Users/x/mon projet/\n", "")))
        self.assertEqual((path, err), ("/Users/x/mon projet", ""))
        cmd, timeout = self.calls[0]
        self.assertEqual(cmd[0], "osascript")
        self.assertIn("choose folder", " ".join(cmd))
        self.assertGreaterEqual(timeout, 60)  # l'utilisateur prend le temps de choisir

    def test_cancel_is_not_an_error(self):
        path, err = pick_folder(self.runner(CmdResult(1, "", "execution error: User canceled. (-128)")))
        self.assertEqual((path, err), (None, ""))

    def test_other_failure_is_explained(self):
        path, err = pick_folder(self.runner(CmdResult(1, "", "boom")))
        self.assertIsNone(path)
        self.assertIn("boom", err)

    def test_empty_answer_is_an_error_not_a_guess(self):
        path, err = pick_folder(self.runner(CmdResult(0, "\n", "")))
        self.assertIsNone(path)
        self.assertTrue(err)


if __name__ == "__main__":
    unittest.main()
