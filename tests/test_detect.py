"""Détection de la commande de vérification d'un projet (par défaut, affichée, désactivable)."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from orch.detect import detect_verify_cmd


class DetectVerifyCmdTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="orch-detect-"))
        self.addCleanup(shutil.rmtree, self.dir, True)

    def put(self, name, text=""):
        (self.dir / name).write_text(text, encoding="utf-8")

    def test_empty_folder_detects_nothing(self):
        self.assertEqual(detect_verify_cmd(self.dir), "")

    def test_makefile_prefers_check_fast_then_check_then_test(self):
        self.put("Makefile", "build:\n\techo b\ncheck:\n\techo c\ntest:\n\techo t\ncheck-fast: generate\n\techo f\n")
        self.assertEqual(detect_verify_cmd(self.dir), "make check-fast")
        self.put("Makefile", "check:\n\techo c\ntest:\n\techo t\n")
        self.assertEqual(detect_verify_cmd(self.dir), "make check")
        self.put("Makefile", "test: deps\n\techo t\n")
        self.assertEqual(detect_verify_cmd(self.dir), "make test")

    def test_makefile_without_a_test_target_is_not_enough(self):
        self.put("Makefile", "build:\n\techo b\nclean:\n\techo c\n")
        self.assertEqual(detect_verify_cmd(self.dir), "")

    def test_a_test_target_name_inside_a_recipe_does_not_count(self):
        self.put("Makefile", "build:\n\techo 'test: not a target'\n")
        self.assertEqual(detect_verify_cmd(self.dir), "")

    def test_swift_package(self):
        self.put("Package.swift", "// swift-tools-version:5.9\n")
        self.assertEqual(detect_verify_cmd(self.dir), "swift test")

    def test_xcode_project_alone_is_not_guessed(self):
        (self.dir / "App.xcodeproj").mkdir()  # le schéma est inconnu : on ne devine pas
        self.assertEqual(detect_verify_cmd(self.dir), "")

    def test_npm_test_script_but_not_the_npm_placeholder(self):
        self.put("package.json", json.dumps({"scripts": {"test": "jest"}}))
        self.assertEqual(detect_verify_cmd(self.dir), "npm test")
        self.put("package.json", json.dumps({"scripts": {"test": 'echo "Error: no test specified" && exit 1'}}))
        self.assertEqual(detect_verify_cmd(self.dir), "")
        self.put("package.json", "{ pas du json")
        self.assertEqual(detect_verify_cmd(self.dir), "")

    def test_python_pytest_and_unittest(self):
        self.put("pytest.ini", "[pytest]\n")
        self.assertEqual(detect_verify_cmd(self.dir), "python3 -m pytest -q")
        (self.dir / "pytest.ini").unlink()
        (self.dir / "tests").mkdir()
        (self.dir / "tests" / "test_a.py").write_text("import unittest\nclass T(unittest.TestCase): pass\n")
        self.assertEqual(detect_verify_cmd(self.dir), "python3 -m unittest discover -s tests")

    def test_rust_and_go(self):
        self.put("Cargo.toml", "[package]\n")
        self.assertEqual(detect_verify_cmd(self.dir), "cargo test")
        (self.dir / "Cargo.toml").unlink()
        self.put("go.mod", "module x\n")
        self.assertEqual(detect_verify_cmd(self.dir), "go test ./...")

    def test_makefile_wins_over_other_markers(self):
        self.put("Makefile", "check:\n\techo c\n")
        self.put("Package.swift", "")
        self.assertEqual(detect_verify_cmd(self.dir), "make check")

    def test_result_is_always_an_acceptable_command(self):
        from orch.verify import clean_verify_cmd
        for name, text in (("Makefile", "check-fast:\n\techo\n"), ("Package.swift", ""), ("go.mod", "")):
            self.put(name, text)
            cmd = detect_verify_cmd(self.dir)
            self.assertEqual(clean_verify_cmd(cmd), cmd)


if __name__ == "__main__":
    unittest.main()
