"""Lot 1 — un arrêt ou une panne pendant l'audit du CEO ne valide jamais une tâche en douce."""
import json
import threading

from orch import tasks as T
from orch.ceo import CeoAnswer
from tests.test_engine import EngineCase, row

ONE = json.dumps({"reply": "Je m'en occupe.", "tasks": [{"title": "A", "worker": "opencode", "detail": "a"}]})


class StopDuringReviewTest(EngineCase):
    def test_stop_during_the_ceo_review_requeues_the_task_and_validates_nothing(self):
        cancel = threading.Event()
        self.ceo.hook = cancel.set  # « Arrêter » cliqué pendant que le CEO vérifie
        self.ceo.answers = [CeoAnswer(None, "annulé", cancelled=True)]
        rc = self.engine().converse("Fais A puis B", cancel=cancel)
        first, second = self.store.snapshot()
        self.assertEqual(rc, 1)
        self.assertEqual((first["status"], first.get("interrupted")), (T.BLOCKED, True))
        self.assertNotIn("verdict", first)
        self.assertEqual(first["attempts"], 0)       # l'essai est rendu, comme un arrêt du worker
        self.assertEqual(second["status"], T.TODO)   # rien ne s'enchaîne sur un travail non vérifié
        self.assertEqual(self.ceo.reports, 0)
        self.assertEqual([m.get("kind") for m in self.msgs()].count("stopped"), 1)  # dit une fois

    def test_ctrl_c_during_the_review_requeues_the_task(self):
        self.seed(row(1))

        def ctrl_c():
            raise KeyboardInterrupt
        self.ceo.hook = ctrl_c
        with self.assertRaises(KeyboardInterrupt):
            self.engine().run_next(supervise=True)
        task = self.store.snapshot()[0]
        self.assertEqual((task["status"], task.get("interrupted")), (T.BLOCKED, True))
        self.assertNotIn("verdict", task)


class UnsupervisedIsNotSuccessTest(EngineCase):
    def test_ceo_unavailable_during_review_gives_rc_1_and_an_honest_report(self):
        self.ceo.chat_default = ONE
        self.ceo.answers = [CeoAnswer(None, "CEO indisponible (délai dépassé) : x")]
        rc = self.engine().converse("Fais A")
        task = self.store.snapshot()[0]
        self.assertEqual((task["status"], task["verdict"]), (T.DONE, T.UNSUPERVISED))
        self.assertEqual(rc, 1)
        self.assertEqual(self.ceo.reports, 1)

    def test_a_review_without_verdict_also_gives_rc_1_and_a_report(self):
        self.ceo.chat_default = ONE
        self.ceo.answers = ["Tout me semble bon."]
        self.assertEqual(self.engine().converse("Fais A"), 1)
        self.assertEqual(self.ceo.reports, 1)

    def test_the_final_report_is_written_once_even_when_the_last_task_was_validated(self):
        self.ceo.answers = [CeoAnswer(None, "CEO indisponible (code 1) : x"), "VERDICT: ok\nBien."]
        rc = self.engine().converse("Fais A puis B")
        self.assertEqual([t["verdict"] for t in self.store.snapshot()], [T.UNSUPERVISED, "ok"])
        self.assertEqual(rc, 1)
        self.assertEqual(self.ceo.reports, 1)

    def test_all_validated_still_means_rc_0_and_one_report(self):
        self.assertEqual(self.engine().converse("Fais A puis B"), 0)
        self.assertEqual(self.ceo.reports, 1)
