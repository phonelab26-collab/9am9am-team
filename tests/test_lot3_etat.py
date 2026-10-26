"""Lot 3, point 6 : reprise après kill -9, tasks.json corrompu, rapports illisibles — jamais en silence."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from orch import tasks as T
from orch.chat import ChatStore
from orch.config import ConfigStore, Paths
from orch.engine import Engine
from orch.store import Store
from tests.test_engine import FakeCeo, FakeWorkers


class StateCase(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="orch-state-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.paths = Paths.for_root(self.dir)
        self.paths.state.mkdir(parents=True)
        self.logs = []
        self.store = Store(self.paths, self.logs.append)

    def write_tasks(self, rows):
        self.paths.tasks.write_text(json.dumps(rows), encoding="utf-8")


class CrashDuringWorkTest(StateCase):
    def test_a_crash_gives_the_attempt_back_like_cancel_and_verify(self):
        self.write_tasks([{"id": 1, "goal": "G", "worker": "opencode", "status": "in_progress", "attempts": 3,
                           "owner": {"pid": 1, "run_id": "mort", "phase": "work"}}])
        self.store.recover_stale()
        t = self.store.snapshot()[0]
        self.assertEqual((t["status"], t["attempts"], t.get("interrupted")), ("blocked", 2, True))
        self.assertEqual(self.store.requeue_blocked("G"), 1)  # reprise automatique encore possible
        self.assertEqual(self.store.snapshot()[0]["status"], "todo")

    def test_three_kill_9_in_a_row_never_exhaust_the_task(self):
        self.write_tasks([{"id": 1, "goal": "G", "worker": "opencode", "status": "todo", "attempts": 0}])
        for _ in range(4):
            claim = self.store.checkout()
            self.assertIsNotNone(claim.task)
            owner = dict(self.store.snapshot()[0]["owner"], run_id="mort")  # le process est « mort »
            claim.lease.release()
            rows = self.store.snapshot()
            rows[0]["owner"] = owner
            self.write_tasks(T.pack(rows))
            self.store.recover_stale()
            self.assertEqual(self.store.requeue_blocked("G"), 1)
        self.assertEqual(self.store.snapshot()[0]["attempts"], 0)


class CorruptTasksTest(StateCase):
    def test_checkout_on_a_corrupt_file_reports_it(self):
        self.paths.tasks.write_text("{pas du json", encoding="utf-8")
        claim = self.store.checkout()
        self.assertIsNone(claim.task)
        damaged = self.store.take_damage()
        self.assertTrue(damaged.startswith("tasks.json.corrupt-"))
        self.assertNotIn(str(self.dir), damaged)
        self.assertEqual(self.store.take_damage(), "")  # dit une fois

    def test_a_healthy_empty_list_is_not_damaged(self):
        claim = self.store.checkout()
        self.assertEqual((claim.task, self.store.take_damage()), (None, ""))

    def test_engine_says_corrupt_and_returns_1_never_nothing_to_do(self):
        self.paths.tasks.write_text("{pas du json", encoding="utf-8")
        chat = ChatStore(self.paths, self.logs.append)
        eng = Engine(self.paths, self.store, ConfigStore(self.paths, self.logs.append), FakeWorkers(),
                     FakeCeo(), self.logs.append, chat=chat)
        rc = eng.run_next().rc
        self.assertEqual(rc, 1)
        self.assertFalse(any("Rien à faire" in m for m in self.logs))
        said = [m["text"] for m in chat.since(0) if m.get("kind") == "error"]
        self.assertEqual(len(said), 1)
        self.assertIn("tasks.json.corrupt-", said[0])


class UnreadableReportsTest(StateCase):
    def test_reports_say_once_that_the_file_is_unreadable(self):
        self.paths.reports.write_text("{oops", encoding="utf-8")
        self.assertEqual(self.store.reports(), [])
        self.assertEqual(self.store.reports(), [])
        said = [m for m in self.logs if "reports.json" in m]
        self.assertEqual(len(said), 1)  # dit une fois, pas à chaque rafraîchissement de la page
        self.assertIn("illisible", said[0])


if __name__ == "__main__":
    unittest.main()
