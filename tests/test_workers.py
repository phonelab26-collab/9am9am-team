import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from orch.ceo import Ceo
from orch.config import FREE_MODELS, Bins, ConfigStore, Paths
from orch.procs import CmdResult
from orch.workers import Workers


def events(*evs):
    return "\n".join(json.dumps(e) for e in evs)


TEXT = lambda s: {"type": "text", "sessionID": "ses_1", "part": {"type": "text", "text": s}}
TOOL = {"type": "tool_use", "part": {"type": "tool", "tool": "write"}}
ERROR = {"type": "error", "error": {"name": "APIError", "data": {"message": "Unexpected server error"}}}


class FakeRunner:
    """Rejoue des CmdResult ; `models` renvoie le catalogue."""

    def __init__(self, results, catalog=None):
        self.results = list(results)
        self.calls = []
        self.catalog = catalog if catalog is not None else "\n".join(FREE_MODELS)
        self.spawned = []

    def __call__(self, cmd, timeout, cwd=None, cancel=None, on_spawn=None, env=None):
        if cmd[1:] == ["models"]:
            self.calls.append(("models",))
            return CmdResult(0, self.catalog, "")
        self.calls.append((cmd, timeout, cwd))
        self.last_env = dict(env or {})
        if on_spawn:
            on_spawn(4242)
        return self.results.pop(0)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class WorkersCase(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="orch-wk-"))
        self.paths = Paths.for_root(self.dir)
        self.logs = []
        self.cfg = ConfigStore(self.paths, self.logs.append)
        self.bins = Bins(claude="/x/claude", opencode="/x/opencode")
        self.clock = Clock()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def workers(self, runner):
        return Workers(self.paths, self.bins, self.cfg, self.logs.append, runner=runner, clock=self.clock)

    def models_run(self, runner):
        return [c[0][3] for c in runner.calls if c[0] != "models" and len(c[0]) > 3]


