"""Lot 1 — « Reprise par Claude Code : désactivée — les agents gratuits seulement » veut dire :
Claude Code (payant) n'est ni proposé au CEO, ni lancé, même sur une tâche qui lui était confiée."""
from tests.test_engine import EngineCase, FakeWorkers, row

WORKERS_LINE = "Workers DISPONIBLES (utilise UNIQUEMENT ceux-là) : "


class ClaudeOffTest(EngineCase):
    def setUp(self):
        super().setUp()
        self.workers = FakeWorkers(workers=("opencode", "claude"), claude=True)

    def offered(self, prompt):
        return prompt.split(WORKERS_LINE, 1)[1].split("\n", 1)[0]

    def test_disabled_claude_is_not_offered_to_the_ceo_in_conversation(self):
        self.cfg.update(escalation="off")
        self.engine().converse("Fais A puis B")
        self.assertEqual(self.offered(self.ceo.prompts[0]), "opencode")
        self.assertNotIn("« claude »", self.ceo.prompts[0])

    def test_disabled_claude_is_not_offered_when_planning_a_goal(self):
        self.cfg.update(escalation="off")
        self.engine().tick("Objectif")
        plan = next(p for p in self.ceo.prompts if "Découpe l'objectif" in p)
        self.assertEqual(self.offered(plan), "opencode")

    def test_enabled_claude_is_still_offered(self):
        self.cfg.update(escalation="on")
        self.engine().converse("Fais A puis B")
        self.assertEqual(self.offered(self.ceo.prompts[0]), "opencode, claude")

    def test_the_refusal_fails_once_is_never_retried_and_costs_nothing(self):
        self.cfg.update(escalation="off")
        self.seed(row(1, goal="Fais A puis B", worker="claude"))
        self.engine().converse("Fais A puis B")
        refused = self.store.snapshot()[0]
        self.assertEqual((refused["status"], refused["attempts"]), ("failed", 1))
        self.assertNotEqual(refused.get("last_failure"), "worker_error")  # ce n'est pas une panne
        claude_runs = [r for r in self.workers.runs if r[0] == "claude"]
        self.assertEqual(claude_runs, [])
        usage = next(m for m in self.chat.since(0) if m.get("kind") == "claude_usage")
        self.assertEqual(usage["data"]["worker_calls"], 0)  # aucun appel payant annoncé à tort
        self.assertFalse(any("a échoué à l'exécution" in line for line in self.logs))

    def test_a_task_already_given_to_claude_is_not_run_when_disabled_and_says_why(self):
        self.cfg.update(escalation="off")
        self.seed(row(1, worker="claude"))
        self.engine().run_next()
        self.assertEqual(self.workers.runs, [])
        task = self.store.snapshot()[0]
        self.assertNotEqual(task["status"], "done")
        self.assertIn("désactivé", task["result"])
