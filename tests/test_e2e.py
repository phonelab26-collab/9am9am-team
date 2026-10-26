"""Bout en bout : le vrai point d'entrée, copié dans un dossier jetable, avec de
faux `claude`/`opencode`. Aucun fichier du projet n'est touché."""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "tests" / "bin"


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def wait_until(pred, seconds=20.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.1)
    return False


class E2ECase(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="orch-e2e-"))
        shutil.copy(ROOT / "mini_orchestrateur.py", self.dir)
        shutil.copytree(ROOT / "orch", self.dir / "orch", ignore=shutil.ignore_patterns("__pycache__"))
        self.env = dict(os.environ, MINI_ORCH_OPENCODE=str(BIN / "fake_opencode"),
                        MINI_ORCH_CLAUDE=str(BIN / "fake_claude"), FAKE_OC_LOG=str(self.dir / "runs.log"))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def cmd(self, *args):
        return [sys.executable, str(self.dir / "mini_orchestrateur.py"), *args]

    def run_cli(self, *args, env=None, timeout=60):
        """stdin = pipe OUVERT (le cas qui gelait opencode) ; tué si ça dépasse."""
        p = subprocess.Popen(self.cmd(*args), env=env or self.env, stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        timer = threading.Timer(timeout, p.kill)
        timer.start()
        try:
            out = p.stdout.read().decode("utf-8", "replace")
            p.wait()
        finally:
            timer.cancel()
            p.stdin.close()
            p.stdout.close()
        return p.returncode, out

    def tasks(self):
        data = json.loads((self.dir / ".mini-orch" / "tasks.json").read_text(encoding="utf-8"))
        return data["tasks"] if isinstance(data, dict) else data

    def in_progress_with_child(self):
        try:
            return any(t["status"] == "in_progress" and t.get("owner", {}).get("child_pgid")
                       for t in self.tasks())
        except (FileNotFoundError, ValueError):
            return False


class E2ETest(E2ECase):
    def test_full_cycle_with_open_stdin(self):
        self.assertEqual(self.run_cli("tick", "G")[0], 0)
        self.assertEqual(self.run_cli("tick", "G")[0], 0)
        rc, out = self.run_cli("tick", "G")
        self.assertEqual(rc, 0)
        self.assertIn("déjà planifié", out)
        self.assertEqual([(t["status"], t["verdict"]) for t in self.tasks()], [("done", "ok")] * 2)
        reports = json.loads((self.dir / ".mini-orch" / "reports.json").read_text(encoding="utf-8"))
        self.assertEqual(len(reports), 1)
        self.assertIn("Rapport du CEO", reports[0]["text"])
        self.assertIn("Rapport du CEO", out + self.run_cli("report", "G")[1])
        self.assertIn("validé", self.run_cli("status")[1])

    def test_crash_during_worker_is_recovered_and_orphan_killed(self):
        env = dict(self.env, FAKE_OC_SLEEP="60", FAKE_OC_GRANDCHILD=str(self.dir / "gc.txt"))
        p = subprocess.Popen(self.cmd("tick", "G"), env=env, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.assertTrue(wait_until(self.in_progress_with_child), "la tâche n'a jamais démarré")
        self.assertTrue(wait_until(lambda: (self.dir / "gc.txt").exists()))
        grandchild = int((self.dir / "gc.txt").read_text().split()[0])
        pgid = next(t["owner"]["child_pgid"] for t in self.tasks() if t["status"] == "in_progress")
        p.send_signal(signal.SIGKILL)
        p.wait()
        self.assertTrue(alive(pgid), "le worker devrait survivre au kill -9 (orphelin)")
        before = (self.dir / ".mini-orch" / "tasks.json").read_bytes()
        self.run_cli("status")
        self.assertEqual((self.dir / ".mini-orch" / "tasks.json").read_bytes(), before)  # lecture seule
        rc, out = self.run_cli("tick", "G")
        self.assertIn("orphelin", out)
        self.assertTrue(wait_until(lambda: not alive(pgid) and not alive(grandchild), 5))
        self.assertEqual(rc, 0)
        self.assertEqual(self.tasks()[0]["status"], "done")

    def test_chat_delegates_and_answers(self):
        rc, out = self.run_cli("chat", "Fais les deux étapes")
        self.assertEqual(rc, 0, out)
        self.assertEqual([(t["status"], t["verdict"]) for t in self.tasks()], [("done", "ok")] * 2)
        chat = json.loads((self.dir / ".mini-orch" / "chat.json").read_text(encoding="utf-8"))
        kinds = [m.get("kind") for m in chat]
        self.assertEqual([m["role"] for m in chat[:2]], ["user", "ceo"])
        for wanted in ("plan", "task_start", "task_result", "verdict", "report"):
            self.assertIn(wanted, kinds)
        env = dict(self.env, FAKE_CEO_CHAT="answer")
        rc, _ = self.run_cli("chat", "Es-tu Claude Code ?", env=env)
        self.assertEqual(rc, 0)
        chat = json.loads((self.dir / ".mini-orch" / "chat.json").read_text(encoding="utf-8"))
        self.assertEqual(chat[-2]["text"], "Modèle conseillé : Sonnet 5.5 · faible — question simple\nOui, je suis Claude Code, le CEO.")
        # le faux CLI renvoie l'enveloppe JSON réelle : 6 600 tokens annoncés → bilan mesuré
        self.assertEqual(chat[-1]["kind"], "claude_usage")
        self.assertIn("1 appel (1 du CEO) · ≈ 7 k tokens traités", chat[-1]["text"])
        self.assertEqual(len(self.tasks()), 2)  # aucune tâche de plus

    def test_desktop_app_mode_starts_without_browser_and_follows_its_parent(self):
        parent = subprocess.Popen(["sleep", "60"])
        p = subprocess.Popen(self.cmd("--dry-run", "gui", "--no-browser", "--parent", str(parent.pid)),
                             env=dict(self.env, BROWSER="false"), stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            url = None
            timer = threading.Timer(20, p.kill)
            timer.start()
            for line in p.stdout:
                found = re.search(r"http://127\.0\.0\.1:\d+/\?h=\S+", line)
                if found:
                    url = found.group(0)
                    break
            timer.cancel()
            self.assertIsNotNone(url, "l'adresse de l'interface n'a pas été affichée")
            # Comme un navigateur : le nonce ?h= pose le cookie, la redirection
            # vers / s'authentifie ensuite grâce à lui.
            import http.cookiejar
            import urllib.request
            opener = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            self.assertIn("9AM9AM-TEAM", opener.open(url, timeout=5).read().decode())
            parent.kill()  # le logiciel hôte disparaît : le serveur doit s'arrêter seul
            parent.wait()
            self.assertEqual(p.wait(timeout=15), 0)
        finally:
            for proc in (p, parent):
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
            p.stdout.close()

    def test_ceo_model_setting_reaches_the_claude_command(self):
        (self.dir / ".mini-orch").mkdir()
        (self.dir / ".mini-orch" / "config.json").write_text(json.dumps({"ceo_model": "opus"}), encoding="utf-8")
        log = self.dir / "claude_args.log"
        env = dict(self.env, FAKE_CLAUDE_ARGS=str(log), FAKE_CEO_CHAT="answer")
        self.assertEqual(self.run_cli("chat", "Bonjour", env=env)[0], 0)
        from orch.claude_cli import JSON_OUTPUT, LEAN_ARGS
        self.assertEqual(json.loads(log.read_text(encoding="utf-8").splitlines()[0]),
                         ["--model", "opus", *LEAN_ARGS, "--tools", "", *JSON_OUTPUT, "-p"])

    def test_crash_during_ceo_review_is_recovered(self):
        env = dict(self.env, FAKE_CEO_VERDICT_SLEEP="60")
        p = subprocess.Popen(self.cmd("tick", "G"), env=env, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        def reviewing():
            try:
                return any(t.get("owner", {}).get("phase") == "review" and t["owner"].get("child_pgid")
                           for t in self.tasks())
            except (FileNotFoundError, ValueError):
                return False
        self.assertTrue(wait_until(reviewing), "la supervision n'a jamais démarré")
        ceo_pgid = next(t["owner"]["child_pgid"] for t in self.tasks() if t["status"] == "in_progress")
        p.send_signal(signal.SIGKILL)
        p.wait()
        self.assertTrue(alive(ceo_pgid), "le CEO devrait survivre au kill -9 (orphelin)")
        rc, out = self.run_cli("tick", "G")
        self.assertIn("orphelin", out)
        self.assertTrue(wait_until(lambda: not alive(ceo_pgid), 5))
        first = self.tasks()[0]
        self.assertEqual((first["status"], first["verdict"]), ("done", "non_supervise"))
        self.assertIn("supervision interrompue", first["result"])

    def test_ctrl_c_during_worker(self):
        if signal.getsignal(signal.SIGINT) is signal.SIG_IGN:
            self.skipTest("SIGINT est ignoré dans ce process (suite lancée en tâche de fond : "
                          "nohup/& met SIGINT à SIG_IGN, et l'enfant l'hérite) : Ctrl-C n'est "
                          "pas délivrable, ce test ne peut rien prouver.")
        env = dict(self.env, FAKE_OC_SLEEP="60", FAKE_OC_GRANDCHILD=str(self.dir / "gc.txt"))
        p = subprocess.Popen(self.cmd("tick", "G"), env=env, stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.assertTrue(wait_until(self.in_progress_with_child))
        self.assertTrue(wait_until(lambda: (self.dir / "gc.txt").exists()))
        grandchild = int((self.dir / "gc.txt").read_text().split()[0])
        p.send_signal(signal.SIGINT)
        out = p.communicate(timeout=20)[0].decode()
        self.assertEqual(p.returncode, 130, out)
        self.assertTrue(wait_until(lambda: not alive(grandchild), 5))
        t = self.tasks()[0]
        self.assertEqual((t["status"], t["attempts"]), ("blocked", 0))
        cfg = json.loads((self.dir / ".mini-orch" / "config.json").read_text()) \
            if (self.dir / ".mini-orch" / "config.json").exists() else {}
        self.assertFalse(cfg.get("dead_models"))

    def test_two_processes_same_new_goal_make_one_plan(self):
        env = dict(self.env, FAKE_CEO_SLEEP="1")
        procs = [subprocess.Popen(self.cmd("plan", "G"), env=env, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.DEVNULL) for _ in range(2)]
        rcs = sorted(p.wait(timeout=60) for p in procs)
        self.assertEqual(rcs, [0, 1])
        self.assertEqual(len(self.tasks()), 2)

    def test_legacy_state_file_status(self):
        (self.dir / ".mini-orch").mkdir()
        shutil.copy(ROOT / "tests" / "fixtures" / "legacy_tasks.json", self.dir / ".mini-orch" / "tasks.json")
        rc, out = self.run_cli("status")
        self.assertEqual(rc, 0)
        self.assertEqual(sum(1 for ln in out.splitlines() if ln.strip().startswith("[")), 13)


if __name__ == "__main__":
    unittest.main()