class OpencodeTest(WorkersCase):
    def test_success_returns_summary_and_spawn_is_reported(self):
        r = FakeRunner([CmdResult(0, events(TEXT("fini")), "")])
        spawned = []
        out = self.workers(r).run("opencode", "p", FREE_MODELS[0], False,
                                  on_spawn=lambda pid, name: spawned.append((pid, name)))
        self.assertEqual(out.code, 0)
        self.assertIn("fini", out.text)
        self.assertEqual(spawned, [(4242, "opencode")])
        self.assertEqual(r.calls[-1][2], str(self.paths.root))  # cwd = dossier de travail

    def test_frozen_model_falls_back_and_is_set_aside(self):
        r = FakeRunner([CmdResult(124, "", "", timed_out=True), CmdResult(0, events(TEXT("ok")), "")])
        out = self.workers(r).run("opencode", "p", FREE_MODELS[0], False)
        self.assertEqual(out.code, 0)
        self.assertEqual(self.models_run(r), FREE_MODELS[:2])
        self.assertNotIn("opencode_model", self.cfg.load())  # le repli n'écrase pas le choix (lot 3)
        self.assertIn(FREE_MODELS[0], self.cfg.dead_models())

    def test_marker_inside_tool_output_is_not_a_dead_model(self):
        r = FakeRunner([CmdResult(1, events(TEXT("ls: fichier not found")), "")])
        out = self.workers(r).run("opencode", "p", FREE_MODELS[0], False)
        self.assertEqual(out.code, 1)
        self.assertEqual(len(self.models_run(r)), 1)
        self.assertEqual(self.cfg.dead_models(), set())

    def test_error_event_means_dead_model(self):
        r = FakeRunner([CmdResult(0, events(ERROR), ""), CmdResult(0, events(TEXT("ok")), "")])
        out = self.workers(r).run("opencode", "p", FREE_MODELS[0], False)
        self.assertEqual(out.code, 0)
        self.assertIn(FREE_MODELS[0], self.cfg.dead_models())

    def test_no_fallback_once_tools_were_used(self):
        r = FakeRunner([CmdResult(124, events(TOOL), "", timed_out=True)])
        out = self.workers(r).run("opencode", "p", FREE_MODELS[0], False)
        self.assertNotEqual(out.code, 0)
        self.assertEqual(len(self.models_run(r)), 1)
        self.assertIn("pas de relance automatique", out.text)

    def test_cancel_is_not_a_dead_model(self):
        r = FakeRunner([CmdResult(130, "", "", cancelled=True)])
        out = self.workers(r).run("opencode", "p", FREE_MODELS[0], False, cancel=threading.Event())
        self.assertTrue(out.cancelled)
        self.assertEqual(self.cfg.dead_models(), set())
        self.assertEqual(len(self.models_run(r)), 1)

    def test_budget_stops_the_fallback_chain(self):
        def slow(cmd, timeout, cwd=None, cancel=None, on_spawn=None, env=None):
            if cmd[1:] == ["models"]:
                return CmdResult(0, "\n".join(FREE_MODELS), "")
            self.clock.t += 1000
            return CmdResult(124, "", "", timed_out=True)
        out = self.workers(slow).run("opencode", "p", FREE_MODELS[0], False)
        self.assertEqual(out.code, 124)
        self.assertIn("budget", out.text)

    def test_catalog_filters_and_is_cached(self):
        r = FakeRunner([CmdResult(0, events(TEXT("a")), "")] * 2, catalog=FREE_MODELS[1])
        w = self.workers(r)
        w.run("opencode", "p", "inconnu/model", False)
        w.run("opencode", "p", "inconnu/model", False)
        self.assertEqual(self.models_run(r), [FREE_MODELS[1]] * 2)
        self.assertEqual(sum(1 for c in r.calls if c == ("models",)), 1)
        self.clock.t += 3600
        r.results.append(CmdResult(0, events(TEXT("a")), ""))
        w.run("opencode", "p", "inconnu/model", False)
        self.assertEqual(sum(1 for c in r.calls if c == ("models",)), 2)

    def test_all_dead_are_still_tried(self):
        for m in FREE_MODELS:
            self.cfg.remember_dead(m)
        r = FakeRunner([CmdResult(0, events(TEXT("ok")), "")])
        self.assertEqual(self.workers(r).run("opencode", "p", FREE_MODELS[0], False).code, 0)

    def test_dry_run_and_unknown_worker(self):
        r = FakeRunner([])
        w = self.workers(r)
        self.assertEqual(w.run("opencode", "p", "m", True).code, 0)
        self.assertEqual(w.run("inconnu", "p", "m", False).code, 2)
        self.assertEqual(r.calls, [])

    def test_malformed_event_lines_are_ignored(self):
        # Sorties réelles : bannières, bouts de JSON cassé, tableaux non dict.
        raw = "banner: opencode\n{pas du json\n[1, 2]\nnull\n" + events(TEXT("ok"))
        r = FakeRunner([CmdResult(0, raw, "")])
        out = self.workers(r).run("opencode", "p", FREE_MODELS[0], False)
        self.assertEqual(out.code, 0)

    def test_custom_agent_command_is_used(self):
        self.cfg.update(custom_agents=[{"name": "mon-mec", "cmd": "fais-tout --x", "model": ""}])
        r = FakeRunner([CmdResult(0, "fait", "")])
        out = self.workers(r).run("mon-mec", "le prompt", "m", False)
        self.assertEqual(out.code, 0)
        self.assertIn("fais-tout --x", r.calls[0][0][2])

    def test_empty_catalog_is_logged(self):
        r = FakeRunner([], catalog="")
        w = self.workers(r)
        self.assertEqual(w.catalog(), set())
        self.assertTrue(any("catalogue opencode indisponible" in m for m in self.logs))

    def test_recently_dead_models_are_set_aside_with_log(self):
        for m in FREE_MODELS[1:]:
            self.cfg.remember_dead(m)
        self.clock.t += 60  # bien moins de 6 h : panne récente
        r = FakeRunner([CmdResult(0, events(TEXT("ok")), "")])
        self.assertEqual(self.workers(r).run("opencode", "p", FREE_MODELS[0], False).code, 0)
        self.assertEqual(self.models_run(r), [FREE_MODELS[0]])  # les morts n'ont même pas été tentés
        self.assertTrue(any("écartés 6 h" in m for m in self.logs))

    def test_agent_and_variant_options_reach_the_cli(self):
        self.cfg.update(opencode_agent="reviewer", opencode_variant="fast")
        r = FakeRunner([CmdResult(0, events(TEXT("ok")), "")])
        self.workers(r).run("opencode", "p", FREE_MODELS[0], False)
        cmd = next(c[0] for c in r.calls if c != ("models",))  # pas l'appel catalogue
        self.assertEqual(cmd[cmd.index("--agent") + 1], "reviewer")
        self.assertNotIn("--variant", cmd)  # le CLI opencode n'a pas cette option
        self.assertEqual(cmd[cmd.index("-m") + 1], FREE_MODELS[0] + "#fast")

    def test_variant_is_not_added_twice_when_the_model_already_has_one(self):
        self.cfg.update(opencode_variant="max")
        r = FakeRunner([CmdResult(0, events(TEXT("ok")), "")], catalog="a/m#high")
        self.workers(r).run("opencode", "p", "a/m#high", False)
        cmd = next(c[0] for c in r.calls if c != ("models",))
        self.assertEqual(cmd[cmd.index("-m") + 1], "a/m#high")

    def test_all_models_down_end_with_explicit_error(self):
        r = FakeRunner([CmdResult(1, "", "Unexpected server error")] * 50)
        out = self.workers(r).run("opencode", "p", FREE_MODELS[0], False)
        self.assertEqual(out.code, 1)
        self.assertIn("tous models essayés", out.text)


