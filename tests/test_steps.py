"""Étapes d'outils d'un agent (« a utilisé 4 outils ») : lues dans le flux d'opencode, jamais inventées."""
import json
import unittest
from pathlib import Path

from orch.config import FREE_MODELS
from orch.procs import CmdResult
from orch.workers import MAX_STEPS, WorkOutcome, parse_events
from tests.test_engine import EngineCase, row
from tests.test_workers import TEXT, FakeRunner, WorkersCase, events

ROOT = "/Users/demo/Documents/projet"


def tool(name, status="completed", start=1000, end=1120, **inp):
    """Événement `tool_use` de la forme réellement émise par `opencode run --format json`."""
    return {"type": "tool_use", "sessionID": "ses_1",
            "part": {"type": "tool", "tool": name, "callID": "c1",
                     "state": {"status": status, "input": inp, "output": "SECRET-OUTPUT-NE-PAS-GARDER",
                               "time": {"start": start, "end": end}}}}


class ParseStepsTest(unittest.TestCase):
    def steps(self, *evs, root=ROOT):
        return parse_events(events(*evs), root=root).steps

    def test_tool_events_become_steps_with_summary_status_and_duration(self):
        got = self.steps(tool("bash", command="ls -la"), tool("glob", pattern="**/*.md", end=1500),
                         tool("read", status="error", filePath="notes.md"))
        self.assertEqual(got, (
            {"tool": "bash", "summary": "ls -la", "status": "completed", "ms": 120},
            {"tool": "glob", "summary": "**/*.md", "status": "completed", "ms": 500},
            {"tool": "read", "summary": "notes.md", "status": "error", "ms": 120}))

    def test_outputs_are_never_kept(self):
        self.assertNotIn("SECRET-OUTPUT", json.dumps(self.steps(tool("bash", command="ls"))))

    def test_absolute_paths_are_shortened_to_the_project_and_the_home(self):
        home = str(Path.home())
        got = self.steps(tool("read", filePath=f"{ROOT}/src/a.py"),
                         tool("bash", command=f"cat {home}/.zshrc {ROOT}/b.txt"))
        self.assertEqual(got[0]["summary"], "./src/a.py")
        self.assertEqual(got[1]["summary"], "cat ~/.zshrc ./b.txt")

    def test_secrets_in_commands_are_masked_before_anything_is_kept(self):
        got = self.steps(tool("bash", command='OPENAI_API_KEY=sk-live-1234 npm test'),
                         tool("bash", command='curl -H "Authorization: Bearer sk-live-abc" https://x'),
                         tool("bash", command="x " * 60 + "TOKEN=abcdef1234567890"))
        joined = json.dumps(got)
        for leaked in ("sk-live", "abcdef", "1234"):
            self.assertNotIn(leaked, joined)
        self.assertEqual(got[0]["summary"], "OPENAI_API_KEY=*** npm test")

    def test_an_unknown_tool_never_shows_arbitrary_input_fields(self):
        got = self.steps(tool("mystery", content="CONTENU-PRIVE", body="AUTRE"))
        self.assertEqual(got[0]["summary"], "")

    def test_a_path_that_merely_contains_the_project_path_is_left_alone(self):
        got = self.steps(tool("read", filePath="/mnt/srv/projet/x"), tool("read", filePath="/srv/projet-2/y"),
                         tool("read", filePath="/srv/projet/z"), root="/srv/projet")
        self.assertEqual([s["summary"] for s in got], ["/mnt/srv/projet/x", "/srv/projet-2/y", "./z"])

    def test_the_resolved_form_of_the_project_path_is_shortened_too(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            real = os.path.realpath(d)  # /private/var/… pour /var/… sur macOS
            got = self.steps(tool("read", filePath=real + "/a.txt"), root=d)
            self.assertEqual(got[0]["summary"], "./a.txt")

    def test_a_huge_command_is_summarised_fast_and_still_masked(self):
        import time
        cmd = "OPENAI_API_KEY=sk-live-1234 " + "a-" * 500000  # ≈ 1 Mo
        start = time.monotonic()
        got = self.steps(tool("bash", command=cmd))
        self.assertLess(time.monotonic() - start, 0.5)
        self.assertTrue(got[0]["summary"].startswith("OPENAI_API_KEY=*** "))
        self.assertNotIn("sk-live", got[0]["summary"])

    def test_summary_is_one_bounded_line(self):
        got = self.steps(tool("bash", command="echo un\n  echo deux\t" + "x" * 400))
        self.assertNotIn("\n", got[0]["summary"])
        self.assertLessEqual(len(got[0]["summary"]), 141)
        self.assertTrue(got[0]["summary"].endswith("…"))
        self.assertTrue(got[0]["summary"].startswith("echo un echo deux"))

    def test_odd_shapes_do_not_break_the_parse(self):
        odd = [{"type": "tool_use", "part": {"type": "tool", "tool": "write"}},          # sans state
               {"type": "tool_use", "part": {"tool": "x", "state": "pas un dict"}},
               {"type": "tool_use", "part": {"tool": "y", "state": {"input": "texte", "time": {"start": "a"}}}},
               {"type": "tool_use", "part": "n'importe quoi"},
               {"type": "tool_use"}]
        got = self.steps(*odd)
        self.assertEqual([s["tool"] for s in got], ["write", "x", "y", "outil", "outil"])  # un tool_use = une étape
        self.assertTrue(all(s["status"] == "unknown" and s["ms"] is None and s["summary"] == "" for s in got))

    def test_steps_are_capped_and_the_rest_is_counted(self):
        ev = parse_events(events(*[tool("bash", command=f"c{i}") for i in range(MAX_STEPS + 7)]), root=ROOT)
        self.assertEqual((len(ev.steps), ev.steps_omitted), (MAX_STEPS, 7))
        self.assertTrue(ev.used_tools)

    def test_text_only_runs_have_no_steps(self):
        ev = parse_events(events(TEXT("bonjour")), root=ROOT)
        self.assertEqual((ev.steps, ev.steps_omitted, ev.used_tools), ((), 0, False))


class WorkersStepsTest(WorkersCase):
    def test_success_carries_the_steps(self):
        r = FakeRunner([CmdResult(0, events(tool("bash", command="ls"), TEXT("fini")), "")])
        out = self.workers(r).run("opencode", "p", FREE_MODELS[0], False)
        self.assertEqual([s["tool"] for s in out.steps], ["bash"])
        self.assertEqual(out.steps_omitted, 0)

    def test_shortened_against_the_working_folder(self):
        cmd = f"cat {self.paths.root}/a.txt"
        r = FakeRunner([CmdResult(0, events(tool("bash", command=cmd), TEXT("ok")), "")])
        out = self.workers(r).run("opencode", "p", FREE_MODELS[0], False)
        self.assertEqual(out.steps[0]["summary"], "cat ./a.txt")

    def test_steps_come_from_the_attempt_that_is_returned(self):
        r = FakeRunner([CmdResult(0, events({"type": "error", "error": {"name": "APIError"}}), ""),
                        CmdResult(0, events(tool("glob", pattern="*.py"), TEXT("ok")), "")])
        out = self.workers(r).run("opencode", "p", FREE_MODELS[0], False)
        self.assertEqual([s["tool"] for s in out.steps], ["glob"])

    def test_cancelled_run_keeps_what_was_done_before_the_stop(self):
        r = FakeRunner([CmdResult(130, events(tool("write", file="a")), "", cancelled=True)])
        out = self.workers(r).run("opencode", "p", FREE_MODELS[0], False)
        self.assertTrue(out.cancelled)
        self.assertEqual([s["tool"] for s in out.steps], ["write"])

    def test_workers_without_tools_report_no_steps(self):
        out = self.workers(FakeRunner([])).run("opencode", "p", FREE_MODELS[0], True)  # dry-run
        self.assertEqual((out.steps, out.steps_omitted), ((), 0))


class EngineStepsTest(EngineCase):
    def result_event(self):
        return next(m for m in self.msgs() if m.get("kind") == "task_result")

    def test_task_result_event_carries_steps_and_the_stopped_flag(self):
        self.seed(row(1))
        steps = ({"tool": "bash", "summary": "ls", "status": "completed", "ms": 80},)
        self.workers.outcomes = [WorkOutcome(0, "fait", steps=steps, steps_omitted=3)]
        self.engine().run_next()
        data = self.result_event()["data"]
        self.assertEqual(data["steps"], list(steps))
        self.assertEqual((data["steps_omitted"], data["cancelled"]), (3, False))
        self.assertEqual(data["status"], "done")

    def test_the_agent_answer_is_kept_long_enough_to_be_read_formatted(self):
        self.seed(row(1))
        answer = "## Résultat\n" + "- une ligne de détail\n" * 120  # ≈ 2 500 caractères
        self.workers.outcomes = [WorkOutcome(0, answer)]
        self.engine().run_next()
        self.assertGreater(len(self.result_event()["text"]), 2000)
        self.assertTrue(self.result_event()["text"].endswith("- une ligne de détail"))

    def test_a_secret_echoed_by_the_agent_is_masked_in_the_chat_event(self):
        self.seed(row(1))
        self.workers.outcomes = [WorkOutcome(0, "J'ai lancé OPENAI_API_KEY=sk-live-9999 npm test")]
        self.engine().run_next()
        text = self.result_event()["text"]
        self.assertNotIn("sk-live", text)
        self.assertIn("OPENAI_API_KEY=***", text)

    def test_a_task_without_tools_has_an_empty_step_list(self):
        self.seed(row(1))
        self.workers.outcomes = [WorkOutcome(0, "fait")]
        self.engine().run_next()
        data = self.result_event()["data"]
        self.assertEqual((data["steps"], data["steps_omitted"]), ([], 0))

    def test_a_stopped_task_is_flagged_so_the_page_can_say_stopped(self):
        self.seed(row(1))
        self.workers.outcomes = [WorkOutcome(130, "interrompu", cancelled=True)]
        self.engine().run_next()
        self.assertTrue(self.result_event()["data"]["cancelled"])


if __name__ == "__main__":
    unittest.main()
