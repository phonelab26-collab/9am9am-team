import json
import unittest
from pathlib import Path

from orch import tasks as T

FIXTURES = Path(__file__).parent / "fixtures"


def row(i, group="G", status="todo", worker="opencode", attempts=0, **extra):
    base = {"id": i, "goal": group, "title": f"t{i}", "worker": worker, "detail": "",
            "status": status, "result": "", "attempts": attempts}
    base.update(extra)
    return base


class ValidateTest(unittest.TestCase):
    def test_legacy_file_is_valid(self):
        data = json.loads((FIXTURES / "legacy_tasks.json").read_text(encoding="utf-8"))
        self.assertEqual(len(T.validate(data)), 13)

    def test_v1_file_is_valid(self):
        data = json.loads((FIXTURES / "v1_tasks.json").read_text(encoding="utf-8"))
        self.assertEqual(len(T.validate(data)), 4)

    def test_rejects_bad_shapes(self):
        bad = [
            {"tasks": []},
            [1],
            [row(1), row(1)],
            [dict(row(1), id="1")],
            [dict(row(1), id=True)],
            [dict(row(1), status="pending")],
            [dict(row(1), title=3)],
            # champs qui pilotent des int()/kill : jamais une valeur douteuse (plantage sinon)
            [dict(row(1), attempts="2")],
            [dict(row(1), attempts="abc")],
            [dict(row(1), attempts=True)],
            [dict(row(1), attempts=1.5)],
            [dict(row(1), status="in_progress", owner=[1])],
            [dict(row(1), status="in_progress", owner={"child_pgid": "4242"})],
        ]
        for data in bad:
            with self.subTest(data=data), self.assertRaises(T.TaskFileError):
                T.validate(data)

    def test_acceptable_shapes_still_pass(self):
        ok = [dict(row(1), attempts=None),
              dict(row(1), status="in_progress", owner={}),
              dict(row(1), status="in_progress",
                   owner={"pid": 12, "run_id": "r", "phase": "work", "child_pgid": 4242})]
        for data in ok:
            with self.subTest(data=data):
                self.assertEqual(len(T.validate([data])), 1)


class GroupAndDependencyTest(unittest.TestCase):
    def test_group_key(self):
        self.assertEqual(T.group_key({"plan_id": "P1", "goal": "G"}), "plan:P1")
        self.assertEqual(T.group_key({"goal": " G "}), "goal:G")
        self.assertIsNone(T.group_key({"goal": ""}))

    def test_blocking_task(self):
        ts = [row(1, status="done"), row(2, status="failed"), row(3), row(4, group="H")]
        self.assertEqual((T.blocking_task(ts[2], ts) or {}).get("id"), 2)
        self.assertIsNone(T.blocking_task(ts[3], ts))

    def test_task_under_review_still_blocks(self):
        ts = [row(1, status="in_progress"), row(2)]
        self.assertEqual(T.blocking_task(ts[1], ts)["id"], 1)

    def test_legacy_tasks_never_blocked(self):
        ts = [row(1, group="", status="failed"), row(2, group="")]
        self.assertIsNone(T.blocking_task(ts[1], ts))

    def test_next_ready_skips_blocked_group(self):
        ts = [row(1, status="failed"), row(2), row(3, group="H")]
        pick, waiting = T.next_ready(ts)
        self.assertEqual(pick["id"], 3)
        self.assertIsNone(waiting)

    def test_next_ready_reports_what_waits(self):
        ts = [row(1, status="failed"), row(2)]
        pick, waiting = T.next_ready(ts)
        self.assertIsNone(pick)
        self.assertEqual((waiting[0]["id"], waiting[1]["id"]), (2, 1))

    def test_worker_filter_does_not_jump_the_queue(self):
        ts = [row(1), row(2, worker="cline")]
        pick, waiting = T.next_ready(ts, worker="cline")
        self.assertIsNone(pick)
        self.assertEqual(waiting[1]["id"], 1)

    def test_goal_filter_is_strict(self):
        ts = [row(1, group="OLD"), row(2, group="G", status="in_progress"), row(3, group="G")]
        self.assertIsNone(T.next_ready(ts, goal="G")[0])  # la 3 attend la 2 : pas de repli sur OLD
        self.assertEqual(T.next_ready(ts)[0]["id"], 1)
        self.assertEqual(T.next_ready([row(1, group="OLD"), row(2, group="G")], goal="G")[0]["id"], 2)