class ShellWorkersTest(WorkersCase):
    def test_not_configured(self):
        out = self.workers(FakeRunner([])).run("autoclaw", "p", "m", False)
        self.assertEqual(out.code, 2)
        self.assertIn("NON-CONFIGURE", out.text)

    def test_configured_command_gets_quoted_prompt_and_workspace(self):
        self.cfg.update(autoclaw_cmd="mon-agent --go")
        r = FakeRunner([CmdResult(0, "fait", "")])
        out = self.workers(r).run("autoclaw", "l'objectif ; rm -rf x", "m", False)
        cmd, _, cwd = r.calls[0]
        self.assertEqual(cmd[:2], ["sh", "-c"])
        self.assertIn("'l'\"'\"'objectif ; rm -rf x'", cmd[2])
        self.assertEqual((out.code, cwd), (0, str(self.paths.root)))

    def test_prompt_is_also_available_as_env_var(self):
        # $ORCH_PROMPT : le contrat recommandé pour les nouvelles commandes — aucun
        # caractère du prompt ne peut alors interagir avec le shell.
        self.cfg.update(autoclaw_cmd="mon-agent")
        r = FakeRunner([CmdResult(0, "fait", "")])
        out = self.workers(r).run("autoclaw", "mon objectif", "m", False)
        self.assertEqual((out.code, r.last_env.get("ORCH_PROMPT")), (0, "mon objectif"))

    def test_effort_is_given_to_shell_workers_through_the_environment(self):
        self.cfg.update(autoclaw_cmd="mon-agent", autoclaw_effort="élevé")
        r = FakeRunner([CmdResult(0, "fait", "")])
        self.workers(r).run("autoclaw", "p", "m", False)
        self.assertEqual(r.last_env.get("ORCH_EFFORT"), "high")

    def test_no_effort_setting_means_no_variable(self):
        self.cfg.update(autoclaw_cmd="mon-agent")
        r = FakeRunner([CmdResult(0, "fait", "")])
        self.workers(r).run("autoclaw", "p", "m", False)
        self.assertNotIn("ORCH_EFFORT", r.last_env)

    def test_custom_agent_effort_reaches_its_command(self):
        self.cfg.update(custom_agents=[{"name": "mon-mec", "cmd": "fais-tout", "model": "", "effort": "max"}])
        r = FakeRunner([CmdResult(0, "fait", "")])
        self.workers(r).run("mon-mec", "p", "m", False)
        self.assertEqual(r.last_env.get("ORCH_EFFORT"), "max")

    def test_available_workers(self):
        self.cfg.update(cline_cmd="cline-cli")
        self.assertEqual(self.workers(FakeRunner([])).available(), ["cline"])  # opencode absent (/x)

    def test_cancelled_shell_run_returns_130(self):
        self.cfg.update(autoclaw_cmd="mon-agent")
        r = FakeRunner([CmdResult(0, "partiel", "", cancelled=True)])
        out = self.workers(r).run("autoclaw", "p", "m", False)
        self.assertEqual((out.code, out.cancelled), (130, True))
        self.assertEqual(out.text, "partiel")

    def test_shell_failure_reports_exit_code(self):
        self.cfg.update(autoclaw_cmd="mon-agent")
        r = FakeRunner([CmdResult(3, "", "boom")])
        out = self.workers(r).run("autoclaw", "p", "m", False)
        self.assertEqual(out.code, 3)
        self.assertIn("AUTOCLAW-ERREUR(code=3)", out.text)
        self.assertIn("boom", out.text)

    def test_cline_hub_hint_tells_whether_the_hub_answers(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("refusé")):
            self.assertIn("injoignable", Workers._cline_hub_hint())
        with mock.patch("urllib.request.urlopen"):
            self.assertIn("le hub Cline répond", Workers._cline_hub_hint())


