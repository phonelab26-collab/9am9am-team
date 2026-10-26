import errno
import fcntl
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from orch import tasks as T
from orch import lockfile as L
from orch.config import ConfigStore, Paths
from orch.lockfile import Lease, LockTimeout, StateLock, lock_for
from orch.store import Store, StoreError, kill_orphan_group

FIXTURES = Path(__file__).parent / "fixtures"


class TmpStateCase(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="orch-test-"))
        self.paths = Paths.for_root(self.dir)
        self.logs = []
        self.store = Store(self.paths, self.logs.append)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write_tasks(self, data):
        self.paths.state.mkdir(parents=True, exist_ok=True)
        self.paths.tasks.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def plan(self, goal="G", n=2, worker="opencode"):
        items = [{"title": f"t{i}", "worker": worker, "detail": ""} for i in range(1, n + 1)]
        return self.store.add_plan(goal, items)


class LockTest(TmpStateCase):
    def test_reentrant_in_one_thread(self):
        lock = lock_for(self.paths.lock)
        with lock.held(), lock.held():
            pass

    def test_same_path_same_lock(self):
        self.assertIs(lock_for(self.paths.lock), lock_for(Path(str(self.paths.lock))))

    def test_exclusive_between_threads_with_explicit_timeout(self):
        lock = StateLock(self.dir / "x.lock", timeout=0.3)
        inside = threading.Event()
        release = threading.Event()

        def hold():
            with lock.held():
                inside.set()
                release.wait(5)

        th = threading.Thread(target=hold)
        th.start()
        inside.wait(5)
        try:
            with self.assertRaises(LockTimeout), lock.held():
                pass
        finally:
            release.set()
            th.join()

    def test_lease_held_then_free(self):
        path = self.dir / "runs" / "r1.lock"
        lease = Lease.acquire(path)
        self.assertTrue(Lease.is_held(path))
        lease.release()
        self.assertFalse(Lease.is_held(path))
        self.assertFalse(path.exists())

    def test_file_lock_timeout_when_another_process_holds_it(self):
        # Le RLock est libre, mais un flock étranger (un autre process) bloque
        # : c'est le message « verrouillé par un autre process ».
        path = self.dir / "z.lock"
        lock = StateLock(path, timeout=0.2)
        fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            with self.assertRaisesRegex(LockTimeout, "un autre process"):
                with lock.held():
                    pass
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def test_unexpected_flock_errno_propagates(self):
        with mock.patch("fcntl.flock", side_effect=OSError(errno.EACCES, "refusé")):
            with self.assertRaises(OSError):
                L._try_flock(1)

    def test_second_lease_on_same_file_refused(self):
        path = self.dir / "runs" / "r2.lock"
        lease = Lease.acquire(path)
        try:
            with self.assertRaisesRegex(LockTimeout, "déjà tenu"):
                Lease.acquire(path)
        finally:
            lease.release()

    def test_release_is_idempotent(self):
        path = self.dir / "runs" / "r3.lock"
        lease = Lease.acquire(path)
        lease.release()
        lease.release()  # second appel : fd déjà refermé, no-op

    def test_release_survives_already_removed_file(self):
        path = self.dir / "runs" / "r4.lock"
        lease = Lease.acquire(path)
        path.unlink()  # un nettoyage concurrent a pu le supprimer
        lease.release()  # FileNotFoundError ignoré
        self.assertFalse(Lease.is_held(path))


