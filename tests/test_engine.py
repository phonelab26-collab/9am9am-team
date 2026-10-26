import json
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path

from orch import tasks as T
from orch.ceo import ADVICE_UNKNOWN, CeoAnswer
from orch.chat import ChatStore
from orch.config import ConfigStore, Paths
from orch.engine import Engine
from orch.escalation import escalated
from orch.prompts import FOLLOWUP_ADVICE
from orch.store import Store
from orch.workers import WorkOutcome

PLAN2 = json.dumps([{"title": "A", "worker": "opencode", "detail": "a"},
                    {"title": "B", "worker": "opencode", "detail": "b"}])


class FakeWorkers:
    def __init__(self, outcomes=None, workers=("opencode",), claude=False):
        self.outcomes = list(outcomes or [])
        self.prompts = []
        self.runs = []  # (worker, model, effort)
        self._available = list(workers)
        self.claude = claude
        self.checks, self.checks_run = [], []
        self.touch = None  # dossier où chaque exécution modifie un fichier (simule un vrai travail)

    def available(self):
        return self._available

    def can_escalate(self):
        return self.claude

    def verify(self, cmd, cancel=None, on_spawn=None):
        self.checks_run.append(cmd)
        return self.checks.pop(0) if self.checks else WorkOutcome(0, "tests OK")

    def run(self, worker, prompt, model, dry_run, cancel=None, on_spawn=None, effort=None):
        self.prompts.append(prompt)
        self.runs.append((worker, model, effort))
        if self.touch is not None:
            (self.touch / f"travail-{len(self.runs)}.txt").write_text(str(len(self.runs)))
        if dry_run:
            return WorkOutcome(0, "[dry-run] OK")
        out = self.outcomes.pop(0) if self.outcomes else WorkOutcome(0, "fait")
        if isinstance(out, Exception):
            raise out
        return out


class FakeCeo:
    def __init__(self, answers=None, plan=PLAN2):
        self.answers = list(answers or [])
        self.plan = plan
        self.calls = 0
        self.reports = 0
        self.prompts = []
        self.chat_answers = []
        self.chat_default = json.dumps({"reply": "Je m'en occupe.", "tasks": [
            {"title": "A", "worker": "opencode", "detail": "a"},
            {"title": "B", "worker": "opencode", "detail": "b"}]})
        self.report_text = "Rapport : tout est fait."
        self.hook = None
        self.tools = []
        self.tokens = None  # tokens annoncés par appel (None = non mesuré, comme un vieux CLI)

    def ask(self, prompt, cancel=None, on_spawn=None, tools=""):
        self.prompts.append(prompt)
        self.tools.append(tools)
        if "Message de l'utilisateur" in prompt:
            ans = self.chat_answers.pop(0) if self.chat_answers else self.chat_default
            return ans if isinstance(ans, CeoAnswer) else CeoAnswer(ans, tokens=self.tokens)
        if "Découpe l'objectif" in prompt:
            return CeoAnswer(self.plan, tokens=self.tokens)
        if "Réponds directement à l'utilisateur" in prompt:
            self.reports += 1
            if isinstance(self.report_text, CeoAnswer):
                return self.report_text
            return CeoAnswer(self.report_text, tokens=self.tokens)
        self.calls += 1
        if on_spawn:
            on_spawn(99999, "claude")
        if self.hook:
            self.hook()
        ans = self.answers.pop(0) if self.answers else "VERDICT: ok\nSuite."
        return ans if isinstance(ans, CeoAnswer) else CeoAnswer(ans, tokens=self.tokens)


class EngineCase(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="orch-eng-"))
        self.paths = Paths.for_root(self.dir)
        self.logs = []
        self.store = Store(self.paths, self.logs.append)
        self.cfg = ConfigStore(self.paths, self.logs.append)
        self.chat = ChatStore(self.paths, self.logs.append)
        self.workers = FakeWorkers()
        self.ceo = FakeCeo()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def engine(self):
        return Engine(self.paths, self.store, self.cfg, self.workers, self.ceo, self.logs.append, chat=self.chat)

    def msgs(self):
        """Conversation sans le bilan de consommation final (testé à part, ClaudeUsageTest)."""
        return [m for m in self.chat.since(0) if m.get("kind") != "claude_usage"]

    def statuses(self):
        return [t["status"] for t in self.store.snapshot()]

    def said(self, text):
        return any(text in m for m in self.logs)

    def seed(self, *rows):
        self.paths.state.mkdir(parents=True, exist_ok=True)
        self.paths.tasks.write_text(json.dumps(list(rows)), encoding="utf-8")


def row(i, goal="G", status="todo", attempts=0, **extra):
    base = {"id": i, "goal": goal, "title": f"t{i}", "worker": "opencode", "detail": "",
            "status": status, "result": "", "attempts": attempts}
    base.update(extra)
    return base


ONE_TASK = json.dumps({"reply": "Je m'en occupe.", "tasks": [
    {"title": "Dur", "worker": "opencode", "detail": "tâche difficile"}]})