class TransitionsTest(unittest.TestCase):
    def test_verdict_ok_validates_and_archives_feedback(self):
        t = row(1, status="in_progress", review="vide", owner={"run_id": "r"})
        out = T.apply_verdict(t, "ok", "suite")
        self.assertEqual((out["status"], out["verdict"]), ("done", "ok"))
        self.assertNotIn("review", out)
        self.assertNotIn("owner", out)
        self.assertEqual(out["prev_review"], "vide")

    def test_verdict_ko_blocks_with_feedback(self):
        out = T.apply_verdict(row(1, status="in_progress", attempts=1), "ko", "Manque X")
        self.assertEqual((out["status"], out["verdict"], out["review"]), ("blocked", "ko", "Manque X"))

    def test_verdict_ko_without_detail_is_explicit(self):
        self.assertTrue(T.apply_verdict(row(1, status="in_progress"), "ko", "")["review"])

    def test_verdict_ko_on_last_attempt_fails(self):
        out = T.apply_verdict(row(1, status="in_progress", attempts=T.MAX_ATTEMPTS), "ko", "x")
        self.assertEqual(out["status"], "failed")

    def test_feedback_is_bounded(self):
        out = T.apply_verdict(row(1, status="in_progress"), "ko", "x " * 900)
        self.assertLessEqual(len(out["review"]), 800)

    def test_no_verdict_means_done_but_unsupervised(self):
        out = T.apply_verdict(row(1, status="in_progress"), None, "CEO muet")
        self.assertEqual((out["status"], out["verdict"]), ("done", T.UNSUPERVISED))

    def test_apply_verdict_does_not_mutate(self):
        t = row(1, status="in_progress")
        T.apply_verdict(t, "ko", "x")
        self.assertEqual(t["status"], "in_progress")

    def test_requeue_blocked_of_one_goal(self):
        ts = [row(1, status="blocked", attempts=1), row(2, group="H", status="blocked", attempts=1),
              row(3, status="blocked", attempts=T.MAX_ATTEMPTS), row(4, status="failed"), row(5, status="done")]
        out, n = T.requeue_blocked(ts, "G")
        self.assertEqual(n, 1)
        self.assertEqual([t["status"] for t in out], ["todo", "blocked", "blocked", "failed", "done"])
        self.assertEqual(ts[0]["status"], "blocked")
        self.assertEqual(T.requeue_blocked(ts, "")[1], 2)

    def test_reset_stuck(self):
        ts = [row(1, status="blocked", attempts=2), row(2, status="failed", attempts=3), row(3, status="done")]
        out, n = T.reset_stuck(ts)
        self.assertEqual(n, 2)
        self.assertEqual([(t["status"], t["attempts"]) for t in out], [("todo", 0), ("todo", 0), ("done", 0)])

    def test_next_id(self):
        self.assertEqual(T.next_id([]), 1)
        self.assertEqual(T.next_id([row(4), row(9)]), 10)


class EnvelopeTest(unittest.TestCase):
    """pack/unpack : tasks.json est versionné {"schema": 2, "tasks": [...]}."""

    def test_roundtrip(self):
        tasks = [row(1), row(2)]
        self.assertEqual(T.unpack(T.pack(tasks)), tasks)

    def test_legacy_list_is_schema_1(self):
        self.assertEqual(T.unpack([row(1)]), [row(1)])

    def test_newer_schema_refused(self):
        with self.assertRaisesRegex(T.TaskFileError, "version plus récente"):
            T.unpack({"schema": T.SCHEMA + 1, "tasks": []})

    def test_broken_envelopes_refused(self):
        for bad in ({"schema": 2},                       # tasks manquant
                    {"schema": "two", "tasks": []},      # schema non entier
                    {"schema": True, "tasks": []},       # bool ≠ entier ici
                    {"tasks": []},                       # pas de schema
                    42, "liste", None):
            with self.subTest(bad=bad), self.assertRaises(T.TaskFileError):
                T.unpack(bad)

    def test_tasks_inside_envelope_are_still_validated(self):
        with self.assertRaises(T.TaskFileError):
            T.unpack({"schema": 2, "tasks": [dict(row(1), status="??")]})


if __name__ == "__main__":
    unittest.main()