class OrphanKillTest(unittest.TestCase):
    """kill_orphan_group : arrêter le groupe d'un worker mort, mais JAMAIS un
    pgid réattribué entre-temps à un autre programme."""

    def test_real_orphan_group_is_killed(self):
        p = subprocess.Popen(["sleep", "60"], start_new_session=True)
        try:
            pgid = os.getpgid(p.pid)
            msgs = kill_orphan_group(pgid, "sleep")
            self.assertTrue(any("arrêté" in m for m in msgs), msgs)
            p.wait(timeout=5)
            self.assertIsNotNone(p.returncode)  # le process est bien sorti
        finally:
            if p.poll() is None:
                p.kill()
                p.wait()

    def test_ps_failure_leaves_group_alone(self):
        with mock.patch("subprocess.run", side_effect=OSError("pas de ps")):
            msgs = kill_orphan_group(1234, "sleep")
        self.assertEqual(len(msgs), 1)
        self.assertIn("non vérifié", msgs[0])

    def test_unknown_pgid_is_ignored(self):
        with mock.patch("subprocess.run") as run:
            run.return_value.stdout = "777 /bin/other\n"
            self.assertEqual(kill_orphan_group(1234, "sleep"), [])

    def test_reattributed_pgid_left_alone(self):
        # Le pgid existe mais porte un autre programme : on ne tue pas l'innocent.
        with mock.patch("subprocess.run") as run:
            run.return_value.stdout = "1234 /usr/bin/vim f.txt\n"
            with mock.patch("orch.store.kill_group") as kg:
                msgs = kill_orphan_group(1234, "sleep")
        kg.assert_not_called()
        self.assertIn("ne correspond pas à 'sleep'", msgs[0])

    def test_interpreted_command_matches_within_first_words(self):
        # `node …/opencode run` : le nom du worker figure dans les 3 premiers mots.
        with mock.patch("subprocess.run") as run:
            run.return_value.stdout = "1234 node /opt/w/opencode run --fast\n"
            with mock.patch("orch.store.kill_group") as kg:
                msgs = kill_orphan_group(1234, "opencode")
        kg.assert_called_once_with(1234)
        self.assertIn("arrêté", msgs[0])