class EscalationTest(EngineCase):
    """Tâche trop dure pour opencode : Claude Code prend le relais, palier par palier, sans boucle."""

    def converse(self, verdicts, outcomes=None, claude=True):
        self.workers = FakeWorkers(outcomes, claude=claude)
        self.ceo.chat_answers = [ONE_TASK]
        self.ceo.answers = list(verdicts)
        return self.engine().converse("Fais la chose dure")

    def test_refusals_climb_the_ladder_then_stop_with_an_honest_report(self):
        rc = self.converse(["VERDICT: ko\ntrop faible"] * 5)
        self.assertEqual(rc, 1)
        self.assertEqual([(w, m, e) for w, m, e in self.workers.runs],
                         [("opencode", self.workers.runs[0][1], None),
                          ("claude", "sonnet", "high"), ("claude", "opus", "max")])
        t = self.store.snapshot()[0]
        self.assertEqual((t["status"], t["worker"], t["tier"], t["origin_worker"]),
                         ("failed", "claude", 2, "opencode"))
        self.assertEqual(self.ceo.reports, 1)  # le CEO explique l'échec, une seule fois
        self.assertEqual(self.ceo.calls, 3)    # 3 supervisions, pas une de plus

    def test_claude_succeeds_at_the_first_rung(self):
        self.assertEqual(self.converse(["VERDICT: ko\nincomplet", "VERDICT: ok"]), 0)
        self.assertEqual([w for w, _, _ in self.workers.runs], ["opencode", "claude"])
        t = self.store.snapshot()[0]
        self.assertEqual((t["status"], t["verdict"], t["tier"]), ("done", "ok", 1))
        esc = [m for m in self.msgs() if m.get("kind") == "escalation"]
        self.assertEqual(len(esc), 1)
        self.assertIn("Sonnet · élevé", esc[0]["text"])
        self.assertTrue(self.said("Claude Code reprend"))

    def test_report_after_a_hard_task_tells_the_user_to_step_back_down(self):
        self.converse(["VERDICT: ko\nincomplet", "VERDICT: ok"])
        report = self.msgs()[-1]
        self.assertEqual(report["kind"], "report")
        self.assertIn("Rapport : tout est fait.", report["text"])
        self.assertTrue(report["text"].rstrip().endswith(FOLLOWUP_ADVICE))

    def test_no_followup_advice_while_the_plan_is_unfinished_or_failed(self):
        # Régression : « la tâche dure est terminée » s'affichait alors qu'une tâche avait ÉCHOUÉ.
        self.seed(row(1, "G", "done", 1, worker="claude", tier=1, origin_worker="opencode", plan_id="p"),
                  row(2, "G", "failed", 3, plan_id="p", last_failure="worker_error"),
                  row(3, "G", "todo", 0, plan_id="p"))
        self.engine().report("G")
        text = self.chat.since(0)[-1]["text"]
        self.assertNotIn("pour la suite", text)
        self.assertNotIn("terminée", text)

    def test_report_without_escalation_has_no_followup_advice(self):
        self.converse(["VERDICT: ok"])
        self.assertNotIn("pour la suite", self.msgs()[-1]["text"])

    def test_worker_timeout_is_retried_on_opencode_never_escalated(self):
        rc = self.converse(["VERDICT: ok"], outcomes=[WorkOutcome(124, "(délai dépassé) model en panne")])
        self.assertEqual(rc, 0)
        self.assertEqual([w for w, _, _ in self.workers.runs], ["opencode", "opencode"])
        self.assertEqual([m for m in self.msgs() if m.get("kind") == "escalation"], [])
        self.assertTrue(self.said("nouvel essai sur opencode"))
        self.assertFalse(any("trop dure" in m for m in self.logs))

    def test_escalation_message_states_the_real_cause(self):
        self.converse(["VERDICT: ko\nincomplet", "VERDICT: ok"])
        esc = [m["text"] for m in self.msgs() if m.get("kind") == "escalation"][0]
        self.assertIn("le CEO a refusé", esc)
        self.assertNotIn("trop dure", esc)

    def test_escalation_can_be_switched_off(self):
        self.cfg.update(escalation="off")
        rc = self.converse(["VERDICT: ko\nnon"] * 5)
        self.assertEqual(rc, 1)
        self.assertEqual([w for w, _, _ in self.workers.runs], ["opencode"] * T.MAX_ATTEMPTS)
        self.assertTrue(self.said("escalade désactivée"))

    def test_escalated_prompt_carries_the_takeover_and_the_ceo_feedback(self):
        self.converse(["VERDICT: ko\nil manque les tests", "VERDICT: ok"])
        self.assertIn("Tu reprends", self.workers.prompts[1])
        self.assertIn("opencode", self.workers.prompts[1])
        self.assertIn("il manque les tests", self.workers.prompts[1])
        self.assertNotIn("Tu reprends", self.workers.prompts[0])

    def test_without_claude_the_task_stays_on_its_worker_and_still_ends(self):
        rc = self.converse(["VERDICT: ko\nnon"] * 5, claude=False)
        self.assertEqual(rc, 1)
        self.assertEqual([w for w, _, _ in self.workers.runs], ["opencode"] * T.MAX_ATTEMPTS)
        self.assertEqual(self.store.snapshot()[0]["status"], "failed")
        self.assertTrue(self.said("renfort Claude Code indisponible"))

    def test_tick_restricted_to_another_worker_does_not_escalate(self):
        self.workers = FakeWorkers(claude=True)
        self.seed(row(1, status="blocked", attempts=1))
        self.engine().tick("G", worker="opencode")
        self.assertEqual([w for w, _, _ in self.workers.runs], ["opencode"])
        self.assertNotIn("tier", self.store.snapshot()[0])

    def test_second_step_message_says_it_climbs_to_opus(self):
        self.converse(["VERDICT: ko\nnon", "VERDICT: ko\nnon", "VERDICT: ok"])
        esc = [m["text"] for m in self.msgs() if m.get("kind") == "escalation"]
        self.assertEqual(len(esc), 2)
        self.assertIn("monte à Opus · max", esc[1])

    def test_user_stop_never_escalates(self):
        cancel = threading.Event()
        self.workers = FakeWorkers([WorkOutcome(130, "stop", cancelled=True)], claude=True)
        self.workers_hook = None
        self.ceo.chat_answers = [ONE_TASK]
        orig = self.workers.run

        def run_and_cancel(*a, **kw):
            out = orig(*a, **kw)
            cancel.set()
            return out
        self.workers.run = run_and_cancel
        self.engine().converse("Fais la chose dure", cancel=cancel)
        t = self.store.snapshot()[0]
        self.assertEqual((t["worker"], t.get("tier")), ("opencode", None))
        self.assertEqual(len(self.workers.runs), 1)