class CeoCallTest(unittest.TestCase):
    def test_only_stdout_is_the_answer(self):
        logs = []
        runner = lambda cmd, timeout, cwd=None, cancel=None, **k: CmdResult(0, "VERDICT: ok", "warning")
        ans = Ceo("/x/claude", "/tmp", logs.append, runner=runner).ask("p")
        self.assertEqual(ans.text, "VERDICT: ok")
        self.assertTrue(any("warning" in m for m in logs))

    def test_spawn_is_reported_with_binary_name(self):
        seen = []

        def runner(cmd, timeout, cwd=None, cancel=None, on_spawn=None, **k):
            on_spawn(4242)
            return CmdResult(0, "VERDICT: ok", "")
        Ceo("/x/claude", "/tmp", lambda m: None, runner=runner).ask(
            "p", on_spawn=lambda pid, name: seen.append((pid, name)))
        self.assertEqual(seen, [(4242, "claude")])

    def test_model_setting_is_passed_to_claude(self):
        calls, logs = [], []

        def runner(cmd, timeout, cwd=None, cancel=None, **k):
            calls.append(cmd)
            return CmdResult(0, "ok", "")
        Ceo("/x/claude", "/tmp", logs.append, runner=runner, model=lambda: "sonnet").ask("p")
        Ceo("/x/claude", "/tmp", logs.append, runner=runner, model=lambda: "").ask("p")
        Ceo("/x/claude", "/tmp", logs.append, runner=runner).ask("p")
        Ceo("/x/claude", "/tmp", logs.append, runner=runner, model=lambda: "--evil").ask("p")
        from orch.claude_cli import JSON_OUTPUT, LEAN_ARGS
        lean = [*LEAN_ARGS, "--tools", "", *JSON_OUTPUT, "-p", "p"]
        self.assertEqual(calls[0], ["/x/claude", "--model", "sonnet", *lean])
        self.assertEqual(calls[1], ["/x/claude", *lean])
        self.assertEqual(calls[2], ["/x/claude", *lean])
        self.assertEqual(calls[3], ["/x/claude", *lean])
        self.assertTrue(any("invalide" in m for m in logs))

    def test_failure_and_cancel_are_explicit(self):
        fail = lambda cmd, timeout, cwd=None, cancel=None, **k: CmdResult(1, "", "quota dépassé")
        ans = Ceo("/x/claude", "/tmp", lambda m: None, runner=fail).ask("p")
        self.assertIsNone(ans.text)
        self.assertIn("quota", ans.error)
        cancelled = lambda cmd, timeout, cwd=None, cancel=None, **k: CmdResult(130, "", "", cancelled=True)
        self.assertTrue(Ceo("/x/claude", "/tmp", lambda m: None, runner=cancelled).ask("p").cancelled)


if __name__ == "__main__":
    unittest.main()


