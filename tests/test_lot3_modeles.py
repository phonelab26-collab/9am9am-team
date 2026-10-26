"""Lot 3, point 1 : modèles de secours = gratuits réellement présents, repli jamais persisté."""
import unittest

from orch.config import FREE_MODELS
from orch.procs import CmdResult
from tests.test_workers import TEXT, FakeRunner, WorkersCase, events

CATALOG = "\n".join(["opencode/mimo-v2.6-flash-free", "opencode/beam-free", "opencode/big-pickle",
                     "google/gemini-2.5-pro", "mon/modele-perso", "opencode/space-bunny-free"])


def timeout():
    return CmdResult(124, "", "", timed_out=True)


class StaleSeedTest(unittest.TestCase):
    def test_seed_only_holds_models_the_catalog_still_has(self):
        for gone in ("opencode/fledge-alpha-free", "opencode/muse-spark-1.3-contributor-free"):
            self.assertNotIn(gone, FREE_MODELS)


class FallbackModelsTest(WorkersCase):
    def test_fallback_uses_free_models_of_the_catalog_never_a_stale_seed(self):
        r = FakeRunner([timeout(), timeout(), CmdResult(0, events(TEXT("ok")), "")], catalog=CATALOG)
        out = self.workers(r).run("opencode", "p", "opencode/mimo-v2.6-flash-free", False)
        self.assertEqual(out.code, 0)
        tried = self.models_run(r)
        self.assertEqual(tried[0], "opencode/mimo-v2.6-flash-free")
        self.assertTrue(all(m in CATALOG.splitlines() for m in tried), tried)
        self.assertNotIn("google/gemini-2.5-pro", tried)  # payant : jamais en repli

    def test_scanned_free_models_come_first_then_the_users_own(self):
        self.cfg.update(custom_models="mon/modele-perso\nabsent/du-catalogue")
        r = FakeRunner([timeout(), timeout(), CmdResult(0, events(TEXT("ok")), "")], catalog=CATALOG)
        w = self.workers(r)
        w.scanned = lambda: ["opencode/space-bunny-free", "opencode/pas-dans-le-catalogue"]
        w.run("opencode", "p", "opencode/mimo-v2.6-flash-free", False)
        tried = self.models_run(r)
        self.assertEqual(tried[:3], ["opencode/mimo-v2.6-flash-free", "mon/modele-perso",
                                     "opencode/space-bunny-free"])
        self.assertNotIn("absent/du-catalogue", tried)
        self.assertNotIn("opencode/pas-dans-le-catalogue", tried)
        self.assertTrue(any("absent" in line and "pas-dans-le-catalogue" in line for line in self.logs))

    def test_fallback_is_not_saved_as_the_users_choice(self):
        self.cfg.update(opencode_model="opencode/mimo-v2.6-flash-free")
        r = FakeRunner([timeout(), CmdResult(0, events(TEXT("ok")), "")], catalog=CATALOG)
        out = self.workers(r).run("opencode", "p", "opencode/mimo-v2.6-flash-free", False)
        self.assertEqual(out.code, 0)
        self.assertEqual(self.cfg.load()["opencode_model"], "opencode/mimo-v2.6-flash-free")
        self.assertIn("opencode/mimo-v2.6-flash-free", self.cfg.dead_models())

    def test_outcome_names_the_model_really_used(self):
        r = FakeRunner([timeout(), CmdResult(0, events(TEXT("ok")), "")], catalog=CATALOG)
        out = self.workers(r).run("opencode", "p", "opencode/mimo-v2.6-flash-free", False)
        used = self.models_run(r)[-1]
        self.assertNotEqual(used, "opencode/mimo-v2.6-flash-free")
        self.assertEqual(out.model, used)

    def test_seed_is_the_last_resort_when_the_catalog_is_unknown(self):
        r = FakeRunner([CmdResult(0, events(TEXT("ok")), "")], catalog="")
        self.workers(r).run("opencode", "p", "a/b", False)
        w = self.workers(FakeRunner([], catalog=""))
        self.assertEqual(w._candidates("a/b"), ["a/b", *FREE_MODELS])


class ScanReachesTheWorkersTest(unittest.TestCase):
    def test_the_controller_gives_the_scan_to_the_workers_even_after_a_switch(self):
        import shutil
        import tempfile
        from pathlib import Path

        from orch.gui_controller import GuiController

        def runner(cmd, timeout, cwd=None, cancel=None, **kw):
            return CmdResult(0, "opencode/zz-new-free\n" if cmd[-1:] == ["models"] else "", "")
        root = Path(tempfile.mkdtemp(prefix="orch-l3-"))
        other = Path(tempfile.mkdtemp(prefix="orch-l3b-"))
        self.addCleanup(shutil.rmtree, root, True)
        self.addCleanup(shutil.rmtree, other, True)
        ctl = GuiController(root, dry_run=False, runner=runner)
        self.addCleanup(ctl.close, 5)
        ctl.scan_free_models()
        ctl.free_models.join(5)
        self.assertEqual(ctl.engine.workers.scanned(), ["opencode/zz-new-free"])
        self.assertEqual(ctl.switch_workspace(str(other.resolve())), (True, ""))
        self.assertEqual(ctl.engine.workers.scanned(), ["opencode/zz-new-free"])


if __name__ == "__main__":
    unittest.main()
