"""Escalade opencode → Claude Code : échelle bornée, monotone, sans jamais boucler."""
import unittest

from orch import tasks as T
from orch.escalation import CLAUDE_WORKER, MAX_TIER, current_tier, escalated, rung_for


def task(**extra):
    base = {"id": 1, "worker": "opencode", "status": "blocked", "attempts": 1, "goal": "G",
            "last_failure": "ceo_ko"}
    base.update(extra)
    return base


class LadderTest(unittest.TestCase):
    def test_first_escalation_is_claude_sonnet_high(self):
        new = escalated(task())
        self.assertEqual((new["worker"], new["tier"], new["origin_worker"]), (CLAUDE_WORKER, 1, "opencode"))
        rung = rung_for(new)
        self.assertEqual((rung.model, rung.effort), ("sonnet", "high"))

    def test_second_escalation_is_claude_opus_max(self):
        new = escalated(escalated(task()))
        self.assertEqual(new["tier"], MAX_TIER)
        rung = rung_for(new)
        self.assertEqual((rung.model, rung.effort), ("opus", "max"))
        self.assertEqual(new["origin_worker"], "opencode")  # l'origine n'est jamais écrasée

    def test_top_rung_cannot_escalate_further(self):
        top = escalated(escalated(task()))
        self.assertIsNone(escalated(top))

    def test_task_planned_on_claude_starts_at_sonnet_and_can_reach_opus(self):
        t = task(worker=CLAUDE_WORKER)
        self.assertEqual(current_tier(t), 1)
        self.assertEqual(rung_for(t).model, "sonnet")
        self.assertEqual(escalated(t)["tier"], 2)

    def test_worker_failure_is_never_escalated(self):
        # Timeout, model en panne, erreur : ce n'est pas de la difficulté, c'est un nouvel essai gratuit.
        self.assertIsNone(escalated(task(last_failure="worker_error")))
        self.assertIsNone(escalated(task(last_failure=None)))

    def test_cancelled_task_is_not_escalated(self):
        self.assertIsNone(escalated(task(interrupted=True)))

    def test_non_claude_task_has_no_rung(self):
        self.assertIsNone(rung_for(task()))

    def test_input_is_never_mutated(self):
        t = task()
        escalated(t)
        self.assertEqual(t, task())

    def test_garbage_tier_is_treated_as_unescalated(self):
        for bad in ("2", None, -3, True, 99):
            self.assertIn(current_tier(task(tier=bad)), (0, MAX_TIER))


class RequeueEscalationTest(unittest.TestCase):
    def test_requeue_escalates_only_when_asked(self):
        out, n = T.requeue_blocked([task()], "G")
        self.assertEqual((n, out[0]["worker"], out[0]["status"]), (1, "opencode", "todo"))
        out, n = T.requeue_blocked([task()], "G", escalate=True)
        self.assertEqual((n, out[0]["worker"], out[0]["tier"], out[0]["status"]), (1, CLAUDE_WORKER, 1, "todo"))

    def test_cap_limits_escalated_tasks_per_request(self):
        from orch.escalation import MAX_ESCALATED_PER_PLAN
        done = [task(id=i, worker=CLAUDE_WORKER, tier=1, origin_worker="opencode", status="done",
                     plan_id="p", last_failure=None) for i in range(1, MAX_ESCALATED_PER_PLAN + 1)]
        fresh = task(id=99, plan_id="p")
        out, n = T.requeue_blocked(done + [fresh], "G", escalate=True)
        self.assertEqual((n, out[-1]["worker"], out[-1]["status"]), (1, "opencode", "todo"))
        # une tâche DÉJÀ escaladée peut encore monter (même tâche, pas une de plus)
        climbing = task(id=98, plan_id="p", worker=CLAUDE_WORKER, tier=1, origin_worker="opencode")
        out, _ = T.requeue_blocked(done[:-1] + [climbing], "G", escalate=True)
        self.assertEqual(out[-1]["tier"], 2)

    def test_cancelled_task_is_requeued_unchanged_and_flag_cleared(self):
        out, _ = T.requeue_blocked([task(interrupted=True)], "G", escalate=True)
        self.assertEqual((out[0]["worker"], "interrupted" in out[0]), ("opencode", False))

    def test_exhausted_attempts_are_not_requeued_even_with_escalation(self):
        out, n = T.requeue_blocked([task(attempts=T.MAX_ATTEMPTS)], "G", escalate=True)
        self.assertEqual((n, out[0]["status"]), (0, "blocked"))

    def test_full_ladder_ends_in_failure_after_max_attempts(self):
        """Simule le moteur : chaque essai échoue ; jamais plus de MAX_ATTEMPTS exécutions."""
        t = task(status="todo", attempts=0)
        runs = []
        for _ in range(20):  # garde-fou du test : l'échelle doit s'arrêter bien avant
            if t["status"] != "todo":
                break
            t = {**t, "status": "in_progress", "attempts": t["attempts"] + 1}
            runs.append((t["worker"], rung_for(t) and rung_for(t).model))
            t = T.apply_verdict(t, "ko", "insuffisant")
            if t["status"] == "blocked":
                [t], _ = T.requeue_blocked([t], "G", escalate=True)
        self.assertEqual(runs, [("opencode", None), (CLAUDE_WORKER, "sonnet"), (CLAUDE_WORKER, "opus")])
        self.assertEqual(t["status"], "failed")


if __name__ == "__main__":
    unittest.main()