class ReadWriteTest(TmpStateCase):
    def test_legacy_file_read_without_rewrite(self):
        self.write_tasks(json.loads((FIXTURES / "legacy_tasks.json").read_text(encoding="utf-8")))
        before = self.paths.tasks.read_bytes()
        self.assertEqual(len(self.store.snapshot()), 13)
        self.assertEqual(self.paths.tasks.read_bytes(), before)

    def test_checkout_releases_lease_when_the_write_fails(self):
        # Échec d'écriture au milieu du checkout : aucun bail ne doit fuiter.
        self.plan(n=1)
        with mock.patch("orch.store.atomic_write_json", side_effect=OSError("disque plein")):
            with self.assertRaises(OSError):
                self.store.checkout()
        self.assertEqual(list(self.paths.runs.glob("*.lock")), [], "un bail a fui après l'échec")

    def test_set_child_without_ownership_changes_nothing(self):
        self.write_tasks([{"id": 1, "title": "x", "status": "todo"}])
        before = self.paths.tasks.read_bytes()
        self.store.set_child(1, "run-x", 4242, "opencode")  # personne ne la possède
        self.assertEqual(self.paths.tasks.read_bytes(), before)

    def test_recover_stale_kills_the_orphan_child(self):
        killed = []

        def killer(pgid, cmd):
            killed.append((pgid, cmd))
            return [f"worker orphelin {cmd} (groupe {pgid}) arrêté."]

        store = Store(self.paths, self.logs.append, orphan_killer=killer)
        # Le groupe doit avoir été lancé (et inscrit) par ce programme : un pgid simplement
        # écrit dans tasks.json n'est jamais tué (voir test_untrusted_state).
        self.plan(n=1)
        claim = store.checkout()
        store.set_child(claim.task["id"], claim.run_id, 4242, "opencode")
        claim.lease.release()  # le process propriétaire meurt
        msgs = store.recover_stale()
        self.assertEqual(killed, [(4242, "opencode")])
        self.assertTrue(any("arrêté" in m for m in msgs))
        self.assertEqual(store.snapshot()[0]["status"], "blocked")

    def test_legacy_file_migrated_to_versioned_envelope_on_first_write(self):
        # schema 1 (liste) lu tel quel, puis réécrit au format courant à la
        # première mutation — la migration est automatique et n'altère rien.
        self.write_tasks(json.loads((FIXTURES / "legacy_tasks.json").read_text(encoding="utf-8")))
        self.plan(goal="ajout", n=1)  # mutation → réécriture
        saved = json.loads(self.paths.tasks.read_text(encoding="utf-8"))
        self.assertEqual(saved["schema"], 2)
        self.assertEqual(len(saved["tasks"]), 14)  # 13 legacy + 1 ajoutée

    def test_writes_are_versioned(self):
        self.plan(goal="G", n=1)
        saved = json.loads(self.paths.tasks.read_text(encoding="utf-8"))
        self.assertEqual(saved["schema"], 2)
        self.assertEqual(len(saved["tasks"]), 1)

    def test_schema_from_newer_version_refused_read_and_write(self):
        # Un fichier d'une version plus récente ne doit JAMAIS être écrasé.
        self.write_tasks({"schema": 99, "tasks": []})
        with self.assertRaisesRegex(StoreError, "version plus récente"):
            self.store.snapshot()
        with self.assertRaisesRegex(StoreError, "version plus récente"):
            self.store.reset_stuck()
        self.assertEqual(json.loads(self.paths.tasks.read_text(encoding="utf-8"))["schema"], 99)

    def test_broken_envelope_refused(self):
        self.write_tasks({"schema": 2})  # tasks manquant
        with self.assertRaises(StoreError):
            self.store.snapshot()
        self.write_tasks({"schema": "two", "tasks": []})  # schema non entier
        with self.assertRaises(StoreError):
            self.store.snapshot()

    def test_incoherent_fields_are_refused_cleanly_never_a_traceback(self):
        # JSON valide mais champ incohérent : message propre et rien d'écrit (avant ce
        # filet, checkout/recover_stale partaient en ValueError non gérée).
        self.write_tasks([{"id": 1, "title": "x", "goal": "G", "status": "todo", "attempts": "abc"}])
        with self.assertRaisesRegex(StoreError, "attempts"):
            self.store.snapshot()
        with self.assertRaises(StoreError):
            self.store.checkout()
        self.write_tasks([{"id": 1, "title": "x", "goal": "G", "status": "in_progress", "attempts": 1,
                           "owner": {"run_id": "r", "phase": "work", "child_pgid": "xyz"}}])
        with self.assertRaisesRegex(StoreError, "child_pgid"):
            self.store.recover_stale()
        self.assertEqual(list(self.paths.runs.glob("*.lock")), [], "aucun bail ne doit fuir")

    def test_unknown_fields_and_order_preserved(self):
        self.write_tasks([{"id": 7, "title": "x", "status": "blocked", "attempts": 1, "perso": {"a": 1}}])
        self.store.reset_stuck()
        saved = json.loads(self.paths.tasks.read_text(encoding="utf-8"))
        self.assertEqual(saved["schema"], 2)  # enveloppe versionnée à l'écriture
        self.assertEqual(saved["tasks"][0]["perso"], {"a": 1})
        self.assertEqual(list(saved["tasks"][0])[:3], ["id", "title", "status"])

    def test_invalid_json_quarantined_on_write_only(self):
        self.paths.state.mkdir(parents=True)
        self.paths.tasks.write_text('[{"id": 1, oops', encoding="utf-8")
        with self.assertRaises(StoreError):
            self.store.snapshot()  # lecture seule : on signale, on ne déplace rien
        self.assertTrue(self.paths.tasks.exists())
        self.plan()
        kept = list(self.paths.state.glob("tasks.json.corrupt-*"))
        self.assertEqual(len(kept), 1)
        self.assertEqual(len(self.store.snapshot()), 2)
        self.assertTrue(any("illisible" in m for m in self.logs))

    def test_second_quarantine_does_not_overwrite_first(self):
        self.paths.state.mkdir(parents=True)
        for _ in range(2):
            self.paths.tasks.write_text("{oops", encoding="utf-8")
            self.plan()
        self.assertEqual(len(list(self.paths.state.glob("tasks.json.corrupt-*"))), 2)

    def test_invalid_schema_refused_and_file_untouched(self):
        self.write_tasks([{"id": 1, "status": "todo"}, {"id": 1, "status": "todo"}])
        before = self.paths.tasks.read_bytes()
        with self.assertRaises(StoreError):
            self.plan()
        self.assertEqual(self.paths.tasks.read_bytes(), before)

    def test_no_tmp_left_and_private_mode(self):
        self.plan()
        self.assertEqual(list(self.paths.state.glob(".*.tmp")), [])
        self.assertEqual(self.paths.tasks.stat().st_mode & 0o777, 0o600)