class ClaudeUsageTest(EngineCase):
    """Chaque demande se termine par la consommation Claude réelle : rien de caché."""

    def usage_events(self):
        return [m for m in self.chat.since(0) if m.get("kind") == "claude_usage"]

    def test_delegation_reports_ceo_calls_and_says_free_agents_cost_nothing(self):
        self.ceo.tokens = 7000
        self.assertEqual(self.engine().converse("Fais A puis B"), 0)
        [ev] = self.usage_events()
        # chat + 2 supervisions + rapport = 4 appels CEO
        self.assertIn("4 appels", ev["text"])
        self.assertIn("28", ev["text"])  # 4 × 7 000 tokens ≈ 28 k
        self.assertIn("agents gratuits", ev["text"])
        self.assertEqual(ev["data"]["ceo_calls"], 4)
        self.assertEqual(ev["data"]["tokens"], 28000)

    def test_supervision_reads_files_other_calls_have_no_tools(self):
        self.engine().converse("Fais A puis B")
        self.assertEqual(sorted(set(self.ceo.tools)), ["", "Read,Glob,Grep"])
        sup = [t for p, t in zip(self.ceo.prompts, self.ceo.tools) if "VERDICT" in p and "Tâche" in p]
        self.assertTrue(sup)
        self.assertEqual(set(sup), {"Read,Glob,Grep"})

    def test_unmeasured_calls_are_said_not_invented(self):
        self.ceo.tokens = None
        self.engine().converse("Fais A puis B")
        [ev] = self.usage_events()
        self.assertIn("non mesuré", ev["text"])
        self.assertIsNone(ev["data"]["tokens"])

    def test_direct_answer_also_reports_its_single_call(self):
        self.ceo.tokens = 5000
        self.ceo.chat_answers = ['{"reply": "Oui.", "tasks": []}']
        self.engine().converse("Es-tu là ?")
        [ev] = self.usage_events()
        self.assertIn("1 appel", ev["text"])


