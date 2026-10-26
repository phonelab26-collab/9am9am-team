"""« Changer de compte » : déconnexion puis `claude auth login --email` lancé par l'app.

JAMAIS le vrai `claude` ici : un vrai logout déconnecterait le compte de la personne qui lance les
tests. Tout passe par un faux lanceur de commandes."""
import json
import re
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from orch.account import AccountSwitch, clean_email
from orch.gui_controller import GuiController
from orch.procs import CmdResult
from tests.test_webui import WebUiCase

ROOT = Path(__file__).resolve().parents[1]
CLAUDE = "/fake/claude"
EMAIL = "autre.compte@exemple.fr"
CONNECTED = {"ok": True, "loggedIn": True, "email": EMAIL, "plan": "max", "org": ""}
LOGGED_OUT = {"ok": False, "loggedIn": False, "error": "non connecté"}
UNKNOWN = {"ok": False, "error": "état du compte inconnu : panne"}


class FakeRunner:
    """Enregistre chaque commande ; `script` donne le résultat de la n-ième (ou de la suivante)."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = []   # (argv, timeout, cwd)
        self.cancels = []

    def __call__(self, cmd, timeout, cwd=None, cancel=None, **kw):
        self.calls.append((list(cmd), timeout, cwd))
        self.cancels.append(cancel)
        res = self.results.pop(0) if self.results else CmdResult(0, "", "")
        return res(cancel) if callable(res) else res


def make(runner, status=None, logs=None, finished=None):
    logs = [] if logs is None else logs
    statuses = list(status if isinstance(status, list) else [status or CONNECTED])
    return AccountSwitch(claude_bin=lambda: CLAUDE, status=lambda: statuses.pop(0) if len(statuses) > 1 else statuses[0],
                         log=logs.append, cwd=lambda: "/home/neutre", runner=runner,
                         on_finish=(finished or (lambda: None))), logs


def wait_done(sw, seconds=5.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if sw.view()["state"] != "running":
            return sw.view()
        time.sleep(0.01)
    raise AssertionError("le changement de compte ne se termine pas : " + str(sw.view()))


class CleanEmailTest(unittest.TestCase):
    def test_accepts_ordinary_addresses(self):
        for ok in ("a@b.co", "prenom.nom+tag@sous.domaine.fr", " espace@autour.com ", "x_y-z@ex-ample.org"):
            with self.subTest(ok=ok):
                self.assertEqual(clean_email(ok), ok.strip())

    def test_refuses_anything_that_could_be_taken_for_an_option_or_is_not_an_address(self):
        for bad in ("", "   ", "a@b", "-x@b.co", "--sso", "a b@c.de", "a@b.c", "a@@b.co", "a@b.co\n--sso",
                    "a@b.co;rm", "a" * 300 + "@b.co", None, 3, "@b.co", "a@.co", "a@b..co"):
            with self.subTest(bad=bad):
                self.assertIsNone(clean_email(bad))


class SwitchFlowTest(unittest.TestCase):
    def test_logout_then_login_with_the_exact_arguments_from_the_neutral_folder(self):
        runner = FakeRunner(CmdResult(0, "", ""), CmdResult(0, "ok", ""))
        sw, logs = make(runner)
        self.assertEqual(sw.start(EMAIL), (True, ""))
        view = wait_done(sw)
        self.assertEqual([c[0] for c in runner.calls],
                         [[CLAUDE, "auth", "logout"], [CLAUDE, "auth", "login", "--email", EMAIL]])
        self.assertEqual({c[2] for c in runner.calls}, {"/home/neutre"})  # jamais le dossier du projet
        self.assertEqual(view["state"], "done")
        self.assertIn(EMAIL, view["message"])

    def test_the_address_never_reaches_the_journal(self):
        sw, logs = make(FakeRunner())
        sw.start(EMAIL)
        wait_done(sw)
        self.assertTrue(logs)
        self.assertFalse([m for m in logs if EMAIL in m or "exemple.fr" in m], logs)

    def test_logout_failing_on_an_already_logged_out_account_goes_on(self):
        runner = FakeRunner(CmdResult(1, "", "not logged in"), CmdResult(0, "", ""))
        sw, _ = make(runner, status=[LOGGED_OUT, CONNECTED])
        sw.start(EMAIL)
        self.assertEqual(wait_done(sw)["state"], "done")
        self.assertEqual(len(runner.calls), 2)

    def test_logout_failing_while_still_logged_in_stops_before_any_login(self):
        runner = FakeRunner(CmdResult(1, "", "keychain locked"))
        sw, _ = make(runner, status=CONNECTED)
        sw.start(EMAIL)
        view = wait_done(sw)
        self.assertEqual(view["state"], "failed")
        self.assertIn("keychain locked", view["message"])
        self.assertEqual(len(runner.calls), 1)  # aucun login lancé par-dessus

    def test_logout_failing_with_an_unknown_account_state_is_a_failure_not_a_guess(self):
        runner = FakeRunner(CmdResult(1, "", "boom"))
        sw, _ = make(runner, status=UNKNOWN)
        sw.start(EMAIL)
        self.assertEqual(wait_done(sw)["state"], "failed")
        self.assertEqual(len(runner.calls), 1)

    def test_login_timeout_is_said(self):
        runner = FakeRunner(CmdResult(0, "", ""), CmdResult(124, "", "", timed_out=True))
        sw, _ = make(runner)
        sw.start(EMAIL)
        view = wait_done(sw)
        self.assertEqual(view["state"], "failed")
        self.assertIn("délai", view["message"])
        self.assertIn("Copier la commande", view["message"])  # le repli manuel est indiqué

    def test_a_failure_after_the_logout_says_there_is_no_active_account_anymore(self):
        for res in (CmdResult(124, "", "", timed_out=True), CmdResult(1, "", "refusé")):
            with self.subTest(code=res.code):
                sw, _ = make(FakeRunner(CmdResult(0, "", ""), res))
                sw.start(EMAIL)
                msg = wait_done(sw)["message"]
                self.assertIn("compte précédent est déconnecté", msg)

    def test_a_failed_logout_does_not_claim_the_account_is_signed_out(self):
        sw, _ = make(FakeRunner(CmdResult(1, "", "keychain locked")), status=CONNECTED)
        sw.start(EMAIL)
        self.assertNotIn("est déconnecté", wait_done(sw)["message"])

    def test_the_failure_reason_reaches_the_journal_masked_and_without_the_address(self):
        err = f"refus pour {EMAIL} : API_KEY=abcdefghijklmnop1234 boom"
        sw, logs = make(FakeRunner(CmdResult(0, "", ""), CmdResult(1, "", err)))
        sw.start(EMAIL)
        wait_done(sw)
        joined = "\n".join(logs)
        self.assertIn("boom", joined)
        for leak in (EMAIL, "exemple.fr", "abcdefghijklmnop1234"):
            self.assertNotIn(leak, joined)

    def test_login_error_is_shown_with_secrets_masked(self):
        err = "échec : API_KEY=abcdefghijklmnop1234 refusée, Bearer sk-ant-zzzzzzzzzzzzzzzz"
        runner = FakeRunner(CmdResult(0, "", ""), CmdResult(1, "", err))
        sw, _ = make(runner)
        sw.start(EMAIL)
        msg = wait_done(sw)["message"]
        self.assertIn("échec", msg)
        for secret in ("abcdefghijklmnop1234", "sk-ant-zzzz"):
            self.assertNotIn(secret, msg)

    def test_a_login_that_leaves_no_account_is_a_failure(self):
        runner = FakeRunner(CmdResult(0, "", ""), CmdResult(0, "", ""))
        sw, _ = make(runner, status=LOGGED_OUT)
        sw.start(EMAIL)
        self.assertEqual(wait_done(sw)["state"], "failed")

    def test_another_account_chosen_in_the_browser_is_reported_not_hidden(self):
        runner = FakeRunner(CmdResult(0, "", ""), CmdResult(0, "", ""))
        sw, _ = make(runner, status={**CONNECTED, "email": "quelquun.d.autre@exemple.fr"})
        sw.start(EMAIL)
        view = wait_done(sw)
        self.assertEqual(view["state"], "done")
        self.assertIn("quelquun.d.autre@exemple.fr", view["message"])
        self.assertIn("différent", view["message"])

    def test_finish_hook_runs_once_whatever_the_outcome(self):
        for runner in (FakeRunner(), FakeRunner(CmdResult(1, "", "x"))):
            finished = []
            sw, _ = make(runner, finished=lambda: finished.append(1))
            sw.start(EMAIL)
            wait_done(sw)
            time.sleep(0.05)
            self.assertEqual(finished, [1])


class SwitchGuardsTest(unittest.TestCase):
    def test_invalid_address_launches_nothing(self):
        runner = FakeRunner()
        sw, _ = make(runner)
        ok, err = sw.start("--sso")
        self.assertFalse(ok)
        self.assertIn("adresse", err)
        self.assertEqual(runner.calls, [])
        self.assertEqual(sw.view()["state"], "idle")

    def test_second_start_while_running_is_refused(self):
        gate = threading.Event()
        runner = FakeRunner(lambda cancel: (gate.wait(5), CmdResult(0, "", ""))[1])
        sw, _ = make(runner)
        self.assertTrue(sw.start(EMAIL)[0])
        ok, err = sw.start(EMAIL)
        self.assertFalse(ok)
        self.assertIn("déjà en cours", err)
        gate.set()
        wait_done(sw)

    def test_cancel_during_login_stops_the_command_and_says_the_account_is_signed_out(self):
        def blocking(cancel):
            cancel.wait(5)
            return CmdResult(130, "", "", cancelled=cancel.is_set())
        runner = FakeRunner(CmdResult(0, "", ""), blocking)
        sw, _ = make(runner)
        sw.start(EMAIL)
        deadline = time.monotonic() + 3
        while len(runner.calls) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(sw.cancel())
        view = wait_done(sw)
        self.assertEqual(view["state"], "cancelled")
        self.assertIn("déconnecté", view["message"])
        self.assertTrue(runner.cancels[1].is_set())  # le même événement est passé à run_cmd

    def test_nothing_can_start_once_the_app_is_closing(self):
        runner = FakeRunner()
        sw, _ = make(runner)
        self.assertTrue(sw.close(timeout=1))
        ok, err = sw.start(EMAIL)
        self.assertFalse(ok)
        self.assertIn("ferme", err)
        self.assertEqual(runner.calls, [])

    def test_cancel_with_nothing_running_is_a_harmless_false(self):
        sw, _ = make(FakeRunner())
        self.assertFalse(sw.cancel())

    def test_close_cancels_and_waits_so_no_orphan_login_survives(self):
        def blocking(cancel):
            cancel.wait(5)
            return CmdResult(130, "", "", cancelled=True)
        runner = FakeRunner(CmdResult(0, "", ""), blocking)
        sw, _ = make(runner)
        sw.start(EMAIL)
        deadline = time.monotonic() + 3
        while len(runner.calls) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(sw.close(timeout=3))
        self.assertFalse(sw.running)


class ControllerTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="orch-acct-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.calls = []

        def runner(cmd, timeout, cwd=None, cancel=None, **kw):
            self.calls.append(list(cmd))
            return CmdResult(0, "", "")
        self.ctl = GuiController(self.root, dry_run=True, runner=runner)
        self.addCleanup(self.ctl.close, 5)

    def test_demo_mode_launches_nothing_and_says_so(self):
        with mock.patch("orch.account.run_cmd") as rc:
            ok, err = self.ctl.switch_account(EMAIL)
        self.assertFalse(ok)
        self.assertIn("démo", err)
        self.assertEqual(rc.call_count, 0)

    def test_refused_while_a_job_is_running(self):
        gate = threading.Event()
        self.ctl.dry_run = False
        self.assertTrue(self.ctl.start("Test", lambda cancel: (gate.wait(5), 0)[1]))
        with mock.patch("orch.account.run_cmd") as rc:
            ok, err = self.ctl.switch_account(EMAIL)
        gate.set()
        self.assertFalse(ok)
        self.assertIn("travail est en cours", err)
        self.assertEqual(rc.call_count, 0)

    def test_no_job_can_start_while_the_account_is_being_switched(self):
        self.ctl.dry_run = False
        gate = threading.Event()

        def slow(cmd, timeout, cwd=None, cancel=None, **kw):
            gate.wait(5)
            return CmdResult(0, "", "")
        with mock.patch("orch.account.run_cmd", side_effect=slow), \
                mock.patch.object(self.ctl, "auth_status", return_value=CONNECTED):
            self.assertEqual(self.ctl.switch_account(EMAIL), (True, ""))
            self.assertFalse(self.ctl.start("Plan", lambda cancel: 0))  # refusé : compte en cours
            self.assertEqual(self.ctl.state_view(0)["account"]["state"], "running")
            gate.set()
            wait_done(self.ctl.account)

    def test_close_reports_a_switch_that_did_not_stop_in_time(self):
        with mock.patch.object(self.ctl.account, "close", return_value=False):
            self.assertFalse(self.ctl.close(timeout=1))

    def test_manual_logout_is_refused_while_switching_or_working(self):
        self.ctl.dry_run = False
        gate = threading.Event()
        with mock.patch("orch.gui_controller.run_cmd") as rc:
            self.assertTrue(self.ctl.start("Test", lambda cancel: (gate.wait(5), 0)[1]))
            ok, err = self.ctl.auth_logout()
            self.assertFalse(ok)
            self.assertIn("travail est en cours", err)
            gate.set()
            deadline = time.monotonic() + 3
            while self.ctl.busy and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(rc.call_count, 0)

    def test_manual_logout_is_refused_during_a_switch(self):
        self.ctl.dry_run = False
        gate = threading.Event()

        def slow(cmd, timeout, cwd=None, cancel=None, **kw):
            gate.wait(5)
            return CmdResult(0, "", "")
        with mock.patch("orch.account.run_cmd", side_effect=slow), \
                mock.patch.object(self.ctl, "auth_status", return_value=CONNECTED), \
                mock.patch("orch.gui_controller.run_cmd") as manual:
            self.assertEqual(self.ctl.switch_account(EMAIL), (True, ""))
            ok, err = self.ctl.auth_logout()
            self.assertFalse(ok)
            self.assertIn("changement de compte", err)
            self.assertEqual(manual.call_count, 0)
            gate.set()
            wait_done(self.ctl.account)

    def test_state_exposes_the_account_view_without_the_address(self):
        view = self.ctl.state_view(0)["account"]
        self.assertEqual(set(view), {"state", "step", "message"})
        self.assertEqual(view["state"], "idle")


class WebTest(WebUiCase):
    def post(self, path, body):
        status, raw, _ = self.request("POST", path, body)
        return status, json.loads(raw)

    def test_invalid_address_is_a_400(self):
        status, data = self.post("/api/auth_switch", {"email": "--sso"})
        self.assertEqual(status, 400)
        self.assertIn("adresse", data["error"])
        self.assertEqual(self.post("/api/auth_switch", {"email": 3})[0], 400)
        self.assertEqual(self.post("/api/auth_switch", {})[0], 400)

    def test_demo_mode_is_a_409_that_says_why(self):
        status, data = self.post("/api/auth_switch", {"email": EMAIL})
        self.assertEqual(status, 409)
        self.assertIn("démo", data["error"])

    def test_needs_the_access_token(self):
        self.assertEqual(self.request("POST", "/api/auth_switch", {"email": EMAIL}, token=False)[0], 403)
        self.assertEqual(self.request("POST", "/api/auth_switch_cancel", {}, token=False)[0], 403)

    def test_manual_logout_during_a_switch_is_a_409(self):
        app = self.info["app"]
        gate = threading.Event()

        def slow(cmd, timeout, cwd=None, cancel=None, **kw):
            gate.wait(5)
            return CmdResult(0, "", "")
        with mock.patch.object(app.ctl, "dry_run", False), \
                mock.patch("orch.account.run_cmd", side_effect=slow), \
                mock.patch.object(app.ctl, "auth_status", return_value=CONNECTED):
            self.assertEqual(self.post("/api/auth_switch", {"email": EMAIL})[0], 202)
            status, data = self.post("/api/auth_logout", {})
            gate.set()
            end = time.monotonic() + 5
            while time.monotonic() < end and self.state()["account"]["state"] == "running":
                time.sleep(0.02)
        self.assertEqual(status, 409)
        self.assertIn("changement de compte", data["error"])

    def test_a_status_read_started_before_the_switch_never_caches_the_old_account(self):
        app = self.info["app"]

        def stale_status():
            app._drop_account_caches()  # le changement se termine PENDANT la lecture
            return {"ok": True, "email": "ancien@exemple.fr"}
        with mock.patch.object(app.ctl, "auth_status", side_effect=stale_status):
            self.assertEqual(app.auth_status()["email"], "ancien@exemple.fr")  # rendu une fois…
        self.assertIsNone(app._auth_cache)  # …mais jamais gardé 5 s

    def test_a_usage_read_started_before_the_switch_never_caches_the_old_account(self):
        app = self.info["app"]

        def stale_usage():
            app._drop_account_caches()
            return {"ok": True, "meters": []}
        with mock.patch.object(app.ctl, "usage_status", side_effect=stale_usage):
            app.usage_status()
        self.assertIsNone(app._usage_cache)  # sinon 2 min avec l'usage de l'ancien compte

    def test_cancel_without_a_switch_is_ok_and_says_nothing_was_running(self):
        status, data = self.post("/api/auth_switch_cancel", {})
        self.assertEqual((status, data), (200, {"ok": True, "cancelled": False}))

    def test_full_flow_ends_done_and_drops_the_account_and_usage_caches(self):
        app = self.info["app"]
        app._auth_cache = (time.monotonic(), {"ok": True, "email": "ancien@exemple.fr"})
        app._usage_cache = (time.monotonic(), {"ok": True})
        calls = []

        def runner(cmd, timeout, cwd=None, cancel=None, **kw):
            calls.append(list(cmd))
            return CmdResult(0, "", "")
        with mock.patch.object(app.ctl, "dry_run", False), \
                mock.patch("orch.account.run_cmd", side_effect=runner), \
                mock.patch.object(app.ctl, "auth_status", return_value=CONNECTED):
            self.assertEqual(self.post("/api/auth_switch", {"email": EMAIL})[0], 202)
            end = time.monotonic() + 5
            while time.monotonic() < end and self.state()["account"]["state"] == "running":
                time.sleep(0.02)
            view = self.state()["account"]
        self.assertEqual(view["state"], "done")
        self.assertIn(EMAIL, view["message"])
        self.assertEqual([c[1:3] for c in calls], [["auth", "logout"], ["auth", "login"]])
        self.assertIsNone(app._auth_cache)
        self.assertIsNone(app._usage_cache)


class PageWiringTest(unittest.TestCase):
    """Les identifiants que le JS manipule existent dans le HTML, et inversement."""

    def setUp(self):
        self.html = (ROOT / "orch" / "web" / "index.html").read_text(encoding="utf-8")
        self.js = (ROOT / "orch" / "web" / "app.js").read_text(encoding="utf-8")

    def test_ids_are_wired_on_both_sides(self):
        for ident in ("auth-switch", "auth-switch-cancel", "auth-switch-state"):
            with self.subTest(ident=ident):
                self.assertRegex(self.html, rf'id="{ident}"')
                self.assertIn(f'"{ident}"', self.js)

    def test_page_calls_the_two_endpoints_and_the_server_serves_them(self):
        webui = (ROOT / "orch" / "webui.py").read_text(encoding="utf-8")
        for path in ("/api/auth_switch", "/api/auth_switch_cancel"):
            with self.subTest(path=path):
                self.assertIn(f'"{path}"', self.js)
                self.assertIn(f'"{path}"', webui)

    def test_the_page_asks_before_signing_the_current_account_out(self):
        self.assertRegex(self.js, r"confirm\([^)]*(déconnect|Déconnect)")

    def test_manual_command_stays_available_as_a_fallback(self):
        self.assertIn('id="auth-login"', self.html)


if __name__ == "__main__":
    unittest.main()