class OperationsTest(TmpStateCase):
    def test_add_plan_ids_and_if_absent(self):
        plan_id, ids = self.plan(n=3)
        self.assertTrue(plan_id)
        self.assertEqual(ids, [1, 2, 3])
        self.assertEqual(self.store.add_plan("G", [{"title": "x", "worker": "opencode"}]), (None, []))
        self.assertEqual(self.plan(goal="H", n=1)[1], [4])

    def test_checkout_claims_in_order_and_holds_lease(self):
        self.plan(n=2)
        claim = self.store.checkout()
        t = T.find(self.store.snapshot(), claim.task["id"])
        self.assertEqual((t["id"], t["status"], t["attempts"]), (1, "in_progress", 1))
        self.assertEqual(t["owner"]["phase"], "work")
        self.assertTrue(Lease.is_held(self.paths.runs / f"{claim.run_id}.lock"))
        self.assertIsNone(self.store.checkout().task)  # la 2 attend la 1
        claim.lease.release()

    def test_record_and_finalize_flow(self):
        self.plan(n=1)
        c = self.store.checkout()
        rec = self.store.record_result(c.task["id"], c.run_id, 0, "fait", supervise=True)
        self.assertEqual((rec["status"], rec["owner"]["phase"]), ("in_progress", "review"))
        fin = self.store.finalize(c.task["id"], c.run_id, "ko", "manque X")
        self.assertEqual((fin["status"], fin["review"]), ("blocked", "manque X"))
        self.assertNotIn("owner", fin)
        c.lease.release()

    def test_wrong_run_id_is_refused(self):
        self.plan(n=1)
        c = self.store.checkout()
        self.assertIsNone(self.store.record_result(c.task["id"], "autre", 0, "x", supervise=False))
        self.assertTrue(any("refus" in m for m in self.logs))
        c.lease.release()

    def test_cancel_gives_the_attempt_back(self):
        self.plan(n=1)
        c = self.store.checkout()
        t = self.store.record_result(c.task["id"], c.run_id, 130, "", supervise=True, cancelled=True)
        self.assertEqual((t["status"], t["attempts"]), ("blocked", 0))
        c.lease.release()

    def test_worker_failure_blocks_then_fails(self):
        self.plan(n=1)
        for expected in ("blocked", "blocked", "failed"):
            self.store.requeue_blocked("G")
            c = self.store.checkout()
            t = self.store.record_result(c.task["id"], c.run_id, 1, "boom", supervise=True)
            c.lease.release()
            self.assertEqual(t["status"], expected)

    def test_recover_leaves_live_run_alone(self):
        self.plan(n=1)
        c = self.store.checkout()
        self.assertEqual(self.store.recover_stale(), [])
        self.assertEqual(self.store.snapshot()[0]["status"], "in_progress")
        c.lease.release()

    def test_recover_dead_run_in_work_and_review_phases(self):
        self.plan(n=2, goal="G")
        self.plan(n=1, goal="H")
        c1 = self.store.checkout()
        c3 = self.store.checkout(goal="H")
        self.store.record_result(c3.task["id"], c3.run_id, 0, "fait", supervise=True)
        c1.lease.release(delete=False)  # process « mort » : bail libéré, fichier resté
        c3.lease.release(delete=False)
        msgs = self.store.recover_stale()
        snap = {t["id"]: t for t in self.store.snapshot()}
        self.assertEqual(snap[1]["status"], "blocked")
        self.assertEqual((snap[3]["status"], snap[3]["verdict"]), ("done", T.UNSUPERVISED))
        self.assertEqual(len(msgs), 2)
        self.assertEqual(list(self.paths.runs.glob("*.lock")), [])

    def test_recover_legacy_in_progress_without_owner(self):
        self.write_tasks([{"id": 1, "title": "x", "status": "in_progress"}])
        self.store.recover_stale()
        self.assertEqual(self.store.snapshot()[0]["status"], "blocked")

    def test_requeue_and_reset(self):
        self.write_tasks([{"id": 1, "goal": "G", "status": "blocked", "attempts": 1},
                          {"id": 2, "goal": "G", "status": "failed", "attempts": 3}])
        self.assertEqual(self.store.requeue_blocked("G"), 1)
        self.assertEqual(self.store.reset_stuck(), 1)
        self.assertEqual([t["status"] for t in self.store.snapshot()], ["todo", "todo"])