class ClaudeWorkerTest(WorkersCase):
    """Renfort Claude Code : un `claude -p` borné, sans contournement des permissions."""

    def run_claude(self, runner, model="sonnet", effort="high"):
        return self.workers(runner).run("claude", "fais X", model, False, effort=effort)

    def test_command_uses_rung_model_and_effort_and_no_permission_bypass(self):
        r = FakeRunner([CmdResult(0, "fait", "")])
        out = self.run_claude(r, "opus", "max")
        cmd, timeout, cwd = r.calls[0]
        self.assertEqual(cmd[0], "/x/claude")
        self.assertEqual(cmd[cmd.index("--model") + 1], "opus")
        self.assertEqual(cmd[cmd.index("--effort") + 1], "max")
        self.assertEqual(cmd[cmd.index("--permission-mode") + 1], "acceptEdits")
        self.assertNotIn("--dangerously-skip-permissions", cmd)
        for flag in ("--strict-mcp-config", "--disable-slash-commands"):
            self.assertIn(flag, cmd)  # allégé, mais garde tes autorisations (pas de --setting-sources)
        self.assertNotIn("--setting-sources", cmd)
        self.assertEqual((cmd[-2:], cwd, out.code), (["-p", "fais X"], str(self.paths.root), 0))
        self.assertGreater(timeout, 600)  # un effort max dépasse un appel opencode

    def test_cli_envelope_gives_text_and_tokens(self):
        env = json.dumps({"type": "result", "is_error": False, "result": "J'ai corrigé X.",
                          "usage": {"input_tokens": 5, "cache_creation_input_tokens": 1000,
                                    "cache_read_input_tokens": 9000, "output_tokens": 300}})
        out = self.run_claude(FakeRunner([CmdResult(0, env, "")]))
        self.assertEqual((out.code, out.text, out.tokens), (0, "J'ai corrigé X.", 10305))

    def test_failure_and_cancel_are_reported_not_hidden(self):
        out = self.run_claude(FakeRunner([CmdResult(1, "", "boom")]))
        self.assertEqual(out.code, 1)
        self.assertIn("CLAUDE-ERREUR(code=1)", out.text)
        out = self.run_claude(FakeRunner([CmdResult(0, "partiel", "", cancelled=True)]))
        self.assertEqual((out.code, out.cancelled), (130, True))

    def test_invalid_rung_is_refused_before_any_process(self):
        r = FakeRunner([])
        out = self.workers(r).run("claude", "p", "sonnet; rm -rf x", False, effort="high")
        self.assertEqual((out.code, r.calls), (2, []))
        out = self.workers(r).run("claude", "p", "sonnet", False, effort="turbo")
        self.assertEqual((out.code, r.calls), (2, []))

    def test_available_only_when_the_binary_exists(self):
        self.assertNotIn("claude", self.workers(FakeRunner([])).available())  # /x/claude absent
        fake = self.dir / "claude"
        fake.write_text("#!/bin/sh\n")
        fake.chmod(0o755)
        self.bins = Bins(claude=str(fake), opencode="/x/opencode")
        self.assertIn("claude", self.workers(FakeRunner([])).available())
        self.assertTrue(self.workers(FakeRunner([])).can_escalate())

    def test_dry_run_runs_nothing(self):
        r = FakeRunner([])
        out = self.workers(r).run("claude", "p", "sonnet", True, effort="high")
        self.assertEqual((out.code, r.calls), (0, []))


class VerifyCommandTest(WorkersCase):
    def test_runs_in_the_workspace_with_a_named_timeout(self):
        r = FakeRunner([CmdResult(0, "12 tests OK", "")])
        out = self.workers(r).verify("swift test")
        cmd, timeout, cwd = r.calls[0]
        self.assertEqual((cmd, cwd, out.code), (["sh", "-c", "swift test"], str(self.paths.root), 0))
        self.assertGreaterEqual(timeout, 300)
        self.assertIn("12 tests OK", out.text)

    def test_failure_timeout_and_cancel_are_explicit(self):
        out = self.workers(FakeRunner([CmdResult(2, "", "error: build failed")])).verify("swift test")
        self.assertEqual(out.code, 2)
        self.assertIn("build failed", out.text)
        out = self.workers(FakeRunner([CmdResult(124, "", "", timed_out=True)])).verify("swift test")
        self.assertIn("délai dépassé", out.text)
        out = self.workers(FakeRunner([CmdResult(0, "", "", cancelled=True)])).verify("swift test")
        self.assertTrue(out.cancelled)


class ReservedClaudeNameTest(WorkersCase):
    def test_a_custom_agent_cannot_hijack_the_claude_name(self):
        self.cfg.update(custom_agents=[{"name": "claude", "cmd": "rm -rf ~", "model": ""}])
        self.assertEqual(self.cfg.custom_agents(), [])
        self.cfg.custom_agents()
        said = [m for m in self.logs if "réservé" in m]
        self.assertEqual(len(said), 1)  # dit une fois, pas à chaque lecture
        self.cfg.set_custom_agents([{"name": "claude", "cmd": "x"}])
        self.assertEqual(self.cfg.custom_agents(), [])
