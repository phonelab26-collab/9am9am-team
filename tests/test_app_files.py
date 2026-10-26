"""Garde-fous du logiciel Mac (app/) : aucun test ici ne lance le logiciel."""
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SWIFT = ROOT / "app" / "main.swift"


class AppFilesTest(unittest.TestCase):
    def test_build_script_is_valid_bash_and_executable(self):
        script = ROOT / "app" / "build.sh"
        self.assertTrue(script.exists())
        self.assertTrue(script.stat().st_mode & 0o111, "build.sh doit être exécutable")
        subprocess.run(["bash", "-n", str(script)], check=True)

    def test_build_script_produces_a_universal_binary(self):
        # Le dépôt voyage entre un Mac Apple Silicon et un Mac Intel (clé USB) : un binaire
        # arm64 seul ne se lance pas sur Intel, un x86_64 seul tourne sous Rosetta mais pas
        # l'inverse sans lui. Les deux architectures + lipo, sinon « Mauvais type de CPU ».
        script = (ROOT / "app" / "build.sh").read_text(encoding="utf-8")
        for needle in ("-target arm64-apple-macos", "-target x86_64-apple-macos", "lipo -create"):
            self.assertIn(needle, script)

    def test_app_path_covers_every_usual_cli_location(self):
        # Lancé depuis le Finder, le PATH est minimal : Homebrew Apple Silicon, Homebrew
        # Intel et MacPorts doivent tous être cherchés, sinon `gh`/`claude` restent invisibles.
        src = SWIFT.read_text(encoding="utf-8")
        for prefix in ("/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin"):
            self.assertIn(f'"{prefix}"', src)

    def test_swift_source_compiles(self):
        if shutil.which("xcrun") is None or subprocess.run(
                ["xcrun", "--find", "swiftc"], capture_output=True).returncode != 0:
            self.skipTest("swiftc indisponible")
        res = subprocess.run(["xcrun", "swiftc", "-typecheck", "-swift-version", "5", str(SWIFT),
                              "-framework", "Cocoa", "-framework", "WebKit"],
                             capture_output=True, text=True, timeout=180)
        self.assertEqual(res.returncode, 0, res.stderr[-1500:])

    def test_app_keeps_its_safety_properties(self):
        src = SWIFT.read_text(encoding="utf-8")
        for needle in ("--no-browser", "--parent", "terminateLater",  # arrêt propre du serveur
                       "runJavaScriptConfirmPanelWithMessage",         # confirm() de la page
                       "runOpenPanelWith",                             # <input type=file> ouvre le sélecteur natif
                       'url.host == "127.0.0.1"',                     # seule l'interface locale s'affiche
                       "standardInput = FileHandle.nullDevice"):       # stdin fermé (opencode gèle sinon)
            self.assertIn(needle, src)


if __name__ == "__main__":
    unittest.main()
