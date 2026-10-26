import os
import signal
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Optional

from orch.procs import run_cmd

PY = sys.executable
# Lance un petit-enfant `sleep 30` qui HÉRITE des pipes, affiche son pid, puis attend.
SPAWN = ("import subprocess, sys, time; g = subprocess.Popen(['sleep', '30']); "
         "print(g.pid, flush=True); time.sleep(float(sys.argv[1]))")
# Idem, mais le pid est aussi écrit dans un fichier : l'annulation n'est déclenchée
# qu'une fois l'enfant réellement lancé (un cancel trop tôt laissait stdout vide).
SPAWN_WITH_PIDFILE = ("import subprocess, sys, time; g = subprocess.Popen(['sleep', '30']); "
                      "open(sys.argv[2], 'w').write(str(g.pid)); print(g.pid, flush=True); "
                      "time.sleep(float(sys.argv[1]))")
# Petit-enfant DÉTACHÉ du groupe (nouvelle session) qui hérite des tuyaux : kill_group
# ne peut pas l'atteindre. Sans vidange bornée, communicate() attendrait sa fin — pour
# toujours. C'est le scénario « worker qui daemonise un helper ».
DETACHED = ("import subprocess, sys, time; "
            "g = subprocess.Popen(['sleep', '300'], start_new_session=True); "
            "open(sys.argv[2], 'w').write(str(g.pid)); "
            "print(g.pid, flush=True); time.sleep(float(sys.argv[1]))")


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def wait_dead(pid: int, seconds: float = 3.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not alive(pid):
            return True
        time.sleep(0.05)
    return False


class RunCmdTest(unittest.TestCase):
    def test_stdin_is_closed(self):
        res = run_cmd([PY, "-c", "import sys; print(repr(sys.stdin.read()))"], timeout=10)
        self.assertTrue(res.ok)
        self.assertEqual(res.out.strip(), "''")

    def test_stdout_and_stderr_are_separate(self):
        res = run_cmd([PY, "-c", "import sys; print('dehors'); print('erreur', file=sys.stderr)"], timeout=10)
        self.assertEqual((res.out.strip(), res.err.strip()), ("dehors", "erreur"))

    def test_missing_binary_is_explicit(self):
        res = run_cmd(["/nonexistent/binaire"], timeout=5)
        self.assertEqual(res.code, 127)
        self.assertIn("introuvable", res.err)
        self.assertNotIn("/nonexistent", res.err)  # pas de chemin système en sortie

    def test_timeout_kills_the_whole_group(self):
        pid_file = self._pid_file()
        t0 = time.monotonic()
        res = run_cmd([PY, "-c", SPAWN_WITH_PIDFILE, "30", str(pid_file)], timeout=2)
        self.assertLess(time.monotonic() - t0, 6)
        self.assertTrue(res.timed_out)
        self.assertEqual(res.code, 124)
        pid = self._read_pid(pid_file)
        self.assertIsNotNone(pid, "l'enfant n'a jamais démarré : rien n'a été prouvé")
        self.assertTrue(wait_dead(pid), "petit-enfant survivant")

    def test_child_exits_but_grandchild_holds_pipes(self):
        t0 = time.monotonic()
        res = run_cmd([PY, "-c", SPAWN, "0"], timeout=30)
        self.assertLess(time.monotonic() - t0, 8)
        self.assertEqual(res.code, 0)
        self.assertTrue(wait_dead(int(res.out.split()[0])))

    def test_detached_grandchild_cannot_hang_the_timeout(self):
        # Un descendant qui a quitté le groupe retient les tuyaux : run_cmd doit rendre
        # la main quand même (vidange bornée), avec le bon verdict.
        pid_file = self._pid_file()
        t0 = time.monotonic()
        res = self._run_bounded([PY, "-c", DETACHED, "300", str(pid_file)], timeout=2)
        self.assertLess(time.monotonic() - t0, 20)
        self.assertTrue(res.timed_out)
        self.assertEqual(res.code, 124)
        self._kill_quietly(self._read_pid(pid_file, seconds=1))

    def test_detached_grandchild_cannot_hang_the_cancel(self):
        pid_file = self._pid_file()
        cancel = threading.Event()
        threading.Thread(target=self._cancel_when_started, args=(pid_file, cancel),
                         daemon=True).start()
        t0 = time.monotonic()
        res = self._run_bounded([PY, "-c", DETACHED, "300", str(pid_file)], timeout=30, cancel=cancel)
        self.assertLess(time.monotonic() - t0, 20)
        self.assertTrue(res.cancelled)
        self.assertEqual(res.code, 130)
        self._kill_quietly(self._read_pid(pid_file, seconds=1))

    def test_cancel_stops_and_is_reported(self):
        pid_file = self._pid_file()
        cancel = threading.Event()
        threading.Thread(target=self._cancel_when_started, args=(pid_file, cancel),
                         daemon=True).start()
        res = run_cmd([PY, "-c", SPAWN_WITH_PIDFILE, "30", str(pid_file)], timeout=30, cancel=cancel)
        self.assertTrue(res.cancelled)
        self.assertFalse(res.ok)
        pid = self._read_pid(pid_file)
        self.assertIsNotNone(pid, "l'enfant n'a jamais démarré : rien n'a été prouvé")
        self.assertTrue(wait_dead(pid), "petit-enfant survivant")

    def test_invalid_utf8_is_replaced(self):
        res = run_cmd([PY, "-c", "import sys; sys.stdout.buffer.write(b'a\\xffb')"], timeout=10)
        self.assertEqual(res.out, "a�b")

    def test_interrupt_kills_child_then_propagates(self):
        seen = []

        def boom(pid):
            seen.append(pid)
            raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            run_cmd(["sleep", "30"], timeout=30, on_spawn=boom)
        self.assertTrue(wait_dead(seen[0]))

    def test_cwd_is_honoured(self):
        res = run_cmd([PY, "-c", "import os; print(os.getcwd())"], timeout=10, cwd="/tmp")
        self.assertTrue(res.out.strip().endswith("/tmp"))

    def test_non_executable_binary_is_126(self):
        tmp = tempfile.NamedTemporaryFile(prefix="orch-noexec-", suffix=".sh", delete=False)
        tmp.write(b"#!/bin/sh\necho jamais\n")
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        os.chmod(tmp.name, 0o644)  # présent, mais pas exécutable
        res = run_cmd([tmp.name], timeout=5)
        self.assertEqual(res.code, 126)
        self.assertIn("non exécutable", res.err)
        self.assertNotIn(tmp.name, res.err)  # pas de chemin système en sortie

    def test_huge_output_keeps_only_the_tail(self):
        res = run_cmd([PY, "-c",
                       "import sys; sys.stdout.write('a' * 2100000); sys.stdout.write('FIN-ICI')"],
                      timeout=30)
        self.assertTrue(res.ok)
        self.assertLessEqual(len(res.out), 2_000_000)  # OUT_MAX
        self.assertTrue(res.out.endswith("FIN-ICI"), "la fin de la sortie doit être gardée")

    def test_endless_output_keeps_memory_bounded_and_the_timeout_holds(self):
        # Lot 1 : la sortie entière restait en mémoire jusqu'à la fin (3,5 Go en 4 s avec `yes`).
        # Mesuré dans un interpréteur neuf : le pic mémoire du process de test ne dit rien.
        probe = ("import resource, sys, time; from orch.procs import run_cmd, OUT_MAX; "
                 "t = time.monotonic(); r = run_cmd(['yes'], timeout=2); "
                 "peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss; "
                 "peak = peak if sys.platform == 'darwin' else peak * 1024; "
                 "print(peak, round(time.monotonic() - t, 2), r.timed_out, len(r.out) <= OUT_MAX, r.out[-4:] == 'y\\ny\\n')")
        out = run_cmd([PY, "-c", probe], timeout=60, cwd=str(Path(__file__).resolve().parents[1]))
        self.assertTrue(out.ok, out.err)
        peak, elapsed, timed_out, bounded, tail_kept = out.out.split()
        self.assertLess(int(peak), 300 * 1024 * 1024, f"pic mémoire {int(peak) // 2**20} Mo")
        self.assertLess(float(elapsed), 2 + 3.0 + 1.5)  # délai + vidange bornée + marge
        self.assertEqual((timed_out, bounded, tail_kept), ("True", "True", "True"))

    def test_env_is_passed_to_the_child(self):
        res = run_cmd([PY, "-c", "import os; print(os.environ.get('ORCH_PROMPT', 'absent'))"],
                      timeout=10, env={"ORCH_PROMPT": "mon objectif"})
        self.assertEqual(res.out.strip(), "mon objectif")

    def _pid_file(self) -> Path:
        """Fichier temporaire où l'enfant écrit le pid de son petit-enfant."""
        tmp = tempfile.TemporaryDirectory(prefix="orch-procs-")
        self.addCleanup(tmp.cleanup)
        return Path(tmp.name) / "petit-enfant.pid"

    def _run_bounded(self, cmd, limit=20.0, **kw):
        """run_cmd dans un fil avec chien de garde : si la borne de sortie disparaît,
        ce test échoue au lieu de pendre la suite de tests pour toujours."""
        holder = {}
        th = threading.Thread(target=lambda: holder.update(res=run_cmd(cmd, **kw)), daemon=True)
        th.start()
        th.join(limit)
        self.assertFalse(th.is_alive(), "run_cmd n'est pas revenu : attente infinie sur les tuyaux")
        return holder["res"]

    @staticmethod
    def _kill_quietly(pid: Optional[int]) -> None:
        if not pid:
            return
        try:
            os.kill(pid, signal.SIGKILL)  # le détaché n'appartient plus à personne
        except ProcessLookupError:
            pass

    @staticmethod
    def _read_pid(path: Path, seconds: float = 5.0) -> Optional[int]:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                text = path.read_text(encoding="utf-8").strip()
            except OSError:
                text = ""
            if text:
                return int(text)
            time.sleep(0.02)
        return None

    @staticmethod
    def _cancel_when_started(pid_file: Path, cancel: threading.Event, seconds: float = 10.0) -> None:
        try:
            end = time.monotonic() + seconds
            while time.monotonic() < end and not pid_file.exists():
                time.sleep(0.02)
        finally:
            cancel.set()  # au pire, annule quand même : le test dira ce qui a manqué


if __name__ == "__main__":
    unittest.main()