class VerifyTest(EngineCase):
    """Commande de vérification : tests d'abord (gratuit), audit de Claude ensuite."""

    def setUp(self):
        super().setUp()
        self.workers.touch = self.dir
        self.cfg.set_verify_cmd(self.paths.root, "make check")
        self.ceo.chat_answers = [json.dumps({"reply": "Je m'en occupe.", "tasks": [
            {"title": "A", "worker": "opencode", "detail": "a"}]})]

    def supervisions(self):
        return [p for p in self.ceo.prompts if "Résultat du worker" in p]

    def test_red_check_sends_the_task_back_to_opencode_without_calling_claude(self):
        self.workers.checks = [WorkOutcome(1, "FAIL: test_x expected 2 got 3")]
        self.assertEqual(self.engine().converse("Corrige A"), 0)
        self.assertEqual([w for w, _, _ in self.workers.runs], ["opencode", "opencode"])
        self.assertEqual(len(self.supervisions()), 1)            # seul l'essai aux tests verts est audité
        self.assertIn("FAIL: test_x", self.workers.prompts[1])   # l'agent reçoit l'erreur
        self.assertIn("Vérification automatique", self.workers.prompts[1])
        checks = [m for m in self.msgs() if m.get("kind") == "check"]
        self.assertEqual([c["data"]["ok"] for c in checks], [False, True])
        self.assertIn("sans appel à Claude", checks[0]["text"])

    def test_green_check_is_shown_to_the_ceo(self):
        self.engine().converse("Corrige A")
        self.assertIn("make check", self.supervisions()[0])
        self.assertIn("réussi", self.supervisions()[0])

    def test_repeated_red_checks_end_in_failure_never_in_escalation(self):
        self.workers.claude = True
        self.workers.checks = [WorkOutcome(1, "rouge")] * T.MAX_ATTEMPTS
        self.assertEqual(self.engine().converse("Corrige A"), 1)
        self.assertEqual([w for w, _, _ in self.workers.runs], ["opencode"] * T.MAX_ATTEMPTS)
        self.assertEqual(self.supervisions(), [])
        t = self.store.snapshot()[0]
        self.assertEqual((t["status"], t["verdict"]), ("failed", "check_ko"))

    def test_no_file_changed_means_no_check(self):
        self.workers.touch = None
        self.engine().converse("Corrige A")
        self.assertEqual(self.workers.checks_run, [])
        self.assertTrue(self.said("aucun fichier modifié"))

    def test_after_a_red_check_the_retry_is_always_verified_even_without_changes(self):
        # Régression (revue) : essai 2 sans modification → la vérification ne doit PAS être sautée.
        self.workers.checks = [WorkOutcome(1, "rouge"), WorkOutcome(1, "toujours rouge")] + [WorkOutcome(1, "r")]
        orig = self.workers.run
        def run_then_stop_touching(*a, **kw):
            out = orig(*a, **kw)
            self.workers.touch = None  # après le 1er essai, l'agent ne modifie plus rien
            return out
        self.workers.run = run_then_stop_touching
        self.engine().converse("Corrige A")
        self.assertEqual(len(self.workers.checks_run), T.MAX_ATTEMPTS)
        self.assertEqual(self.supervisions(), [])  # jamais audité avec des tests connus rouges
        self.assertEqual(self.store.snapshot()[0]["status"], "failed")

    def test_command_not_found_or_too_slow_fails_fast_without_burning_attempts(self):
        for code, word in ((127, "introuvable"), (124, "délai")):
            with self.subTest(code=code):
                self.setUp()
                self.workers.checks = [WorkOutcome(code, "sh: swiftt: command not found")]
                self.assertEqual(self.engine().converse("Corrige A"), 1)
                t = self.store.snapshot()[0]
                self.assertEqual((t["status"], t["verdict"]), ("failed", "check_error"))
                self.assertEqual(len(self.workers.runs), 1)  # pas d'essais gaspillés sur un réglage cassé
                self.assertIn(word, t["review"])
                self.assertIsNone(escalated(t))

    def test_summary_shows_avoided_checks_even_without_any_claude_call(self):
        self.ceo.chat_answers = []
        e = self.engine()
        e._usage = e._new_usage()
        e._usage["avoided"] = 2
        e._say_usage()
        [ev] = [m for m in self.chat.since(0) if m.get("kind") == "claude_usage"]
        self.assertIn("2 vérifications Claude évitées", ev["text"])

    def test_off_means_no_check_even_if_a_command_could_be_detected(self):
        (self.paths.root / "Makefile").write_text("check-fast:\n\techo ok\n")
        self.cfg.set_verify_cmd(self.paths.root, "off")
        self.engine().converse("Corrige A")
        self.assertEqual(self.workers.checks_run, [])

    def test_detected_command_is_used_once_approved_and_announced(self):
        (self.paths.root / "Makefile").write_text("check-fast:\n\techo ok\n")
        self.cfg.set_verify_cmd(self.paths.root, "")
        self.cfg.approve_verify(self.paths.root, "make check-fast")  # sans accord : rien (test_verify_consent)
        self.engine().converse("Corrige A")
        self.assertEqual(self.workers.checks_run, ["make check-fast"])
        self.assertTrue(self.said("détectée dans le projet et autorisée"))

    def test_nothing_to_run_when_nothing_is_set_or_detected(self):
        self.cfg.set_verify_cmd(self.paths.root, "")
        self.engine().converse("Corrige A")
        self.assertEqual(self.workers.checks_run, [])

    def test_usage_counts_claude_calls_avoided(self):
        self.ceo.tokens = 4000
        self.workers.checks = [WorkOutcome(1, "rouge")]
        self.engine().converse("Corrige A")
        [ev] = [m for m in self.chat.since(0) if m.get("kind") == "claude_usage"]
        self.assertIn("1 vérification Claude évitée", ev["text"])
        self.assertEqual(ev["data"]["avoided"], 1)