class EscalationStoreTest(TmpStateCase):
    def test_requeue_with_escalation_hands_the_task_to_claude(self):
        self.write_tasks([{"id": 1, "goal": "G", "worker": "opencode", "status": "blocked", "attempts": 1,
                           "last_failure": "ceo_ko"}])
        self.assertEqual(self.store.requeue_blocked("G", escalate=True), 1)
        t = self.store.snapshot()[0]
        self.assertEqual((t["status"], t["worker"], t["tier"], t["origin_worker"]), ("todo", "claude", 1, "opencode"))

    def test_user_cancel_is_flagged_and_never_escalates(self):
        self.write_tasks([{"id": 1, "goal": "G", "worker": "opencode", "status": "todo", "attempts": 0}])
        c = self.store.checkout()
        self.store.record_result(c.task["id"], c.run_id, 130, "", supervise=True, cancelled=True)
        self.assertTrue(self.store.snapshot()[0]["interrupted"])
        self.assertEqual(self.store.requeue_blocked("G", escalate=True), 1)
        t = self.store.snapshot()[0]
        self.assertEqual((t["worker"], "interrupted" in t), ("opencode", False))

    def test_a_real_failure_after_a_cancel_is_escalatable_again(self):
        self.write_tasks([{"id": 1, "goal": "G", "worker": "opencode", "status": "todo", "attempts": 0,
                           "interrupted": True}])
        c = self.store.checkout()
        self.store.record_result(c.task["id"], c.run_id, 1, "boom", supervise=True)
        self.assertNotIn("interrupted", self.store.snapshot()[0])

    def test_failure_cause_is_recorded(self):
        self.write_tasks([{"id": 1, "goal": "G", "worker": "opencode", "status": "todo", "attempts": 0}])
        c = self.store.checkout()
        self.store.record_result(c.task["id"], c.run_id, 124, "(délai dépassé)", supervise=True)
        self.assertEqual(self.store.snapshot()[0]["last_failure"], "worker_error")
        self.store.requeue_blocked("G", escalate=True)
        self.assertEqual(self.store.snapshot()[0]["worker"], "opencode")  # pas d'escalade sur un timeout
        c = self.store.checkout()
        self.store.record_result(c.task["id"], c.run_id, 0, "fait", supervise=True)
        self.store.finalize(c.task["id"], c.run_id, "ko", "insuffisant")
        self.assertEqual(self.store.snapshot()[0]["last_failure"], "ceo_ko")

    def test_crash_during_verification_is_an_interruption_never_a_silent_pass(self):
        self.write_tasks([{"id": 1, "goal": "G", "worker": "opencode", "status": "in_progress", "attempts": 1,
                           "owner": {"pid": 1, "run_id": "mort", "phase": "verify"}}])
        self.store.recover_stale()
        t = self.store.snapshot()[0]
        self.assertEqual((t["status"], t.get("interrupted"), t["attempts"]), ("blocked", True, 0))
        self.assertNotEqual(t.get("verdict"), "non_supervise")

    def test_verify_phase_roundtrip(self):
        self.write_tasks([{"id": 1, "goal": "G", "worker": "opencode", "status": "todo", "attempts": 0}])
        c = self.store.checkout()
        self.store.record_result(c.task["id"], c.run_id, 0, "fait", supervise=True)
        self.assertEqual(self.store.begin_verify(1, c.run_id)["owner"]["phase"], "verify")
        self.assertEqual(self.store.end_verify(1, c.run_id)["owner"]["phase"], "review")
        self.assertEqual(self.store.finalize(1, c.run_id, "ok", "")["status"], "done")

    def test_crash_recovery_is_an_interruption_not_a_failure(self):
        self.write_tasks([{"id": 1, "goal": "G", "worker": "opencode", "status": "in_progress", "attempts": 1,
                           "owner": {"pid": 1, "run_id": "mort", "phase": "work"}}])
        self.store.recover_stale()
        self.assertTrue(self.store.snapshot()[0]["interrupted"])
        self.store.requeue_blocked("G", escalate=True)
        t = self.store.snapshot()[0]
        self.assertEqual((t["status"], t["worker"], "interrupted" in t), ("todo", "opencode", False))

    def test_manual_retry_keeps_the_tier_but_resets_attempts(self):
        self.write_tasks([{"id": 1, "goal": "G", "worker": "claude", "tier": 2, "status": "failed",
                           "attempts": 3}])
        self.store.reset_stuck()
        t = self.store.snapshot()[0]
        self.assertEqual((t["status"], t["attempts"], t["tier"], t["worker"]), ("todo", 0, 2, "claude"))


