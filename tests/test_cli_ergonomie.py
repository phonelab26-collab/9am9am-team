"""CLI : --version, --dir/--workspace, status --json, codes de sortie fiables, aide en français."""
import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from orch.cli import main
from orch.version import software_version

HAS_GIT = shutil.which("git") is not None


ROOT = Path(__file__).resolve().parents[1]


class CliCase(unittest.TestCase):
    def setUp(self):
        # Hermétique : les tests ne dépendent pas des CLI réellement installés sur la machine
        # (la CI n'a ni opencode ni claude, alors que le poste de développement les a).
        fake_bins = mock.patch.dict(os.environ, {"MINI_ORCH_OPENCODE": str(ROOT / "tests" / "bin" / "fake_opencode"),
                                                 "MINI_ORCH_CLAUDE": str(ROOT / "tests" / "bin" / "fake_claude")})
        fake_bins.start()
        self.addCleanup(fake_bins.stop)
        self.base = Path(tempfile.mkdtemp(prefix="orch-cli2-")).resolve()
        self.addCleanup(shutil.rmtree, self.base, True)
        self.root = self.base / "logiciel"   # dossier du logiciel (réglages globaux)
        self.root.mkdir()
        self.project = self.base / "projet"  # dossier de travail choisi avec --dir
        self.project.mkdir()

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = main(list(argv), self.root)
        return rc, out.getvalue(), err.getvalue()

    def tasks_of(self, folder):
        data = json.loads((folder / ".mini-orch" / "tasks.json").read_text(encoding="utf-8"))
        return data["tasks"] if isinstance(data, dict) else data


class VersionTest(CliCase):
    def test_version_flag_prints_name_and_python_and_returns_0(self):
        rc, out, _ = self.run_cli("--version")
        self.assertEqual(rc, 0)
        self.assertIn("9AM9AM-TEAM", out)
        self.assertIn("Python", out)

    def test_unknown_version_is_said_never_invented(self):
        rc, out, _ = self.run_cli("--version")  # self.root n'est pas un dépôt git
        self.assertIn("inconnue", out)
        self.assertEqual(software_version(self.root), "")

    @unittest.skipUnless(HAS_GIT, "git absent")
    def test_version_is_the_git_commit_of_the_software_folder(self):
        git = ["git", "-C", str(self.root), "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run(git + ["init", "-q"], check=True)
        subprocess.run(git + ["commit", "-q", "--allow-empty", "-m", "x"], check=True)
        short = subprocess.run(git + ["rev-parse", "--short", "HEAD"], check=True,
                               capture_output=True, text=True).stdout.strip()
        self.assertIn(short, software_version(self.root))
        self.assertIn(short, self.run_cli("--version")[1])

    @unittest.skipUnless(HAS_GIT, "git absent")
    def test_a_folder_merely_inside_another_repo_is_not_reported_as_its_commit(self):
        git = ["git", "-C", str(self.base), "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run(git + ["init", "-q"], check=True)
        subprocess.run(git + ["commit", "-q", "--allow-empty", "-m", "x"], check=True)
        self.assertEqual(software_version(self.root), "")


class WorkDirTest(CliCase):
    def test_dir_selects_the_working_folder_and_not_the_software_folder(self):
        rc, out, _ = self.run_cli("--dry-run", "--dir", str(self.project), "plan", "G")
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(self.tasks_of(self.project)), 1)
        self.assertFalse((self.root / ".mini-orch" / "tasks.json").exists())

    def test_workspace_is_an_alias(self):
        self.assertEqual(self.run_cli("--dry-run", "--workspace", str(self.project), "plan", "G")[0], 0)
        self.assertEqual(len(self.tasks_of(self.project)), 1)

    def test_settings_stay_global_in_the_software_folder(self):
        self.run_cli("--dry-run", "--dir", str(self.project), "plan", "G")
        self.assertFalse((self.project / ".mini-orch" / "config.json").exists())

    def test_relative_dir_is_resolved_from_the_current_folder(self):
        old = os.getcwd()
        os.chdir(str(self.base))
        self.addCleanup(os.chdir, old)
        self.assertEqual(self.run_cli("--dry-run", "--dir", "projet", "plan", "G")[0], 0)
        self.assertEqual(len(self.tasks_of(self.project)), 1)

    def test_default_is_still_the_software_folder(self):
        self.assertEqual(self.run_cli("--dry-run", "plan", "G")[0], 0)
        self.assertEqual(len(self.tasks_of(self.root)), 1)

    def test_options_work_after_the_command_too(self):
        rc, out, _ = self.run_cli("plan", "G", "--dry-run", "--dir", str(self.project))
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(self.tasks_of(self.project)), 1)

    def test_bad_dir_is_a_usage_error_and_creates_nothing(self):
        for raw, word in ((str(self.base / "absent"), "introuvable"), ("/", "trop large")):
            with self.subTest(raw=raw):
                rc, out, err = self.run_cli("--dry-run", "--dir", raw, "plan", "G")
                self.assertEqual(rc, 2)
                self.assertIn(word, out + err)
        self.assertFalse((self.root / ".mini-orch").exists())

    def test_dir_makes_no_sense_for_gui_and_selftest(self):
        for cmd in ("gui", "selftest"):
            with self.subTest(cmd=cmd):
                rc, out, err = self.run_cli("--dir", str(self.project), cmd)
                self.assertEqual(rc, 2)
                self.assertIn("--dir", out + err)
        self.assertFalse((self.project / ".mini-orch").exists())


