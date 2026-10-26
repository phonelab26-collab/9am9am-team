"""Lot 3, points 7-8 : pas de processus orphelin, dossier de travail disparu dit comme tel."""
import os
import shutil
import signal
import tempfile
import threading
import time
import unittest
from pathlib import Path

from orch.config import Bins, ConfigStore, Paths
from orch.gui_controller import GuiController
from orch.procs import CmdResult, run_cmd
from orch.verify import INFRA_CODES
from orch.workers import Workers
from tests.test_workers import TEXT, FakeRunner, events


class RunCmdTest(unittest.TestCase):
    def test_already_cancelled_spawns_nothing(self):
        cancel = threading.Event()
        cancel.set()
        marker = Path(tempfile.mkdtemp(prefix="orch-pc-")) / "lance"
        self.addCleanup(shutil.rmtree, marker.parent, True)
        res = run_cmd(["sh", "-c", f"touch {marker}"], timeout=5, cancel=cancel)
        self.assertTrue(res.cancelled)
        self.assertFalse(marker.exists())

    def test_missing_cwd_is_not_a_missing_binary(self):
        gone = Path(tempfile.mkdtemp(prefix="orch-gone-"))
        shutil.rmtree(gone)
        res = run_cmd(["sh", "-c", "true"], timeout=5, cwd=str(gone))
        self.assertNotEqual(res.code, 0)
        self.assertIn("dossier de travail introuvable", res.err)
        self.assertNotIn("binaire", res.err)
        self.assertNotIn(str(gone.parent), res.err)  # pas de chemin système
        self.assertIn(res.code, INFRA_CODES)

    def test_missing_binary_still_says_binary(self):
        res = run_cmd(["/nulle/part/xyz"], timeout=5)
        self.assertIn("binaire introuvable", res.err)


class CatalogCancelTest(unittest.TestCase):
    def test_catalog_scan_receives_the_cancel_event(self):
        d = Path(tempfile.mkdtemp(prefix="orch-cat-"))
        self.addCleanup(shutil.rmtree, d, True)
        paths = Paths.for_root(d)
        seen = []

        def runner(cmd, timeout, cwd=None, cancel=None, on_spawn=None, env=None):
            seen.append((cmd[1:], cancel))
            if cmd[1:] == ["models"]:
                return CmdResult(0, "a/b-free\n", "")
            return CmdResult(0, events(TEXT("ok")), "")
        w = Workers(paths, Bins("/x/claude", "/x/opencode"), ConfigStore(paths, lambda m: None), lambda m: None,
                    runner=runner)
        cancel = threading.Event()
        w.run("opencode", "p", "a/b-free", False, cancel=cancel)
        models_calls = [c for cmd, c in seen if cmd == ["models"]]
        self.assertEqual(models_calls, [cancel])


class CloseJoinsTheScanTest(unittest.TestCase):
    def test_close_waits_for_the_scan_to_end(self):
        started = threading.Event()

        def runner(cmd, timeout, cwd=None, cancel=None, **kw):
            if cmd[-1:] == ["models"]:
                started.set()
                while not cancel.is_set():
                    time.sleep(0.02)
                time.sleep(0.2)  # l'enfant met un instant à mourir
                return CmdResult(130, "", "", cancelled=True)
            return CmdResult(0, "", "")
        root = Path(tempfile.mkdtemp(prefix="orch-cl-"))
        self.addCleanup(shutil.rmtree, root, True)
        ctl = GuiController(root, dry_run=False, runner=runner)
        ctl.scan_free_models()
        self.assertTrue(started.wait(3))
        ctl.close(timeout=5)
        self.assertTrue(ctl.free_models.join(0))  # plus aucun scan vivant après close


class CliSignalsTest(unittest.TestCase):
    def test_install_turns_sigterm_and_sighup_into_keyboard_interrupt(self):
        from orch.cli import install_signal_handlers
        old = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGHUP)}
        self.addCleanup(lambda: [signal.signal(s, h) for s, h in old.items()])
        install_signal_handlers()
        for sig in (signal.SIGTERM, signal.SIGHUP):
            with self.assertRaises(KeyboardInterrupt):
                os.kill(os.getpid(), sig)
                time.sleep(1)


class DeletedRootTest(unittest.TestCase):
    def test_a_deleted_project_root_is_not_recreated_by_any_state_write(self):
        from orch.chat import ChatStore
        from orch.journal import Journal
        from orch.store import Store, StoreError
        root = Path(tempfile.mkdtemp(prefix="orch-root-"))
        paths = Paths.for_root(root)
        logs = []
        journal = Journal(paths, logs.append)
        store, chat = Store(paths, journal), ChatStore(paths, journal)
        store.add_plan("G", [{"title": "x", "worker": "opencode"}])
        shutil.rmtree(root)
        with self.assertRaisesRegex(StoreError, "introuvable"):
            store.add_plan("H", [{"title": "x", "worker": "opencode"}])
        with self.assertRaises(FileNotFoundError):
            chat.add("user", "x")
        journal("encore un message")
        self.assertFalse(root.exists())
        self.assertTrue(any("journal.log inaccessible" in m and "supprimé" in m for m in logs), logs)


if __name__ == "__main__":
    unittest.main()