class ReportsTest(TmpStateCase):
    def test_add_replace_and_list(self):
        self.assertEqual(self.store.reports(), [])
        self.store.add_report("G", "premier")
        self.store.add_report("H", "autre")
        self.store.add_report("G", "second")
        reps = self.store.reports()
        self.assertEqual([(r["goal"], r["text"]) for r in reps], [("G", "second"), ("H", "autre")])
        self.assertTrue(all(r["at"] for r in reps))
        self.assertEqual(self.paths.reports.stat().st_mode & 0o777, 0o600)

    def test_keeps_only_recent_ones(self):
        for i in range(25):
            self.store.add_report(f"G{i}", "x")
        self.assertEqual(len(self.store.reports()), 20)

    def test_unreadable_file_is_tolerated_and_kept(self):
        self.paths.state.mkdir(parents=True)
        self.paths.reports.write_text("{oops", encoding="utf-8")
        self.assertEqual(self.store.reports(), [])
        self.store.add_report("G", "ok")
        self.assertEqual(len(self.store.reports()), 1)
        self.assertEqual(len(list(self.paths.state.glob("reports.json.corrupt-*"))), 1)


class ConfigTest(TmpStateCase):
    def setUp(self):
        super().setUp()
        self.cfg = ConfigStore(self.paths, self.logs.append)

    def test_update_merges_and_keeps_dead_models(self):
        self.cfg.update(dead_models={"x/y": 1.0}, opencode_model="a/b")
        self.cfg.update(opencode_model="c/d")
        self.assertEqual(self.cfg.load(), {"dead_models": {"x/y": 1.0}, "opencode_model": "c/d"})
        self.assertEqual(self.paths.config.stat().st_mode & 0o777, 0o600)

    def test_unreadable_config_quarantined(self):
        self.paths.state.mkdir(parents=True)
        self.paths.config.write_text('{"autoclaw_cmd": "important", oops', encoding="utf-8")
        # lot 3 : réglages par défaut, SAUF la reprise payante par Claude, coupée par prudence
        self.assertEqual(self.cfg.load(), {"escalation": "off"})
        self.assertEqual(len(list(self.paths.state.glob("config.json.corrupt-*"))), 1)
        self.assertTrue(any("config.json" in m for m in self.logs))

    def test_invalid_dead_models_reported_not_crashing(self):
        self.cfg.update(dead_models={"a/b": "hier", "c/d": 9e18})
        self.assertEqual(self.cfg.dead_models(now=1e9), {"c/d"})
        self.assertTrue(any("a/b" in m for m in self.logs))

    def test_remember_dead_and_ttl(self):
        self.cfg.remember_dead("m/x", now=1000.0)
        self.assertEqual(self.cfg.dead_models(now=1000.0 + 60), {"m/x"})
        self.assertEqual(self.cfg.dead_models(now=1000.0 + 7 * 3600), set())

    def test_ceo_model_setting(self):
        self.assertEqual(self.cfg.ceo_model(), "")
        self.cfg.update(ceo_model="opus")
        self.assertEqual(self.cfg.ceo_model(), "opus")
        self.cfg.update(ceo_model=5)
        self.assertEqual(self.cfg.ceo_model(), "")

    def test_ceo_mode_setting_defaults_to_local(self):
        self.assertEqual(self.cfg.ceo_mode(), "local")
        self.cfg.update(ceo_mode="cloud")
        self.assertEqual(self.cfg.ceo_mode(), "cloud")
        self.cfg.update(ceo_mode=" CLOUD ")
        self.assertEqual(self.cfg.ceo_mode(), "cloud")
        self.cfg.update(ceo_mode="distant")
        self.assertEqual(self.cfg.ceo_mode(), "local")
        self.cfg.update(ceo_mode=5)
        self.assertEqual(self.cfg.ceo_mode(), "local")

    def test_ceo_cloud_session_setting(self):
        self.assertEqual(self.cfg.ceo_cloud_session(), "")
        self.cfg.update(ceo_cloud_session="session_abc123")
        self.assertEqual(self.cfg.ceo_cloud_session(), "session_abc123")
        self.cfg.update(ceo_cloud_session=5)
        self.assertEqual(self.cfg.ceo_cloud_session(), "")

    def test_worker_cmd_config_over_env(self):
        os.environ["MINI_ORCH_CLINE_CMD"] = "depuis-env"
        try:
            self.assertEqual(self.cfg.worker_cmd("cline"), "depuis-env")
            self.cfg.update(cline_cmd="depuis-config")
            self.assertEqual(self.cfg.worker_cmd("cline"), "depuis-config")
        finally:
            del os.environ["MINI_ORCH_CLINE_CMD"]


if __name__ == "__main__":
    unittest.main()