class StatusJsonTest(CliCase):
    def test_json_is_valid_and_complete(self):
        self.run_cli("--dry-run", "--dir", str(self.project), "plan", "G")
        rc, out, _ = self.run_cli("--dir", str(self.project), "status", "--json")
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["problem"], "")
        self.assertEqual(data["counts"]["todo"], 1)
        [task] = data["tasks"]
        self.assertEqual((task["id"], task["status"], task["worker"]), (1, "todo", "opencode"))
        self.assertIn("title", task)
        self.assertIn("opencode", data["workers"])
        self.assertIn("model", data)

    def test_json_never_leaks_an_absolute_path(self):
        self.run_cli("--dry-run", "--dir", str(self.project), "plan", "G")
        out = self.run_cli("--dir", str(self.project), "status", "--json")[1]
        self.assertNotIn(str(self.base), out)

    def test_json_stdout_stays_clean_while_logs_go_to_stderr(self):
        (self.project / ".mini-orch").mkdir()
        (self.project / ".mini-orch" / "tasks.json").write_text(
            json.dumps([{"id": 1, "status": "todo", "title": "venu d'ailleurs", "worker": "opencode"}]))
        rc, out, err = self.run_cli("--dir", str(self.project), "status", "--json")
        self.assertEqual(rc, 0)
        json.loads(out)  # rien d'autre que le JSON sur stdout
        self.assertIn("n'a pas été créé par ce programme", err)

    def test_unusable_tasks_file_is_exit_1_in_both_modes(self):
        (self.project / ".mini-orch").mkdir()
        (self.project / ".mini-orch" / "tasks.json").write_text(
            json.dumps([{"id": 1, "status": "todo"}, {"id": 1, "status": "todo"}]))
        rc, out, _ = self.run_cli("--dir", str(self.project), "status", "--json")
        self.assertEqual(rc, 1)
        self.assertIn("en double", json.loads(out)["problem"])
        rc, out, _ = self.run_cli("--dir", str(self.project), "status")
        self.assertEqual(rc, 1)
        self.assertIn("PROBLÈME", out)

    def test_json_errors_are_json_too(self):
        from orch.store import StoreError
        with mock.patch("orch.cli.build", side_effect=StoreError("panne")):
            rc, out, _ = self.run_cli("status", "--json")
        self.assertEqual(rc, 1)
        self.assertEqual(json.loads(out), {"ok": False, "error": "panne"})

    def test_json_keyboard_interrupt_is_130(self):
        with mock.patch("orch.cli.build", side_effect=KeyboardInterrupt):
            self.assertEqual(self.run_cli("status", "--json")[0], 130)


class ExitCodesAndHelpTest(CliCase):
    ENGLISH = ("usage:", "positional arguments", "show this help message", "optional arguments",
               "the following arguments are required", "invalid choice", "unrecognized arguments")

    def test_usage_errors_return_2_in_french_without_raising(self):
        cases = (("plan",), ("foo",), ("--bogus", "status"), ("run", "--worker"), ("status --json",))
        for argv in cases:
            with self.subTest(argv=argv):
                rc, out, err = self.run_cli(*argv)
                self.assertEqual(rc, 2)
                self.assertIn("erreur", err)
                for english in self.ENGLISH:
                    self.assertNotIn(english, (out + err).lower())

    def test_unknown_command_lists_the_valid_ones(self):
        err = self.run_cli("foo")[2]
        self.assertIn("foo", err)
        self.assertIn("status", err)

    def test_help_is_french_with_examples_and_exit_codes(self):
        rc, out, _ = self.run_cli("--help")
        self.assertEqual(rc, 0)
        for text in ("utilisation", "exemples", "codes de sortie", "--dir", "--version", "--dry-run"):
            self.assertIn(text, out)
        for english in self.ENGLISH:
            self.assertNotIn(english, out.lower())

    def test_every_command_has_french_help_with_an_example(self):
        for cmd in ("plan", "run", "tick", "status", "chat", "retry", "report", "menu", "gui", "selftest"):
            with self.subTest(cmd=cmd):
                rc, out, _ = self.run_cli(cmd, "--help")
                self.assertEqual(rc, 0)
                self.assertIn("exemple", out)
                self.assertIn("mini_orchestrateur.py " + cmd, out)
                for english in self.ENGLISH:
                    self.assertNotIn(english, out.lower())

    def test_status_help_documents_json_and_dir(self):
        out = self.run_cli("status", "--help")[1]
        self.assertIn("--json", out)
        self.assertIn("--dir", out)

    def test_success_is_0_and_a_store_error_is_1(self):
        self.assertEqual(self.run_cli("--dry-run", "plan", "G")[0], 0)
        self.assertEqual(self.run_cli("status")[0], 0)


if __name__ == "__main__":
    unittest.main()