class PlanTest(EngineCase):
    def test_plan_saves_tasks_with_goal_and_plan_id(self):
        self.assertEqual(self.engine().plan("G", dry_run=False), 0)
        snap = self.store.snapshot()
        self.assertEqual([t["title"] for t in snap], ["A", "B"])
        self.assertEqual({t["goal"] for t in snap}, {"G"})
        self.assertEqual(len({t["plan_id"] for t in snap}), 1)

    def test_refusals_are_explicit(self):
        e = self.engine()
        self.assertEqual(e.plan("   ", dry_run=False), 1)
        self.assertTrue(self.said("Objectif vide"))
        e.plan("G", dry_run=False)
        self.assertEqual(e.plan("G", dry_run=False), 1)
        self.assertTrue(self.said("déjà planifié"))
        self.workers._available = []
        self.assertEqual(e.plan("H", dry_run=False), 1)
        self.assertTrue(self.said("Aucun worker"))

    def test_ceo_failure_or_bad_plan_saves_nothing(self):
        self.ceo.plan = "pas de json"
        self.assertEqual(self.engine().plan("G", dry_run=False), 1)
        self.assertTrue(self.said("Plan refusé"))
        self.assertEqual(self.store.snapshot(), [])

    def test_two_concurrent_plans_of_same_goal_make_one_plan(self):
        e = self.engine()
        slow = self.ceo.ask

        def delayed(prompt, cancel=None):
            time.sleep(0.2)
            return slow(prompt, cancel)
        self.ceo.ask = delayed
        rcs = []
        threads = [threading.Thread(target=lambda: rcs.append(e.plan("G", dry_run=False))) for _ in range(2)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        self.assertEqual(sorted(rcs), [0, 1])
        self.assertEqual(len(self.store.snapshot()), 2)


class RunTest(EngineCase):
    def test_run_without_supervision_is_marked(self):
        self.seed(row(1))
        rep = self.engine().run_next()
        self.assertEqual((rep.rc, rep.task["status"], rep.task["verdict"]), (0, "done", T.UNSUPERVISED))
        self.assertTrue(rep.run_id)
        self.assertEqual(self.ceo.calls, 0)

    def test_worker_failure_blocks(self):
        self.workers.outcomes = [WorkOutcome(1, "boom")]
        self.seed(row(1))
        rep = self.engine().run_next()
        self.assertEqual((rep.rc, self.statuses()), (1, ["blocked"]))

    def test_worker_crash_never_leaves_in_progress(self):
        self.workers.outcomes = [RuntimeError("bug")]
        self.seed(row(1))
        self.assertEqual(self.engine().run_next().rc, 1)
        self.assertEqual(self.statuses(), ["blocked"])
        self.assertIn("RuntimeError", self.store.snapshot()[0]["result"])

    def test_cancel_gives_attempt_back(self):
        self.workers.outcomes = [WorkOutcome(130, "", cancelled=True)]
        self.seed(row(1))
        self.assertEqual(self.engine().run_next().rc, 1)
        t = self.store.snapshot()[0]
        self.assertEqual((t["status"], t["attempts"]), ("blocked", 0))

    def test_dependency_blocks_with_message(self):
        self.seed(row(1, status="failed"), row(2))
        self.assertEqual(self.engine().run_next().rc, 1)
        self.assertTrue(self.said("Rien d'exécutable"))
        self.assertTrue(self.said("retry"))
        self.assertEqual(self.statuses(), ["failed", "todo"])

    def test_nothing_to_do(self):
        self.assertEqual(self.engine().run_next().rc, 0)
        self.assertTrue(self.said("Rien à faire"))


class TickTest(EngineCase):
    def test_new_goal_planned_run_and_validated(self):
        self.assertEqual(self.engine().tick("G"), 0)
        snap = self.store.snapshot()
        self.assertEqual([(t["status"], t.get("verdict")) for t in snap], [("done", "ok"), ("todo", None)])
        self.assertIn("Objectif global : G", self.workers.prompts[0])

    def test_ko_then_retry_with_feedback_then_ok(self):
        self.ceo.answers = ["VERDICT: ko\nIl manque la case Questions.", "VERDICT: ok"]
        e = self.engine()
        self.assertEqual(e.tick("G"), 1)
        t = self.store.snapshot()[0]
        self.assertEqual((t["status"], t["review"]), ("blocked", "Il manque la case Questions"))
        self.assertEqual(e.tick("G"), 0)
        self.assertIn("Il manque la case Questions", self.workers.prompts[-1])
        t = self.store.snapshot()[0]
        self.assertEqual((t["status"], t["prev_review"]), ("done", "Il manque la case Questions"))

    def test_ko_forever_ends_failed_then_tick_stops(self):
        self.ceo.answers = ["VERDICT: ko\nnon"] * T.MAX_ATTEMPTS
        e = self.engine()
        for _ in range(T.MAX_ATTEMPTS):
            e.tick("G")
        self.assertEqual(self.store.snapshot()[0]["status"], "failed")
        self.assertEqual(e.tick("G"), 1)
        self.assertTrue(self.said("retry"))
        self.assertEqual(self.ceo.calls, T.MAX_ATTEMPTS)

    def test_ceo_unavailable_or_unclear_is_not_validated(self):
        self.ceo.answers = [CeoAnswer(None, "CEO indisponible (code 1)"), "VERDICT: ok ou ko"]
        e = self.engine()
        self.assertEqual(e.tick("G"), 1)
        self.assertEqual(e.tick("G"), 1)
        snap = self.store.snapshot()
        self.assertEqual([t["verdict"] for t in snap], [T.UNSUPERVISED, T.UNSUPERVISED])
        self.assertTrue(self.said("NON validée"))

    def test_ceo_not_called_when_worker_failed(self):
        self.workers.outcomes = [WorkOutcome(1, "boom")]
        self.engine().tick("G")
        self.assertEqual(self.ceo.calls, 0)

    def test_goal_tasks_only_old_tasks_untouched(self):
        self.seed(row(9, goal=""))
        self.engine().tick("G")
        snap = {t["id"]: t["status"] for t in self.store.snapshot()}
        self.assertEqual(snap[9], "todo")
        self.assertEqual(snap[10], "done")

    def test_finished_goal_is_not_replanned(self):
        self.seed(row(1, status="done"))
        self.assertEqual(self.engine().tick("G"), 0)
        self.assertEqual(len(self.store.snapshot()), 1)
        self.assertTrue(self.said("déjà planifié"))

    def test_failed_goal_asks_for_retry(self):
        self.seed(row(1, status="failed", attempts=T.MAX_ATTEMPTS))
        self.assertEqual(self.engine().tick("G"), 1)
        self.assertEqual(len(self.store.snapshot()), 1)
        self.assertTrue(self.said("retry"))

    def test_blocked_of_goal_resumed_without_replan(self):
        self.seed(row(1, status="blocked", attempts=1))
        self.assertEqual(self.engine().tick("G"), 0)
        self.assertEqual(self.statuses(), ["done"])

    def test_empty_goal(self):
        self.assertEqual(self.engine().tick(""), 1)
        self.seed(row(1, status="failed", attempts=3))
        self.assertEqual(self.engine().tick(""), 1)
        self.assertTrue(self.said("retry"))

    def test_verdict_refused_if_task_changed_during_supervision(self):
        def tamper():
            doc = json.loads(self.paths.tasks.read_text(encoding="utf-8"))
            (doc["tasks"] if isinstance(doc, dict) else doc)[0]["owner"]["run_id"] = "autre"
            self.paths.tasks.write_text(json.dumps(doc), encoding="utf-8")
        self.ceo.hook = tamper
        self.assertEqual(self.engine().tick("G"), 1)
        self.assertTrue(self.said("refusée"))

    def test_supervising_ceo_is_tracked_as_child(self):
        seen = []
        self.ceo.hook = lambda: seen.append(self.store.snapshot()[0]["owner"])
        self.engine().tick("G")
        self.assertEqual((seen[0]["phase"], seen[0]["child_pgid"], seen[0]["child_cmd"]),
                         ("review", 99999, "claude"))

    def test_dry_run_tick(self):
        self.assertEqual(self.engine().tick("G", dry_run=True), 0)
        self.assertEqual(self.store.snapshot()[0]["verdict"], T.UNSUPERVISED)
        self.assertTrue(self.said("dry-run"))
        self.assertEqual(self.ceo.calls, 0)


class ReportTest(EngineCase):
    def test_last_validated_task_triggers_the_ceo_report(self):
        e = self.engine()
        e.tick("G")
        self.assertEqual(self.ceo.reports, 0)  # une tâche sur deux seulement
        e.tick("G")
        self.assertEqual(self.ceo.reports, 1)
        rep = self.store.reports()
        self.assertEqual((rep[0]["goal"], rep[0]["text"]), ("G", "Rapport : tout est fait."))
        self.assertTrue(self.said("Rapport du CEO"))

    def test_no_report_while_something_is_not_done_or_validated(self):
        self.ceo.answers = ["VERDICT: ko\nnon"]
        self.engine().tick("G")
        self.assertEqual(self.ceo.reports, 0)

    def test_no_report_in_dry_run_or_for_legacy_tasks(self):
        self.engine().tick("G", dry_run=True)
        self.seed(row(1, goal=""))
        self.engine().tick("")
        self.assertEqual(self.ceo.reports, 0)

    def test_ceo_failure_does_not_change_the_tick_result(self):
        self.ceo.report_text = CeoAnswer(None, "CEO indisponible (code 1)")
        e = self.engine()
        e.tick("G")
        self.assertEqual(e.tick("G"), 0)
        self.assertEqual(self.store.reports(), [])
        self.assertTrue(self.said("Rapport impossible"))

    def test_report_on_demand_and_default_goal(self):
        self.seed(row(1, goal="ancien", status="done"), row(2, goal="récent", status="done"))
        self.assertEqual(self.engine().report(""), 0)
        self.assertEqual(self.store.reports()[0]["goal"], "récent")

    def test_report_needs_tasks(self):
        self.assertEqual(self.engine().report("inconnu"), 1)
        self.assertTrue(self.said("aucune tâche"))
        self.assertEqual(self.ceo.reports, 0)

    def test_report_action_in_dry_run_asks_nothing(self):
        self.seed(row(1, status="done"))
        self.assertEqual(self.engine().report("G", dry_run=True), 0)
        self.assertEqual(self.ceo.reports, 0)


class ConverseTest(EngineCase):
    def roles(self):
        return [(m["role"], m.get("kind") or "") for m in self.msgs()]

    def kinds(self):
        return [m.get("kind") for m in self.msgs()]

    def test_direct_answer_needs_no_worker(self):
        self.ceo.chat_answers = ['{"reply": "Oui, je suis Claude Code.", "tasks": [], '
                                 '"conseil": {"modele": "Sonnet 5.5", "effort": "faible", "raison": "question simple"}}']
        self.assertEqual(self.engine().converse("Es-tu Claude ?"), 0)
        msgs = self.msgs()
        self.assertEqual([(m["role"], m["text"]) for m in msgs],
                         [("user", "Es-tu Claude ?"),
                          ("ceo", "Modèle conseillé : Sonnet 5.5 · faible — question simple\nOui, je suis Claude Code.")])
        self.assertEqual((self.workers.prompts, self.store.snapshot()), ([], []))

    def test_delegation_plans_runs_verifies_and_reports(self):
        self.assertEqual(self.engine().converse("Fais A puis B"), 0)
        self.assertEqual([(t["status"], t["verdict"]) for t in self.store.snapshot()], [("done", "ok")] * 2)
        kinds = self.kinds()
        self.assertEqual(kinds[:2], [None, "reply"])
        for wanted in ("plan", "task_start", "task_result", "verdict", "report"):
            self.assertIn(wanted, kinds)
        self.assertEqual(kinds.count("task_start"), 2)
        msgs = self.msgs()
        self.assertEqual((msgs[0]["role"], msgs[1]["role"], msgs[1]["text"]), ("user", "ceo", ADVICE_UNKNOWN + "\nJe m'en occupe."))
        self.assertEqual(msgs[-1]["kind"], "report")
        self.assertEqual(msgs[-1]["text"], "Rapport : tout est fait.")
        self.assertEqual(self.ceo.reports, 1)
        self.assertEqual(next(m for m in msgs if m.get("kind") == "plan")["data"]["tasks"][0]["title"], "A")

    def test_refusal_is_retried_with_the_ceo_feedback(self):
        self.ceo.answers = ["VERDICT: ko\nIl manque X.", "VERDICT: ok", "VERDICT: ok"]
        self.assertEqual(self.engine().converse("Fais A puis B"), 0)
        self.assertEqual([t["status"] for t in self.store.snapshot()], ["done", "done"])
        self.assertIn("Il manque X", self.workers.prompts[1])
        self.assertEqual(self.kinds().count("verdict"), 3)

    def test_repeated_refusals_end_with_an_honest_report(self):
        self.ceo.answers = ["VERDICT: ko\nnon"] * T.MAX_ATTEMPTS
        self.assertEqual(self.engine().converse("Fais A puis B"), 1)
        snap = self.store.snapshot()
        self.assertEqual([t["status"] for t in snap], ["failed", "todo"])
        self.assertEqual(self.ceo.reports, 1)
        self.assertEqual(self.msgs()[-1]["kind"], "report")

    def test_ceo_unavailable_is_said_in_the_conversation(self):
        self.ceo.chat_answers = [CeoAnswer(None, "CEO indisponible (code 1)")]
        self.assertEqual(self.engine().converse("Salut"), 1)
        last = self.msgs()[-1]
        self.assertEqual((last["role"], last["kind"]), ("ceo", "error"))
        self.assertIn("indisponible", last["text"])

    def test_answer_without_json_is_kept_as_a_reply(self):
        self.ceo.chat_answers = ["Bonjour, ça va bien."]
        self.assertEqual(self.engine().converse("Ça va ?"), 0)
        self.assertEqual(self.msgs()[-1]["text"], ADVICE_UNKNOWN + "\nBonjour, ça va bien.")
        self.assertEqual(self.store.snapshot(), [])

    def test_empty_answer_is_reported(self):
        self.ceo.chat_answers = ['{"reply": "", "tasks": []}']
        self.assertEqual(self.engine().converse("Salut"), 1)
        self.assertEqual(self.msgs()[-1]["kind"], "error")

    def test_cannot_delegate_without_a_worker(self):
        self.workers._available = []
        self.assertEqual(self.engine().converse("Fais A"), 1)
        last = self.msgs()[-1]
        self.assertEqual(last["kind"], "error")
        self.assertIn("aucun worker", last["text"].lower())
        self.assertEqual(self.store.snapshot(), [])

    def test_history_is_sent_to_the_ceo(self):
        self.ceo.chat_answers = ['{"reply": "Première réponse", "tasks": []}',
                                 '{"reply": "Seconde", "tasks": []}']
        e = self.engine()
        e.converse("Première question")
        e.converse("Suite ?")
        second = self.ceo.prompts[-1]
        self.assertIn("Première question", second)
        self.assertIn("Première réponse", second)
        self.assertTrue(second.rstrip().endswith("Suite ?") or "Suite ?" in second.split("Message de l'utilisateur")[1])

    def test_same_message_twice_makes_two_plans(self):
        e = self.engine()
        e.converse("Fais A puis B")
        e.converse("Fais A puis B")
        snap = self.store.snapshot()
        self.assertEqual(len(snap), 4)
        self.assertEqual(len({t["plan_id"] for t in snap}), 2)
        self.assertTrue(all(t["status"] == "done" for t in snap))

    def test_empty_message_is_refused(self):
        self.assertEqual(self.engine().converse("   "), 1)
        self.assertEqual(self.msgs(), [])

    def test_stop_ends_the_loop_and_says_so(self):
        cancel = threading.Event()
        self.workers.outcomes = [WorkOutcome(130, "", cancelled=True)]
        real_run = self.workers.run

        def run_then_stop(*args, **kwargs):  # l'utilisateur clique « Arrêter » pendant le worker
            out = real_run(*args, **kwargs)
            cancel.set()
            return out
        self.workers.run = run_then_stop
        self.assertEqual(self.engine().converse("Fais A puis B", cancel=cancel), 1)
        self.assertEqual([t["status"] for t in self.store.snapshot()], ["blocked", "todo"])
        self.assertEqual(self.msgs()[-1]["kind"], "stopped")
        self.assertEqual(self.ceo.reports, 0)

    def test_dry_run_converse(self):
        self.assertEqual(self.engine().converse("Fais A", dry_run=True), 0)
        self.assertEqual(self.ceo.calls, 0)
        self.assertEqual(len(self.store.snapshot()), 1)
        self.assertEqual(self.store.snapshot()[0]["status"], "done")

    def test_events_only_when_a_chat_is_attached(self):
        e = Engine(self.paths, self.store, self.cfg, self.workers, self.ceo, self.logs.append)
        self.assertEqual(e.converse("Fais A puis B"), 0)  # sans ChatStore : pas d'erreur
        self.assertEqual(self.msgs(), [])


class OtherTest(EngineCase):
    def test_retry_resets_blocked_and_failed(self):
        self.seed(row(1, status="blocked", attempts=2), row(2, status="failed", attempts=3))
        self.assertEqual(self.engine().retry(), 0)
        self.assertEqual([(t["status"], t["attempts"]) for t in self.store.snapshot()], [("todo", 0), ("todo", 0)])

    def test_status_is_read_only_and_reports_problems(self):
        self.seed(row(1, status="in_progress"))
        before = self.paths.tasks.read_bytes()
        lines = self.engine().status_lines()
        self.assertEqual(self.paths.tasks.read_bytes(), before)
        self.assertTrue(any("[1]" in ln for ln in lines))
        self.paths.tasks.write_text("{oops", encoding="utf-8")
        self.assertTrue(any("illisible" in ln for ln in self.engine().status_lines()))
        self.assertTrue(self.paths.tasks.exists())

    def test_status_marks_verdicts(self):
        self.seed(row(1, status="done", verdict="ok"), row(2, status="done", verdict=T.UNSUPERVISED))
        text = "\n".join(self.engine().status_lines())
        self.assertIn("validé", text)
        self.assertIn("non supervisé", text)


class CloudRepoPromptTest(EngineCase):
    """Mode cloud : le dépôt GitHub choisi est injecté dans les prompts du CEO."""

    def _cloud_engine(self, repo="phonelab26-collab/9am9am-team"):
        from orch.ceo import Ceo
        from orch.procs import CmdResult
        seen = []
        ceo = Ceo("claude", str(self.dir), self.logs.append,
                  runner=lambda cmd, **k: (seen.append(cmd), CmdResult(0, PLAN2, ""))[1],
                  mode=lambda: "cloud", cloud_session=lambda: "session_abc123",
                  cloud_repo=lambda: repo)
        return Engine(self.paths, self.store, self.cfg, self.workers, ceo, self.logs.append,
                      chat=self.chat), seen

    def test_plan_prompt_contains_the_selected_repo(self):
        e, seen = self._cloud_engine()
        self.assertEqual(e.plan("G", dry_run=False), 0)
        prompt = seen[0][-1]
        self.assertIn("phonelab26-collab/9am9am-team", prompt)
        self.assertIn("https://github.com/phonelab26-collab/9am9am-team", prompt)
        self.assertIn("Découpe l'objectif", prompt)

    def test_plan_prompt_asks_for_a_repo_when_none_selected(self):
        e, seen = self._cloud_engine(repo="")
        self.assertEqual(e.plan("G", dry_run=False), 0)
        self.assertIn("Aucun dépôt GitHub n'est sélectionné", seen[0][-1])

    def test_local_mode_never_injects_the_repo(self):
        from orch.ceo import Ceo
        from orch.procs import CmdResult
        seen = []
        ceo = Ceo("claude", str(self.dir), self.logs.append,
                  runner=lambda cmd, **k: (seen.append(cmd), CmdResult(0, PLAN2, ""))[1],
                  mode=lambda: "local", cloud_session=lambda: "session_abc123",
                  cloud_repo=lambda: "phonelab26-collab/9am9am-team")
        e = Engine(self.paths, self.store, self.cfg, self.workers, ceo, self.logs.append, chat=self.chat)
        self.assertEqual(e.plan("G", dry_run=False), 0)
        self.assertNotIn("github.com", seen[0][-1])
        self.assertNotIn("--cloud", seen[0])  # aucun drapeau cloud en local


if __name__ == "__main__":
    unittest.main()
