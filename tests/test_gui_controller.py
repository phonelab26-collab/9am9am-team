
import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from orch import gui_controller
from orch.gui_controller import GuiController
from orch.procs import CmdResult

ROOT = Path(__file__).resolve().parents[1]


def drain(ctl, seconds=5.0):
    """Messages jusqu'à la fin du job (('done', rc) inclus)."""
    out, end = [], time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            item = ctl.messages.get(timeout=0.05)
        except Exception:  # queue.Empty
            continue
        out.append(item)
        if item[0] == "done":
            break
    return out


class ControllerTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="orch-gui-"))
        self.calls = []

        def runner(cmd, timeout, cwd=None, cancel=None, **kw):
            self.calls.append(cmd)
            return CmdResult(0, "Selftest : tout est OK (3 tests).", "")
        self.ctl = GuiController(self.root, dry_run=True, runner=runner)

    def tearDown(self):
        self.ctl.close(timeout=5)
        shutil.rmtree(self.root, ignore_errors=True)

    def test_team_view_shows_the_effort_of_each_agent(self):
        cfg = self.ctl.engine.cfg
        cfg.update(ceo_effort="ultracode", opencode_variant="high", cline_effort="faible")
        self.assertEqual(self.ctl.effort_label("ceo"), "ultracode")
        self.assertEqual(self.ctl.effort_label("opencode"), "high")
        self.assertEqual(self.ctl.effort_label("cline"), "faible")
        self.assertEqual(self.ctl.effort_label("autoclaw"), "par défaut")
        self.assertEqual(self.ctl.effort_label("claude"), "")  # déjà dans le libellé du renfort
        self.assertEqual(self.ctl.team_view([])["ceo"]["effort"], "ultracode")

    def test_demo_mode_does_not_scan_opencode_and_says_so(self):
        self.assertFalse(self.ctl.scan_free_models())
        scan = self.ctl.state_view(0)["free_models"]
        self.assertEqual(scan["status"], "off")
        self.assertIn("mode démo", scan["error"])
        self.assertFalse(any(cmd[-1:] == ["models"] for cmd in self.calls))

    def test_free_models_scanned_are_offered_in_the_state(self):
        def runner(cmd, timeout, cwd=None, cancel=None, **kw):
            if cmd[-1:] == ["models"]:
                return CmdResult(0, "opencode/zz-new-free\ngoogle/gemini-2.5-pro\n", "")
            return CmdResult(0, "", "")
        ctl = GuiController(self.root, dry_run=False, runner=runner)
        self.addCleanup(ctl.close, 5)
        self.assertTrue(ctl.scan_free_models())
        self.assertTrue(ctl.free_models.join(5))
        state = ctl.state_view(0)
        self.assertEqual(state["free_models"]["status"], "ok")
        self.assertEqual(state["free_models"]["models"], ["opencode/zz-new-free"])
        self.assertIn("opencode/zz-new-free", state["models"])
        self.assertNotIn("google/gemini-2.5-pro", state["models"])

    def test_claude_reinforcement_label_shows_the_ladder(self):
        self.assertEqual(self.ctl.model_label("claude", None), "renfort : Sonnet · élevé → Opus · max")

    # ---- dossier de travail ------------------------------------------------------
    def make_project(self, name="projet"):
        proj = Path(tempfile.mkdtemp(prefix=f"orch-{name}-"))
        self.addCleanup(shutil.rmtree, proj, True)
        return proj.resolve()

    def test_workspace_defaults_to_the_app_folder(self):
        self.assertEqual(self.ctl.workspace_root, self.root.resolve())

    def test_switching_workspace_moves_work_but_keeps_global_settings(self):
        self.ctl.save_setting("ceo_model", "opus")
        proj = self.make_project()
        ok, err = self.ctl.switch_workspace(str(proj))
        self.assertEqual((ok, err), (True, ""))
        self.assertEqual(self.ctl.workspace_root, proj)
        self.assertEqual(self.ctl.engine.ceo._cwd, str(proj))          # le CEO travaille dans le projet
        self.assertEqual(self.ctl.engine.cfg.ceo_model(), "opus")       # réglages globaux conservés
        self.assertTrue((proj / ".mini-orch").is_dir())                 # tâches/conversation du projet
        self.assertFalse((proj / ".mini-orch" / "config.json").exists())  # pas de réglages dispersés

    def test_chosen_workspace_is_remembered_at_next_launch(self):
        proj = self.make_project()
        self.ctl.switch_workspace(str(proj))
        again = GuiController(self.root, dry_run=True, runner=lambda *a, **k: CmdResult(0, "", ""))
        self.addCleanup(again.close, 5)
        self.assertEqual(again.workspace_root, proj)

    def test_startup_recovers_a_task_left_by_a_dead_process(self):
        # kill -9 en pleine tâche : à l'ouverture de l'interface, la tâche repasse en
        # file et c'est annoncé — sans attendre qu'un « Tick » soit lancé.
        state = self.root / ".mini-orch"
        state.mkdir(exist_ok=True)
        (state / "tasks.json").write_text(json.dumps(
            {"schema": 2, "tasks": [{"id": 1, "title": "t", "goal": "G", "worker": "opencode",
                                     "status": "in_progress", "result": "", "attempts": 1,
                                     "owner": {"run_id": "dead1234", "phase": "work"}}]}),
            encoding="utf-8")
        ctl = GuiController(self.root, dry_run=True, runner=lambda *a, **k: CmdResult(0, "", ""))
        self.addCleanup(ctl.close, 5)
        self.assertEqual(ctl.engine.store.snapshot()[0]["status"], "blocked")
        kind, text = ctl.messages.get(timeout=2)
        self.assertEqual(kind, "log")
        self.assertIn("interrompu", text)

    def test_startup_recovers_even_with_an_unreadable_state(self):
        # Un tasks.json incohérent est dit, mais ne doit pas empêcher l'interface de
        # démarrer (et le fichier n'est jamais écrasé).
        state = self.root / ".mini-orch"
        state.mkdir(exist_ok=True)
        (state / "tasks.json").write_text('{"schema": 2}', encoding="utf-8")
        ctl = GuiController(self.root, dry_run=True, runner=lambda *a, **k: CmdResult(0, "", ""))
        self.addCleanup(ctl.close, 5)
        self.assertTrue(any(kind == "log" and "Reprise" in text
                            for kind, text in self._drain_queue(ctl)))
        self.assertTrue(any("PROBLÈME" in ln for ln in ctl.task_lines()))
        self.assertEqual((state / "tasks.json").read_text(encoding="utf-8"), '{"schema": 2}')

    @staticmethod
    def _drain_queue(ctl, seconds=2.0):
        out, end = [], time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                out.append(ctl.messages.get(timeout=0.05))
            except Exception:  # queue.Empty
                return out
        return out

    def test_missing_remembered_workspace_falls_back_to_the_app_folder_and_says_so(self):
        proj = self.make_project()
        self.ctl.switch_workspace(str(proj))
        shutil.rmtree(proj)
        again = GuiController(self.root, dry_run=True, runner=lambda *a, **k: CmdResult(0, "", ""))
        self.addCleanup(again.close, 5)
        self.assertEqual(again.workspace_root, self.root.resolve())
        logs = []
        while not again.messages.empty():
            kind, m = again.messages.get_nowait()
            logs += [m] if kind == "log" else []
        self.assertTrue(any("introuvable" in str(m) for m in logs), logs)

    def test_switch_is_refused_while_working_and_for_bad_folders(self):
        gate = threading.Event()
        self.ctl.start("long", lambda cancel: gate.wait(5) and 0)
        ok, err = self.ctl.switch_workspace(str(self.make_project()))
        gate.set()
        drain(self.ctl)
        self.assertFalse(ok)
        self.assertIn("en cours", err)
        ok, err = self.ctl.switch_workspace("/")
        self.assertEqual((ok, self.ctl.workspace_root), (False, self.root.resolve()))

    def test_unsaved_setting_keeps_the_old_workspace_and_says_why(self):
        proj = self.make_project()
        from orch.config import ConfigStore
        orig = ConfigStore.update

        def broken(cfg, **values):
            if "workspace" in values:
                raise OSError("disque plein")
            return orig(cfg, **values)
        ConfigStore.update = broken
        self.addCleanup(setattr, ConfigStore, "update", orig)
        ok, err = self.ctl.switch_workspace(str(proj))
        self.assertFalse(ok)
        self.assertIn("disque plein", err)
        self.assertEqual(self.ctl.workspace_root, self.root.resolve())

    def test_state_tells_the_page_which_workspace_it_shows(self):
        proj = self.make_project()
        before = self.ctl.state_view(0)["workspace"]
        self.ctl.switch_workspace(str(proj))
        self.assertNotEqual(self.ctl.state_view(0)["workspace"], before)

    def test_pick_workspace_uses_the_native_picker_then_switches(self):
        proj = self.make_project()
        self.ctl._run = lambda cmd, timeout, **k: CmdResult(0, str(proj) + "/\n", "")
        res = self.ctl.pick_workspace()
        self.assertEqual((res["ok"], res["root"]), (True, proj.name))
        self.assertEqual(self.ctl.workspace_root, proj)
        self.ctl._run = lambda cmd, timeout, **k: CmdResult(1, "", "User canceled. (-128)")
        self.assertEqual(self.ctl.pick_workspace(), {"ok": False, "cancelled": True})
        self.assertEqual(self.ctl.workspace_root, proj)

    def test_one_job_at_a_time_and_done_message(self):
        gate = threading.Event()
        self.assertTrue(self.ctl.start("long", lambda cancel: gate.wait(5) and 0))
        self.assertTrue(self.ctl.busy)
        self.assertFalse(self.ctl.start("autre", lambda cancel: 0))
        gate.set()
        msgs = drain(self.ctl)
        self.assertEqual(msgs[-1], ("done", 0))
        self.assertFalse(self.ctl.busy)

    def test_start_is_atomic_across_threads(self):
        barrier, gate, results = threading.Barrier(8), threading.Event(), []
        real = gui_controller._new_cancel

        def slow_cancel():  # élargit la plage de temps entre « occupé ? » et le démarrage
            time.sleep(0.05)
            return real()
        self.addCleanup(setattr, gui_controller, "_new_cancel", real)
        gui_controller._new_cancel = slow_cancel

        def call():
            barrier.wait()
            results.append(self.ctl.start("job", lambda cancel: (gate.wait(5), 0)[1]))
        threads = [threading.Thread(target=call) for _ in range(8)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        gate.set()
        drain(self.ctl)
        self.assertEqual(results.count(True), 1)

    def test_stop_reaches_the_job(self):
        seen = []

        def job(cancel):
            seen.append(cancel.wait(5))
            return 1
        self.ctl.start("job", job)
        self.ctl.stop()
        drain(self.ctl)
        self.assertEqual(seen, [True])

    def test_close_while_busy_cancels_and_waits(self):
        self.ctl.start("job", lambda cancel: (cancel.wait(5), 1)[1])
        self.assertTrue(self.ctl.close(timeout=5))
        self.assertFalse(self.ctl.busy)

    def test_job_exception_is_reported(self):
        def boom(cancel):
            raise RuntimeError("bug")
        self.ctl.start("job", boom)
        msgs = drain(self.ctl)
        self.assertTrue(any(kind == "log" and "RuntimeError" in text for kind, text in msgs[:-1]))
        self.assertEqual(msgs[-1], ("done", 1))

    def test_engine_actions_run_in_dry_run(self):
        self.ctl.start("tick", self.ctl.tick_job("G", ""))
        msgs = drain(self.ctl)
        self.assertEqual(msgs[-1], ("done", 0))
        self.assertTrue(any("Plan CEO" in text for kind, text in msgs if kind == "log"))
        self.assertTrue(any("[1]" in line for line in self.ctl.task_lines()))

    def test_tasks_signature_changes_on_write(self):
        before = self.ctl.tasks_signature()
        self.ctl.start("plan", self.ctl.plan_job("G"))
        drain(self.ctl)
        self.assertNotEqual(self.ctl.tasks_signature(), before)

    def test_task_lines_report_a_broken_file(self):
        (self.root / ".mini-orch").mkdir(exist_ok=True)
        (self.root / ".mini-orch" / "tasks.json").write_text("{oops", encoding="utf-8")
        self.assertTrue(any("PROBLÈME" in ln for ln in self.ctl.task_lines()))

    def test_setting_saves_only_its_key(self):
        self.ctl.save_setting("autoclaw_cmd", "  mon-agent  ")
        self.ctl.save_setting("opencode_model", "a/b")
        cfg = json.loads((self.root / ".mini-orch" / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg, {"autoclaw_cmd": "mon-agent", "opencode_model": "a/b"})

    def test_selftest_runs_in_a_subprocess(self):
        self.ctl.start("selftest", self.ctl.selftest_job)
        msgs = drain(self.ctl)
        self.assertEqual(msgs[-1], ("done", 0))
        self.assertEqual(self.calls[0][0], sys.executable)
        self.assertEqual(self.calls[0][-1], "selftest")

    def test_progress_text(self):
        self.assertEqual(self.ctl.progress_text(), "Prêt.")
        self.ctl.start("Tick", lambda cancel: (cancel.wait(5), 0)[1])
        self.assertIn("Tick", self.ctl.progress_text())
        self.ctl.stop()
        drain(self.ctl)


if __name__ == "__main__":
    unittest.main()
