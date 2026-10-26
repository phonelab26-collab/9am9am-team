"""Plusieurs process réels sur le même .mini-orch (CLI + interface en même temps)."""
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = dict(os.environ, PYTHONPATH=str(ROOT))

CLAIMER = """
import sys
from pathlib import Path
from orch.config import Paths
from orch.store import Store
store = Store(Paths.for_root(Path(sys.argv[1])), lambda m: None)
while True:
    c = store.checkout()
    if c.task is None:
        break
    print(c.task["id"], flush=True)
    store.record_result(c.task["id"], c.run_id, 0, "ok", supervise=False)
    c.lease.release()
"""

WRITER = """
import sys
from pathlib import Path
from orch.config import Paths
from orch.store import Store
store = Store(Paths.for_root(Path(sys.argv[1])), lambda m: None)
i = 0
while True:
    i += 1
    store.add_plan(f"objectif {i}", [{"title": "x" * 300, "worker": "opencode"}] * 3)
"""


class ConcurrencyTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="orch-conc-"))
        sys.path.insert(0, str(ROOT))
        from orch.config import Paths
        from orch.store import Store
        self.store = Store(Paths.for_root(self.dir), lambda m: None)

    def test_each_task_claimed_exactly_once(self):
        for g in range(10):  # 10 plans de 3 tâches, tâches sans dépendance entre plans
            self.store.add_plan(f"G{g}", [{"title": f"t{i}", "worker": "opencode"} for i in range(3)])
        procs = [subprocess.Popen([sys.executable, "-c", CLAIMER, str(self.dir)], env=ENV,
                                  stdout=subprocess.PIPE, stdin=subprocess.DEVNULL) for _ in range(4)]
        claimed = []
        for p in procs:
            out, _ = p.communicate(timeout=60)
            self.assertEqual(p.returncode, 0)
            claimed += [int(x) for x in out.split()]
        self.assertEqual(sorted(claimed), list(range(1, 31)))
        snap = self.store.snapshot()
        self.assertTrue(all(t["status"] == "done" and t["attempts"] == 1 for t in snap))

    def test_file_stays_valid_when_writer_is_killed(self):
        tasks = self.dir / ".mini-orch" / "tasks.json"
        ids = []
        for _ in range(20):
            p = subprocess.Popen([sys.executable, "-c", WRITER, str(self.dir)], env=ENV,
                                 stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                # Attendre la 1ʳᵉ écriture COMPLÈTE avant de tuer : sur un disque lent,
                # SIGKILL à 0,15 s tombait avant elle et le fichier n'existait pas encore.
                self.assertTrue(self._wait_for(tasks, 20.0), "le writer n'a jamais écrit")
                time.sleep(0.15)  # retarde le kill : le writer est en pleine boucle d'écriture
            finally:
                if p.poll() is None:
                    p.send_signal(signal.SIGKILL)
                p.wait()
            data = json.loads(tasks.read_text(encoding="utf-8"))
            ids = [t["id"] for t in (data["tasks"] if isinstance(data, dict) else data)]
            self.assertEqual(len(ids), len(set(ids)))
        self.assertGreater(len(ids), 0)

    @staticmethod
    def _wait_for(path, seconds: float) -> bool:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if path.exists():
                return True
            time.sleep(0.02)
        return False


if __name__ == "__main__":
    unittest.main()
