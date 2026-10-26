import http.client
import json
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import urlparse

import os
import subprocess
from orch import webui

ROOT = Path(__file__).resolve().parents[1]


class WebUiCase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="orch-web-"))
        # Le serveur lit MINI_ORCH_OPENCODE / MINI_ORCH_CLAUDE à son démarrage : sans ça, la
        # liste des workers (dont le renfort Claude Code) dépendrait des binaires de la machine.
        fake_bins = mock.patch.dict(
            os.environ, {"MINI_ORCH_OPENCODE": str(ROOT / "tests" / "bin" / "fake_opencode"),
                         "MINI_ORCH_CLAUDE": str(ROOT / "tests" / "bin" / "fake_claude")})
        fake_bins.start()
        self.addCleanup(fake_bins.stop)
        ready = threading.Event()
        self.info = {}

        def on_ready(url, app):
            self.info.update(url=url, app=app)
            ready.set()
        self.rc = []
        self.thread = threading.Thread(target=lambda: self.rc.append(webui.serve(
            self.root, dry_run=True, open_browser=False, on_ready=on_ready)), daemon=True)
        self.thread.start()
        self.assertTrue(ready.wait(10), "le serveur n'a pas démarré")
        u = urlparse(self.info["url"])
        self.host, self.port = u.hostname, u.port
        # Le handshake ?h=nonce pose un cookie contenant le jeton : on suit le vrai
        # parcours du navigateur, ce qui valide le flux de bout en bout à chaque test.
        conn = http.client.HTTPConnection(self.host, self.port, timeout=10)
        conn.request("GET", f"/?h={u.query.split('h=', 1)[1]}",
                     headers={"Host": f"{self.host}:{self.port}"})
        resp = conn.getresponse()
        self.assertEqual(resp.status, 302, "le handshake doit rediriger vers /")
        cookie = dict(kv.strip().split("=", 1) for kv in resp.getheader("Set-Cookie").split(";")
                      if "=" in kv)
        self.token = cookie["orch"]
        self.assertIn("HttpOnly", resp.getheader("Set-Cookie"))
        self.assertIn("SameSite=Strict", resp.getheader("Set-Cookie"))
        conn.close()

    def tearDown(self):
        if self.thread.is_alive():
            self.request("POST", "/api/action", {"action": "quit"})
            self.thread.join(15)
        shutil.rmtree(self.root, ignore_errors=True)

    def request(self, method, path, body=None, token=True, host=None, ctype="application/json"):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=10)
        headers = {"Host": host or f"{self.host}:{self.port}"}
        if token:
            headers["X-Token"] = self.token
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = ctype
        conn.request(method, path, body=data, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        conn.close()
        return resp.status, raw, dict(resp.getheaders())

    def state(self, since=0):
        status, raw, _ = self.request("GET", f"/api/state?since={since}")
        self.assertEqual(status, 200)
        return json.loads(raw)

    def wait_idle(self, seconds=15):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if not self.state()["busy"]:
                return
            time.sleep(0.1)
        self.fail("toujours occupé")


class NoReverseDnsTest(WebUiCase):
    """http.server.HTTPServer.server_bind appelle socket.getfqdn() : une résolution DNS INVERSE
    qui a pris 35 s sur un poste à DNS lent (runner CI macOS) — l'interface restait invisible.
    Le serveur ne tourne que sur 127.0.0.1 : aucune résolution n'est nécessaire."""

    def setUp(self):
        patcher = mock.patch("socket.getfqdn", side_effect=AssertionError("DNS inverse interdit au démarrage"))
        patcher.start()
        self.addCleanup(patcher.stop)
        super().setUp()

    def test_server_starts_and_answers_without_any_reverse_dns_lookup(self):
        self.assertEqual(self.request("GET", "/api/state?since=0")[0], 200)

    def test_server_name_is_the_literal_local_address(self):
        from orch.webui import LocalServer
        server = LocalServer(("127.0.0.1", 0), mock.Mock())
        self.addCleanup(server.server_close)
        self.assertEqual(server.server_name, "127.0.0.1")


class AccessTest(WebUiCase):
    def test_page_needs_the_token(self):
        self.assertEqual(self.request("GET", "/", token=False)[0], 403)
        status, raw, headers = self.request("GET", f"/?t={self.token}", token=False)
        self.assertEqual(status, 200)
        self.assertIn(b"9AM9AM-TEAM", raw)
        self.assertIn("default-src 'none'", headers.get("Content-Security-Policy", ""))
        self.assertEqual(headers.get("Referrer-Policy"), "no-referrer")

    def test_static_files_need_the_token(self):
        self.assertEqual(self.request("GET", "/app.js", token=False)[0], 403)
        self.assertEqual(self.request("GET", f"/app.js?t={self.token}", token=False)[0], 200)
        self.assertEqual(self.request("GET", f"/app.css?t={self.token}", token=False)[0], 200)

    def test_api_needs_the_header_token(self):
        self.assertEqual(self.request("GET", "/api/state", token=False)[0], 403)
        self.assertEqual(self.request("POST", "/api/action", {"action": "retry"}, token=False)[0], 403)

    def test_usage_line_reads_claude_usage_and_is_cached(self):
        from orch.procs import CmdResult
        out = "Current session: 36% used · resets 7pm\nCurrent week (all models): 31% used · resets Oct 8"
        with mock.patch("orch.gui_controller.run_cmd") as rc, \
                mock.patch.object(self.info["app"].ctl, "dry_run", False):
            rc.return_value = CmdResult(0, out, "")
            status, raw, _ = self.request("GET", "/api/usage")
            self.request("GET", "/api/usage")
        data = json.loads(raw)
        self.assertEqual(status, 200)
        self.assertEqual([(m["label"], m["pct"]) for m in data["meters"]], [("session", 36), ("semaine", 31)])
        self.assertEqual(rc.call_args[0][0][1:], ["-p", "/usage"])
        self.assertEqual(rc.call_count, 1)  # 2e requête : servie par le cache

    def test_usage_in_demo_mode_launches_nothing_and_says_so(self):
        with mock.patch("orch.gui_controller.run_cmd") as rc:
            status, raw, _ = self.request("GET", "/api/usage")
        data = json.loads(raw)
        self.assertEqual((status, data["ok"], rc.call_count), (200, False, 0))
        self.assertIn("démo", data["error"])

    def test_usage_failure_is_explained_not_hidden(self):
        from orch.procs import CmdResult
        with mock.patch("orch.gui_controller.run_cmd") as rc, \
                mock.patch.object(self.info["app"].ctl, "dry_run", False):
            rc.return_value = CmdResult(1, "", "pas connecté")
            status, raw, _ = self.request("GET", "/api/usage")
        data = json.loads(raw)
        self.assertEqual((status, data["ok"]), (200, False))
        self.assertIn("pas connecté", data["error"])

    def test_auth_logout_reports_success(self):
        # JAMAIS le vrai `claude auth logout` ici : run_cmd est mocké.
        from orch.procs import CmdResult
        with mock.patch("orch.gui_controller.run_cmd") as rc:
            rc.return_value = CmdResult(0, "", "")
            status, raw, _ = self.request("POST", "/api/auth_logout", {})
        self.assertEqual(status, 200, raw)
        self.assertEqual(rc.call_count, 1)
        self.assertEqual(rc.call_args[0][0][1:], ["auth", "logout"])

    def test_auth_logout_failure_is_explained(self):
        from orch.procs import CmdResult
        with mock.patch("orch.gui_controller.run_cmd") as rc:
            rc.return_value = CmdResult(1, "", "pas connecté")
            status, raw, _ = self.request("POST", "/api/auth_logout", {})
        self.assertEqual(status, 500)
        self.assertIn("pas connecté", json.loads(raw)["error"])

    def test_handshake_nonce_is_single_use(self):
        # Le nonce de l'URL d'ouverture : usage unique, le 2e passage est refusé.
        app = self.info["app"]
        with app.handshake_lock:
            app.handshake.update({"nonce": "nonce-test-123", "at": time.monotonic(), "used": False})
        conn = http.client.HTTPConnection(self.host, self.port, timeout=10)
        conn.request("GET", "/?h=nonce-test-123", headers={"Host": f"{self.host}:{self.port}"})
        self.assertEqual(conn.getresponse().status, 302)
        conn.request("GET", "/?h=nonce-test-123", headers={"Host": f"{self.host}:{self.port}"})
        self.assertEqual(conn.getresponse().status, 403)
        conn.close()

    def test_expired_handshake_is_refused(self):
        app = self.info["app"]
        with app.handshake_lock:
            app.handshake.update({"nonce": "nonce-perime", "at": time.monotonic() - 120, "used": False})
        conn = http.client.HTTPConnection(self.host, self.port, timeout=10)
        conn.request("GET", "/?h=nonce-perime", headers={"Host": f"{self.host}:{self.port}"})
        self.assertEqual(conn.getresponse().status, 403)
        conn.close()

    def test_cookie_grants_static_pages_without_query_token(self):
        # Le navigateur authentifié ne doit plus dépendre d'un ?t= dans l'URL.
        conn = http.client.HTTPConnection(self.host, self.port, timeout=10)
        headers = {"Host": f"{self.host}:{self.port}",
                   "Cookie": f"orch={self.token}"}
        for path in ("/", "/app.js", "/app.css"):
            conn.request("GET", path, headers=headers)
            resp = conn.getresponse()
            self.assertEqual(resp.status, 200, path)
            resp.read()
        conn.close()

    def test_url_opening_contains_no_token(self):
        # Le jeton ne doit jamais apparaître dans l'URL d'ouverture (historique,
        # extensions, copier/coller) : seulement le nonce one-shot.
        self.assertNotIn("t=", urlparse(self.info["url"]).query)
        self.assertIn("h=", urlparse(self.info["url"]).query)

    def test_foreign_host_is_refused(self):  # DNS rebinding
        self.assertEqual(self.request("GET", "/api/state", host="evil.example:80")[0], 403)

    def test_post_must_be_json(self):
        self.assertEqual(self.request("POST", "/api/action", {"action": "retry"}, ctype="text/plain")[0], 415)

    def test_bad_content_length_is_refused(self):
        for value in ("abc", "-5"):
            with self.subTest(value=value):
                conn = http.client.HTTPConnection(self.host, self.port, timeout=10)
                conn.putrequest("POST", "/api/action", skip_host=True)
                for k, v in (("Host", f"{self.host}:{self.port}"), ("X-Token", self.token),
                             ("Content-Type", "application/json"), ("Content-Length", value)):
                    conn.putheader(k, v)
                conn.endheaders()
                self.assertEqual(conn.getresponse().status, 400)
                conn.close()

    def test_unknown_routes(self):
        self.assertEqual(self.request("GET", f"/secret?t={self.token}")[0], 404)
        self.assertEqual(self.request("POST", "/api/action", {"action": "rm -rf"})[0], 400)


class ActionsTest(WebUiCase):
    def test_tick_runs_and_shows_up_in_state(self):
        status, raw, _ = self.request("POST", "/api/action", {"action": "tick", "goal": "G"})
        self.assertEqual(status, 202, raw)
        self.wait_idle()
        s = self.state()
        self.assertEqual([(t["id"], t["status"]) for t in s["tasks"]], [(1, "done")])
        self.assertTrue(any("Plan CEO" in line for line in s["journal"]))
        self.assertEqual(s["last_rc"], 0)
        later = self.state(since=s["next"])
        self.assertEqual(later["journal"], [])

    def test_one_job_at_a_time(self):
        gate = threading.Event()
        self.info["app"].ctl.start("long", lambda cancel: gate.wait(5) and 0)
        try:
            self.assertEqual(self.request("POST", "/api/action", {"action": "retry"})[0], 409)
            self.assertTrue(self.state()["busy"])
        finally:
            gate.set()

    def test_stop_reaches_the_job(self):
        seen = []
        self.info["app"].ctl.start("job", lambda cancel: seen.append(cancel.wait(5)) or 1)
        self.assertEqual(self.request("POST", "/api/action", {"action": "stop"})[0], 202)
        self.wait_idle()
        self.assertEqual(seen, [True])

    def test_detected_verify_command_needs_an_explicit_consent(self):
        (self.root / "Makefile").write_text("check-fast:\n\techo ok\n")
        s = self.state()["settings"]
        self.assertEqual((s["verify_source"], s["verify_files"]), ("pending", ["Makefile"]))
        status, raw, _ = self.request("POST", "/api/verify_approve", {"cmd": "make test"})
        self.assertEqual(status, 409)  # pas la commande affichée : refus, rien d'autorisé
        self.assertIn("make check-fast", json.loads(raw)["error"])
        self.assertEqual(self.request("POST", "/api/verify_approve", {"cmd": 3})[0], 400)
        self.assertEqual(self.request("POST", "/api/verify_approve", {"cmd": "make check-fast"})[0], 200)
        self.assertEqual(self.state()["settings"]["verify_source"], "detected")

    def test_ceo_model_setting_is_validated_and_shown(self):
        for bad in ("--dangerously-skip-permissions", "a b", "x" * 300):
            with self.subTest(bad=bad[:20]):
                self.assertEqual(self.request("POST", "/api/setting", {"key": "ceo_model", "value": bad})[0], 400)
        self.assertEqual(self.request("POST", "/api/setting", {"key": "ceo_model", "value": " opus "})[0], 200)
        s = self.state()
        self.assertEqual(s["settings"]["ceo_model"], "opus")
        self.assertIn("sonnet", s["ceo_models"])
        self.assertEqual(s["team"]["ceo"]["model"], "opus")
        self.assertEqual(self.request("POST", "/api/setting", {"key": "ceo_model", "value": ""})[0], 200)
        self.assertEqual(self.state()["team"]["ceo"]["model"], "par défaut")

    def test_ceo_mode_setting_is_validated_and_shown(self):
        for bad in ("distant", "--cloud", "local cloud", "x" * 100):
            with self.subTest(bad=bad[:20]):
                self.assertEqual(self.request("POST", "/api/setting", {"key": "ceo_mode", "value": bad})[0], 400)
        self.assertEqual(self.state()["settings"]["ceo_mode"], "local")
        self.assertEqual(self.state()["team"]["ceo"]["mode"], "local")
        self.assertEqual(self.request("POST", "/api/setting", {"key": "ceo_mode", "value": " CLOUD "})[0], 200)
        self.assertEqual(self.state()["settings"]["ceo_mode"], "CLOUD".lower())
        # Sans session valide le CEO tourne en local : la page montre le mode EFFECTIF (lot 3).
        ceo = self.state()["team"]["ceo"]
        self.assertEqual((ceo["mode"], ceo["mode_setting"]), ("local", "cloud"))
        self.assertTrue(ceo["fallback"])
        self.request("POST", "/api/setting", {"key": "ceo_cloud_session", "value": "session_abc123"})
        self.assertEqual(self.state()["team"]["ceo"]["mode"], "cloud")
        self.assertEqual(self.request("POST", "/api/setting", {"key": "ceo_mode", "value": "local"})[0], 200)

    def test_ceo_cloud_session_setting_is_validated(self):
        for bad in ("ma session", "https://example.com/x", "--cloud x", "session_"):
            with self.subTest(bad=bad[:20]):
                self.assertEqual(self.request(
                    "POST", "/api/setting", {"key": "ceo_cloud_session", "value": bad})[0], 400)
        good = "https://claude.ai/code/session_abc123"
        self.assertEqual(self.request("POST", "/api/setting",
                                      {"key": "ceo_cloud_session", "value": good})[0], 200)
        self.assertEqual(self.state()["settings"]["ceo_cloud_session"], good)

    def test_ceo_cloud_repo_setting_is_validated(self):
        for bad in ("pas-de-slash", "a b/c", "https://github.com/a/b"):
            with self.subTest(bad=bad):
                self.assertEqual(self.request(
                    "POST", "/api/setting", {"key": "ceo_cloud_repo", "value": bad})[0], 400)
        good = "phonelab26-collab/9am9am-team"
        self.assertEqual(self.request("POST", "/api/setting",
                                      {"key": "ceo_cloud_repo", "value": good})[0], 200)
        self.assertEqual(self.state()["settings"]["ceo_cloud_repo"], good)
        # Le dépôt ne change rien en mode local (c'est le mode cloud qui l'active).
        self.assertEqual(self.state()["team"]["ceo"]["where"], "💻 local")
        self.request("POST", "/api/setting", {"key": "ceo_cloud_session", "value": "session_abc123"})
        self.assertEqual(self.request("POST", "/api/setting", {"key": "ceo_mode", "value": "cloud"})[0], 200)
        s = self.state()
        self.assertEqual(s["team"]["ceo"]["repo"], good)
        self.assertEqual(s["team"]["ceo"]["where"], "☁️ " + good)
        self.assertEqual(self.request("POST", "/api/setting", {"key": "ceo_mode", "value": "local"})[0], 200)

    def test_ui_color_setting_is_validated_and_shown(self):
        # La couleur est dérivée en CSS côté client : la moindre sortie de route
        # (« red », un url(), un point-virgule) serait une porte à CSS.
        for bad in ("red", "#fff", "#abc", "url(javascript:alert(1))", "#e95420;", "rgb(1,2,3)"):
            with self.subTest(bad=bad):
                self.assertEqual(self.request("POST", "/api/setting", {"key": "ui_color", "value": bad})[0], 400)
        self.assertEqual(self.request("POST", "/api/setting", {"key": "ui_color", "value": " #FFB000 "})[0], 200)
        self.assertEqual(self.state()["settings"]["ui_color"], "#ffb000")  # normalisé en minuscules
        # Vide = retour au défaut du projet.
        self.assertEqual(self.request("POST", "/api/setting", {"key": "ui_color", "value": ""})[0], 200)
        self.assertEqual(self.state()["settings"]["ui_color"], "")

    def test_github_repos_endpoint_reports_when_gh_missing(self):
        # Sans binaire `gh`, message explicite. On force l'absence : la machine de
        # dev a souvent `gh` d'installé, et le vrai parcours réseau (4 à 13 s
        # mesurés) dépasserait le timeout de 10 s de ce test.
        from orch.github import GithubRepos
        self.info["app"].repos = GithubRepos(self.info["app"].ctl.log, gh_bin="")
        status, raw, _ = self.request("GET", "/api/github_repos")
        body = json.loads(raw)
        self.assertEqual(status, 503, raw)
        self.assertFalse(body["ok"])
        self.assertFalse(body["gh"])
        self.assertIn("gh", body["error"].lower())

    def test_github_repos_needs_the_token(self):
        status, _, _ = self.request("GET", "/api/github_repos", token=False)
        self.assertEqual(status, 403)

    def test_github_repos_lists_fake_repos(self):
        # Le serveur a déjà construit GithubRepos : on le branche à la main sur un faux `gh`
        # qui respecte la séquence réelle : auth status → auth token → repo list.
        from orch.github import GithubRepos
        from orch.procs import CmdResult
        accounts = json.dumps({"hosts": {"github.com": [
            {"state": "success", "login": "phonelab26-collab", "active": True}]}})
        out = json.dumps([{"nameWithOwner": "phonelab26-collab/9am9am-team",
                           "isPrivate": False, "updated": "2026-09-28"}])
        script = [accounts, out]  # compte actif : aucun jeton à charger

        def runner(cmd, *a, **k):
            return CmdResult(0, script.pop(0), "")

        self.info["app"].repos = GithubRepos(
            self.info["app"].ctl.log, runner=runner, gh_bin="/fake/gh")
        status, raw, _ = self.request("GET", "/api/github_repos?force=1")
        body = json.loads(raw)
        self.assertEqual(status, 200, raw)
        self.assertTrue(body["ok"])
        self.assertEqual(body["repos"][0]["repo"], "phonelab26-collab/9am9am-team")

    def test_free_models_scan_runs_at_opening_and_demo_mode_says_it_skipped(self):
        scan = self.state()["free_models"]
        self.assertEqual(scan["status"], "off")  # « idle » = le scan d'ouverture n'aurait pas eu lieu
        self.assertIn("mode démo", scan["error"])
        status, raw, _ = self.request("POST", "/api/free_models_scan", {})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["ok"], False)

    def test_free_models_scan_needs_the_token(self):
        self.assertEqual(self.request("POST", "/api/free_models_scan", {}, token=False)[0], 403)

    def test_worker_model_shown_in_team(self):
        self.assertTrue(self.state()["team"]["workers"][0]["model"])

    def test_settings_whitelist(self):
        self.assertEqual(self.request("POST", "/api/setting", {"key": "evil", "value": "x"})[0], 400)
        self.assertEqual(self.request("POST", "/api/setting", {"key": "cline_cmd", "value": " cli "})[0], 200)
        cfg = json.loads((self.root / ".mini-orch" / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["cline_cmd"], "cli")
        self.assertEqual(self.state()["settings"]["cline_cmd"], "cli")

    def test_settings_reject_invalid_worker_values(self):
        bad = [
            {"key": "opencode_model", "value": "-model avec espace"},
            {"key": "opencode_agent", "value": "-x y"},
            {"key": "opencode_variant", "value": "avec espace"},
            {"key": "custom_models", "value": "pas-un-model"},
            {"key": "ceo_cloud_repo", "value": "pas de depot"},
            {"key": "ceo_mode", "value": "hybride"},
        ]
        for payload in bad:
            with self.subTest(payload=payload):
                self.assertEqual(self.request("POST", "/api/setting", payload)[0], 400)

    def test_effort_settings_accept_the_five_levels_and_refuse_junk(self):
        for key in ("ceo_effort", "autoclaw_effort", "cline_effort"):
            for good in ("faible", "normal", "élevé", "max", "ultracode", ""):
                with self.subTest(key=key, good=good):
                    self.assertEqual(self.request("POST", "/api/setting", {"key": key, "value": good})[0], 200)
            for bad in ("turbo", "high", "-x"):
                with self.subTest(key=key, bad=bad):
                    self.assertEqual(self.request("POST", "/api/setting", {"key": key, "value": bad})[0], 400)
        self.request("POST", "/api/setting", {"key": "ceo_effort", "value": "Eleve"})
        self.assertEqual(self.state()["settings"]["ceo_effort"], "élevé")  # stocké normalisé

    def test_escalation_setting_accepts_on_off_only(self):
        for good in ("on", "off"):
            self.assertEqual(self.request("POST", "/api/setting", {"key": "escalation", "value": good})[0], 200)
        self.assertEqual(self.request("POST", "/api/setting", {"key": "escalation", "value": "toujours"})[0], 400)
        self.assertEqual(self.state()["settings"]["escalation"], "off")

    def test_verify_command_is_saved_for_the_current_workspace(self):
        self.assertEqual(self.request("POST", "/api/setting", {"key": "verify_cmd", "value": "swift test"})[0], 200)
        self.assertEqual(self.state()["settings"]["verify_cmd"], "swift test")
        self.assertEqual(self.state()["settings"]["verify_source"], "custom")
        self.request("POST", "/api/setting", {"key": "verify_cmd", "value": "off"})
        self.assertEqual(self.state()["settings"]["verify_source"], "off")
        self.request("POST", "/api/setting", {"key": "verify_cmd", "value": ""})
        self.assertIn(self.state()["settings"]["verify_source"], ("detected", "none"))
        for bad in ("TOKEN=x make", "a\nb"):
            self.assertEqual(self.request("POST", "/api/setting", {"key": "verify_cmd", "value": bad})[0], 400)

    def test_custom_agent_keeps_its_effort(self):
        status, raw, _ = self.request("POST", "/api/custom_agent",
                                      {"op": "add", "name": "mono", "cmd": "agent", "effort": "max"})
        self.assertEqual(status, 200, raw)
        self.assertEqual([a["effort"] for a in self.state()["custom_agents"] if a["name"] == "mono"], ["max"])
        status, _, _ = self.request("POST", "/api/custom_agent",
                                    {"op": "add", "name": "mono2", "cmd": "agent", "effort": "turbo"})
        self.assertEqual(status, 400)

    def test_github_force_fetch_is_throttled_to_one_per_minute(self):
        # force=1 deux fois de suite : un seul fetch forcé, le second sert le cache.
        from orch.github import ReposResult

        class CountingRepos:
            def __init__(self):
                self.forced = 0

            def list(self, force=False):
                if force:
                    self.forced += 1
                return ReposResult(True)

            def available(self):
                return True

        repos = CountingRepos()
        self.info["app"].repos = repos
        self.assertEqual(self.request("GET", "/api/github_repos?force=1")[0], 200)
        self.assertEqual(self.request("GET", "/api/github_repos?force=1")[0], 200)
        self.assertEqual(repos.forced, 1, "le second force doit être servi du cache")

    def test_broken_task_file_is_reported(self):
        (self.root / ".mini-orch").mkdir(exist_ok=True)
        (self.root / ".mini-orch" / "tasks.json").write_text("{oops", encoding="utf-8")
        self.assertIn("illisible", self.state()["problem"])

    def test_quit_stops_the_server(self):
        self.assertEqual(self.request("POST", "/api/action", {"action": "quit"})[0], 202)
        self.thread.join(15)
        self.assertFalse(self.thread.is_alive())
        self.assertEqual(self.rc, [0])


class ChatApiTest(WebUiCase):
    def chat(self, since=0):
        status, raw, _ = self.request("GET", f"/api/state?since=0&chat_since={since}")
        self.assertEqual(status, 200)
        return json.loads(raw)

    def test_send_message_and_read_the_conversation(self):
        status, raw, _ = self.request("POST", "/api/chat", {"message": "Bonjour l'équipe"})
        self.assertEqual(status, 202, raw)
        self.wait_idle()
        s = self.chat()
        roles = [m["role"] for m in s["chat"]]
        self.assertEqual(roles[0], "user")
        self.assertEqual(s["chat"][0]["text"], "Bonjour l'équipe")
        self.assertIn("ceo", roles)
        self.assertEqual(s["chat_last"], s["chat"][-1]["id"])
        self.assertEqual(self.chat(since=s["chat_last"])["chat"], [])
        self.assertEqual(len(self.chat(since=1)["chat"]), len(s["chat"]) - 1)

    def test_message_validation(self):
        for body in ({}, {"message": ""}, {"message": "   "}, {"message": 5}, {"message": "x" * 5000}):
            with self.subTest(body=str(body)[:30]):
                self.assertEqual(self.request("POST", "/api/chat", body)[0], 400)

    def test_message_plus_big_pasted_files_is_accepted_and_the_trimming_is_said(self):
        # Lot 3 : le texte (4000) et les pièces jointes ont chacun leur plafond ; ce qui ne
        # tient pas est écarté fichier entier et dit à l'utilisateur, jamais coupé en douce.
        body = {"message": "x" * 4000,
                "pasted": [{"name": f"n{i}.txt", "text": "y" * 6000} for i in range(5)]}
        status, raw, _ = self.request("POST", "/api/chat", body)
        self.assertEqual(status, 202, raw)
        self.assertIn("notes", json.loads(raw))
        self.wait_idle()
        user = self.chat()["chat"][0]
        self.assertEqual(user["text"], "x" * 4000)

    def test_one_conversation_at_a_time(self):
        gate = threading.Event()
        self.info["app"].ctl.start("long", lambda cancel: gate.wait(5) and 0)
        try:
            self.assertEqual(self.request("POST", "/api/chat", {"message": "Salut"})[0], 409)
        finally:
            gate.set()

    def test_chat_needs_the_token(self):
        self.assertEqual(self.request("POST", "/api/chat", {"message": "x"}, token=False)[0], 403)

    def test_chat_with_files_and_workspace_endpoints(self):
        hello = self.root / "HELLO.md"
        hello.write_text("bonjour", encoding="utf-8")
        status, raw, _ = self.request("GET", "/api/workspace?dir=")
        self.assertEqual(status, 200)
        data = json.loads(raw)
        self.assertIn("entries", data)
        self.assertTrue(any("HELLO.md" in e["name"] for e in data["entries"]))
        status, raw, _ = self.request("POST", "/api/workspace_read", {"path": "HELLO.md"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["path"], "HELLO.md")
        self.assertEqual(self.request("POST", "/api/workspace_read", {"path": "../x"})[0], 400)
        status, raw, _ = self.request("GET", "/api/workspace?dir=../x")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["entries"], [])
        status, raw, _ = self.request(
            "POST", "/api/chat", {"message": "Lis le fichier", "files": ["HELLO.md", "../x"]})
        self.assertEqual(status, 202, raw)
        self.assertIn("notes", json.loads(raw))
        self.wait_idle()
        user = self.chat()["chat"][0]
        self.assertEqual(user["text"], "Lis le fichier")  # jamais le bloc brut dans la bulle
        self.assertEqual(user["data"]["files"], ["HELLO.md"])

    def test_chat_with_pasted_local_files(self):
        status, raw, _ = self.request(
            "POST", "/api/chat",
            {"message": "Voici un fichier de mon Mac",
             "pasted": [{"name": "doc.txt", "text": "contenu local"},
                        {"name": "../x", "text": ""}]})
        self.assertEqual(status, 202, raw)
        self.wait_idle()
        user = self.chat()["chat"][0]
        self.assertEqual(user["text"], "Voici un fichier de mon Mac")
        self.assertEqual(user["data"]["files"], ["doc.txt"])

    def test_chat_rejects_oversized_body(self):
        big = "x" * (260 * 1024)
        self.assertEqual(self.request("POST", "/api/chat", {"message": big})[0], 413)


class TeamViewTest(WebUiCase):
    def test_idle_team_and_reports_in_state(self):
        s = self.state()
        self.assertEqual(s["team"]["ceo"]["name"], "Claude Code")
        self.assertFalse(s["team"]["ceo"]["active"])
        self.assertEqual([w["name"] for w in s["team"]["workers"]], ["opencode", "claude"])
        claude = next(w for w in s["team"]["workers"] if w["name"] == "claude")
        self.assertIn("Opus · max", claude["model"])
        self.assertEqual(s["reports"], [])

    def test_ceo_shown_as_working_while_planning_and_reviewing(self):
        ctl = self.info["app"].ctl
        gate = threading.Event()
        ctl.start("Plan CEO", lambda cancel: gate.wait(5) and 0)
        try:
            ceo = self.state()["team"]["ceo"]
            self.assertTrue(ceo["active"])
            self.assertIn("planifie", ceo["state"])
        finally:
            gate.set()
        self.wait_idle()
        store = ctl.engine.store
        store.add_plan("G", [{"title": "t", "worker": "opencode"}])
        claim = store.checkout()
        gate.clear()
        ctl.start("Tick", lambda cancel: gate.wait(5) and 0)
        try:
            team = self.state()["team"]
            self.assertTrue(team["workers"][0]["active"])
            self.assertFalse(team["ceo"]["active"])
            store.record_result(claim.task["id"], claim.run_id, 0, "fait", supervise=True)
            team = self.state()["team"]
            self.assertTrue(team["ceo"]["active"])
            self.assertIn("vérifie", team["ceo"]["state"])
            self.assertFalse(team["workers"][0]["active"])
        finally:
            gate.set()
            claim.lease.release()

    def test_ceo_reflects_while_answering_a_message(self):
        gate = threading.Event()
        self.info["app"].ctl.start("Conversation", lambda cancel: gate.wait(5) and 0)
        try:
            self.assertIn("réfléchit", self.state()["team"]["ceo"]["state"])
        finally:
            gate.set()
        self.wait_idle()

    def test_report_action_and_state(self):
        self.request("POST", "/api/action", {"action": "tick", "goal": "G"})
        self.wait_idle()
        self.info["app"].ctl.engine.store.add_report("G", "Le CEO répond.")
        self.assertEqual(self.state()["reports"][0]["text"], "Le CEO répond.")
        self.assertEqual(self.request("POST", "/api/action", {"action": "report", "goal": "G"})[0], 202)
        self.wait_idle()


class CustomAgentApiTest(WebUiCase):
    """La route la plus sensible : une commande arbitraire stockée puis exécutée."""

    def test_add_and_list_custom_agent(self):
        status, raw, _ = self.request("POST", "/api/custom_agent",
                                      {"op": "add", "name": "mon-api", "cmd": "mon-agent --go",
                                       "model": "opencode/mon-model"})
        self.assertEqual(status, 200, raw)
        body = json.loads(raw)
        self.assertEqual(body["agents"][0]["name"], "mon-api")
        self.assertEqual(self.state()["team"]["workers"][-1]["name"], "mon-api")

    def test_rejects_invalid_name_and_cmd(self):
        for payload in ({"op": "add", "name": "MAUVAIS NOM", "cmd": "x"},
                        {"op": "add", "name": "ok-name", "cmd": ""},
                        {"op": "add", "name": "ok-name", "cmd": "x" * 2001},
                        {"op": "add", "name": "", "cmd": "x"},
                        {"op": "delete", "name": "../evil"},
                        {"op": "delete", "name": ""}):
            with self.subTest(payload=payload):
                self.assertEqual(self.request("POST", "/api/custom_agent", payload)[0], 400)

    def test_rejects_secrets_in_cmd(self):
        for cmd in ("curl -H 'X-API_KEY=abc' http://x", "echo MON_SECRET", "use $TOKEN",
                    "mon-agent --password=toto", "MA_CLE=xyz agent"):
            with self.subTest(cmd=cmd):
                status, raw, _ = self.request("POST", "/api/custom_agent",
                                              {"op": "add", "name": "voleur", "cmd": cmd})
                self.assertEqual(status, 400, raw)
        self.assertEqual(self.state()["team"]["workers"], [
            w for w in self.state()["team"]["workers"] if w["name"] != "voleur"])

    def test_claude_name_is_reserved_for_the_escalation(self):
        status, raw, _ = self.request("POST", "/api/custom_agent", {"op": "add", "name": "claude", "cmd": "x"})
        self.assertEqual(status, 400, raw)

    def test_replaces_existing_agent_with_same_name(self):
        for cmd in ("agent-v1", "agent-v2"):
            self.request("POST", "/api/custom_agent", {"op": "add", "name": "mono", "cmd": cmd})
        cfg = json.loads((self.root / ".mini-orch" / "config.json").read_text(encoding="utf-8"))
        customs = [a for a in cfg["custom_agents"] if a["name"] == "mono"]
        self.assertEqual(len(customs), 1)
        self.assertEqual(customs[0]["cmd"], "agent-v2")

    def test_delete_removes_agent(self):
        self.request("POST", "/api/custom_agent", {"op": "add", "name": "mono", "cmd": "agent"})
        status, raw, _ = self.request("POST", "/api/custom_agent", {"op": "delete", "name": "mono"})
        self.assertEqual(status, 200, raw)
        cfg = json.loads((self.root / ".mini-orch" / "config.json").read_text(encoding="utf-8"))
        self.assertEqual([a for a in cfg["custom_agents"] if a["name"] == "mono"], [])

    def test_cannot_shadow_builtin_worker(self):
        self.assertEqual(self.request("POST", "/api/custom_agent",
                                      {"op": "add", "name": "opencode", "cmd": "evil"})[0], 400)

    def test_unknown_op_is_rejected(self):
        self.assertEqual(self.request("POST", "/api/custom_agent", {"op": "hack"})[0], 400)


class StateApiGuardTest(WebUiCase):
    def test_state_since_bounded_not_crashing(self):
        for bad in ("999999999999999999999", "-5", "abc"):
            with self.subTest(bad=bad):
                status, raw, _ = self.request("GET", f"/api/state?since={bad}&chat_since={bad}")
                self.assertEqual(status, 200, raw)
                self.assertIn("journal", json.loads(raw))

    def test_workspace_traversal_rejected_via_http(self):
        for evil in ("..", "../..", "a/../../etc", "/etc", "a/..%2F.."):
            with self.subTest(evil=evil):
                status, raw, _ = self.request("GET", f"/api/workspace?dir={evil}")
                data = json.loads(raw)
                self.assertIn("hors du dossier", data.get("note", ""), raw)
                self.assertEqual(data.get("entries"), [], raw)


class RateLimitTest(WebUiCase):
    """Chaque appel /api/auth lance un sous-process : il faut un cache court."""

    def test_auth_status_is_cached_briefly(self):
        from orch.procs import CmdResult

        calls = []

        def fake_run(*a, **k):
            calls.append(1)
            return CmdResult(0, json.dumps({"loggedIn": True, "email": "a@b.c"}), "")

        with mock.patch("orch.gui_controller.run_cmd", fake_run):
            for _ in range(5):
                self.request("GET", "/api/auth")
        self.assertEqual(len(calls), 1, "5 appels HTTP = 1 seul sous-process")

    def test_force_refresh_is_throttled(self):
        from orch.github import ReposResult

        app = self.info["app"]
        seen = []

        class FakeRepos:
            def available(self):
                return True

            def list(self, force=False):
                seen.append(force)
                return ReposResult(True)

        app.repos = FakeRepos()
        with app._lock:
            app._last_force = float("-inf")  # fenêtre vide : le 1er force passe, les suivants non
        for _ in range(3):
            self.request("GET", "/api/github_repos?force=1")
        self.assertEqual(seen, [True, False, False],
                         "un seul fetch forcé par fenêtre de 60 s")


class ParentWatchTest(unittest.TestCase):
    def test_callback_fires_when_the_parent_process_dies(self):
        parent = subprocess.Popen(["sleep", "0.4"])
        gone = threading.Event()
        webui.watch_parent(parent.pid, gone.set, interval=0.05)
        self.assertFalse(gone.is_set())
        parent.wait()
        self.assertTrue(gone.wait(5), "le serveur n'a pas vu la disparition de son parent")

    def test_callback_never_fires_while_the_parent_lives(self):
        gone = threading.Event()
        stop = webui.watch_parent(os.getpid(), gone.set, interval=0.05)
        self.assertFalse(gone.wait(0.5))
        stop.set()

    def test_stop_event_ends_the_watch_without_calling_back(self):
        parent = subprocess.Popen(["sleep", "0.3"])
        gone = threading.Event()
        stop = webui.watch_parent(parent.pid, gone.set, interval=0.05)
        stop.set()
        parent.wait()
        self.assertFalse(gone.wait(0.5))


class FrontendStaticTest(unittest.TestCase):
    """Les titres/résultats viennent des workers : jamais interprétés comme du HTML."""

    def test_no_html_injection_sinks(self):
        js = (ROOT / "orch" / "web" / "app.js").read_text(encoding="utf-8")
        for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
            with self.subTest(sink=sink):
                self.assertNotIn(sink, js)

    def test_no_tk_left(self):
        self.assertFalse((ROOT / "orch" / "gui.py").exists())


if __name__ == "__main__":
    unittest.main()
